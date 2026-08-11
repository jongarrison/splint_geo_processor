"""
BrepUnion2.py

Two ways to merge breps into one solid:
  * robust_brep_union: the general-purpose case. Does the obvious thing first
    (Brep.CreateBooleanUnion at document tolerance) and raises cleanly on failure. Use this
    when the two solids' intersection isn't already known analytically - CreateBooleanUnion has
    to discover it, which is where the classic failure modes (coincident/tangent faces,
    insufficient overlap) come from.
  * graft_open_brep_into_face: for the special case where a caller has *already engineered* an
    exact analytic match between an open brep's mouth and a hole it wants cut into another
    brep's planar face (e.g. SupportPathRamp.py's ramp duct, whose mouth curve is the same
    curve used to cut the hole). No boolean regularization at all - just cut the hole (planar
    face rebuild) and Brep.JoinBreps the pieces together, since JoinBreps only needs to zipper
    naked edges it already knows coincide. Much more reliable than CreateBooleanUnion for this
    narrow case, but only applicable when that exact-match precondition holds.

This replaces the over-engineered BrepUnion.py for new code. Existing older splint
designs still use BrepUnion.py and should migrate here when refactored.

Fallback strategies for robust_brep_union will be added incrementally only when real failures
demand them (same incremental spirit as BrepDifference.py's history).
"""

import Rhino.Geometry as rg
import scriptcontext as sc
from splintcommon import log


class BrepUnionError(Exception):
    """Raised when a brep merge (boolean union or graft-join) fails."""
    pass


def _log_naked_edges(brep, label):
    """Diagnostic: log the count and midpoint of every naked (unpaired) edge on brep. Used to
    pinpoint exactly where a JoinBreps result failed to zipper into a closed solid."""
    naked = [e for e in brep.Edges if e.Valence == rg.EdgeAdjacency.Naked]
    log("{0}: {1} naked edge(s)".format(label, len(naked)))
    for e in naked:
        mid = e.PointAtNormalizedLength(0.5)
        log("  naked edge at ({0:.3f}, {1:.3f}, {2:.3f}), length={3:.3f}mm".format(
            mid.X, mid.Y, mid.Z, e.GetLength()))


def robust_brep_union(breps, tolerance=None):
    """Union a list of breps into one solid.

    Args:
        breps: list of rg.Brep (at least 2, all must be valid closed solids).
        tolerance: float or None (defaults to doc model absolute tolerance).

    Returns:
        tuple: (result_brep, True, method_string) on success.

    Raises:
        BrepUnionError: union failed (returned None, empty, or multiple pieces).
        ValueError: fewer than 2 valid breps provided.
    """
    if not breps or len(breps) < 2:
        raise ValueError("robust_brep_union requires at least 2 breps (got {0})".format(
            0 if not breps else len(breps)))

    tol = tolerance
    if tol is None or tol <= 0.0:
        tol = sc.doc.ModelAbsoluteTolerance

    # Log input diagnostics
    for i, b in enumerate(breps):
        vol = b.GetVolume() if b is not None else 0
        log("BrepUnion2: input[{0}] IsValid={1} IsSolid={2} faces={3} vol={4:.1f}".format(
            i, b.IsValid if b else False, b.IsSolid if b else False,
            b.Faces.Count if b else 0, vol))

    # The obvious call - same thing Rhino's UI BooleanUnion does.
    result = rg.Brep.CreateBooleanUnion(breps, tol)

    if result is None or len(result) == 0:
        raise BrepUnionError(
            "Brep.CreateBooleanUnion returned nothing (tolerance={0})".format(tol))

    if len(result) == 1:
        log("BrepUnion2: SUCCESS - 1 result brep, faces={0} vol={1:.1f}".format(
            result[0].Faces.Count, result[0].GetVolume()))
        return result[0], True, "CreateBooleanUnion(tol={0})".format(tol)

    # Multiple results means the inputs didn't actually merge (returned as separate pieces).
    vols = [b.GetVolume() for b in result]
    log("BrepUnion2: FAILED - CreateBooleanUnion returned {0} separate pieces "
        "(volumes: {1}). Inputs likely don't volumetrically overlap.".format(
            len(result), ["%.1f" % v for v in vols]))
    raise BrepUnionError(
        "CreateBooleanUnion returned {0} separate breps instead of 1 merged solid "
        "(volumes: {1}) - insufficient volumetric overlap between inputs".format(
            len(result), ["%.1f" % v for v in vols]))


def graft_open_brep_into_face(base_brep, face_index, new_outer_curve, open_duct_brep,
                              tolerance=None, join_tolerance=None, graft_debug=None):
    """Replace base_brep.Faces[face_index]'s OUTER boundary with new_outer_curve and Join in
    open_duct_brep to produce one closed, watertight solid - with NO boolean regularization.

    This is for grafting a protrusion whose mouth sits ON the face's own perimeter (e.g.
    SupportPathRamp.py's ramp, rooted on a rail that IS part of the splint's outer silhouette) -
    NOT for punching an interior hole. Because the new feature touches the face's boundary
    rather than sitting purely inside it, the face's OUTER LOOP itself must change shape (bulge
    outward along the new feature's footprint); existing INNER loops (bores etc.) are preserved
    untouched. The caller is responsible for actually splicing new_outer_curve together (see
    SupportPathRamp.py's boundary-splice helper) - this function only rebuilds the face and
    joins in the duct.

    Given that new_outer_curve is coplanar with the face and open_duct_brep's mouth is
    literally the same curve as the notch spliced into new_outer_curve, Brep.JoinBreps only has
    to zipper together naked edges it already knows coincide - a much more reliable path than
    asking Brep.CreateBooleanUnion to discover an intersection between two overlapping solids
    (the classic coincident-face / insufficient-overlap failure modes robust_brep_union has no
    fallback for).

    Steps:
      1. Duplicate base_brep; collect the face's existing INNER loops only (e.g. a bore rim
         that already punches through this face) as 3D curves - the outer loop is replaced
         wholesale by new_outer_curve, not preserved.
      2. Rebuild the face as a single planar Brep via Brep.CreatePlanarBreps, passing
         new_outer_curve plus the preserved inner loop curves.
      3. Remove the old face from base_brep (Faces.RemoveAt + Compact), leaving its neighbors'
         edges naked at that footprint.
      4. Brep.JoinBreps([body_without_old_face, new_face, open_duct_brep], tolerance) - the
         naked edges at the old/new boundary and at the duct's mouth should all zipper together
         since they are literally the same curves.

    Args:
        base_brep: rg.Brep to graft into. Not mutated; a new Brep is returned.
        face_index: index into base_brep.Faces of the single planar face to rebuild (typically
            resolved via BrepEdgeLocator.find_planar_face_by_plane).
        new_outer_curve: the face's complete NEW outer boundary - closed, planar, coplanar with
            base_brep.Faces[face_index]. Caller builds this by splicing the new feature's
            footprint into the face's existing outer loop (this function does not construct or
            verify that splice).
        open_duct_brep: an open (non-solid) rg.Brep whose ONLY naked boundary is the same curve
            spliced into new_outer_curve - typically a swept/lofted tube, closed everywhere
            except its mouth.
        tolerance: float or None (defaults to doc model absolute tolerance). Used for the
            CreatePlanarBreps face rebuild - keep this TIGHT (document tolerance). A loosened
            tolerance here can make a genuinely-separate nearby inner loop (e.g. an anchor bore
            a fraction of a mm from the new notch) read as touching the new outer boundary,
            which splits the rebuilt face into multiple pieces instead of one.
        join_tolerance: float or None (defaults to `tolerance`). Used ONLY for the final
            Brep.JoinBreps zip step. This is the one that may need loosening beyond document
            tolerance (e.g. to absorb a few microns of upstream chamfer drift at the shared
            boundary) without affecting the face rebuild's inner-loop separation above.

    Returns:
        rg.Brep: the closed, watertight, outward-oriented result.

    Raises:
        BrepUnionError: face_index is out of range, the face rebuild fails, or JoinBreps does
            not merge everything into a single closed solid.
    """
    tol = tolerance
    if tol is None or tol <= 0.0:
        tol = sc.doc.ModelAbsoluteTolerance
    jtol = join_tolerance
    if jtol is None or jtol <= 0.0:
        jtol = tol

    if face_index < 0 or face_index >= base_brep.Faces.Count:
        raise BrepUnionError(
            "graft_open_brep_into_face: face_index {0} out of range (brep has {1} "
            "face(s))".format(face_index, base_brep.Faces.Count))

    result = base_brep.DuplicateBrep()
    old_face = result.Faces[face_index]

    # Preserve only INNER loops (pre-existing holes, e.g. an anchor bore rim through this cap).
    # The OUTER loop is replaced wholesale by new_outer_curve, not preserved.
    loop_curves = [new_outer_curve]
    for i in range(old_face.Loops.Count):
        loop = old_face.Loops[i]
        if loop.LoopType != rg.BrepLoopType.Inner:
            continue
        loop_curve = loop.To3dCurve()
        if loop_curve is None:
            raise BrepUnionError(
                "graft_open_brep_into_face: failed to extract loop {0} of face {1} as a 3D "
                "curve".format(i, face_index))
        loop_curves.append(loop_curve)

    new_face_pieces = rg.Brep.CreatePlanarBreps(loop_curves, tol)
    if new_face_pieces is None or len(new_face_pieces) != 1:
        n = 0 if new_face_pieces is None else len(new_face_pieces)
        raise BrepUnionError(
            "graft_open_brep_into_face: rebuilding face {0} with the new outer boundary did "
            "not yield exactly one planar face (got {1}) - check new_outer_curve is coplanar, "
            "closed, and non-self-intersecting".format(face_index, n))
    new_face_brep = new_face_pieces[0]
    if graft_debug is not None:
        graft_debug["new_face_brep"] = new_face_brep

    result.Faces.RemoveAt(face_index)
    result.Compact()
    if graft_debug is not None:
        graft_debug["body_without_face"] = result.DuplicateBrep()

    pieces_to_join = [result, new_face_brep, open_duct_brep]
    for i, b in enumerate(pieces_to_join):
        log("BrepUnion2.graft: input[{0}] IsValid={1} faces={2}".format(
            i, b.IsValid if b else False, b.Faces.Count if b else 0))

    joined = rg.Brep.JoinBreps(pieces_to_join, jtol)
    if joined is None or len(joined) != 1:
        n = 0 if joined is None else len(joined)
        raise BrepUnionError(
            "graft_open_brep_into_face: JoinBreps did not merge the body, new cap face, and "
            "duct into a single brep (got {0} piece(s)) - naked edges did not zipper together "
            "within tolerance {1}".format(n, jtol))
    grafted = joined[0]
    if not grafted.IsSolid:
        _log_naked_edges(grafted, "graft_open_brep_into_face")
        raise BrepUnionError(
            "graft_open_brep_into_face: joined result is not a closed solid (naked edges "
            "remain) - faces={0}".format(grafted.Faces.Count))

    if grafted.SolidOrientation == rg.BrepSolidOrientation.Inward:
        grafted.Flip()
        log("graft_open_brep_into_face: flipped grafted solid normals to outward")

    log("graft_open_brep_into_face: SUCCESS - faces={0} vol={1:.1f}".format(
        grafted.Faces.Count, grafted.GetVolume()))
    return grafted
