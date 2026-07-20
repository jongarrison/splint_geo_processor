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
  3. Brep.CreateFromSweep(ramp_rail, ramp_profile) -> an open tube; CapPlanarHoles closes it
     into a solid. Nudge that solid by a tiny distance opposite start_tangent (into the
     splint body) so its flush starting face isn't exactly coplanar with the splint's own
     face - coincident faces are a classic boolean-union failure mode - then boolean-union
     it in via BrepUnion.robust_brep_union.

Failure model: raises SupportPathRampError on any problem. Debug observability follows the
RingSlit.py out-parameter pattern: pass debug={} to receive every intermediate construction
artifact, which survives whether the call succeeds or raises.
"""

import math
import Rhino
import Rhino.Geometry as rg
from splintcommon import log
from BrepUnion2 import robust_brep_union, BrepUnionError


class SupportPathRampError(Exception):
    """Raised when the support-path-ramp construction or union fails cleanly for any reason.
    No debug payload is attached; construction geometry is exposed via the optional `debug`
    dict parameter to build_support_path_ramp, which survives whether the call succeeds or
    raises."""
    pass


# Nudge distance (mm) the swept ramp solid is shifted opposite its own starting tangent before
# unioning, so its flush starting face never sits exactly coplanar with the splint's distal
# face - coincident faces are a classic boolean-union failure mode. 10 microns: far below
# print resolution, comfortably above float noise (matches RingSlit._INTERSECTION_JOIN_TOL_MM's
# reasoning).
_DEFAULT_UNION_EPSILON_MM = 0.1


def _dput(debug, key, value):
    """Populate a key on the optional debug out-dict, no-op if debug is None. Keeps the
    call sites in build_support_path_ramp compact and lets us extend the debug schema without
    scattering `if debug is not None` guards throughout the body."""
    if debug is not None:
        debug[key] = value


def build_support_path_ramp(splint_solid, support_rail, start_tangent,
                            ramp_thickness, ramp_length, ramp_arc_radius,
                            bend_z_sign=-1.0, rail_trim_mm=2.5,
                            tolerance=None, union_epsilon_mm=None, debug=None):
    """Grow a ramp off one end of support_rail and union it into splint_solid.

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
        rail_trim_mm (float, mm): distance to trim off EACH end of support_rail before
            building the ramp profile. Keeps the ramp's footprint inboard of the rail's
            endpoints (which sit at anchor-ring intersections), avoiding geometry that would
            collide with the ring walls. Default 2.5mm.
        tolerance (float or None): document unit tolerance for joins / sweep / union. None
            (default) uses RhinoDoc.ModelAbsoluteTolerance.
        union_epsilon_mm (float or None): nudge distance (see module docstring). None uses
            _DEFAULT_UNION_EPSILON_MM.
        debug (dict or None): optional out-parameter dict populated progressively during
            construction (RingSlit.py-style). Survives a raise - whatever got built before the
            failure is still here to inspect. Keys: "trimmed_rail", "rail_top", "rail_bottom",
            "cap_start", "cap_end", "ramp_profile", "ramp_rail_plane", "ramp_rail",
            "ramp_tube" (pre-cap), "ramp_solid" (capped, pre-nudge), "ramp_solid_nudged",
            "result_brep" (only on success).

    Returns:
        rg.Brep: splint_solid with the ramp unioned in.

    Raises:
        SupportPathRampError: any construction or union step fails cleanly.
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
    epsilon = union_epsilon_mm
    if epsilon is None or epsilon <= 0.0:
        epsilon = _DEFAULT_UNION_EPSILON_MM

    tangent = rg.Vector3d(start_tangent)
    if not tangent.Unitize():
        raise SupportPathRampError("start_tangent is zero-length; cannot normalise")

    # --- Step 1: ramp_profile (closed "stadium" curve) --------------------------------
    # Trim rail_trim_mm off each end so the ramp footprint sits inboard of the anchor-ring
    # intersections (the rail's raw endpoints are exactly where it meets the ring edges).
    rail_length = support_rail.GetLength()
    if rail_trim_mm > 0.0 and rail_length > rail_trim_mm * 2.0 + 1.0:
        ok_s, t_s = support_rail.LengthParameter(rail_trim_mm)
        ok_e, t_e = support_rail.LengthParameter(rail_length - rail_trim_mm)
        if ok_s and ok_e and t_s < t_e:
            trimmed = support_rail.Trim(t_s, t_e)
            if trimmed is not None:
                support_rail = trimmed
                log("build_support_path_ramp: trimmed {0:.2f}mm off each end of rail "
                    "(original {1:.2f}mm -> {2:.2f}mm)".format(
                        rail_trim_mm, rail_length, support_rail.GetLength()))
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

    # --- Step 3: extrude along curve, cap, nudge, union ---------------------------------
    # Use Brep.CreateFromSweep with the simple overload. The profile is a CLOSED planar curve
    # sitting at the rail's start point. Rhino's simple Sweep1 uses the Frenet frame by default
    # which tilts the profile, BUT for a planar closed profile whose plane normal is aligned
    # with the rail's start tangent, the profile begins perpendicular to the rail and the
    # Frenet rotation IS what we want for a tube that follows the curve (the cross-section
    # stays perpendicular to the rail tangent at every point). For our use case where we want
    # the profile to keep its INITIAL orientation (no rotation), we instead sample points along
    # the rail and loft translated copies of the profile.
    # Approach: translate the profile to N evenly-spaced points along the rail, then loft them.
    n_sections = 12  # enough for a smooth ~30deg arc
    loft_curves = []
    for si in range(n_sections + 1):
        frac = float(si) / n_sections
        ok_param, t_param = ramp_rail.NormalizedLengthParameter(frac)
        if not ok_param:
            continue
        pt = ramp_rail.PointAt(t_param)
        offset = pt - start_point  # translation from profile's original position
        section = ramp_profile.DuplicateCurve()
        section.Translate(rg.Vector3d(offset))
        loft_curves.append(section)
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

    ramp_solid = ramp_tube.CapPlanarHoles(tol)
    if ramp_solid is None or not ramp_solid.IsSolid:
        raise SupportPathRampError(
            "failed to cap the swept ramp tube into a closed solid (capped={0}, "
            "IsSolid={1})".format(
                ramp_solid is not None,
                ramp_solid.IsSolid if ramp_solid is not None else None))
    _dput(debug, "ramp_solid", ramp_solid)
    log("build_support_path_ramp: ramp_solid built and capped, faces={0}".format(
        ramp_solid.Faces.Count))

    # Ensure outward-facing normals - the loft+cap can produce an inward-oriented solid,
    # which CreateBooleanUnion would treat as a subtraction rather than an addition.
    if ramp_solid.SolidOrientation == rg.BrepSolidOrientation.Inward:
        ramp_solid.Flip()
        log("build_support_path_ramp: flipped ramp_solid normals to outward")

    ramp_solid_nudged = ramp_solid.DuplicateBrep()
    nudge = rg.Vector3d(-tangent.X * epsilon, -tangent.Y * epsilon, -tangent.Z * epsilon)
    if not ramp_solid_nudged.Translate(nudge):
        raise SupportPathRampError("failed to nudge ramp_solid before union")
    _dput(debug, "ramp_solid_nudged", ramp_solid_nudged)
    log("build_support_path_ramp: nudged {0:.4f}mm opposite start_tangent before union".format(
        epsilon))

    # Diagnostic: the nudge above only guards against EXACT face-coincidence - it assumes
    # substantial volumetric overlap already exists between ramp_solid and splint_solid at
    # the sweep's start. If that assumption is wrong (e.g. the profile's footprint mostly
    # lies outside splint_solid to begin with), no nudge will fix it and the union will fail
    # regardless of epsilon. Log the two bounding boxes' actual separation/overlap so a
    # "insufficient overlap" union failure can be diagnosed without guessing.
    bbox_splint = splint_solid.GetBoundingBox(True)
    bbox_ramp = ramp_solid_nudged.GetBoundingBox(True)
    bbox_gap_x = max(bbox_splint.Min.X - bbox_ramp.Max.X, bbox_ramp.Min.X - bbox_splint.Max.X, 0.0)
    bbox_gap_y = max(bbox_splint.Min.Y - bbox_ramp.Max.Y, bbox_ramp.Min.Y - bbox_splint.Max.Y, 0.0)
    bbox_gap_z = max(bbox_splint.Min.Z - bbox_ramp.Max.Z, bbox_ramp.Min.Z - bbox_splint.Max.Z, 0.0)
    bbox_overlap = (bbox_gap_x == 0.0 and bbox_gap_y == 0.0 and bbox_gap_z == 0.0)
    log("build_support_path_ramp: bbox check splint=[{0:.2f}..{1:.2f}, {2:.2f}..{3:.2f}, "
        "{4:.2f}..{5:.2f}] ramp=[{6:.2f}..{7:.2f}, {8:.2f}..{9:.2f}, {10:.2f}..{11:.2f}] "
        "bboxes_overlap={12} gap(x,y,z)=({13:.3f},{14:.3f},{15:.3f})".format(
            bbox_splint.Min.X, bbox_splint.Max.X, bbox_splint.Min.Y, bbox_splint.Max.Y,
            bbox_splint.Min.Z, bbox_splint.Max.Z,
            bbox_ramp.Min.X, bbox_ramp.Max.X, bbox_ramp.Min.Y, bbox_ramp.Max.Y,
            bbox_ramp.Min.Z, bbox_ramp.Max.Z,
            bbox_overlap, bbox_gap_x, bbox_gap_y, bbox_gap_z))

    try:
        result_brep, success, method = robust_brep_union(
            [splint_solid, ramp_solid_nudged], tol)
    except BrepUnionError as exc:
        raise SupportPathRampError(
            "union of ramp_solid into splint_solid failed: {0}".format(exc))
    except Exception as exc:
        raise SupportPathRampError(
            "union of ramp_solid into splint_solid raised: {0}: {1}".format(
                type(exc).__name__, exc))
    _dput(debug, "result_brep", result_brep)
    log("build_support_path_ramp: ramp unioned OK via '{0}', faces={1}".format(
        method, result_brep.Faces.Count))
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
