"""
BrepSlit.py
Cut a sizing slit in a splint brep and fillet the exposed edges.

The slit is defined by an untrimmed planar surface (the slit panel) that
intersects the splint. The panel is thickened symmetrically along its normal
to create a solid cutter, which is boolean-subtracted from the splint.
The sharp edges revealed by the cut are then filleted for skin comfort.
"""

import Rhino.Geometry as rg
import scriptcontext as sc
from splintcommon import log
from BrepDifference import robust_brep_difference


class BrepSlitError(Exception):
    """Raised when the slit operation fails."""
    pass


def _thicken_surface(surface, thickness):
    """Extrude an untrimmed surface symmetrically along its normal to create a solid cutter.

    Translates two copies of the surface boundary +/- half thickness along the
    surface normal, straight-lofts the walls, and caps to form a solid.

    Args:
        surface: Untrimmed planar Surface or single-face Brep.
        thickness: Total slit width in mm. Half is extruded each direction.

    Returns:
        Solid Brep cutter, or None on failure.
    """
    # Normalize input to a Surface
    if isinstance(surface, rg.Brep):
        if surface.Faces.Count != 1:
            log("ERROR: slit panel brep has {} faces, expected 1".format(surface.Faces.Count))
            return None
        srf = surface.Faces[0].UnderlyingSurface()
    elif isinstance(surface, rg.Surface):
        srf = surface
    else:
        log("ERROR: unexpected slit panel type: {}".format(type(surface).__name__))
        return None

    # Get normal at surface center
    normal = srf.NormalAt(srf.Domain(0).Mid, srf.Domain(1).Mid)
    normal.Unitize()
    half = thickness / 2.0
    log("Thickening panel: thickness={:.4f}mm, normal=({:.3f},{:.3f},{:.3f})".format(
        thickness, normal.X, normal.Y, normal.Z))

    # Translate two copies of the surface boundary, loft walls, cap to solid
    brep_a = srf.ToBrep()
    brep_b = srf.ToBrep()
    brep_a.Transform(rg.Transform.Translation(normal * half))
    brep_b.Transform(rg.Transform.Translation(normal * (-half)))

    curve_a = brep_a.Faces[0].OuterLoop.To3dCurve()
    curve_b = brep_b.Faces[0].OuterLoop.To3dCurve()

    lofted = rg.Brep.CreateFromLoft(
        [curve_a, curve_b],
        rg.Point3d.Unset, rg.Point3d.Unset,
        rg.LoftType.Straight, False
    )
    if not lofted or len(lofted) == 0:
        log("ERROR: Loft between translated boundaries failed")
        return None

    capped = lofted[0].CapPlanarHoles(sc.doc.ModelAbsoluteTolerance)
    if capped and capped.IsSolid:
        log("Cutter built: solid={}, valid={}, faces={}".format(
            capped.IsSolid, capped.IsValid, capped.Faces.Count))
        return capped

    log("ERROR: CapPlanarHoles failed on lofted walls")
    return None


def _find_slit_edges(brep, original_brep, tolerance):
    """Find edges created by the slit cut via midpoint comparison.

    Any edge on the result whose midpoint doesn't match any pre-cut edge
    midpoint is classified as new (created by the boolean difference).

    Args:
        brep: The brep after the slit cut.
        original_brep: The brep before the slit cut.
        tolerance: Distance tolerance for midpoint matching.

    Returns:
        List of edge indices that are new (from the cut).
    """
    match_tol = tolerance * 10

    # Snapshot original edge midpoints
    original_midpoints = []
    for edge in original_brep.Edges:
        original_midpoints.append(edge.PointAt(edge.Domain.Mid))

    new_edge_indices = []
    for edge in brep.Edges:
        mid_pt = edge.PointAt(edge.Domain.Mid)
        is_original = any(mid_pt.DistanceTo(op) < match_tol for op in original_midpoints)
        if not is_original:
            new_edge_indices.append(edge.EdgeIndex)

    log("Slit edge detection: {} new edges found (of {} total, vs {} original)".format(
        len(new_edge_indices), brep.Edges.Count, len(original_midpoints)))
    return new_edge_indices


def _fillet_edges_by_index(brep, edge_indices, radius, tolerance):
    """Fillet specific edges by index. Tries batch first, then one-at-a-time.

    Args:
        brep: Input brep.
        edge_indices: List of edge indices to fillet.
        radius: Fillet radius in mm.
        tolerance: Model tolerance.

    Returns:
        Filleted brep (or original if filleting fails).
    """
    if not edge_indices:
        log("No edges to fillet")
        return brep

    blend_type = rg.BlendType.Fillet
    rail_type = rg.RailType.DistanceFromEdge
    radii = [radius] * len(edge_indices)

    # Try batch first
    log("Attempting batch fillet on {} edges, radius={:.3f}".format(len(edge_indices), radius))
    result = rg.Brep.CreateFilletEdges(
        brep, edge_indices, radii, radii, blend_type, rail_type, tolerance
    )
    if result and len(result) > 0:
        log("Batch fillet succeeded: {} brep(s)".format(len(result)))
        return result[0]

    log("Batch fillet failed, trying one-at-a-time")

    # One-at-a-time with re-detection each pass
    # Snapshot midpoints of target edges
    target_points = []
    for idx in edge_indices:
        edge = brep.Edges[idx]
        mid_t = edge.Domain.Mid
        target_points.append(edge.PointAt(mid_t))

    current = brep
    match_tol = tolerance * 10
    max_passes = len(edge_indices) + 5
    total_filleted = 0

    for pass_num in range(max_passes):
        # Find current edges matching our target midpoints
        candidates = []
        for edge in current.Edges:
            mid_t = edge.Domain.Mid
            mid_pt = edge.PointAt(mid_t)
            for tp in target_points:
                if mid_pt.DistanceTo(tp) < match_tol:
                    candidates.append(edge.EdgeIndex)
                    break

        if not candidates:
            log("Fillet pass {}: no target edges remain, done ({} filleted)".format(
                pass_num, total_filleted))
            break

        filleted = False
        for idx in candidates:
            res = rg.Brep.CreateFilletEdges(
                current, [idx], [radius], [radius], blend_type, rail_type, tolerance
            )
            if res and len(res) > 0:
                log("  Fillet pass {}: edge {} succeeded".format(pass_num, idx))
                current = res[0]
                total_filleted += 1
                filleted = True
                break
            else:
                log("  Fillet pass {}: edge {} FAILED".format(pass_num, idx))

        if not filleted:
            # Try remaining as batch
            batch_radii = [radius] * len(candidates)
            res = rg.Brep.CreateFilletEdges(
                current, candidates, batch_radii, batch_radii,
                blend_type, rail_type, tolerance
            )
            if res and len(res) > 0:
                log("  Batch fallback succeeded for remaining {} edges".format(len(candidates)))
                current = res[0]
                total_filleted += len(candidates)
                break
            log("No more edges could be filleted ({} of {} done)".format(
                total_filleted, len(edge_indices)))
            break

    return current


def cut_slit(splint_brep, slit_panel, slit_thickness, fillet_radius,
             tolerance=None):
    """Cut a sizing slit in a splint brep and fillet the exposed edges.

    Args:
        splint_brep: The solid splint Brep.
        slit_panel: Untrimmed planar surface defining the slit center plane.
                    Its intersection with splint_brep defines the slit location.
        slit_thickness: Total slit width in mm (removed symmetrically from panel).
        fillet_radius: Radius in mm for filleting the exposed slit edges.
        tolerance: Model tolerance (uses doc tolerance if None).

    Returns:
        The splint Brep with slit cut and edges filleted.

    Raises:
        BrepSlitError: If the boolean difference fails.
    """
    if tolerance is None:
        tolerance = sc.doc.ModelAbsoluteTolerance

    log("=" * 60)
    log("CUT SLIT: thickness={:.3f}mm, fillet_r={:.3f}mm, tol={:.4f}".format(
        slit_thickness, fillet_radius, tolerance))
    log("=" * 60)

    # Validate inputs
    if splint_brep is None or not splint_brep.IsValid:
        raise BrepSlitError("Input splint brep is None or invalid")
    if slit_panel is None:
        raise BrepSlitError("Slit panel is None")

    log("Splint: solid={}, valid={}, faces={}, edges={}".format(
        splint_brep.IsSolid, splint_brep.IsValid,
        splint_brep.Faces.Count, splint_brep.Edges.Count))

    # Step 1: Thicken the slit panel into a solid cutter
    cutter = _thicken_surface(slit_panel, slit_thickness)
    if cutter is None:
        raise BrepSlitError("Failed to create slit cutter from panel")

    # Verify bounding box overlap between splint and cutter
    bb_splint = splint_brep.GetBoundingBox(True)
    bb_cutter = cutter.GetBoundingBox(True)
    overlap = not (
        bb_splint.Min.X > bb_cutter.Max.X or bb_cutter.Min.X > bb_splint.Max.X or
        bb_splint.Min.Y > bb_cutter.Max.Y or bb_cutter.Min.Y > bb_splint.Max.Y or
        bb_splint.Min.Z > bb_cutter.Max.Z or bb_cutter.Min.Z > bb_splint.Max.Z
    )
    if not overlap:
        raise BrepSlitError("Slit cutter does not overlap splint brep")

    # Step 2: Boolean difference using robust multi-strategy approach
    log("Subtracting slit cutter from splint via robust_brep_difference...")
    slit_brep, success, method = robust_brep_difference(
        splint_brep, cutter, base_tolerance=tolerance
    )
    log("Boolean difference: success={}, method={}, faces={}, edges={}".format(
        success, method, slit_brep.Faces.Count, slit_brep.Edges.Count))

    # Step 3: Find edges created by the cut
    new_edges = _find_slit_edges(slit_brep, splint_brep, tolerance)

    if not new_edges:
        log("WARNING: no new edges detected from cut, skipping fillet")
        return slit_brep

    # Step 4: Fillet the new edges
    if fillet_radius > 0:
        slit_brep = _fillet_edges_by_index(slit_brep, new_edges, fillet_radius, tolerance)
        log("After fillet: solid={}, valid={}, faces={}, edges={}".format(
            slit_brep.IsSolid, slit_brep.IsValid,
            slit_brep.Faces.Count, slit_brep.Edges.Count))
    else:
        log("Fillet radius is 0, skipping fillet")

    log("=" * 60)
    log("SLIT COMPLETE")
    log("=" * 60)
    return slit_brep
