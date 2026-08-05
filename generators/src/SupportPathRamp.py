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
     rail, a parallel copy shifted -Z by ramp_thickness, and two semicircular end caps
     (diameter = ramp_thickness) closing the two open ends.
  2. ramp_rail: a planar arc starting at ramp_profile's reference point (the rail's
     PointAtStart), tangent to a caller-supplied start_tangent direction (typically the same
     elevation-angle direction a supported finger's phalanx already travels), sweeping toward
     the caller-specified side (bend_z_sign: -1.0 volar / +1.0 dorsal - flips with the
     splint's elevation-angle sign) by arc_length / arc_radius radians. The arc's own plane
     is derived via Gram-Schmidt from start_tangent and world Z, so this works whether or not
     start_tangent happens to be perpendicular to world Z (true whenever the caller's
     elevation angle is nonzero).
  3. ramp_profile is planar by construction, and (per the dev-notes derivation) exactly
     coplanar with the splint's flat distal-cap face - rail_bottom's -Z shift IS that face's
     own plane's in-plane axis. Crucially, rail_top itself is not just coplanar but literally
     ON that face's own outer boundary edge (the support rail IS a piece of the splint's
     perimeter), so grafting the ramp in is a NOTCH splice into the face's boundary, not a
     hole punched in its interior: the face's outer loop is split where rail_top touches it,
     that segment is discarded, and the "U" remainder of ramp_profile (cap_end + rail_bottom +
     cap_start) is spliced in instead. Sample points along ramp_rail to loft translated copies
     of ramp_profile into an open duct (capped only at the FAR end; the near end/mouth stays
     an exact duplicate of ramp_profile), then BrepUnion2.graft_open_brep_into_face rebuilds
     the cap face with the spliced boundary and Brep.JoinBreps zippers the known-coincident
     naked edges together - a topological stitch, not a boolean regularization, which
     sidesteps the coincident-face / insufficient-overlap failure modes that occasionally
     trip up general boolean union.

Failure model: raises SupportPathRampError on any problem. Debug observability follows the
RingSlit.py out-parameter pattern: pass debug={} to receive every intermediate construction
artifact, which survives whether the call succeeds or raises.
"""

import math
import Rhino
import Rhino.Geometry as rg
from splintcommon import log
from BrepUnion2 import graft_open_brep_into_face, BrepUnionError
from BrepEdgeLocator import find_planar_face_by_plane, nearest_planar_face

# Minimum tolerance for the final brep-level JoinBreps in graft_open_brep_into_face. By Phase 9,
# splint_solid's cap face boundary near rail_top has already been reshaped a few microns by
# Phase 7.5's variable-radius perimeter chamfer (RelativeMotion.py), so the actual current edge
# no longer matches the pristine rail_top/rail_bottom curves to document tolerance - see the
# call site below for the full explanation.
_GRAFT_JOIN_TOL_MM = 0.05


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
            "ramp_u", "outer_loop_curve", "boundary_long_way", "new_outer_curve",
            "ramp_rail_plane", "ramp_rail", "ramp_tube" (pre-cap), "far_cap", "open_duct",
            "result_brep" (only on success).

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

    cap_start = _stadium_end_cap(rail_top, rail_bottom, at_start=True, thickness=ramp_thickness)
    cap_end = _stadium_end_cap(rail_top, rail_bottom, at_start=False, thickness=ramp_thickness)
    if cap_start is None or cap_end is None:
        raise SupportPathRampError("failed to build one or both stadium end-cap arcs")
    _dput(debug, "cap_start", cap_start)
    _dput(debug, "cap_end", cap_end)

    joined = rg.Curve.JoinCurves([rail_top, cap_end, rail_bottom, cap_start], tol)
    if joined is None or len(joined) != 1 or not joined[0].IsClosed:
        n = 0 if joined is None else len(joined)
        raise SupportPathRampError(
            "failed to join rail_top/rail_bottom/end-caps into a single closed ramp_profile "
            "(got {0} piece(s))".format(n))
    ramp_profile = joined[0]
    _dput(debug, "ramp_profile", ramp_profile)

    # ramp_profile is planar by construction (rail_top lies on the splint's flat distal-cap
    # face's own boundary edge; rail_bottom is rail_top shifted by a pure world-Z translation,
    # which is that SAME plane's own in-plane axis - see the module docstring). Fit its plane
    # directly rather than requiring the caller to pass distal_profile_plane in: this keeps
    # the function's signature general-purpose (nothing RelativeMotion-specific) while still
    # letting us find the exact cap face to graft into, by plane match rather than by index
    # (indices shift across the earlier chamfer/slit/emboss phases).
    ok_plane, profile_plane = ramp_profile.TryGetPlane(tol)
    if not ok_plane:
        raise SupportPathRampError(
            "ramp_profile is not planar (unexpected - it is built entirely from a world-Z "
            "shift of a planar rail); cannot locate the splint's cap face to graft into")
    _dput(debug, "ramp_profile_plane", profile_plane)

    face_match = find_planar_face_by_plane(splint_solid, profile_plane, point_tol=tol)
    if face_match is None:
        nearest = nearest_planar_face(splint_solid, profile_plane)
        raise SupportPathRampError(
            "could not find a single planar face on splint_solid coplanar with ramp_profile "
            "(nearest planar face: {0})".format(nearest))
    _dput(debug, "target_face_index", face_match.face_index)
    log("build_support_path_ramp: ramp_profile plane matches splint_solid face {0} "
        "(gap={1:.4f}mm)".format(face_match.face_index, face_match.gap))

    # ramp_profile's rail_top segment sits exactly ON that face's own outer boundary edge (it
    # IS a piece of the splint's perimeter, not an interior curve) - so grafting the ramp is NOT
    # a hole punched into the face's interior; it is a NOTCH that bulges the face's own outer
    # boundary outward. Build the replacement boundary directly: the "U" portion of
    # ramp_profile (cap_end + rail_bottom + cap_start, i.e. everything except rail_top) splices
    # in wherever the old boundary ran under rail_top.
    ramp_u_joined = rg.Curve.JoinCurves([cap_end, rail_bottom, cap_start], tol)
    if ramp_u_joined is None or len(ramp_u_joined) != 1:
        raise SupportPathRampError(
            "failed to join cap_end/rail_bottom/cap_start into the ramp's open 'U' notch "
            "curve (got {0} piece(s))".format(0 if ramp_u_joined is None else len(ramp_u_joined)))
    ramp_u = ramp_u_joined[0]
    _dput(debug, "ramp_u", ramp_u)

    target_face = splint_solid.Faces[face_match.face_index]
    outer_loop_curve = target_face.OuterLoop.To3dCurve()
    if outer_loop_curve is None:
        raise SupportPathRampError(
            "could not extract face {0}'s outer loop as a 3D curve".format(
                face_match.face_index))
    _dput(debug, "outer_loop_curve", outer_loop_curve)

    ok_s, t_s = outer_loop_curve.ClosestPoint(rail_top.PointAtStart)
    ok_e, t_e = outer_loop_curve.ClosestPoint(rail_top.PointAtEnd)
    if not ok_s or not ok_e:
        raise SupportPathRampError(
            "could not project rail_top's endpoints onto face {0}'s outer boundary".format(
                face_match.face_index))
    boundary_pieces = outer_loop_curve.Split([t_s, t_e])
    if boundary_pieces is None or len(boundary_pieces) != 2:
        raise SupportPathRampError(
            "splitting face {0}'s outer boundary at rail_top's endpoints did not yield 2 "
            "pieces (got {1}) - rail_top may not lie on this face's boundary".format(
                face_match.face_index, 0 if boundary_pieces is None else len(boundary_pieces)))
    # Keep the "long way round" piece (the rest of the perimeter); drop the short piece that
    # coincides with rail_top itself, identified by which midpoint is farther from rail_top's.
    rail_top_mid = rail_top.PointAtNormalizedLength(0.5)
    boundary_long_way = max(
        boundary_pieces, key=lambda p: p.PointAtNormalizedLength(0.5).DistanceTo(rail_top_mid))
    _dput(debug, "boundary_long_way", boundary_long_way)

    new_outer_joined = rg.Curve.JoinCurves([boundary_long_way, ramp_u], tol)
    if new_outer_joined is None or len(new_outer_joined) != 1 or not new_outer_joined[0].IsClosed:
        raise SupportPathRampError(
            "failed to splice the ramp's 'U' notch into face {0}'s outer boundary into a "
            "single closed curve (got {1} piece(s))".format(
                face_match.face_index, 0 if new_outer_joined is None else len(new_outer_joined)))
    new_outer_curve = new_outer_joined[0]
    _dput(debug, "new_outer_curve", new_outer_curve)

    # IsClosed above only confirms the splice forms a closed loop - it says nothing about
    # whether that loop is SIMPLE. For a tight mid-support rail (short arc, flanked closely by
    # anchor-bridge corners on both sides), the ramp's inward ramp_thickness offset can push
    # rail_bottom/the end caps back across boundary_long_way, which downstream only surfaced as
    # a cryptic "CreatePlanarBreps got 2 pieces" failure deep inside graft_open_brep_into_face.
    # Catch it here instead, right where new_outer_curve was actually built, so the error names
    # the actual self-crossing location(s).
    self_ix = rg.Intersect.Intersection.CurveSelf(new_outer_curve, tol)
    if self_ix is not None and self_ix.Count > 0:
        locations = ", ".join(
            "({0:.2f},{1:.2f},{2:.2f})".format(ev.PointA.X, ev.PointA.Y, ev.PointA.Z)
            for ev in self_ix)
        # Identify which named sub-curve each crossing point sits on, to pin down exactly which
        # pieces of the splice are colliding (e.g. the ramp's own end-cap vs. the cradle's tip
        # turnaround on a narrow end-support prong) - this is what actually let us diagnose the
        # AASX_20.json cantilevered-prong failure in one shot, so it stays a permanent aid.
        named_curves = {
            "rail_top": rail_top, "rail_bottom": rail_bottom, "cap_start": cap_start,
            "cap_end": cap_end, "boundary_long_way": boundary_long_way}
        for ev in self_ix:
            pt = ev.PointA
            dists = []
            for name, crv in named_curves.items():
                ok_cp, t_cp = crv.ClosestPoint(pt)
                if ok_cp:
                    dists.append((crv.PointAt(t_cp).DistanceTo(pt), name))
            dists.sort()
            log("build_support_path_ramp: DIAG self-ix at ({0:.2f},{1:.2f},{2:.2f}) nearest: "
                "{3}".format(pt.X, pt.Y, pt.Z,
                             ", ".join("{0}={1:.3f}mm".format(n, d) for d, n in dists)))
        raise SupportPathRampError(
            "new_outer_curve self-intersects at {0} location(s) after splicing the ramp's "
            "'U' notch into face {1}'s outer boundary: {2} - the ramp footprint (thickness="
            "{3:.2f}mm) does not fit cleanly on this rail (try a shorter/thinner ramp, or "
            "larger trim_start_mm/trim_end_mm)".format(
                self_ix.Count, face_match.face_index, locations, ramp_thickness))

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

    # --- Step 3: loft an OPEN duct (capped only at the far end), then graft it in ------------
    # Use Brep.CreateFromSweep with the simple overload. The profile is a CLOSED planar curve
    # sitting at the rail's start point. Rhino's simple Sweep1 uses the Frenet frame by default
    # which tilts the profile, BUT for a planar closed profile whose plane normal is aligned
    # with the rail's start tangent, the profile begins perpendicular to the rail and the
    # Frenet rotation IS what we want for a tube that follows the curve (the cross-section
    # stays perpendicular to the rail tangent at every point). For our use case where we want
    # the profile to keep its INITIAL orientation (no rotation), we instead sample points along
    # the rail and loft translated copies of the profile.
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

    # Note: no orientation flip here - open_duct is NOT a closed solid (its mouth is open), so
    # Brep.SolidOrientation is meaningless on it (always "None"/unknown). The final grafted
    # result IS a closed solid, and graft_open_brep_into_face flips that if needed.

    # The final brep-level JoinBreps needs a looser tolerance than the rest of this function:
    # by Phase 9, splint_solid has already been through Phase 7.5's variable-radius perimeter
    # chamfer, which reshapes the cap face's boundary right where rail_top sits (tapering to a
    # small but nonzero distance at the rail's own ends - see _CHAMFER_PERIMETER_ENDPOINT_MM in
    # RelativeMotion.py). So the ACTUAL current boundary edge is offset by a few microns from
    # the pristine rail_top/rail_bottom curves used to build ramp_profile/new_outer_curve - too
    # small to matter for the curve-level joins above, but enough to leave a naked edge if
    # JoinBreps is held to document tolerance. Crucially, this looseness must be confined to
    # that one JoinBreps call: passing it to the CreatePlanarBreps face rebuild too would risk
    # misreading a nearby-but-genuinely-separate pre-existing inner loop (e.g. an anchor bore a
    # few hundredths of a mm from a tight mid-support rail's notch) as touching the new outer
    # boundary, splitting the rebuilt face into multiple pieces instead of one.
    graft_tol = max(tol, _GRAFT_JOIN_TOL_MM)
    try:
        result_brep = graft_open_brep_into_face(
            splint_solid, face_match.face_index, new_outer_curve, open_duct,
            tolerance=tol, join_tolerance=graft_tol)
    except BrepUnionError as exc:
        raise SupportPathRampError(
            "graft of ramp duct into splint_solid failed: {0}".format(exc))
    except Exception as exc:
        raise SupportPathRampError(
            "graft of ramp duct into splint_solid raised: {0}: {1}".format(
                type(exc).__name__, exc))
    _dput(debug, "result_brep", result_brep)
    log("build_support_path_ramp: ramp grafted OK, faces={0}".format(result_brep.Faces.Count))
    return result_brep


def _stadium_end_cap(rail_top, rail_bottom, at_start, thickness):
    """Build a semicircular cap curve (diameter = thickness) joining rail_top's and
    rail_bottom's corresponding endpoint (start or end), bulging OUTWARD (away from the rest
    of the curve) so the resulting stadium shape closes cleanly. Returns None if the
    resulting arc is invalid."""
    if at_start:
        p_top = rail_top.PointAtStart
        p_bottom = rail_bottom.PointAtStart
        tangent = rail_top.TangentAtStart
        tangent.Reverse()  # bulge away from the curve body at the START end
    else:
        p_top = rail_top.PointAtEnd
        p_bottom = rail_bottom.PointAtEnd
        tangent = rail_top.TangentAtEnd
    if not tangent.Unitize():
        return None
    mid = rg.Point3d(
        (p_top.X + p_bottom.X) / 2.0,
        (p_top.Y + p_bottom.Y) / 2.0,
        (p_top.Z + p_bottom.Z) / 2.0)
    r = thickness / 2.0
    through = rg.Point3d(
        mid.X + tangent.X * r, mid.Y + tangent.Y * r, mid.Z + tangent.Z * r)
    arc = rg.Arc(p_top, through, p_bottom)
    if not arc.IsValid:
        return None
    return arc.ToNurbsCurve()


def _build_offset_stadium(rail_top, thickness, d, tol):
    """Build a stadium profile inset by `d` from the original. Same construction as the
    original (2 lines + 2 arcs) so all loft sections have identical curve structure.
    Returns None if d is too large for a valid result."""
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
    # Build rail_bottom shifted further down by the reduced thickness
    new_thickness = thickness - 2.0 * d
    inner_rail_bottom = inner_rail_top.DuplicateCurve()
    inner_rail_bottom.Translate(rg.Vector3d(0.0, 0.0, -new_thickness))
    # End caps with the reduced thickness
    cap_s = _stadium_end_cap(inner_rail_top, inner_rail_bottom,
                             at_start=True, thickness=new_thickness)
    cap_e = _stadium_end_cap(inner_rail_top, inner_rail_bottom,
                             at_start=False, thickness=new_thickness)
    if cap_s is None or cap_e is None:
        return None
    joined = rg.Curve.JoinCurves(
        [inner_rail_top, cap_e, inner_rail_bottom, cap_s], tol)
    if joined is None or len(joined) != 1 or not joined[0].IsClosed:
        return None
    return joined[0]
