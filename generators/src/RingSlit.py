"""
RingSlit.py

Cut a through-slit across a ring wall of a splint brep so the ring can spread open
(designed for anchor finger rings, but applies generally to any tube/ring-shaped wall
in a splint solid).

The cutter is an "inverted stadium" (hourglass) profile: a rectangle with two inward-
bulging semi-circular arcs on the tangential ends. After boolean subtraction the wall
material on each side of the slit has a concave (radius r) face, so there is no sharp
skin-contact lip and NO separate slit-edge finishing pass is required.

See dev-notes/260702_Dev_Process_RelativeMotion_splint.md Phase 7.6 for the full design
rationale, math, and caller wiring guidance.

Cutter geometry (in slit_cutter_plane, with u = slit_gap_axis_line tangential,
v = slit_location_vector radial, normal = p1_line_oriented direction):

    r = _WALL_MARGIN * ring_wall_thickness / 2                (default margin 1.15)
    C = slit_gap_width / 2 + r
    Arc centers on the u-axis at (+/-C, 0), arcs bulge INWARD toward the origin.
    Closest points on the two arcs are slit_gap_width apart along u.
    Total cutter tangential extent: slit_gap_width + 2r
    Total cutter radial extent:     2r  (== _WALL_MARGIN * wall_thickness)

Placement (why the panel-intersection step):
    An anchor ring's wall cross-section can be skewed (trapezoidal) when adjacent-finger
    pip_neighbor_fwd_offset values shift bridge attachments longitudinally. Placing the
    cutter at a naive midpoint would drift off the wall's real geometric center. Instead
    we build a large panel with one edge along the extended P1 line and the other edge
    offset in slit_location_vector, intersect it with the splint, and use the AREA
    CENTROID of the resulting closed cross-section curve as the cutter origin.

Failure model: raises RingSlitError on any problem. Never returns partial / degenerate
geometry. The caller decides whether to fail hard or log-and-continue.
"""

import Rhino
import Rhino.Geometry as rg
from Rhino.Geometry.Intersect import Intersection
from splintcommon import log
from BrepDifference import robust_brep_difference


class RingSlitError(Exception):
    """Raised when the ring-slit operation fails cleanly for any reason."""
    pass


# Radial overshoot factor: cutter's radial extent = _WALL_MARGIN * wall_thickness.
# 1.15 (15% overshoot) is enough to punch cleanly through the wall even with small
# measurement noise. Increase if a splint's wall thickness measurement is noisier;
# decrease only if the extra overshoot is causing unwanted trim into adjacent geometry.
_WALL_MARGIN = 1.15

# Distance to shoot the wall-thickness ray (mm). Splint ring bores are <=~50mm across,
# so 500mm is 10x safety without wasting time on absurd hits.
_RAY_LENGTH_MM = 500.0

# Default plausible wall thickness range (mm). Hits outside this range indicate the ray
# hit unintended geometry (bridge, spine, another anchor) instead of the target wall.
# Overridable via wall_thickness_range param.
_DEFAULT_WALL_MIN_MM = 0.1
_DEFAULT_WALL_MAX_MM = 20.0

# Dot-product tolerance for the perpendicularity check between the P1 line direction and
# slit_location_vector. 1e-3 accepts vectors up to ~0.06 degrees off perpendicular, well
# beyond any real construction noise but catches obvious caller mistakes.
_PERP_DOT_TOL = 1e-3


def cut_ring_slit(splint_solid,
                  p1_line_oriented,
                  slit_location_vector,
                  slit_gap_width,
                  cutter_depth,
                  tolerance=None,
                  wall_thickness_range=None,
                  panel_length_multiplier=0.8,
                  edge_radar_length_mm=40.0):
    """Cut a through-slit across a ring wall of splint_solid.

    Applies an inverted-stadium cutter (rectangle + two inward-bulging semi-circles) that
    leaves rounded concave faces on the remaining wall material - no separate edge
    finishing needed. See the module docstring for the geometry and rationale.

    Uses a panel/splint intersection to locate the wall's true area centroid (robust to
    skewed cross-sections caused by pip_neighbor_fwd_offset in the splint construction).

    IMPORTANT (caller responsibility): choose slit_location_vector so that the wall-
    thickness ray from the derived ring_centroid in that direction hits exactly the
    intended wall - i.e. exactly TWO brep surfaces in sequence (inner bore then outer
    wall). Rays that hit bridges, other anchors, or return-spine geometry first will
    produce a bad wall measurement or a bad cut. The module validates the ray-hit spacing
    against wall_thickness_range but cannot otherwise verify semantic correctness.

    Args:
        splint_solid (rg.Brep): the splint solid to cut. Not mutated; a new Brep is
            returned.
        p1_line_oriented (rg.Line): the finger's P1 line (MCP -> PIP) in the splint's
            current oriented world coordinates. Its direction defines the cutter's
            extrusion axis; the ring_centroid is derived by projecting the panel-
            intersection cross-section centroid onto the extended P1 line.
        slit_location_vector (rg.Vector3d): from the P1 line outward toward the ring wall
            to cut. Must be non-zero and perpendicular to p1_line_oriented.Direction
            (within _PERP_DOT_TOL); will be normalised.
        slit_gap_width (float, mm): the narrowest tangential opening of the resulting
            slit (distance between the two inward-arc innermost points). Must be > 0.
        cutter_depth (float, mm): half-length of the symmetric extrusion. Should be
            clearly larger than the ring's band width along the P1 axis (e.g.
            longitudinal_band_width_mm * 3) since ring cross-sections can be skewed
            (trapezoidal) rather than clean rectangles.
        tolerance (float or None): document unit tolerance for intersection / boolean.
            None (default) uses RhinoDoc.ModelAbsoluteTolerance.
        wall_thickness_range (tuple(min_mm, max_mm) or None): plausible range for the
            measured wall thickness. Default (0.1, 20.0). Hits outside this range raise
            a clean error - useful to catch "ray hit wrong surfaces" bugs early.
        panel_length_multiplier (float): extend p1_line by this * p1_length on EACH end
            when building the wall-detection panel. Default 4.0 (very generous; panel
            length becomes p1_length * (1 + 2 * multiplier)).
        edge_radar_length_mm (float): panel width in the slit_location direction. Must
            be long enough to reach past the splint's outer surface. Default 200 mm.

    Returns:
        A 7-tuple:
          - splint_solid_result (rg.Brep): the cut solid
          - slit_cutter_brep (rg.Brep): the cutter used (for baking / previewing)
          - ring_wall_thickness (float, mm): measured wall thickness (inner-to-outer hit)
          - ring_wall_centroid (rg.Point3d): area centroid of the wall cross-section; the
            cutter's 2D-profile origin
          - slit_cutter_profile_curve (rg.Curve): closed 2D profile curve in
            slit_cutter_plane (for previewing before the cut is applied)
          - panel_brep (rg.Brep): the panel used to intersect the splint. In current use
            this is a rectangle (anchor P1 line is perpendicular to slit_location), but
            it becomes a parallelogram for skewed future variants where P1 isn't exactly
            perpendicular - the algorithm handles either
          - ring_wall_cross_section_curve (rg.Curve): the closed 2D curve where the panel
            cut through the wall (for debugging placement)

    Raises:
        RingSlitError: input vectors are degenerate or non-perpendicular; panel/splint
            intersection returns nothing usable; ray fails to find two valid wall hits;
            wall thickness outside plausible range; cutter construction fails; boolean
            subtraction fails.
        ValueError: numeric inputs are invalid (non-positive gap width, cutter depth,
            multipliers, etc.).
    """
    # --- Validate inputs --------------------------------------------------------------
    if splint_solid is None or not isinstance(splint_solid, rg.Brep):
        raise RingSlitError("splint_solid must be a Rhino Brep (got {0})".format(
            type(splint_solid).__name__))
    if not splint_solid.IsSolid:
        raise RingSlitError("splint_solid must be a closed solid Brep")
    if not isinstance(p1_line_oriented, rg.Line):
        raise RingSlitError("p1_line_oriented must be a rg.Line (got {0})".format(
            type(p1_line_oriented).__name__))
    if p1_line_oriented.Length < 1e-6:
        raise RingSlitError("p1_line_oriented has zero length; cannot derive frame")
    if slit_gap_width <= 0.0:
        raise ValueError("slit_gap_width must be > 0 (got {0})".format(slit_gap_width))
    if cutter_depth <= 0.0:
        raise ValueError("cutter_depth must be > 0 (got {0})".format(cutter_depth))
    if panel_length_multiplier < 0.0:
        raise ValueError("panel_length_multiplier must be >= 0 (got {0})".format(
            panel_length_multiplier))
    if edge_radar_length_mm <= 0.0:
        raise ValueError("edge_radar_length_mm must be > 0 (got {0})".format(
            edge_radar_length_mm))

    tol = tolerance
    if tol is None or tol <= 0.0:
        tol = Rhino.RhinoDoc.ActiveDoc.ModelAbsoluteTolerance

    wall_min, wall_max = wall_thickness_range or (_DEFAULT_WALL_MIN_MM, _DEFAULT_WALL_MAX_MM)
    if wall_min <= 0.0 or wall_max <= wall_min:
        raise ValueError("wall_thickness_range must be (min>0, max>min); got ({0}, {1})".format(
            wall_min, wall_max))

    # --- Normalise + orthogonalise the frame ------------------------------------------
    # loc: radial direction through the wall; ori: cutter extrusion axis (= p1_line
    # direction); gap_axis: tangential axis (right-handed cross of ori and loc).
    loc = rg.Vector3d(slit_location_vector)
    if not loc.Unitize():
        raise RingSlitError("slit_location_vector is zero-length; cannot normalise")
    ori = p1_line_oriented.Direction
    ori.Unitize()  # Line.Direction is unit already, but be defensive
    dot = loc.X * ori.X + loc.Y * ori.Y + loc.Z * ori.Z
    if abs(dot) > _PERP_DOT_TOL:
        raise RingSlitError(
            "p1_line_oriented direction and slit_location_vector must be perpendicular "
            "(dot product = {0:.6f}, tolerance = {1})".format(dot, _PERP_DOT_TOL))
    gap_axis = rg.Vector3d.CrossProduct(ori, loc)
    if not gap_axis.Unitize():
        raise RingSlitError("cross product of p1 direction and slit_location_vector is "
                            "degenerate")
    log("cut_ring_slit: frame ori=({0:.3f},{1:.3f},{2:.3f}) loc=({3:.3f},{4:.3f},{5:.3f}) "
        "gap_axis=({6:.3f},{7:.3f},{8:.3f})".format(
            ori.X, ori.Y, ori.Z, loc.X, loc.Y, loc.Z,
            gap_axis.X, gap_axis.Y, gap_axis.Z))

    # --- Build the wall-detection panel, intersect with splint, derive ring_centroid --
    # Extend the P1 line by panel_length_multiplier on each end so the panel is guaranteed
    # to overshoot the splint's extents along the P1 axis. Then build a 4-corner panel:
    # the extended P1 line on one edge, translated by loc * edge_radar_length_mm on the
    # opposite edge. In current use (anchor P1 perpendicular to slit_location) this is a
    # rectangle; if a future splint variant has skewed P1 orientation it becomes a
    # planar parallelogram - Brep.CreateFromCornerPoints handles both.
    p1_len = p1_line_oriented.Length
    ext = p1_len * panel_length_multiplier
    p1_start_ext = rg.Point3d(
        p1_line_oriented.From.X - ori.X * ext,
        p1_line_oriented.From.Y - ori.Y * ext,
        p1_line_oriented.From.Z - ori.Z * ext)
    p1_end_ext = rg.Point3d(
        p1_line_oriented.To.X + ori.X * ext,
        p1_line_oriented.To.Y + ori.Y * ext,
        p1_line_oriented.To.Z + ori.Z * ext)
    p1_line_extended = rg.Line(p1_start_ext, p1_end_ext)

    c1 = p1_start_ext
    c2 = p1_end_ext
    c3 = rg.Point3d(
        p1_end_ext.X + loc.X * edge_radar_length_mm,
        p1_end_ext.Y + loc.Y * edge_radar_length_mm,
        p1_end_ext.Z + loc.Z * edge_radar_length_mm)
    c4 = rg.Point3d(
        p1_start_ext.X + loc.X * edge_radar_length_mm,
        p1_start_ext.Y + loc.Y * edge_radar_length_mm,
        p1_start_ext.Z + loc.Z * edge_radar_length_mm)
    panel_brep = rg.Brep.CreateFromCornerPoints(c1, c2, c3, c4, tol)
    if panel_brep is None:
        raise RingSlitError(
            "failed to build wall-detection panel brep from 4 corner points")
    log("cut_ring_slit: panel {0:.1f}mm along P1 axis x {1:.1f}mm along slit_location".format(
        p1_line_extended.Length, edge_radar_length_mm))

    # Intersect panel with splint. Intersection curves lie in the panel plane and trace
    # where the panel crosses splint faces. `Intersection.BrepBrep` returns ONE curve per
    # face crossed, so a wall cross-section that spans multiple faces (inner bore face +
    # outer wall face + two rim faces) comes back as several open segments sharing
    # endpoints. Join them first, then filter to closed loops.
    ok_ix, raw_curves, _xsection_pts = Intersection.BrepBrep(panel_brep, splint_solid, tol)
    if not ok_ix or raw_curves is None or len(raw_curves) == 0:
        raise RingSlitError(
            "panel/splint intersection returned no curves. Check that p1_line_oriented "
            "passes through the ring bore and slit_location_vector points toward a wall.")
    joined_xsection = rg.Curve.JoinCurves(list(raw_curves), tol)
    if joined_xsection is None or len(joined_xsection) == 0:
        raise RingSlitError(
            "JoinCurves on {0} panel/splint intersection segment(s) returned nothing".format(
                len(raw_curves)))
    xsection_curves = list(joined_xsection)
    closed_curves = [c for c in xsection_curves if c.IsClosed]
    if len(closed_curves) == 0:
        raise RingSlitError(
            "panel/splint intersection joined into {0} curve(s) but none are closed - the "
            "panel may not fully enclose the wall cross-section. Consider increasing "
            "edge_radar_length_mm or panel_length_multiplier.".format(len(xsection_curves)))

    # Pick the closed curve whose area centroid is closest to the extended P1 line -
    # that's the ring wall (other closed curves might come from adjacent-finger bridges,
    # return-spine geometry, etc., all of which sit farther from the P1 axis).
    p1_line_ext_curve = rg.LineCurve(p1_line_extended)
    candidates = []  # list of (dist_to_p1_line, closed_curve, area_centroid)
    for c in closed_curves:
        amp = rg.AreaMassProperties.Compute(c)
        if amp is None:
            continue
        cent = amp.Centroid
        ok_cp, t_line = p1_line_ext_curve.ClosestPoint(cent)
        if not ok_cp:
            continue
        proj_pt = p1_line_ext_curve.PointAt(t_line)
        candidates.append((cent.DistanceTo(proj_pt), c, cent))
    if not candidates:
        raise RingSlitError(
            "all {0} closed intersection curve(s) had unusable area properties".format(
                len(closed_curves)))
    candidates.sort(key=lambda tup: tup[0])
    best_dist, ring_wall_cross_section_curve, ring_wall_centroid = candidates[0]
    if len(closed_curves) > 1:
        log("cut_ring_slit: panel intersected {0} closed curve(s); picked one nearest the "
            "extended P1 line (centroid gap {1:.3f} mm)".format(
                len(closed_curves), best_dist))
    # ring_centroid: project the wall centroid onto the extended P1 line. Sits inside
    # the ring bore, on the finger axis, at the same longitudinal position as the wall
    # cross-section's centroid. Used as the origin of the wall-thickness ray-shoot.
    ok_cp, t_line = p1_line_ext_curve.ClosestPoint(ring_wall_centroid)
    if not ok_cp:
        raise RingSlitError("could not project ring_wall_centroid onto extended P1 line")
    ring_centroid = p1_line_ext_curve.PointAt(t_line)
    log("cut_ring_slit: ring_wall_centroid=({0:.2f},{1:.2f},{2:.2f})  "
        "ring_centroid=({3:.2f},{4:.2f},{5:.2f})  wall-to-axis dist={6:.3f}mm".format(
            ring_wall_centroid.X, ring_wall_centroid.Y, ring_wall_centroid.Z,
            ring_centroid.X, ring_centroid.Y, ring_centroid.Z, best_dist))

    # --- Measure wall thickness via a ray shoot from ring_centroid --------------------
    ray_end = rg.Point3d(
        ring_centroid.X + loc.X * _RAY_LENGTH_MM,
        ring_centroid.Y + loc.Y * _RAY_LENGTH_MM,
        ring_centroid.Z + loc.Z * _RAY_LENGTH_MM)
    ray_curve = rg.LineCurve(ring_centroid, ray_end)
    ok_ray, _overlap_crvs, hit_points = Intersection.CurveBrep(ray_curve, splint_solid, tol)
    if not ok_ray or hit_points is None or len(hit_points) < 2:
        n = 0 if hit_points is None else len(hit_points)
        raise RingSlitError(
            "wall-thickness ray from ring_centroid found {0} hit(s), expected >= 2. Check "
            "that the panel-derived ring_centroid is actually inside the bore.".format(n))
    hits = sorted(list(hit_points), key=lambda p: p.DistanceTo(ring_centroid))
    inner_hit = hits[0]
    outer_hit = hits[1]
    wall_thickness = inner_hit.DistanceTo(outer_hit)
    if wall_thickness < wall_min or wall_thickness > wall_max:
        raise RingSlitError(
            "measured ring wall thickness {0:.3f}mm is outside plausible range "
            "[{1}, {2}] mm - the ray probably hit the wrong pair of surfaces. Check "
            "ring_centroid placement and slit_location_vector direction, or widen "
            "wall_thickness_range if this splint really does have such a wall.".format(
                wall_thickness, wall_min, wall_max))
    log("cut_ring_slit: wall_thickness={0:.3f}mm".format(wall_thickness))

    # --- Build the cutter's 2D profile curve, centered on ring_wall_centroid ----------
    # Use the panel-derived wall centroid as the cutter origin - it captures the true
    # geometric center of the (possibly skewed) wall cross-section, not just the ray
    # midpoint.
    r = _WALL_MARGIN * wall_thickness / 2.0
    C = slit_gap_width / 2.0 + r

    p_tl = _combine(ring_wall_centroid, gap_axis, -C, loc, +r)
    p_tr = _combine(ring_wall_centroid, gap_axis, +C, loc, +r)
    p_bl = _combine(ring_wall_centroid, gap_axis, -C, loc, -r)
    p_br = _combine(ring_wall_centroid, gap_axis, +C, loc, -r)
    left_inner = _combine(ring_wall_centroid, gap_axis, -(C - r), loc, 0.0)
    right_inner = _combine(ring_wall_centroid, gap_axis, +(C - r), loc, 0.0)

    # Arc(start, through, end): the through point disambiguates which of the two possible
    # arcs (inward-bulging vs outward-bulging). left_inner/right_inner are the innermost
    # points -> inward-bulging arcs.
    left_arc = rg.Arc(p_tl, left_inner, p_bl)
    right_arc = rg.Arc(p_br, right_inner, p_tr)
    if not left_arc.IsValid or not right_arc.IsValid:
        raise RingSlitError(
            "cutter arc construction produced invalid geometry (r={0:.3f}mm, "
            "gap_width={1:.3f}mm)".format(r, slit_gap_width))
    left_arc_crv = left_arc.ToNurbsCurve()
    right_arc_crv = right_arc.ToNurbsCurve()

    # Straight sides; directions chosen so the joined polycurve traces CCW when viewed
    # from +ori (top: tr->tl, bottom: bl->br).
    top_line = rg.LineCurve(p_tr, p_tl)
    bottom_line = rg.LineCurve(p_bl, p_br)
    joined = rg.Curve.JoinCurves([top_line, left_arc_crv, bottom_line, right_arc_crv], tol)
    if joined is None or len(joined) != 1 or not joined[0].IsClosed:
        n = 0 if joined is None else len(joined)
        raise RingSlitError(
            "failed to join cutter profile pieces into a single closed curve (got "
            "{0} piece(s))".format(n))
    profile_curve = joined[0]

    # --- Extrude symmetrically around slit_cutter_plane, then boolean subtract --------
    # Extrusion.Create extrudes along the profile curve's plane normal. That normal points
    # either +ori or -ori depending on which way JoinCurves stitched the profile - so use
    # TryGetPlane to detect the actual direction, then translate the start curve by
    # -plane_normal * cutter_depth so the +plane_normal extrusion by 2*cutter_depth lands
    # symmetric around ring_wall_centroid regardless of trace orientation.
    ok_pl, profile_plane = profile_curve.TryGetPlane(tol)
    if not ok_pl:
        raise RingSlitError("cutter profile is not planar - cannot determine extrusion axis")
    plane_normal = profile_plane.Normal
    plane_normal.Unitize()
    normal_dot_ori = plane_normal.X * ori.X + plane_normal.Y * ori.Y + plane_normal.Z * ori.Z
    if abs(normal_dot_ori) < 0.99:
        raise RingSlitError(
            "cutter profile plane normal ({0:.3f},{1:.3f},{2:.3f}) not aligned with p1 "
            "direction (dot={3:.4f})".format(
                plane_normal.X, plane_normal.Y, plane_normal.Z, normal_dot_ori))
    start_curve = profile_curve.DuplicateCurve()
    if not start_curve.Translate(plane_normal * (-cutter_depth)):
        raise RingSlitError("failed to translate cutter profile for symmetric extrusion")
    extrusion = rg.Extrusion.Create(start_curve, 2.0 * cutter_depth, True)  # cap=True
    if extrusion is None:
        raise RingSlitError(
            "cutter extrusion failed (r={0:.3f}mm, gap_width={1:.3f}mm, "
            "depth=+/-{2:.2f}mm)".format(r, slit_gap_width, cutter_depth))
    cutter_brep = extrusion.ToBrep()
    if cutter_brep is None or not cutter_brep.IsSolid:
        raise RingSlitError(
            "cutter extrusion did not convert to a closed solid Brep "
            "(IsSolid={0})".format(cutter_brep and cutter_brep.IsSolid))
    log("cut_ring_slit: cutter built  r={0:.3f}mm  gap={1:.3f}mm  depth=+/-{2:.2f}mm  "
        "faces={3}  plane_normal.ori={4:+.3f}".format(
            r, slit_gap_width, cutter_depth, cutter_brep.Faces.Count, normal_dot_ori))

    try:
        result_brep, success, method = robust_brep_difference(
            splint_solid, cutter_brep, tol)
    except Exception as exc:
        raise RingSlitError(
            "boolean subtraction of slit cutter raised: {0}: {1}".format(
                type(exc).__name__, exc))
    if not success or result_brep is None:
        raise RingSlitError(
            "boolean subtraction of slit cutter failed after all fallbacks "
            "(method last tried: {0})".format(method))
    log("cut_ring_slit: slit cut OK via '{0}', result IsSolid={1} faces={2}".format(
        method, result_brep.IsSolid, result_brep.Faces.Count))

    return (result_brep, cutter_brep, wall_thickness, ring_wall_centroid, profile_curve,
            panel_brep, ring_wall_cross_section_curve)


def _combine(origin, u_axis, u_scale, v_axis, v_scale):
    """Return `origin + u_axis*u_scale + v_axis*v_scale` as a Point3d.
    Small helper to keep the profile-corner construction readable."""
    return rg.Point3d(
        origin.X + u_axis.X * u_scale + v_axis.X * v_scale,
        origin.Y + u_axis.Y * u_scale + v_axis.Y * v_scale,
        origin.Z + u_axis.Z * u_scale + v_axis.Z * v_scale)
