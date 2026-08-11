"""
SupportPathRamp.py

Grow a solid "ramp" protuberance off one end of a splint's distal support-perimeter rail, as
a foundation for future distal-face features. General-purpose: nothing here is RelativeMotion-
specific - callers just need an oriented support-perimeter rail curve and a splint solid to
attach the ramp to.

See dev-notes/260702_Dev_Process_RelativeMotion_splint.md "Support Path Ramp" section for the
full design rationale and coordinate-frame reasoning this module implements.

Construction summary (see cited dev-notes for the worked-out math):
  1. ramp_profile: a closed, planar "stadium" curve built from the caller-oriented support
     rail, a parallel copy shifted -Z by ramp_thickness, and two G1 blend-curve end caps
     (tangent to both rails) closing the two open ends.
  2. ramp_rail: a planar arc starting at ramp_profile's reference point (the rail's
     PointAtStart), tangent to a caller-supplied start_tangent direction, sweeping toward
     the caller-specified side (bend_z_sign) by arc_length / arc_radius radians.
  3. Loft translated copies of ramp_profile along ramp_rail with a tapered tip to form an
     open duct, then cap the far end. Close the near end (mouth) with a "boot": a short loft
     from a tapered, inset boot_profile (positioned _BOOT_INSET_MM into the splint body along
     the cap face's inward normal) back to ramp_profile, plus a planar boot cap. The boot
     prevents a coincident planar face at the cap face, which would block BooleanUnion. The
     resulting closed ramp_solid is merged into splint_solid via Brep.CreateBooleanUnion.

Failure model: raises SupportPathRampError on any problem. Debug observability follows the
RingSlit.py out-parameter pattern: pass debug={} to receive every intermediate construction
artifact, which survives whether the call succeeds or raises.
"""

import math
import Rhino
import Rhino.Geometry as rg
from splintcommon import log
from BrepUnion import robust_brep_union, BrepUnionError as _BrepUnionError
from BrepEdgeLocator import find_planar_face_by_plane, nearest_planar_face


class SupportPathRampError(Exception):
    """Raised when the support-path-ramp construction or graft fails cleanly for any reason.
    No debug payload is attached; construction geometry is exposed via the optional `debug`
    dict parameter to build_support_path_ramp, which survives whether the call succeeds or
    raises."""
    pass


def _dput(debug, key, value):
    """Populate a key on the optional debug out-dict, no-op if debug is None. Keeps the
    call sites in build_support_path_ramp compact and lets us extend the debug schema without
    scattering `if debug is not None` guards throughout the body."""
    if debug is not None:
        debug[key] = value


def build_support_path_ramp(splint_solid, support_rail, start_tangent,
                            ramp_thickness, ramp_length, ramp_arc_radius,
                            bend_z_sign=-1.0, trim_start_mm=2.5, trim_end_mm=2.5,
                            tolerance=None, debug=None):
    """Grow a ramp off one end of support_rail and graft it into splint_solid's distal cap face.

    Args:
        splint_solid (rg.Brep): the solid to attach the ramp to. Not mutated; a new Brep is
            returned.
        support_rail (rg.Curve): an OPEN curve marking the support-perimeter run to root the
            ramp on. The ramp attaches at support_rail.PointAtStart - the caller must orient
            the curve before calling if a specific end matters (e.g. a "+Y start" convention).
        start_tangent (rg.Vector3d): unit (or near-unit; renormalised defensively) tangent
            direction the ramp initially travels in, starting from support_rail.PointAtStart.
            Typically the same elevation-angle direction a supported finger's phalanx already
            travels: world +X rotated by the splint's relative_elevation_angle.
        ramp_thickness (float, mm): both the profile's constant band thickness (the -Z shift
            distance between the rail and its shifted copy) and the end-cap semicircle
            diameter (radius = thickness / 2). Must be > 0.
        ramp_length (float, mm): ARC LENGTH (not chord) of the swept path. Must be > 0.
        ramp_arc_radius (float, mm): radius of curvature of the swept path; sweep angle =
            ramp_length / ramp_arc_radius (radians). Must be > 0.
        bend_z_sign (float): -1.0 (default) curves the ramp toward -Z (volar); +1.0 curves it
            toward +Z (dorsal). Caller picks the sign from relative_elevation_angle (-1.0 when
            angle >= 0, +1.0 when negative), matching the codebase's usual elevation-sign
            convention (e.g. RingSlit's interior-anchor slit side, Phase 4/5's support side).
        trim_start_mm (float, mm): distance to trim off support_rail.PointAtStart before
            building the ramp profile. Keeps the ramp's footprint inboard of that endpoint
            (which normally sits at an anchor-ring intersection), avoiding geometry that would
            collide with the ring wall. Default 2.5mm.
        trim_end_mm (float, mm): same as trim_start_mm but for support_rail.PointAtEnd.
            trim_start_mm and trim_end_mm are independent so a caller can pass 0 on whichever
            end is a cantilevered end-support rail's free/tip end (the splint's own edge, with
            no adjacent anchor to clear) while keeping the normal margin on the other,
            anchor-bridged end - see RelativeMotion.py's Phase 9 call site.
        tolerance (float or None): document unit tolerance for joins / loft / graft. None
            (default) uses RhinoDoc.ModelAbsoluteTolerance.
        debug (dict or None): optional out-parameter dict populated progressively during
            construction (RingSlit.py-style). Survives a raise - whatever got built before the
            failure is still here to inspect. Keys: "trimmed_rail", "rail_top", "rail_bottom",
            "cap_start", "cap_end", "ramp_profile", "ramp_profile_plane", "target_face_index",
            "ramp_rail_plane", "ramp_rail", "loft_curves", "ramp_tube", "far_cap",
            "open_duct" (ramp_tube+far_cap for preview), "boot_profile", "boot_loft",
            "boot_cap", "ramp_solid", "result_brep" (only on success).

    Returns:
        rg.Brep: splint_solid with the ramp grafted in.

    Raises:
        SupportPathRampError: any construction or graft step fails cleanly.
        ValueError: numeric inputs are invalid (non-positive thickness/length/radius).
    """
    if splint_solid is None or not isinstance(splint_solid, rg.Brep):
        raise SupportPathRampError("splint_solid must be a Rhino Brep (got {0})".format(
            type(splint_solid).__name__))
    if not splint_solid.IsSolid:
        raise SupportPathRampError("splint_solid must be a closed solid Brep")
    if support_rail is None or not isinstance(support_rail, rg.Curve):
        raise SupportPathRampError("support_rail must be a rg.Curve (got {0})".format(
            type(support_rail).__name__))
    if support_rail.IsClosed:
        raise SupportPathRampError("support_rail must be an OPEN curve (got a closed curve)")
    if ramp_thickness <= 0.0:
        raise ValueError("ramp_thickness must be > 0 (got {0})".format(ramp_thickness))
    if ramp_length <= 0.0:
        raise ValueError("ramp_length must be > 0 (got {0})".format(ramp_length))
    if ramp_arc_radius <= 0.0:
        raise ValueError("ramp_arc_radius must be > 0 (got {0})".format(ramp_arc_radius))

    tol = tolerance
    if tol is None or tol <= 0.0:
        tol = Rhino.RhinoDoc.ActiveDoc.ModelAbsoluteTolerance

    tangent = rg.Vector3d(start_tangent)
    if not tangent.Unitize():
        raise SupportPathRampError("start_tangent is zero-length; cannot normalise")

    # --- Step 1: ramp_profile (closed "stadium" curve) --------------------------------
    # Trim trim_start_mm/trim_end_mm off the rail's respective ends so the ramp footprint sits
    # inboard of the anchor-ring intersections (the rail's raw endpoints normally sit exactly
    # where it meets the ring edges) - except on a cantilevered end-support rail's free/tip end,
    # where the caller passes 0 since there's no adjacent ring to clear there.
    rail_length = support_rail.GetLength()
    total_trim = trim_start_mm + trim_end_mm
    if total_trim > 0.0 and rail_length > total_trim + 1.0:
        ok_s, t_s = support_rail.LengthParameter(trim_start_mm)
        ok_e, t_e = support_rail.LengthParameter(rail_length - trim_end_mm)
        if ok_s and ok_e and t_s < t_e:
            trimmed = support_rail.Trim(t_s, t_e)
            if trimmed is not None:
                support_rail = trimmed
                log("build_support_path_ramp: trimmed {0:.2f}mm off start / {1:.2f}mm off end "
                    "of rail (original {2:.2f}mm -> {3:.2f}mm)".format(
                        trim_start_mm, trim_end_mm, rail_length, support_rail.GetLength()))
    _dput(debug, "trimmed_rail", support_rail)

    rail_top = support_rail.DuplicateCurve()
    rail_bottom = support_rail.DuplicateCurve()
    if not rail_bottom.Translate(rg.Vector3d(0.0, 0.0, -ramp_thickness)):
        raise SupportPathRampError("failed to translate rail_bottom by -Z ramp_thickness")
    _dput(debug, "rail_top", rail_top)
    _dput(debug, "rail_bottom", rail_bottom)

    cap_start = _stadium_end_cap(rail_top, rail_bottom, at_start=True)
    cap_end = _stadium_end_cap(rail_top, rail_bottom, at_start=False)
    if cap_start is None or cap_end is None:
        raise SupportPathRampError("failed to build one or both stadium end-cap arcs")
    _dput(debug, "cap_start", cap_start)
    _dput(debug, "cap_end", cap_end)

    joined = rg.Curve.JoinCurves([rail_top, cap_end, rail_bottom, cap_start], tol)
    if joined is None or len(joined) != 1 or not joined[0].IsClosed:
        n = 0 if joined is None else len(joined)
        raise SupportPathRampError(
            "failed to join stadium curves into ramp_profile (got {0} piece(s))".format(n))
    ramp_profile = joined[0]
    _dput(debug, "ramp_profile", ramp_profile)

    # Fit ramp_profile's plane to locate the splint's cap face and get its inward normal.
    ok_plane, profile_plane = ramp_profile.TryGetPlane(tol)
    if not ok_plane:
        raise SupportPathRampError("ramp_profile is not planar; cannot locate the cap face")
    _dput(debug, "ramp_profile_plane", profile_plane)

    face_match = find_planar_face_by_plane(splint_solid, profile_plane, point_tol=tol)
    if face_match is None:
        nearest = nearest_planar_face(splint_solid, profile_plane)
        raise SupportPathRampError(
            "could not find a planar splint face coplanar with ramp_profile "
            "(nearest: {0})".format(nearest))
    _dput(debug, "target_face_index", face_match.face_index)
    log("build_support_path_ramp: ramp_profile plane matches splint_solid face {0} "
        "(gap={1:.4f}mm)".format(face_match.face_index, face_match.gap))

    # Cap face inward normal: boot_profile is offset in this direction so ramp_solid has no
    # coincident face with the cap face, enabling a clean BooleanUnion.
    _face_obj = splint_solid.Faces[face_match.face_index]
    _fu = (_face_obj.Domain(0).Min + _face_obj.Domain(0).Max) * 0.5
    _fv = (_face_obj.Domain(1).Min + _face_obj.Domain(1).Max) * 0.5
    _face_outward = _face_obj.NormalAt(_fu, _fv)
    inward_dir = rg.Vector3d(-_face_outward.X, -_face_outward.Y, -_face_outward.Z)
    inward_dir.Unitize()
    # Verify: a point 1mm along inward_dir from the rail midpoint should be inside the solid.
    _rail_mid = rail_top.PointAtNormalizedLength(0.5)
    _test_pt = rg.Point3d(_rail_mid.X + inward_dir.X, _rail_mid.Y + inward_dir.Y,
                          _rail_mid.Z + inward_dir.Z)
    if not splint_solid.IsPointInside(_test_pt, tol, False):
        inward_dir.Reverse()
        log("build_support_path_ramp: reversed inward_dir (face normal was pointing outward)")

    # --- Step 2: ramp_rail (planar arc, tangent-anchored at ramp_profile's start) -----
    start_point = rail_top.PointAtStart
    # Component of world Z perpendicular to the tangent (Gram-Schmidt) - this keeps the arc's
    # plane containing both the tangent and a vertical direction even when start_tangent isn't
    # itself perpendicular to world Z (true whenever the caller's elevation angle is nonzero).
    z_axis = rg.Vector3d.ZAxis
    dot = rg.Vector3d.Multiply(tangent, z_axis)
    bend_dir = rg.Vector3d(
        z_axis.X - tangent.X * dot,
        z_axis.Y - tangent.Y * dot,
        z_axis.Z - tangent.Z * dot)
    if not bend_dir.Unitize():
        raise SupportPathRampError(
            "start_tangent is parallel to world Z; cannot derive a bend direction for "
            "ramp_rail (degenerate elevation angle?)")
    # Curve toward the caller-specified side (bend_z_sign: -1.0 volar / +1.0 dorsal) rather
    # than a hardcoded direction, since the correct bend side flips with the splint's
    # relative_elevation_angle sign (see docstring).
    if bend_dir.Z * bend_z_sign < 0.0:
        bend_dir.Reverse()
    arc_center = rg.Point3d(
        start_point.X + bend_dir.X * ramp_arc_radius,
        start_point.Y + bend_dir.Y * ramp_arc_radius,
        start_point.Z + bend_dir.Z * ramp_arc_radius)
    # Arc(plane, radius, angle) starts at plane.Origin + radius*plane.XAxis with initial
    # tangent (d/dtheta at theta=0) along plane.YAxis - so XAxis = -bend_dir (radial,
    # center->start_point) and YAxis = tangent (already perpendicular to bend_dir by the
    # Gram-Schmidt step above) reproduces exactly the start point/tangent we need.
    neg_bend_dir = rg.Vector3d(-bend_dir.X, -bend_dir.Y, -bend_dir.Z)
    arc_plane = rg.Plane(arc_center, neg_bend_dir, tangent)
    _dput(debug, "ramp_rail_plane", arc_plane)
    sweep_angle_rad = ramp_length / ramp_arc_radius
    ramp_rail_arc = rg.Arc(arc_plane, ramp_arc_radius, sweep_angle_rad)
    if not ramp_rail_arc.IsValid:
        raise SupportPathRampError(
            "ramp_rail arc construction produced invalid geometry (radius={0:.3f}mm, "
            "sweep={1:.4f}rad)".format(ramp_arc_radius, sweep_angle_rad))
    ramp_rail = ramp_rail_arc.ToNurbsCurve()
    _dput(debug, "ramp_rail", ramp_rail)
    log("build_support_path_ramp: ramp_rail arc radius={0:.2f}mm length={1:.2f}mm "
        "sweep={2:.1f}deg start=({3:.2f},{4:.2f},{5:.2f})".format(
            ramp_arc_radius, ramp_length, math.degrees(sweep_angle_rad),
            start_point.X, start_point.Y, start_point.Z))

    # --- Step 3: loft an open duct (capped at the far end; near end/mouth stays open) ----
    # --- Tip taper (smooth rounding of the ramp's outer +Y/+X and -Y/+X corners) --------
    # The last few loft sections are built as progressively smaller stadiums (same topology
    # as the original profile: 2 lines + 2 arcs) so the loft tapers inward and the tip
    # corners come out rounded instead of sharp.
    #
    # Tuning knobs:
    #   n_sections       - total loft sections (0..n_sections inclusive)
    #   taper_start_idx  - sections 0..taper_start_idx are full-size; after that, taper
    #   max_offset       - maximum inset (mm) at the final section (controls how aggressively
    #                      the corners round; larger = more rounding, smaller = subtler)
    #   progression      - quarter-circle (smooth ease-in): d = max_offset * (1 - sqrt(1 - t^2))
    #                      where t = (si - taper_start_idx) / n_tapered, t in (0, 1]
    n_sections = 12
    taper_start_idx = 8
    max_offset = ramp_thickness * 0.3
    n_tapered = n_sections - taper_start_idx  # 4
    log("build_support_path_ramp: tip taper last {0} sections, max_offset={1:.2f}mm".format(
        n_tapered, max_offset))

    # Push loft_curves[0] (the mouth) just inside the splint so the ramp_tube walls cross
    # the cap face rather than being tangent to it, giving BooleanUnion a clean intersection.
    _MOUTH_INSET_MM = 0.3
    _mouth_inset_vec = rg.Vector3d(
        inward_dir.X * _MOUTH_INSET_MM,
        inward_dir.Y * _MOUTH_INSET_MM,
        inward_dir.Z * _MOUTH_INSET_MM)

    loft_curves = []
    for si in range(n_sections + 1):
        frac = float(si) / n_sections
        ok_param, t_param = ramp_rail.NormalizedLengthParameter(frac)
        if not ok_param:
            continue
        pt = ramp_rail.PointAt(t_param)
        offset = pt - start_point  # translation from profile's original position

        if si > taper_start_idx:
            # Build a smaller stadium from scratch (quarter-circle inward progression)
            t = float(si - taper_start_idx) / n_tapered
            d = max_offset * (1.0 - math.sqrt(1.0 - t * t))
            section = _build_offset_stadium(rail_top, ramp_thickness, d, tol)
            if section is None:
                section = ramp_profile.DuplicateCurve()
            section.Translate(rg.Vector3d(offset))
        elif si == 0:
            # Mouth section: offset into the splint so ramp_tube walls cleanly cross the cap face.
            section = ramp_profile.DuplicateCurve()
            section.Translate(rg.Vector3d(_mouth_inset_vec))
        else:
            section = ramp_profile.DuplicateCurve()
            section.Translate(rg.Vector3d(offset))
        loft_curves.append(section)
    _dput(debug, "loft_curves", list(loft_curves))
    if len(loft_curves) < 2:
        raise SupportPathRampError(
            "could not sample enough points along ramp_rail for loft ({0} sections)".format(
                len(loft_curves)))
    loft_result = rg.Brep.CreateFromLoft(loft_curves, rg.Point3d.Unset, rg.Point3d.Unset,
                                          rg.LoftType.Tight, False)
    loft_list = list(loft_result) if loft_result else []
    if len(loft_list) == 0:
        raise SupportPathRampError("loft of translated profile sections returned nothing")
    ramp_tube = loft_list[0]
    for extra in loft_list[1:]:
        ramp_tube.Append(extra)
    _dput(debug, "ramp_tube", ramp_tube)

    # Cap ONLY the far end (loft_curves[-1]) - the near end stays open. loft_curves[0] is a
    # zero-offset duplicate of ramp_profile, so open_duct's mouth is geometrically identical
    # (not just close) to the curve we are about to cut into splint_solid's cap face, which is
    # exactly what lets the final JoinBreps below zipper them together with no boolean solver.
    far_cap_pieces = rg.Brep.CreatePlanarBreps([loft_curves[-1]], tol)
    if far_cap_pieces is None or len(far_cap_pieces) != 1:
        n = 0 if far_cap_pieces is None else len(far_cap_pieces)
        raise SupportPathRampError(
            "failed to build the ramp duct's far-end cap (got {0} planar piece(s))".format(n))
    far_cap = far_cap_pieces[0]
    _dput(debug, "far_cap", far_cap)

    duct_pieces = rg.Brep.JoinBreps([ramp_tube, far_cap], tol)
    if duct_pieces is None or len(duct_pieces) != 1:
        n = 0 if duct_pieces is None else len(duct_pieces)
        raise SupportPathRampError(
            "failed to join the ramp tube and its far cap into a single open duct (got {0} "
            "piece(s))".format(n))
    open_duct = duct_pieces[0]
    _dput(debug, "open_duct", open_duct)
    log("build_support_path_ramp: open_duct built (far end capped, mouth open), "
        "faces={0}".format(open_duct.Faces.Count))

    # --- Step 4: boot (closes the mouth; offset into splint to avoid coincident cap face) ---
    # boot_profile is a shrunken copy of ramp_profile displaced _BOOT_INSET_MM along the face
    # inward normal. The short loft from boot_profile back to ramp_profile (the mouth) creates
    # walls that pierce the cap face at ramp_profile, giving BooleanUnion a clean intersection
    # curve rather than a coincident planar region.
    _BOOT_INSET_MM = -0.8
    _BOOT_TAPER_MM = 0.8   # mm trimmed from each end of rail_top
    _BOOT_THICKNESS_FRAC = 0.3  # boot Z-height as fraction of ramp_thickness
    # Build boot_profile: shorter rail AND collapsed Z-height, vertically centered within
    # ramp_profile's Z span so both top and bottom edges flare symmetrically to ramp_profile.
    _boot_rail_len = rail_top.GetLength()
    _boot_thickness = ramp_thickness * _BOOT_THICKNESS_FRAC
    _boot_z_shift = (ramp_thickness - _boot_thickness) / 2.0  # center boot vertically
    boot_profile = None
    if 2.0 * _BOOT_TAPER_MM < _boot_rail_len - 1.0:
        _ok_s, _t_s = rail_top.LengthParameter(_BOOT_TAPER_MM)
        _ok_e, _t_e = rail_top.LengthParameter(_boot_rail_len - _BOOT_TAPER_MM)
        if _ok_s and _ok_e and _t_s < _t_e:
            _brt = rail_top.Trim(_t_s, _t_e)
            if _brt is not None:
                _brt.Translate(rg.Vector3d(0.0, 0.0, -_boot_z_shift))
                _brb = _brt.DuplicateCurve()
                _brb.Translate(rg.Vector3d(0.0, 0.0, -_boot_thickness))
                _bcs = _stadium_end_cap(_brt, _brb, at_start=True)
                _bce = _stadium_end_cap(_brt, _brb, at_start=False)
                if _bcs is not None and _bce is not None:
                    _bj = rg.Curve.JoinCurves([_brt, _bce, _brb, _bcs], tol)
                    if _bj and len(_bj) == 1 and _bj[0].IsClosed:
                        boot_profile = _bj[0]
    if boot_profile is None:
        boot_profile = ramp_profile.DuplicateCurve()
    inward_vec = rg.Vector3d(
        inward_dir.X * _BOOT_INSET_MM,
        inward_dir.Y * _BOOT_INSET_MM,
        inward_dir.Z * _BOOT_INSET_MM)
    boot_profile.Translate(rg.Vector3d(inward_vec))
    _dput(debug, "boot_profile", boot_profile)

    boot_loft_result = rg.Brep.CreateFromLoft(
        [boot_profile, loft_curves[0]], rg.Point3d.Unset, rg.Point3d.Unset,
        rg.LoftType.Tight, False)
    boot_loft_list = list(boot_loft_result) if boot_loft_result else []
    if not boot_loft_list:
        raise SupportPathRampError("boot loft (boot_profile -> mouth) returned nothing")
    boot_loft = boot_loft_list[0]
    _dput(debug, "boot_loft", boot_loft)

    boot_cap_pieces = rg.Brep.CreatePlanarBreps([boot_profile], tol)
    if boot_cap_pieces is None or len(boot_cap_pieces) != 1:
        n = 0 if boot_cap_pieces is None else len(boot_cap_pieces)
        raise SupportPathRampError(
            "boot planar cap failed (got {0} piece(s))".format(n))
    boot_cap = boot_cap_pieces[0]
    _dput(debug, "boot_cap", boot_cap)

    ramp_solid_pieces = rg.Brep.JoinBreps(
        [ramp_tube, far_cap, boot_loft, boot_cap], tol)
    if ramp_solid_pieces is None or len(ramp_solid_pieces) != 1:
        n = 0 if ramp_solid_pieces is None else len(ramp_solid_pieces)
        raise SupportPathRampError(
            "failed to join ramp components into one solid (got {0} piece(s))".format(n))
    ramp_solid = ramp_solid_pieces[0]
    if not ramp_solid.IsSolid:
        raise SupportPathRampError(
            "ramp solid is not closed after join (naked edges remain)")
    # JoinBreps can produce inward-facing normals; BooleanUnion treats an inward solid as its
    # complement (equivalent to a difference), so flip if needed before the union.
    if ramp_solid.SolidOrientation == rg.BrepSolidOrientation.Inward:
        ramp_solid.Flip()
        log("build_support_path_ramp: flipped ramp_solid to outward orientation")
    _dput(debug, "ramp_solid", ramp_solid)
    log("build_support_path_ramp: ramp_solid closed, faces={0}".format(
        ramp_solid.Faces.Count))

    # --- Step 5: Boolean Union ---------------------------------------------------
    try:
        result_brep, success, method = robust_brep_union(
            [splint_solid, ramp_solid], base_tolerance=tol, check_volumes=False)
    except (_BrepUnionError, Exception) as exc:
        raise SupportPathRampError(
            "BooleanUnion of splint_solid and ramp_solid failed: {0}".format(exc))
    if not success or result_brep is None:
        raise SupportPathRampError(
            "BooleanUnion did not succeed (method={0})".format(method))
    if not result_brep.IsSolid:
        raise SupportPathRampError(
            "BooleanUnion result is not a closed solid (faces={0})".format(
                result_brep.Faces.Count))
    # Repair heals non-manifold edges left by the jiggle transform round-trip, which IsSolid
    # does not catch (it only checks for naked edges, not 3+-valence edges).
    result_brep.Repair(tol)
    nm_edges = sum(1 for e in result_brep.Edges
                   if e.Valence == rg.EdgeAdjacency.NonManifold)
    if nm_edges > 0:
        log("build_support_path_ramp: {0} non-manifold edge(s) remain after repair".format(
            nm_edges))
    _dput(debug, "result_brep", result_brep)
    log("build_support_path_ramp: BooleanUnion OK, faces={0}".format(
        result_brep.Faces.Count))
    return result_brep


def _stadium_end_cap(rail_top, rail_bottom, at_start):
    """G1 blend curve joining rail_top and rail_bottom at their shared end, tangent to both
    rails. More compact than a semicircle - doesn't bulge perpendicular to the rail direction
    so the stadium footprint stays closer to the perimeter. Returns None on failure."""
    if at_start:
        t_top = rail_top.Domain.T0
        t_bot = rail_bottom.Domain.T0
        rev_top = True   # at T0: reverse natural direction to depart backward (away from body)
        rev_bot = True  # at T0: natural direction (into curve) = arrive from outside
    else:
        t_top = rail_top.Domain.T1
        t_bot = rail_bottom.Domain.T1
        rev_top = False  # at T1: natural direction = depart forward (away from body)
        rev_bot = False   # at T1: reverse = depart backward = arrive from outside
    return rg.Curve.CreateBlendCurve(
        rail_top, t_top, rev_top, rg.BlendContinuity.Tangency,
        rail_bottom, t_bot, rev_bot, rg.BlendContinuity.Tangency)


def _build_offset_stadium(rail_top, thickness, d, tol, bottom_inset_mm=0.0):
    """Build a stadium profile inset by `d` from the original. `bottom_inset_mm` additionally
    shortens inner_rail_bottom from each end to match a ramp_profile built with an inset
    eff_bottom. Returns None if d is too large for a valid result."""
    rail_len = rail_top.GetLength()
    if d <= 0.0 or 2.0 * d >= thickness or 2.0 * d >= rail_len:
        return None
    # Trim the rail by d from each end
    ok_s, t_s = rail_top.LengthParameter(d)
    ok_e, t_e = rail_top.LengthParameter(rail_len - d)
    if not ok_s or not ok_e or t_s >= t_e:
        return None
    inner_rail_top = rail_top.Trim(t_s, t_e)
    if inner_rail_top is None:
        return None
    # Shift rail_top down by d (inward from the top edge)
    inner_rail_top.Translate(rg.Vector3d(0.0, 0.0, -d))
    # Build rail_bottom shifted further down by the reduced thickness; additionally shorten
    # from each end by bottom_inset_mm to mirror the main ramp_profile's eff_bottom shape.
    new_thickness = thickness - 2.0 * d
    inner_rail_bottom = inner_rail_top.DuplicateCurve()
    if bottom_inset_mm > 0.0:
        inner_len = inner_rail_bottom.GetLength()
        ok_bi_s, t_bi_s = inner_rail_bottom.LengthParameter(bottom_inset_mm)
        ok_bi_e, t_bi_e = inner_rail_bottom.LengthParameter(inner_len - bottom_inset_mm)
        if ok_bi_s and ok_bi_e and t_bi_s < t_bi_e:
            trimmed_inner = inner_rail_bottom.Trim(t_bi_s, t_bi_e)
            if trimmed_inner is not None:
                inner_rail_bottom = trimmed_inner
    inner_rail_bottom.Translate(rg.Vector3d(0.0, 0.0, -new_thickness))
    # End caps with the reduced thickness
    cap_s = _stadium_end_cap(inner_rail_top, inner_rail_bottom, at_start=True)
    cap_e = _stadium_end_cap(inner_rail_top, inner_rail_bottom, at_start=False)
    if cap_s is None or cap_e is None:
        return None
    joined = rg.Curve.JoinCurves(
        [inner_rail_top, cap_e, inner_rail_bottom, cap_s], tol)
    if joined is None or len(joined) != 1 or not joined[0].IsClosed:
        return None
    return joined[0]
