"""
RelativeMotion.py
Central point of contact for the RelativeMotion finger splint Design Definition.

Keeps the geometry logic here in Python (observable/testable in Rhino) so the bound
RelativeMotion.gh stays as thin as possible. Functions return RhinoCommon geometry types.

See dev-notes/260702_Dev_Process_RelativeMotion_splint.md for the construction rationale.
"""

import math
from importlib import reload
from Rhino.Geometry import Point3d, Vector3d, Line, Plane, Circle, Cylinder, Transform, LineCurve
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

# Tolerance (document units, mm) for plane/curve intersection and trim operations.
_INTERSECT_TOL = 1e-6


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
