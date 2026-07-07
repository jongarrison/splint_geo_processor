"""
RelativeMotion.py
Central point of contact for the RelativeMotion finger splint Design Definition.

Keeps the geometry logic here in Python (observable/testable in Rhino) so the bound
RelativeMotion.gh stays as thin as possible. Functions return RhinoCommon geometry types.

See dev-notes/260702_Dev_Process_RelativeMotion_splint.md for the construction rationale.
"""

import math
from importlib import reload
from Rhino.Geometry import (Point3d, Vector3d, Line, Plane, Circle, Cylinder, Transform,
                            LineCurve, Curve, CurveOffsetCornerStyle, BlendContinuity)
from Rhino.Geometry.Intersect import Intersection
from splintcommon import log

import TwoDCirclePositioning
reload(TwoDCirclePositioning)
from TwoDCirclePositioning import multiple_circle_positioning


# Minimum edge-to-edge gap (mm) between adjacent finger circles when packing laterally.
DEFAULT_MIN_CENTER_GAP = 1.0

# Elevation angle clamp (degrees). Provisional range pending review with hand therapist Liz.
MIN_ELEVATION_ANGLE = -120.0
MAX_ELEVATION_ANGLE = 45.0

# Total angular width (deg) of a supported finger's preserved support arc. Provisional.
DEFAULT_SUPPORT_ARC_DEG = 120.0

# Radial wall thickness (mm) of an anchor ring: the gap between the finger-contact ellipse
# and the ring's outer boundary. Provisional pending fit review.
DEFAULT_RADIAL_BAND_THICKNESS_MM = 3.0

# Bridge shape-control (mm). web_fillet_r_mm is the target concave blend radius at a joint;
# min_web_width_mm is the minimum neck width the blend should preserve (enforcement deferred).
# Provisional; mirrors the BuddyRingsDuo hourglass_r / min_isthmus_width knobs.
DEFAULT_WEB_FILLET_R_MM = 4.0
DEFAULT_MIN_WEB_WIDTH_MM = 2.0

# Tolerance (document units, mm) for plane/curve intersection and trim operations.
_INTERSECT_TOL = 1e-6

# Looser tolerance (mm) for stitching the finished perimeter pieces with Curve.JoinCurves.
_JOIN_TOL = 1e-2


def setup_finger_positions(raw_data, min_center_gap=DEFAULT_MIN_CENTER_GAP):
    """Build the base finger skeleton for a RelativeMotion splint from the raw json.

    Returns four parallel lists (one entry per included finger, in anatomical
    if->mf->rf->sf order):
      - mcp_points:   Point3d at each MCP joint center
      - p1_lines:     Line for each P1 phalanx (MCP center to PIP center)
      - p1_circles:   Circle of the P1 mid cross-section, centered at the P1 midpoint,
                      built in the World YZ plane (normal +X)
      - p1_cylinders: open (uncapped) Brep cylinder per finger, using p1_circle
                      as the profile and spanning the p1_line from MCP to PIP

    Coordinate frame: +X distal, +Z dorsal / -Z volar, Y lateral. Each phalanx is purely
    along +X in this function. Not yet handled: relative_elevation_angle, is_slitted.
    """
    included = [f for f in raw_data["finger_data"] if f.get("is_included")]
    if not included:
        log("setup_finger_positions: no included fingers in raw_data")
        return [], [], []

    # Step 1: lateral packing. multiple_circle_positioning converts each p1_mid_circ to a
    # radius internally and returns the per-finger radii plus baseline tangent offsets
    # (which, for circles resting on the baseline, are the centers' lateral positions).
    circumferences = [f["p1_mid_circ"] for f in included]
    _, _, _, radii, _, tangent_offsets = multiple_circle_positioning(
        circumferences, raw_data["all_splint_finger_circ"], min_center_gap)

    # Right hand: "if" at +Y, fingers march toward -Y. Left hand mirrors the sign.
    y_sign = -1.0 if raw_data["is_right_hand"] else 1.0

    # Cumulative distal offset: PIP X starts at 0 for the first included finger, then each
    # finger shifts by its pip_neighbor_fwd_offset relative to its preceding neighbor.
    pip_x = 0.0

    mcp_points = []
    p1_lines = []
    p1_circles = []
    p1_cylinders = []

    for i, finger in enumerate(included):
        if i > 0:
            pip_x += finger["pip_neighbor_fwd_offset"]
        radius = radii[i]

        # Step 2: PIP center - Y from the baseline tangent offset, Z = radius (each circle
        # rests tangent on the Z=0 volar baseline), X from the cumulative offset above.
        pip_center = Point3d(pip_x, y_sign * tangent_offsets[i], radius)

        # Step 4: MCP center is the PIP center projected -X by p1_length.
        mcp_center = Point3d(pip_x - finger["p1_length"], pip_center.Y, pip_center.Z)

        # P1 mid cross-section circle, centered at the phalanx midpoint in World YZ (normal +X).
        mid_point = Point3d((mcp_center.X + pip_center.X) / 2.0, pip_center.Y, pip_center.Z)
        circle_plane = Plane(mid_point, Vector3d.XAxis)
        p1_circle = Circle(circle_plane, radius)

        # Open (uncapped) cylinder: the p1_circle profile extruded along the p1_line.
        # Left uncapped so later plane intersections reveal incomplete cuts; solids for
        # boolean subtraction are built in a later phase.
        p1_line = Line(mcp_center, pip_center)
        axis = pip_center - mcp_center
        axis.Unitize()
        base_circle = Circle(Plane(mcp_center, axis), radius)
        p1_cylinder = Cylinder(base_circle, p1_line.Length).ToBrep(False, False)

        mcp_points.append(mcp_center)
        p1_lines.append(p1_line)
        p1_circles.append(p1_circle)
        p1_cylinders.append(p1_cylinder)

    log("setup_finger_positions: built {0} finger(s)".format(len(included)))
    return mcp_points, p1_lines, p1_circles, p1_cylinders


def elevate_supported_fingers(raw_data, mcp_points, p1_lines, p1_circles, p1_cylinders):
    """Rotate each supported finger up to relative_elevation_angle about its MCP.

    Pure transform stage over setup_finger_positions' outputs. Supported (included,
    non-anchor) fingers rotate in the XZ plane about a Y-parallel axis through their own
    (fixed) MCP center; anchors are left unchanged. Positive angle tilts the finger up
    (PIP toward +Z). raw_data is the authority for the angle and the anchor/support flags.

    Returns five index-aligned lists (one entry per included finger), all equal length:
      - mcp_points   (Point3d, unchanged - the pivot stays fixed)
      - p1_lines     (Line, rotated)
      - p1_circles   (Circle, rotated)
      - p1_cylinders (Brep, rotated)
      - transforms   (Transform applied to each finger; identity for anchors)

    Copies are returned so the caller's original geometry is not mutated.
    """
    included = [f for f in raw_data["finger_data"] if f.get("is_included")]

    angle_deg = raw_data["relative_elevation_angle"]
    clamped = max(MIN_ELEVATION_ANGLE, min(MAX_ELEVATION_ANGLE, angle_deg))
    if clamped != angle_deg:
        log("elevate_supported_fingers: clamped angle {0} to {1}".format(angle_deg, clamped))
    angle_rad = math.radians(clamped)

    # A +X phalanx swings toward +Z under a rotation about -Y, so a positive input angle
    # reads as "up" (dorsal). This holds for both hands (only Y is mirrored upstream).
    up_axis = Vector3d(0.0, -1.0, 0.0)

    out_mcp = []
    out_lines = []
    out_circles = []
    out_cylinders = []
    transforms = []

    for i, finger in enumerate(included):
        pivot = mcp_points[i]
        if finger.get("is_anchor_finger"):
            xform = Transform.Identity
        else:
            xform = Transform.Rotation(angle_rad, up_axis, pivot)

        # Copy each geometry before transforming so the inputs stay untouched.
        mcp = Point3d(mcp_points[i])
        line = Line(p1_lines[i].From, p1_lines[i].To)
        circle = Circle(p1_circles[i].Plane, p1_circles[i].Radius)
        cylinder = p1_cylinders[i].DuplicateBrep()

        mcp.Transform(xform)
        line.Transform(xform)
        circle.Transform(xform)
        cylinder.Transform(xform)

        out_mcp.append(mcp)
        out_lines.append(line)
        out_circles.append(circle)
        out_cylinders.append(cylinder)
        transforms.append(xform)

    supported_count = sum(1 for f in included if not f.get("is_anchor_finger"))
    log("elevate_supported_fingers: rotated {0} supported finger(s) at {1} deg".format(
        supported_count, clamped))
    return out_mcp, out_lines, out_circles, out_cylinders, transforms


def _lowest_circle_point(circle):
    """Return the Point3d on a Circle with the smallest world Z.

    Projects world -Z into the circle's plane to get the in-plane direction of steepest
    descent, then steps one radius that way from the center. Falls back to the center for
    a (near-)horizontal circle where every point shares the same Z.
    """
    center = circle.Center
    normal = circle.Plane.Normal
    world_down = Vector3d(0.0, 0.0, -1.0)
    # Remove the out-of-plane component so the direction lies in the circle's plane.
    in_plane = world_down - Vector3d.Multiply(world_down, normal) * normal
    if in_plane.Length < 1e-9:
        return Point3d(center)
    in_plane.Unitize()
    return center + in_plane * circle.Radius


def build_profile_plane(raw_data, p1_circles):
    """Build the plane that the extruded splint profile outline will live in.

    The splint body is carried by the anchor fingers, so the profile plane is defined by
    where those anchors contact the volar baseline. p1_circles is the full per-included-finger
    list from the previous phase (index-aligned to the included fingers); anchors are not
    rotated, so their circles are the same before and after elevation.

    Construction:
      1. For each anchor finger, take the lowest point of its p1_circle.
      2. Project that point onto the world XY plane (drop Z). Kept for future-proofing even
         though anchor circles currently rest on the Z=0 baseline (projection is a no-op today).
      3. Best-fit a line through the projected points (exact for two anchors, least-squares
         for three or more) via Line.TryFitLineToPoints.
      4. Return the vertical plane that contains that line and is perpendicular to world XY.

    Returns a single RhinoCommon Plane, or None if there are fewer than two anchor fingers.
    """
    included = [f for f in raw_data["finger_data"] if f.get("is_included")]

    anchor_points = []
    for i, finger in enumerate(included):
        if not finger.get("is_anchor_finger"):
            continue
        lowest = _lowest_circle_point(p1_circles[i])
        # Project onto world XY (future-proofing; anchors sit on Z=0 today).
        anchor_points.append(Point3d(lowest.X, lowest.Y, 0.0))

    if len(anchor_points) < 2:
        log("build_profile_plane: need at least 2 anchor fingers, got {0}".format(
            len(anchor_points)))
        return None

    ok, fit_line = Line.TryFitLineToPoints(anchor_points)
    if not ok:
        log("build_profile_plane: line fit failed for {0} anchor point(s)".format(
            len(anchor_points)))
        return None

    # Vertical plane: X axis along the (horizontal) fit line, Y axis straight up world Z.
    direction = fit_line.Direction
    direction.Unitize()
    profile_plane = Plane(fit_line.From, direction, Vector3d.ZAxis)

    log("build_profile_plane: fit plane through {0} anchor point(s)".format(len(anchor_points)))
    return profile_plane


def _plane_horizontal_axis(profile_plane):
    """Return the in-plane horizontal unit vector (perpendicular to world Z, lying in the
    vertical profile plane), forced to point toward world +Y. Used as the 'u' axis for
    angular-sweep measurements; the 'w' axis is simply world +Z."""
    h = Vector3d(profile_plane.XAxis)  # plane XAxis is the horizontal fit-line direction
    h.Z = 0.0
    if h.Length < 1e-9:
        h = Vector3d(0.0, 1.0, 0.0)
    h.Unitize()
    if h.Y < 0.0:
        h.Reverse()
    return h


def _line_plane_point(line, plane):
    """Point where a Line crosses a Plane; falls back to the line midpoint if parallel."""
    ok, t = Intersection.LinePlane(line, plane)
    if not ok:
        return line.PointAt(0.5)
    return line.PointAt(t)


def _nearest_section_curve(curves, center):
    """From the plane/Brep intersection curves, pick the one closest to the section center
    (guards against stray fragments); returns the single cross-section curve."""
    best = None
    best_dist = None
    for c in curves:
        ok, t = c.ClosestPoint(center)
        if not ok:
            continue
        dist = c.PointAt(t).DistanceTo(center)
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best = c
    return best


def _curve_param_at_angle(section, center, h, angle):
    """Curve parameter where a ray from center (at 'angle' in the h / world-Z plane basis)
    hits the section curve, or None if the ray misses (partial/open intersection)."""
    direction = h * math.cos(angle) + Vector3d.ZAxis * math.sin(angle)
    ray = LineCurve(center, center + direction * 1.0e4)
    events = Intersection.CurveCurve(section, ray, _INTERSECT_TOL, _INTERSECT_TOL)
    if events is None or events.Count == 0:
        return None
    return events[0].ParameterA


def _param_between(a, b, m):
    """True if param m lies on the increasing-direction sweep from a to b (with wrap)."""
    if a <= b:
        return a <= m <= b
    return m >= a or m <= b


def _orient_start_plus_y(curve, center, h):
    """Reverse the curve if needed so it starts on the +Y side and ends on the -Y side
    (the +Y end is the one with the larger projection onto h)."""
    start_u = Vector3d.Multiply(curve.PointAtStart - center, h)
    end_u = Vector3d.Multiply(curve.PointAtEnd - center, h)
    if start_u < end_u:
        curve.Reverse()
    return curve


def extract_finger_cross_sections(raw_data, profile_plane, p1_cylinders_oriented,
                                  p1_lines_oriented, support_arc_deg=DEFAULT_SUPPORT_ARC_DEG):
    """Intersect the profile plane with each oriented finger cylinder and keep the full
    section for anchors or a support arc for supported fingers.

    Cylinders are uncapped, so a full crossing yields a closed ellipse and a partial crossing
    yields an open arc; partial arcs are acceptable for supported fingers as long as they
    still span support_arc_deg. No curve joining is performed.

    Inputs are index-aligned to the included fingers (anatomical order):
      - raw_data:               authority for is_anchor_finger and the sign of the elevation.
      - profile_plane:          from build_profile_plane.
      - p1_cylinders_oriented:  elevated cylinders from elevate_supported_fingers.
      - p1_lines_oriented:      elevated P1 lines; their crossing with the plane gives each
                                section center for the angular-sweep measurement.
      - support_arc_deg:        total angular width of the preserved arc (supported fingers).

    Returns two index-aligned lists (one entry per included finger; None where a section
    could not be built):
      - full_intersection_curves:      raw plane/cylinder section (closed ellipse or open arc)
      - preserved_intersection_curves: full ellipse for anchors, the support arc for supports
    """
    included = [f for f in raw_data["finger_data"] if f.get("is_included")]

    h = _plane_horizontal_axis(profile_plane)
    # Support sits underneath (world -Z) when the finger is elevated up, above (+Z) otherwise.
    # Measured in the (h, world-Z) basis: -Z is angle -pi/2, +Z is +pi/2.
    elevation = raw_data["relative_elevation_angle"]
    support_center_angle = -math.pi / 2.0 if elevation >= 0 else math.pi / 2.0
    half = math.radians(support_arc_deg) / 2.0

    full_curves = []
    preserved_curves = []

    for i, finger in enumerate(included):
        ok, curves, _pts = Intersection.BrepPlane(
            p1_cylinders_oriented[i], profile_plane, _INTERSECT_TOL)
        if not ok or curves is None or curves.Length == 0:
            log("extract_finger_cross_sections: no intersection for finger index {0}".format(i))
            full_curves.append(None)
            preserved_curves.append(None)
            continue

        center = _line_plane_point(p1_lines_oriented[i], profile_plane)
        section = _nearest_section_curve(curves, center)
        if section is None:
            log("extract_finger_cross_sections: no usable section curve for finger index "
                "{0}".format(i))
            full_curves.append(None)
            preserved_curves.append(None)
            continue
        full_curves.append(section)

        if finger.get("is_anchor_finger"):
            # Full ring: keep the whole section curve.
            preserved_curves.append(section.DuplicateCurve())
            continue

        # Supported finger: trim to the support arc centered on -Z (or +Z) about the center.
        t_mid = _curve_param_at_angle(section, center, h, support_center_angle)
        t_a = _curve_param_at_angle(section, center, h, support_center_angle + half)
        t_b = _curve_param_at_angle(section, center, h, support_center_angle - half)

        if t_mid is None or t_a is None or t_b is None:
            # Partial/open section that does not span the full arc window: keep what we have
            # and flag it (may be shorter than support_arc_deg).
            log("extract_finger_cross_sections: incomplete support arc for finger index "
                "{0}; keeping the available section".format(i))
            preserved_curves.append(_orient_start_plus_y(section.DuplicateCurve(), center, h))
            continue

        if section.IsClosed and not _param_between(t_a, t_b, t_mid):
            arc = section.Trim(t_b, t_a)
        else:
            lo, hi = (t_a, t_b) if t_a <= t_b else (t_b, t_a)
            arc = section.Trim(lo, hi)

        if arc is None:
            log("extract_finger_cross_sections: arc trim failed for finger index {0}".format(i))
            preserved_curves.append(None)
            continue

        preserved_curves.append(_orient_start_plus_y(arc, center, h))

    kept = sum(1 for c in preserved_curves if c is not None)
    log("extract_finger_cross_sections: built {0} section(s), {1} preserved".format(
        len(included), kept))
    return full_curves, preserved_curves


def _offset_curve_outward(curve, plane, distance, tolerance):
    """Offset a closed planar curve outward by 'distance' within 'plane'.

    Curve.Offset's direction depends on the curve's orientation, so we try both signs and
    keep the closed result that is longer than the input (i.e. the one that encloses it).
    Returns the offset Curve, or None if neither sign yields a valid outer curve.
    """
    input_len = curve.GetLength()
    best = None
    best_len = None
    for signed in (distance, -distance):
        pieces = curve.Offset(plane, signed, tolerance, CurveOffsetCornerStyle.Round)
        if pieces is None or len(pieces) == 0:
            continue
        candidate = pieces[0]
        if len(pieces) > 1:
            joined = Curve.JoinCurves(pieces, tolerance)
            if joined is None or len(joined) == 0:
                continue
            candidate = joined[0]
        if not candidate.IsClosed:
            continue
        clen = candidate.GetLength()
        # An outward offset lengthens the perimeter; skip the inward (shorter) result.
        if clen <= input_len:
            continue
        if best_len is None or clen > best_len:
            best = candidate
            best_len = clen
    return best


def _split_ring_hemispheres(ring, h):
    """Split a closed ring curve into (+Z hemisphere, -Z hemisphere) at its extreme points
    along the in-plane horizontal axis h (the +Y and -Y extremes).

    Each hemisphere is oriented +Y start to -Y end, matching the Phase 4 support arcs so the
    perimeter walk's bridges line up. Returns (pos_hemi, neg_hemi), or (None, None) if the
    split fails.
    """
    center = ring.GetBoundingBox(True).Center
    far = 1.0e6
    ok_p, t_p = ring.ClosestPoint(center + h * far)   # +Y extreme (nearest to a far +h point)
    ok_m, t_m = ring.ClosestPoint(center - h * far)   # -Y extreme
    if not ok_p or not ok_m:
        return None, None
    segments = ring.Split([t_p, t_m])
    if segments is None or len(segments) != 2:
        return None, None
    a, b = segments[0], segments[1]
    # The segment with the higher mid-point world Z is the dorsal (+Z) hemisphere.
    a_z = a.PointAtNormalizedLength(0.5).Z
    b_z = b.PointAtNormalizedLength(0.5).Z
    pos_hemi, neg_hemi = (a, b) if a_z >= b_z else (b, a)
    _orient_start_plus_y(pos_hemi, center, h)
    _orient_start_plus_y(neg_hemi, center, h)
    return pos_hemi, neg_hemi


def build_exterior_anchor_rings(raw_data, profile_plane, preserved_intersection_curves,
                                radial_band_thickness_mm=DEFAULT_RADIAL_BAND_THICKNESS_MM):
    """Build each anchor finger's exterior ring and split it into +Z/-Z hemispheres (Path A).

    Each anchor's preserved (closed) Phase 4 section is the ring's inner boundary; we offset
    it outward within the profile plane by radial_band_thickness_mm to form the outer
    boundary, then split the ring at its +Y/-Y extremes into a dorsal (+Z) and a volar (-Z)
    hemisphere so the perimeter walk can bridge to it from either side. Supported fingers,
    and any anchor whose ring cannot be built, come back None.

    Inputs are index-aligned to the included fingers (anatomical order):
      - raw_data:                      authority for is_anchor_finger.
      - profile_plane:                 from build_profile_plane (the ring's work plane).
      - preserved_intersection_curves: Phase 4 sections; anchors are closed curves.
      - radial_band_thickness_mm:      radial wall thickness of the ring.

    Returns three index-aligned lists (None where supported or the build fails):
      - exterior_anchor_rings          (closed Curve, the ring's outer boundary)
      - exterior_ring_pos_hemispheres  (+Z / dorsal half, oriented +Y start to -Y end)
      - exterior_ring_neg_hemispheres  (-Z / volar half, oriented +Y start to -Y end)
    """
    included = [f for f in raw_data["finger_data"] if f.get("is_included")]
    h = _plane_horizontal_axis(profile_plane)

    rings = []
    pos_hemis = []
    neg_hemis = []

    for i, finger in enumerate(included):
        section = preserved_intersection_curves[i]
        if not finger.get("is_anchor_finger") or section is None:
            rings.append(None)
            pos_hemis.append(None)
            neg_hemis.append(None)
            continue

        ring = _offset_curve_outward(section, profile_plane, radial_band_thickness_mm,
                                     _INTERSECT_TOL)
        if ring is None:
            log("build_exterior_anchor_rings: outward offset failed for finger index "
                "{0}".format(i))
            rings.append(None)
            pos_hemis.append(None)
            neg_hemis.append(None)
            continue

        pos_hemi, neg_hemi = _split_ring_hemispheres(ring, h)
        if pos_hemi is None or neg_hemi is None:
            log("build_exterior_anchor_rings: hemisphere split failed for finger index "
                "{0}; keeping the ring only".format(i))
        rings.append(ring)
        pos_hemis.append(pos_hemi)
        neg_hemis.append(neg_hemi)

    built = sum(1 for r in rings if r is not None)
    log("build_exterior_anchor_rings: built {0} anchor ring(s)".format(built))
    return rings, pos_hemis, neg_hemis


def plan_perimeter_walk(raw_data, exterior_ring_pos_hemispheres,
                        exterior_ring_neg_hemispheres, preserved_intersection_curves):
    """Lay out the ordered profile-perimeter walk (Phase 5, Pass 1 - no bridges yet).

    Walks the support side over the included fingers in if->sf order, then the return side
    back over the anchors only (support runs are leapt over on the return). Each entry is a
    slot dict {kind, finger_index, curve} where kind is one of 'anchor_support_side',
    'support_arc', or 'anchor_return_side' and finger_index is the included-list index. The
    curves can be previewed in walk order to sanity-check the layout before bridges are added.

    Support side uses the +Z hemispheres and the return side the -Z hemispheres when
    relative_elevation_angle >= 0; the two swap for a negative angle (the raised finger, and
    therefore the support arcs, sit on the opposite side of the perimeter).
    """
    included = [f for f in raw_data["finger_data"] if f.get("is_included")]
    angle = raw_data["relative_elevation_angle"]
    if angle >= 0:
        support_hemis = exterior_ring_pos_hemispheres
        return_hemis = exterior_ring_neg_hemispheres
    else:
        support_hemis = exterior_ring_neg_hemispheres
        return_hemis = exterior_ring_pos_hemispheres

    segments = []

    # Support side: forward over every included finger.
    for i, finger in enumerate(included):
        if finger.get("is_anchor_finger"):
            segments.append({"kind": "anchor_support_side", "finger_index": i,
                             "curve": support_hemis[i]})
        else:
            segments.append({"kind": "support_arc", "finger_index": i,
                             "curve": preserved_intersection_curves[i]})

    # Return side: backward, landing on anchors only (support runs are leapt over).
    for i in range(len(included) - 1, -1, -1):
        if included[i].get("is_anchor_finger"):
            segments.append({"kind": "anchor_return_side", "finger_index": i,
                             "curve": return_hemis[i]})

    log("plan_perimeter_walk: laid out {0} segment(s)".format(len(segments)))
    return segments


def _facing_endpoints(curve_a, curve_b):
    """Return (param_a, param_b) of the closest pair of endpoints between two curves - i.e.
    the ends that face each other across a gap."""
    ends_a = [(curve_a.Domain.T0, curve_a.PointAtStart),
              (curve_a.Domain.T1, curve_a.PointAtEnd)]
    ends_b = [(curve_b.Domain.T0, curve_b.PointAtStart),
              (curve_b.Domain.T1, curve_b.PointAtEnd)]
    best_d = None
    best_ta = curve_a.Domain.T0
    best_tb = curve_b.Domain.T0
    for ta, pa in ends_a:
        for tb, pb in ends_b:
            d = pa.DistanceTo(pb)
            if best_d is None or d < best_d:
                best_d = d
                best_ta = ta
                best_tb = tb
    return best_ta, best_tb


def _blend_reverse(curve, t):
    """Reverse flag for Curve.CreateBlendCurve: the blend must head into the gap. The natural
    (non-reversed) tangent follows increasing parameter, which points off the curve at its end
    (T1) but into the curve body at its start (T0) - so reverse only at the start endpoint."""
    return abs(t - curve.Domain.T0) < abs(t - curve.Domain.T1)


def _extreme_point_param(curve, direction):
    """Curve param maximizing dot(point, direction), by dense sampling. Robust for the convex
    ring / arc curves used here (avoids relying on analytic extrema of skewed ellipses)."""
    dom = curve.Domain
    steps = 240
    span = dom.T1 - dom.T0
    best_t = dom.T0
    best_v = None
    for i in range(steps + 1):
        t = dom.T0 + span * (float(i) / steps)
        p = curve.PointAt(t)
        v = p.X * direction.X + p.Y * direction.Y + p.Z * direction.Z
        if best_v is None or v > best_v:
            best_v = v
            best_t = t
    return best_t


def _common_tangent(curve_a, curve_b, plane, outward):
    """Common tangent line to two convex coplanar curves on the 'outward' side, found by an
    iterative supporting-line fixpoint (works on the true ellipse/arc curves, so it tolerates
    a skewed profile_plane). Returns (ta, tb, pa, pb) or None."""
    n = Vector3d(outward)
    if n.Length < 1e-9:
        return None
    n.Unitize()
    ta = curve_a.Domain.T0
    tb = curve_b.Domain.T0
    for _ in range(60):
        ta = _extreme_point_param(curve_a, n)
        tb = _extreme_point_param(curve_b, n)
        pa = curve_a.PointAt(ta)
        pb = curve_b.PointAt(tb)
        d = pb - pa
        if d.Length < 1e-9:
            return None
        d.Unitize()
        n_new = Vector3d.CrossProduct(plane.Normal, d)
        if n_new.Length < 1e-9:
            break
        n_new.Unitize()
        if Vector3d.Multiply(n_new, outward) < 0.0:
            n_new.Reverse()
        if (n_new - n).Length < 1e-7:
            n = n_new
            break
        n = n_new
    return ta, tb, curve_a.PointAt(ta), curve_b.PointAt(tb)


def _trim_keep_far(curve, t, ref_point):
    """Split curve at param t and keep the piece whose midpoint is farther from ref_point
    (drops the stub nearest the leapt-over neighbor)."""
    pieces = curve.Split([t])
    if pieces is None or len(pieces) == 0:
        return curve
    if len(pieces) == 1:
        return pieces[0]
    best = None
    best_d = None
    for p in pieces:
        d = p.PointAtNormalizedLength(0.5).DistanceTo(ref_point)
        if best_d is None or d > best_d:
            best_d = d
            best = p
    return best


def create_tangent_blend_bridge(curve_a, curve_b):
    """Smooth G1 (tangent) blend joining the facing near ends of two profile segments.

    Used for support-to-support and anchor-to-anchor gaps on both the support and return
    sides. Each side is built independently (no shared-hourglass caching - a tangent blend is
    cheap, so the two halves are computed separately for simplicity). Returns the bridge
    Curve, or None if the blend fails.
    """
    ta, tb = _facing_endpoints(curve_a, curve_b)
    return Curve.CreateBlendCurve(curve_a, ta, _blend_reverse(curve_a, ta),
                                  BlendContinuity.Tangency,
                                  curve_b, tb, _blend_reverse(curve_b, tb),
                                  BlendContinuity.Tangency)


def create_supportpath_bridge_anchor_to_support(anchor_curve, support_curve, support_center):
    """Bridge from an anchor ring hemisphere to a supported finger's support arc.

    Deliberately simple: extend a straight line off the support arc's near end, tangent to the
    arc there, until it strikes the anchor hemisphere. The bridge is G1-continuous with the
    support arc and meets the anchor with a small (acceptable) angular discontinuity. Only the
    anchor is trimmed - back to the strike point, dropping the stub that faced the support; the
    support arc is left whole.

    Returns (bridge_line, anchor_curve_revised, support_curve_revised); on failure the bridge
    is None and the curves are returned untrimmed.
    """
    # Near end of the support arc (the endpoint closest to the anchor) and its outward tangent.
    ta, _tb = _facing_endpoints(support_curve, anchor_curve)
    p_near = support_curve.PointAt(ta)
    tan = support_curve.TangentAt(ta)
    if _blend_reverse(support_curve, ta):
        tan.Reverse()  # TangentAt follows increasing param; at the start end that aims inward
    tan.Unitize()

    # Cast a generous ray from the near end toward the anchor and take the closest hit.
    reach = p_near.DistanceTo(anchor_curve.GetBoundingBox(True).Center) * 2.0
    probe = LineCurve(p_near, p_near + tan * reach)
    events = Intersection.CurveCurve(probe, anchor_curve, _INTERSECT_TOL, _INTERSECT_TOL)
    hit = None
    th_anchor = None
    if events is not None and events.Count > 0:
        best_d = None
        for ev in events:
            d = ev.PointA.DistanceTo(p_near)
            if best_d is None or d < best_d:
                best_d = d
                hit = ev.PointA
                th_anchor = ev.ParameterB
    else:
        # The tangent extension can graze past the anchor (e.g. a shifted profile_plane skews
        # the arc's end tangent away from the ring). Fall back to a straight chord to the
        # anchor's nearest point - a slightly larger discontinuity, but always connects.
        ok, t_close = anchor_curve.ClosestPoint(p_near)
        if not ok:
            log("create_supportpath_bridge_anchor_to_support: tangent extension missed and "
                "closest-point fallback failed; leaving gap")
            return None, anchor_curve, support_curve
        th_anchor = t_close
        hit = anchor_curve.PointAt(t_close)
        log("create_supportpath_bridge_anchor_to_support: tangent extension missed the "
            "anchor; using closest-point chord fallback (gap {0:.2f} mm)".format(
                p_near.DistanceTo(hit)))

    bridge = LineCurve(p_near, hit)
    rev_anchor = _trim_keep_far(anchor_curve, th_anchor, support_center)
    return bridge, rev_anchor, support_curve


def create_return_leap_bridge(hemi_a, ring_a, hemi_b, ring_b, profile_plane, elevation_ge0):
    """Return-side leap: a straight common-tangent line across the return-side of the two
    anchor rings that bracket a support run, trimming each anchor's return hemisphere at its
    tangent point.

    Tangency is computed against the true ring curves (not best-fit circles) so a skewed
    profile_plane is handled. Returns (bridge_line, hemi_a_revised, hemi_b_revised); the
    revised hemispheres drop the stub facing the leapt-over supports.
    """
    # Return side is the volar (-Z) side when the finger is raised, dorsal (+Z) otherwise.
    outward = Vector3d(0.0, 0.0, -1.0) if elevation_ge0 else Vector3d(0.0, 0.0, 1.0)
    result = _common_tangent(ring_a, ring_b, profile_plane, outward)
    if result is None:
        log("create_return_leap_bridge: common tangent not found; leaving gap")
        return None, hemi_a, hemi_b
    _ta, _tb, pa, pb = result
    bridge = LineCurve(pa, pb)

    center_a = ring_a.GetBoundingBox(True).Center
    center_b = ring_b.GetBoundingBox(True).Center
    ok_a, th_a = hemi_a.ClosestPoint(pa)
    ok_b, th_b = hemi_b.ClosestPoint(pb)
    rev_a = _trim_keep_far(hemi_a, th_a, center_b) if ok_a else hemi_a
    rev_b = _trim_keep_far(hemi_b, th_b, center_a) if ok_b else hemi_b
    return bridge, rev_a, rev_b


def _support_between(included, i, j):
    """True if any included finger strictly between indices i and j is a supported finger."""
    lo, hi = (i, j) if i < j else (j, i)
    return any(not included[k].get("is_anchor_finger") for k in range(lo + 1, hi))


def weld_perimeter_walk(raw_data, walk_segments, profile_plane, exterior_anchor_rings,
                        web_fillet_r_mm=DEFAULT_WEB_FILLET_R_MM,
                        min_web_width_mm=DEFAULT_MIN_WEB_WIDTH_MM):
    """Bridge the ordered walk slots and join them into one closed profile perimeter (Pass 2).

    For each adjacent slot pair (including the loop-closing pair) the matching bridge is built
    and any trimmed hemispheres are written back, then every piece is stitched with
    Curve.JoinCurves. Same-anchor hemisphere pairs (the two turn-arounds) join directly with
    no bridge. End-support caps (a supported finger at either end of the walk) are not yet
    implemented and are logged as an unhandled pair.

    Inputs:
      - walk_segments:         ordered slots from plan_perimeter_walk.
      - profile_plane:         the shared work plane (from build_profile_plane).
      - exterior_anchor_rings: full rings from build_exterior_anchor_rings (for the leap's
                               true-curve tangency), index-aligned to the included fingers.
      - web_fillet_r_mm:       reserved for the deferred stress-relief fillet at anchor joints.
      - min_web_width_mm:      minimum neck width (safeguard; enforcement deferred).

    Returns (closed_profile_curve, bridge_curves) - the second list is for observability /
    previewing the individual bridges. Raises ValueError if the pieces do not join into a
    closed perimeter (the failing bridges are named in the message).
    """
    included = [f for f in raw_data["finger_data"] if f.get("is_included")]
    elevation_ge0 = raw_data["relative_elevation_angle"] >= 0
    count = len(walk_segments)
    work = [s["curve"] for s in walk_segments]
    bridges = []
    failures = []

    for k in range(count):
        a = walk_segments[k]
        b = walk_segments[(k + 1) % count]
        j = (k + 1) % count
        ka, kb = a["kind"], b["kind"]
        fa, fb = a["finger_index"], b["finger_index"]
        ca, cb = work[k], work[j]
        if ca is None or cb is None:
            continue

        # Turn-around / loop closure: same anchor's two hemispheres meet at the far extreme.
        if fa == fb and set([ka, kb]) <= set(["anchor_support_side", "anchor_return_side"]):
            continue

        bridge = None
        if ka == "support_arc" and kb == "support_arc":
            bridge = create_tangent_blend_bridge(ca, cb)
        elif set([ka, kb]) == set(["anchor_support_side", "support_arc"]):
            if ka == "anchor_support_side":
                ai, si = k, j
            else:
                ai, si = j, k
            support_center = work[si].GetBoundingBox(True).Center
            bridge, rev_anchor, rev_support = create_supportpath_bridge_anchor_to_support(
                work[ai], work[si], support_center)
            work[ai] = rev_anchor
            work[si] = rev_support
        elif ka == "anchor_support_side" and kb == "anchor_support_side":
            bridge = create_tangent_blend_bridge(ca, cb)
        elif ka == "anchor_return_side" and kb == "anchor_return_side":
            if _support_between(included, fa, fb):
                bridge, rev_a, rev_b = create_return_leap_bridge(
                    ca, exterior_anchor_rings[fa], cb, exterior_anchor_rings[fb],
                    profile_plane, elevation_ge0)
                work[k] = rev_a
                work[j] = rev_b
            else:
                bridge = create_tangent_blend_bridge(ca, cb)
        else:
            log("weld_perimeter_walk: unhandled pair {0}->{1} (finger {2}->{3}); likely an "
                "end-support cap (not yet implemented)".format(ka, kb, fa, fb))
            failures.append("{0}->{1} (finger {2}->{3}): unhandled pair".format(ka, kb, fa, fb))
            continue

        if bridge is None:
            log("weld_perimeter_walk: bridge failed for {0}->{1} (finger {2}->{3})".format(
                ka, kb, fa, fb))
            failures.append("{0}->{1} (finger {2}->{3}): bridge returned None".format(
                ka, kb, fa, fb))
            continue
        bridges.append(bridge)

    pieces = [c for c in work if c is not None] + bridges
    joined = Curve.JoinCurves(pieces, _JOIN_TOL)
    closed = None
    if joined is not None and len(joined) > 0:
        for jc in joined:
            if jc.IsClosed:
                closed = jc
                break

    joined_count = 0 if joined is None else len(joined)
    piece_count = len([c for c in work if c is not None])
    log("weld_perimeter_walk: {0} segment piece(s), {1} bridge(s), joined into {2} curve(s)"
        "{3}".format(piece_count, len(bridges), joined_count,
                     "" if closed is not None else " (not closed)"))

    if closed is None:
        detail = "; ".join(failures) if failures else "no failed bridges reported"
        raise ValueError(
            "weld_perimeter_walk: failed to build a closed profile perimeter "
            "({0} segment piece(s) + {1} bridge(s) joined into {2} curve(s), none closed). "
            "Failures: {3}".format(piece_count, len(bridges), joined_count, detail))
    return closed, bridges
