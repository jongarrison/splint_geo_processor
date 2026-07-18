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
v = slit_location_vector radial, normal = slit_cutting_orientation_vector):

    r = _WALL_MARGIN * ring_wall_thickness / 2                (default margin 1.15)
    C = slit_gap_width / 2 + r
    Arc centers on the u-axis at (+/-C, 0), arcs bulge INWARD toward the origin.
    Closest points on the two arcs are slit_gap_width apart along u.
    Total cutter tangential extent: slit_gap_width + 2r
    Total cutter radial extent:     2r  (== _WALL_MARGIN * wall_thickness)

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

# Distance to shoot the wall-detection ray (mm). A splint's ring bore is <=~50mm across
# in the worst case, so 500mm is 10x safety without wasting time on absurd hits.
_RAY_LENGTH_MM = 500.0

# Default plausible wall thickness range (mm). Hits outside this range indicate the ray
# hit unintended geometry (bridge, spine, another anchor) instead of the target wall.
# Overridable via wall_thickness_range param.
_DEFAULT_WALL_MIN_MM = 0.1
_DEFAULT_WALL_MAX_MM = 20.0

# Dot-product tolerance for the perpendicularity check on the two input vectors.
# 1e-3 accepts vectors up to ~0.06 degrees off perpendicular, which is well beyond
# any real construction noise but catches obvious caller mistakes.
_PERP_DOT_TOL = 1e-3


def cut_ring_slit(splint_solid,
                  ring_centroid,
                  slit_location_vector,
                  slit_cutting_orientation_vector,
                  slit_gap_width,
                  cutter_depth,
                  tolerance=None,
                  wall_thickness_range=None):
    """Cut a through-slit across a ring wall of splint_solid.

    Applies an inverted-stadium cutter (rectangle + two inward-bulging semi-circles) that
    leaves rounded concave faces on the remaining wall material - no separate edge finishing
    needed. See the module docstring for the geometry and rationale.

    IMPORTANT (caller responsibility): choose slit_location_vector so that a ray from
    ring_centroid in that direction hits exactly the intended wall - i.e. exactly TWO
    brep surfaces in sequence (inner bore then outer wall). Rays that hit bridges, other
    anchors, or return-spine geometry first will produce a bad wall measurement or a bad
    cut. The module validates the ray-hit spacing against wall_thickness_range but cannot
    otherwise verify semantic correctness.

    Args:
        splint_solid (rg.Brep): the splint solid to cut. Not mutated; a new Brep is returned.
        ring_centroid (rg.Point3d): a point INSIDE the ring bore. Typical choice for an
            anchor ring: bore centerline at the mid-band-width position along the finger axis.
        slit_location_vector (rg.Vector3d): from ring_centroid toward the ring wall to cut.
            Must be non-zero; will be normalised.
        slit_cutting_orientation_vector (rg.Vector3d): direction to extrude the cutter (the
            slit's axial direction). For an anchor ring this is the finger axis. Must be
            perpendicular (within _PERP_DOT_TOL) to slit_location_vector, non-zero; will be
            normalised.
        slit_gap_width (float, mm): the narrowest tangential opening of the resulting slit
            (distance between the two inward-arc innermost points). Must be > 0.
        cutter_depth (float, mm): half-length of the symmetric extrusion. Should be clearly
            larger than the ring's band width along the extrusion direction (e.g.
            band_width_mm * 2) since ring cross-sections can be skewed (trapezoidal) rather
            than clean rectangles.
        tolerance (float or None): document unit tolerance for intersection / boolean.
            None (default) uses RhinoDoc.ModelAbsoluteTolerance.
        wall_thickness_range (tuple(min_mm, max_mm) or None): plausible range for the
            measured wall thickness. Default (0.1, 20.0). Hits outside this range raise a
            clean error - useful to catch "ray hit wrong surfaces" bugs early.

    Returns:
        A tuple:
          - splint_solid_result (rg.Brep): the cut solid
          - slit_cutter_brep (rg.Brep): the cutter used (for baking / previewing)
          - ring_wall_thickness (float, mm): measured wall thickness (inner-to-outer hit)
          - ring_wall_centroid (rg.Point3d): midpoint of the two wall hits; the cutter's
            2D-profile origin (for debugging placement)
          - slit_cutter_profile_curve (rg.Curve): closed 2D profile curve in slit_cutter_plane
            (for previewing before the cut is applied)

    Raises:
        RingSlitError: input vectors are degenerate or non-perpendicular; ray fails to find
            two valid wall hits; wall thickness outside plausible range; cutter construction
            fails; boolean subtraction fails.
        ValueError: numeric inputs are invalid (non-positive gap width or cutter depth).
    """
    # --- Validate inputs --------------------------------------------------------------
    if splint_solid is None or not isinstance(splint_solid, rg.Brep):
        raise RingSlitError("splint_solid must be a Rhino Brep (got {0})".format(
            type(splint_solid).__name__))
    if not splint_solid.IsSolid:
        raise RingSlitError("splint_solid must be a closed solid Brep")
    if not isinstance(ring_centroid, rg.Point3d):
        raise RingSlitError("ring_centroid must be a Point3d (got {0})".format(
            type(ring_centroid).__name__))
    if slit_gap_width <= 0.0:
        raise ValueError("slit_gap_width must be > 0 (got {0})".format(slit_gap_width))
    if cutter_depth <= 0.0:
        raise ValueError("cutter_depth must be > 0 (got {0})".format(cutter_depth))

    tol = tolerance
    if tol is None or tol <= 0.0:
        tol = Rhino.RhinoDoc.ActiveDoc.ModelAbsoluteTolerance

    wall_min, wall_max = wall_thickness_range or (_DEFAULT_WALL_MIN_MM, _DEFAULT_WALL_MAX_MM)
    if wall_min <= 0.0 or wall_max <= wall_min:
        raise ValueError("wall_thickness_range must be (min>0, max>min); got ({0}, {1})".format(
            wall_min, wall_max))

    # --- Normalise + orthogonalise the frame ------------------------------------------
    # Work on copies so caller vectors are not mutated.
    loc = rg.Vector3d(slit_location_vector)
    ori = rg.Vector3d(slit_cutting_orientation_vector)
    if not loc.Unitize():
        raise RingSlitError("slit_location_vector is zero-length; cannot normalise")
    if not ori.Unitize():
        raise RingSlitError("slit_cutting_orientation_vector is zero-length; cannot normalise")
    dot = loc.X * ori.X + loc.Y * ori.Y + loc.Z * ori.Z
    if abs(dot) > _PERP_DOT_TOL:
        raise RingSlitError(
            "slit_location_vector and slit_cutting_orientation_vector must be "
            "perpendicular (dot product = {0:.6f}, tolerance = {1})".format(dot, _PERP_DOT_TOL))
    # slit_gap_axis_line = perpendicular to both, right-handed with (ori, loc). Sign is
    # not semantically important - the cutter is symmetric across slit_gap_axis_line.
    gap_axis = rg.Vector3d.CrossProduct(ori, loc)
    if not gap_axis.Unitize():
        raise RingSlitError("cross product of input vectors is degenerate")

    log("cut_ring_slit: frame ori=({0:.3f},{1:.3f},{2:.3f}) loc=({3:.3f},{4:.3f},{5:.3f}) "
        "gap_axis=({6:.3f},{7:.3f},{8:.3f})".format(
            ori.X, ori.Y, ori.Z, loc.X, loc.Y, loc.Z,
            gap_axis.X, gap_axis.Y, gap_axis.Z))

    # --- Find the ring wall via a ray shoot from ring_centroid ------------------------
    ray_start = rg.Point3d(ring_centroid)
    ray_end = rg.Point3d(ring_centroid.X + loc.X * _RAY_LENGTH_MM,
                         ring_centroid.Y + loc.Y * _RAY_LENGTH_MM,
                         ring_centroid.Z + loc.Z * _RAY_LENGTH_MM)
    ray_curve = rg.LineCurve(ray_start, ray_end)
    # Intersection.CurveBrep returns (bool ok, Curve[] overlap_curves, Point3d[] hit_points).
    ok, _overlap_crvs, hit_points = Intersection.CurveBrep(ray_curve, splint_solid, tol)
    if not ok or hit_points is None or len(hit_points) < 2:
        n = 0 if hit_points is None else len(hit_points)
        raise RingSlitError(
            "wall-detection ray from ring_centroid found {0} hit(s), expected >= 2. Check "
            "that ring_centroid is inside the ring bore and slit_location_vector points "
            "toward a clean ring wall (no bridges, other anchors, or spine geometry in the "
            "way).".format(n))

    # Sort hits by distance from ring_centroid; take first (inner bore) and second (outer wall).
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
    wall_centroid = rg.Point3d(
        (inner_hit.X + outer_hit.X) / 2.0,
        (inner_hit.Y + outer_hit.Y) / 2.0,
        (inner_hit.Z + outer_hit.Z) / 2.0)
    log("cut_ring_slit: wall_thickness={0:.3f}mm  wall_centroid=({1:.2f},{2:.2f},{3:.2f}) "
        "inner_hit=({4:.2f},{5:.2f},{6:.2f}) outer_hit=({7:.2f},{8:.2f},{9:.2f})".format(
            wall_thickness,
            wall_centroid.X, wall_centroid.Y, wall_centroid.Z,
            inner_hit.X, inner_hit.Y, inner_hit.Z,
            outer_hit.X, outer_hit.Y, outer_hit.Z))

    # --- Build the cutter's 2D profile curve in slit_cutter_plane ---------------------
    # Frame at wall_centroid with u=gap_axis (tangential), v=loc (radial), n=ori (extrusion).
    r = _WALL_MARGIN * wall_thickness / 2.0
    C = slit_gap_width / 2.0 + r

    # Four arc-endpoint corners
    p_tl = _combine(wall_centroid, gap_axis, -C, loc, +r)   # top-left  (-C, +r)
    p_tr = _combine(wall_centroid, gap_axis, +C, loc, +r)   # top-right (+C, +r)
    p_bl = _combine(wall_centroid, gap_axis, -C, loc, -r)   # bottom-left  (-C, -r)
    p_br = _combine(wall_centroid, gap_axis, +C, loc, -r)   # bottom-right (+C, -r)

    # Innermost "through" points of each inward-bulging arc (arc peak at radial midpoint).
    left_inner = _combine(wall_centroid, gap_axis, -(C - r), loc, 0.0)   # (-C+r, 0)
    right_inner = _combine(wall_centroid, gap_axis, +(C - r), loc, 0.0)  # (+C-r, 0)

    # Arc constructor: Arc(start, through, end). The "through" point is on the arc between
    # start and end, disambiguating which of the two possible arcs (short vs long, inward vs
    # outward) we mean. left_inner / right_inner are the innermost points so we get the
    # inward-bulging arcs.
    left_arc = rg.Arc(p_tl, left_inner, p_bl)
    right_arc = rg.Arc(p_br, right_inner, p_tr)
    if not left_arc.IsValid or not right_arc.IsValid:
        raise RingSlitError(
            "cutter arc construction produced invalid geometry (r={0:.3f}mm, "
            "gap_width={1:.3f}mm)".format(r, slit_gap_width))
    left_arc_crv = left_arc.ToNurbsCurve()
    right_arc_crv = right_arc.ToNurbsCurve()

    # Straight sides (top: tr->tl going -u, bottom: bl->br going +u). Directions chosen so
    # the joined polycurve traces the boundary CCW when viewed from +ori.
    top_line = rg.LineCurve(p_tr, p_tl)
    bottom_line = rg.LineCurve(p_bl, p_br)

    joined = rg.Curve.JoinCurves([top_line, left_arc_crv, bottom_line, right_arc_crv], tol)
    if joined is None or len(joined) != 1 or not joined[0].IsClosed:
        n = 0 if joined is None else len(joined)
        raise RingSlitError(
            "failed to join cutter profile pieces into a single closed curve (got "
            "{0} piece(s), closed={1})".format(
                n, joined and n == 1 and joined[0].IsClosed))
    profile_curve = joined[0]

    # --- Extrude symmetrically around slit_cutter_plane -------------------------------
    # Translate the profile back by cutter_depth along -ori, then Extrusion.Create by
    # 2*cutter_depth in +ori. Result centered on wall_centroid along ori.
    start_curve = profile_curve.DuplicateCurve()
    if not start_curve.Translate(ori * (-cutter_depth)):
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
        "faces={3}".format(r, slit_gap_width, cutter_depth, cutter_brep.Faces.Count))

    # --- Boolean subtract --------------------------------------------------------------
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

    return result_brep, cutter_brep, wall_thickness, wall_centroid, profile_curve


def _combine(origin, u_axis, u_scale, v_axis, v_scale):
    """Return `origin + u_axis*u_scale + v_axis*v_scale` as a Point3d.
    Small helper to keep the profile-corner construction readable."""
    return rg.Point3d(
        origin.X + u_axis.X * u_scale + v_axis.X * v_scale,
        origin.Y + u_axis.Y * u_scale + v_axis.Y * v_scale,
        origin.Z + u_axis.Z * u_scale + v_axis.Z * v_scale)
