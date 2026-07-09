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
                            LineCurve, Curve, CurveOffsetCornerStyle, BlendContinuity, Arc,
                            Brep, LoftType, BrepSolidOrientation)
from Rhino.Geometry.Intersect import Intersection
from splintcommon import log

import TwoDCirclePositioning
reload(TwoDCirclePositioning)
from TwoDCirclePositioning import multiple_circle_positioning

import BrepDifference
reload(BrepDifference)
from BrepDifference import robust_brep_difference


# Elevation angle clamp (degrees). Provisional range pending review with hand therapist Liz.
MIN_ELEVATION_ANGLE = -120.0
MAX_ELEVATION_ANGLE = 45.0

# Total angular width (deg) of a supported finger's preserved support arc. Provisional.
DEFAULT_SUPPORT_ARC_DEG = 120.0

# Tolerance (document units, mm) for plane/curve intersection and trim operations.
_INTERSECT_TOL = 1e-6

# Looser tolerance (mm) for stitching the finished perimeter pieces with Curve.JoinCurves.
_JOIN_TOL = 1e-2

# Tolerance (mm) for capping the planar loft ends (Phase 6) into a closed solid.
_CAP_TOL = 1e-2

# Sentinel a bridge builder returns when the two segments meet directly (their trimmed ends are
# coincident), so no bridge curve is needed. Distinct from None, which signals a real failure.
_DIRECT_JOIN = object()


def setup_finger_positions(raw_data, min_center_gap):
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
        return [], [], [], []

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


# NOTE (future): if fitted splints don't match the expected hand fit, revisit this profile-plane
# calculation first. A single flat plane across non-collinear MCP joints is inherently a
# compromise; today it is an equal-weighted least-squares line through every posed (elevated) P1
# midpoint. Options to revisit: weight anchors more heavily, or set the plane orientation from the
# anchors alone while still positioning it through all fingers.
def build_profile_plane(raw_data, p1_lines_oriented):
    """Build the plane that the extruded splint profile outline will live in.

    A flat splint spans fingers whose MCP joints are not collinear, so the plane is a
    least-squares compromise across every present finger in its posed (elevated) state - not just
    the anchors. p1_lines_oriented is the elevated per-included-finger P1 line list (index-aligned
    to the included fingers), so the plane adapts to the prescribed posture.

    Construction:
      1. For each included finger, take the midpoint of its elevated P1 line.
      2. Project that midpoint onto world XY (drop Z); the plane is vertical, so only the XY
         footprint sets it.
      3. Best-fit a line through the projected midpoints (exact for two fingers, least-squares
         for three or more) via Line.TryFitLineToPoints.
      4. Return the vertical plane through the points' centroid, X axis along the (sign-stable)
         fit line, Y axis straight up world Z.

    Returns a single RhinoCommon Plane, or None if there are fewer than two included fingers.
    """
    included = [f for f in raw_data["finger_data"] if f.get("is_included")]

    midpoints = []
    for i in range(len(included)):
        mid = p1_lines_oriented[i].PointAt(0.5)
        # Project onto world XY; the vertical plane is defined purely by this footprint.
        midpoints.append(Point3d(mid.X, mid.Y, 0.0))

    if len(midpoints) < 2:
        log("build_profile_plane: need at least 2 included fingers, got {0}".format(
            len(midpoints)))
        return None

    ok, fit_line = Line.TryFitLineToPoints(midpoints)
    if not ok:
        log("build_profile_plane: line fit failed for {0} midpoint(s)".format(len(midpoints)))
        return None

    # Sign-stabilize the in-plane X direction (toward +Y) so the previewed plane frame is
    # deterministic across permutations; the infinite plane is unaffected either way.
    direction = fit_line.Direction
    direction.Unitize()
    if direction.Y < 0.0:
        direction.Reverse()

    # Centroid origin (not the arbitrary fit-line endpoint) keeps the frame centered and stable.
    cx = sum(p.X for p in midpoints) / len(midpoints)
    cy = sum(p.Y for p in midpoints) / len(midpoints)
    origin = Point3d(cx, cy, 0.0)

    profile_plane = Plane(origin, direction, Vector3d.ZAxis)

    log("build_profile_plane: fit plane through {0} finger midpoint(s)".format(len(midpoints)))
    return profile_plane


def build_profile_planes(raw_data, p1_lines_oriented, longitudinal_band_thickness_mm):
    """Build the two parallel profile planes: the splint band's proximal and distal faces.

    build_profile_plane defines the centre plane; the printed band has real thickness along the
    hand's proximal-distal axis (World +X here), so its two outline profiles live on planes offset
    from that centre by +/- longitudinal_band_thickness_mm / 2 along World X. Generating a profile
    on each face and lofting between them gives the band its front and back surfaces.

    Returns (proximal_plane, distal_plane) - proximal is the -X (toward the hand) face, distal the
    +X (toward the fingertip) face - or (None, None) if the centre plane could not be built.
    """
    center_plane = build_profile_plane(raw_data, p1_lines_oriented)
    if center_plane is None:
        return None, None

    # Offset along the hand's proximal-distal axis (World +X), NOT the plane normal: the band's
    # thickness is measured along the finger length, and the fit-line tilt would otherwise skew
    # the two faces off that axis.
    half = longitudinal_band_thickness_mm / 2.0
    proximal = Plane(center_plane)
    proximal.Translate(Vector3d.XAxis * (-half))
    distal = Plane(center_plane)
    distal.Translate(Vector3d.XAxis * half)

    log("build_profile_planes: split centre plane into proximal/distal faces {0:.2f} mm "
        "apart along World X".format(longitudinal_band_thickness_mm))
    return proximal, distal


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
                                  p1_lines_oriented, support_prong_arc_deg,
                                  support_arc_deg=DEFAULT_SUPPORT_ARC_DEG):
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
      - support_prong_arc_deg:  total angular width of the preserved arc for END-support fingers
                                (their cradle prongs; usually wider than mid-supports).
      - support_arc_deg:        total angular width of the preserved arc for MID-support fingers
                                (those flanked by an anchor on each side).

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

        # Supported finger: trim to the support arc centered on -Z (or +Z) about the center. End
        # supports use the wider prong arc so their end cradles get more finger contact.
        arc_deg = support_prong_arc_deg if _is_end_support(included, i) else support_arc_deg
        half = math.radians(arc_deg) / 2.0
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
                                radial_band_thickness_mm):
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


def _is_end_support(included, i):
    """True if included finger i is a supported finger at either end of the included run.

    An end support has an anchor on only one side, so its support arc cannot bridge to a
    second anchor - it needs a closed end cap instead (build_end_support_cradles).
    """
    if included[i].get("is_anchor_finger"):
        return False
    return i == 0 or i == len(included) - 1


def _offset_arc_outward(arc, plane, distance):
    """Offset an open arc by 'distance' to the side that lengthens it (outward, away from the
    finger centre). Returns the offset Curve, or None if neither side yields a longer arc."""
    input_len = arc.GetLength()
    best = None
    best_len = None
    for signed in (distance, -distance):
        pieces = arc.Offset(plane, signed, _INTERSECT_TOL, CurveOffsetCornerStyle.Round)
        if pieces is None or len(pieces) == 0:
            continue
        candidate = pieces[0]
        if len(pieces) > 1:
            joined = Curve.JoinCurves(pieces, _JOIN_TOL)
            if joined is None or len(joined) == 0:
                continue
            candidate = joined[0]
        clen = candidate.GetLength()
        # Outward (larger-radius) offset lengthens the arc; skip the inward (shorter) result.
        if clen <= input_len:
            continue
        if best_len is None or clen > best_len:
            best = candidate
            best_len = clen
    return best


def _build_cradle_curve(arc, anchor_center, plane, thickness):
    """Turn a support arc into a closed-end cradle: the arc, a parallel return edge offset
    outward by 'thickness', and a semicircle (radius thickness/2) capping their free ends.

    'anchor_center' is the adjacent anchor's centre, used to tell the near end (toward the
    anchor, left open for bridging) from the free end (capped). Returns one open U-shaped curve
    whose two endpoints are the support prong and return prong near the anchor, or None.
    """
    return_edge = _offset_arc_outward(arc, plane, thickness)
    if return_edge is None:
        return None

    # Free end = the arc endpoint farther from the adjacent anchor; the near end stays open.
    if arc.PointAtStart.DistanceTo(anchor_center) <= arc.PointAtEnd.DistanceTo(anchor_center):
        free_t = arc.Domain.T1
        near_t = arc.Domain.T0
    else:
        free_t = arc.Domain.T0
        near_t = arc.Domain.T1
    arc_free = arc.PointAt(free_t)
    arc_near = arc.PointAt(near_t)
    ok, rt_free = return_edge.ClosestPoint(arc_free)
    if not ok:
        return None
    ret_free = return_edge.PointAt(rt_free)

    # --- TEMP DIAGNOSTIC: dump the cradle-assembly geometry so +20 (works) vs -20 (fails) can be
    # compared step by step. Remove once the negative-elevation cradle is fixed.
    _as, _ae = arc.PointAtStart, arc.PointAtEnd
    _rs, _re = return_edge.PointAtStart, return_edge.PointAtEnd
    log("  cradle.dbg arc.start=({0:.2f},{1:.2f},{2:.2f}) arc.end=({3:.2f},{4:.2f},{5:.2f})".format(
        _as.X, _as.Y, _as.Z, _ae.X, _ae.Y, _ae.Z))
    log("  cradle.dbg anchor_center=({0:.2f},{1:.2f},{2:.2f}) d(start)={3:.2f} d(end)={4:.2f} "
        "-> near_end={5}".format(
            anchor_center.X, anchor_center.Y, anchor_center.Z,
            _as.DistanceTo(anchor_center), _ae.DistanceTo(anchor_center),
            "start" if near_t == arc.Domain.T0 else "end"))
    log("  cradle.dbg return_edge.start=({0:.2f},{1:.2f},{2:.2f}) "
        "return_edge.end=({3:.2f},{4:.2f},{5:.2f}) len={6:.2f}".format(
            _rs.X, _rs.Y, _rs.Z, _re.X, _re.Y, _re.Z, return_edge.GetLength()))
    log("  cradle.dbg arc_near=({0:.2f},{1:.2f},{2:.2f}) arc_free=({3:.2f},{4:.2f},{5:.2f}) "
        "ret_free=({6:.2f},{7:.2f},{8:.2f})".format(
            arc_near.X, arc_near.Y, arc_near.Z, arc_free.X, arc_free.Y, arc_free.Z,
            ret_free.X, ret_free.Y, ret_free.Z))
    # --- END TEMP DIAGNOSTIC

    # Semicircle cap through the two free ends, bulging off the arc (away from the anchor).
    cap_dir = arc.TangentAt(free_t)
    if _blend_reverse(arc, free_t):
        cap_dir.Reverse()
    cap_dir.Unitize()
    mid = Point3d((arc_free.X + ret_free.X) / 2.0, (arc_free.Y + ret_free.Y) / 2.0,
                  (arc_free.Z + ret_free.Z) / 2.0)
    far = Point3d(mid.X + cap_dir.X * (thickness / 2.0),
                  mid.Y + cap_dir.Y * (thickness / 2.0),
                  mid.Z + cap_dir.Z * (thickness / 2.0))
    cap = Arc(arc_free, far, ret_free).ToNurbsCurve()
    if cap is None:
        return None

    joined = Curve.JoinCurves([arc, cap, return_edge], _JOIN_TOL)
    if joined is None or len(joined) != 1:
        return None
    cradle = joined[0]
    # Orient so PointAtStart is the support prong (support arc's near end) and PointAtEnd is the
    # return prong. The two prongs are only 'thickness' apart, so the weld selects each prong by
    # this fixed orientation rather than nearest-endpoint guessing.
    if cradle.PointAtStart.DistanceTo(arc_near) > cradle.PointAtEnd.DistanceTo(arc_near):
        cradle.Reverse()
    # --- TEMP DIAGNOSTIC: final prong positions after orientation.
    _cs, _ce = cradle.PointAtStart, cradle.PointAtEnd
    log("  cradle.dbg FINAL support_prong=({0:.2f},{1:.2f},{2:.2f}) "
        "return_prong=({3:.2f},{4:.2f},{5:.2f})".format(
            _cs.X, _cs.Y, _cs.Z, _ce.X, _ce.Y, _ce.Z))
    # --- END TEMP DIAGNOSTIC
    return cradle


def build_end_support_cradles(raw_data, profile_plane, preserved_intersection_curves,
                              exterior_anchor_rings, single_sided_support_thickness_mm):
    """Build a closed-end cradle for each end-support finger (Path A companion for supports).

    A finger supported at the start or end of the included run has an anchor on only one side,
    so its support arc cannot bridge to a second anchor. Instead its free end is capped: the
    support arc is paired with a parallel return edge offset outward by
    single_sided_support_thickness_mm and joined at the free end by a semicircle (radius =
    thickness / 2). The single U-shaped curve becomes that finger's one walk visit; its two
    near ends (support prong, return prong) bridge to the adjacent anchor's support and return
    hemispheres - condensing arc + cap + return into a single visit.

    Returns an index-aligned list (None for anchors and mid-support fingers).
    """
    included = [f for f in raw_data["finger_data"] if f.get("is_included")]
    cradles = []
    for i in range(len(included)):
        arc = preserved_intersection_curves[i]
        if arc is None or not _is_end_support(included, i):
            cradles.append(None)
            continue
        neighbor = i + 1 if i == 0 else i - 1
        neighbor_ring = exterior_anchor_rings[neighbor]
        if neighbor_ring is None:
            log("build_end_support_cradles: finger {0} has no adjacent anchor ring; "
                "skipping cradle".format(i))
            cradles.append(None)
            continue
        anchor_center = neighbor_ring.GetBoundingBox(True).Center
        cradle = _build_cradle_curve(arc, anchor_center, profile_plane,
                                     single_sided_support_thickness_mm)
        if cradle is None:
            log("build_end_support_cradles: cradle build failed for finger {0}".format(i))
        cradles.append(cradle)

    built = sum(1 for c in cradles if c is not None)
    log("build_end_support_cradles: built {0} end-support cradle(s)".format(built))
    return cradles


def plan_perimeter_walk(raw_data, exterior_ring_pos_hemispheres,
                        exterior_ring_neg_hemispheres, preserved_intersection_curves,
                        end_support_cradles):
    """Lay out the ordered profile-perimeter walk (Phase 5, Pass 1 - no bridges yet).

    Walks the support side over the included fingers in if->sf order, then the return side
    back over the anchors only (support runs are leapt over on the return). Each entry is a
    slot dict {kind, finger_index, curve} where kind is one of 'anchor_support_side',
    'support_arc', 'end_support_cradle', or 'anchor_return_side' and finger_index is the
    included-list index. The curves can be previewed in walk order to sanity-check the layout
    before bridges are added.

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
        elif end_support_cradles[i] is not None:
            segments.append({"kind": "end_support_cradle", "finger_index": i,
                             "curve": end_support_cradles[i]})
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


def _trim_keep_near(curve, t, keep_point):
    """Split curve at param t and keep the piece whose nearest endpoint is keep_point (drops the
    stub that pokes into an overlapping neighbor, keeping the outer arc up to keep_point)."""
    pieces = curve.Split([t])
    if pieces is None or len(pieces) == 0:
        return curve
    if len(pieces) == 1:
        return pieces[0]
    best = None
    best_d = None
    for p in pieces:
        d = min(p.PointAtStart.DistanceTo(keep_point), p.PointAtEnd.DistanceTo(keep_point))
        if best_d is None or d < best_d:
            best_d = d
            best = p
    return best


def _split_fillet_pieces(pieces, curve_a, curve_b, ta, tb):
    """Sort Curve.CreateFilletCurves output (join=False, trim=True) into
    (arc, trimmed_a, trimmed_b).

    Each trimmed input still carries its original far (non-facing) endpoint, so it is matched
    by proximity to that endpoint; the leftover piece is the fillet arc. Returns
    (None, curve_a, curve_b) if the output is not the expected three pieces (fillet did not fit).
    """
    if pieces is None or len(pieces) != 3:
        return None, curve_a, curve_b
    # Materialize once: indexing a .NET Curve[] can hand back a fresh wrapper each access, so
    # 'is' identity is unreliable. Hold stable references and match by index instead.
    items = list(pieces)
    # Far (kept) endpoint of each input is the domain end opposite the facing param.
    far_a = curve_a.PointAt(curve_a.Domain.T1 if _blend_reverse(curve_a, ta)
                            else curve_a.Domain.T0)
    far_b = curve_b.PointAt(curve_b.Domain.T1 if _blend_reverse(curve_b, tb)
                            else curve_b.Domain.T0)

    def near_far(i, pt):
        return min(items[i].PointAtStart.DistanceTo(pt), items[i].PointAtEnd.DistanceTo(pt))

    ia = min(range(3), key=lambda i: near_far(i, far_a))
    ib = min([i for i in range(3) if i != ia], key=lambda i: near_far(i, far_b))
    ic = [i for i in range(3) if i != ia and i != ib][0]
    return items[ic], items[ia], items[ib]


def create_rounded_corner_bridge(curve_a, curve_b, radius_mm):
    """Constant-radius rounded corner joining the facing near ends of two profile segments,
    trimming both back to the tangency points.

    Generic (type-agnostic) helper: the caller picks radius_mm per joint type - a tight radius
    for structural anchor-to-anchor joints, a larger one for support-to-support joints the
    finger contacts. If the fillet will not fit at radius_mm it falls back to a plain G1
    tangent blend with no trimming.

    Returns (bridge_curve, curve_a_revised, curve_b_revised); on the blend fallback the two
    curves come back untrimmed, matching the write-back contract of the other bridge builders.
    """
    ta, tb = _facing_endpoints(curve_a, curve_b)
    pa = curve_a.PointAt(ta)
    pb = curve_b.PointAt(tb)

    if radius_mm > 0.0:
        pieces = Curve.CreateFilletCurves(curve_a, pa, curve_b, pb, radius_mm,
                                          False, True, False, _INTERSECT_TOL, _INTERSECT_TOL)
        arc, rev_a, rev_b = _split_fillet_pieces(pieces, curve_a, curve_b, ta, tb)
        if arc is not None:
            return arc, rev_a, rev_b
        log("create_rounded_corner_bridge: fillet at {0:.2f} mm did not fit; "
            "falling back to tangent blend".format(radius_mm))

    blend = Curve.CreateBlendCurve(curve_a, ta, _blend_reverse(curve_a, ta),
                                   BlendContinuity.Tangency,
                                   curve_b, tb, _blend_reverse(curve_b, tb),
                                   BlendContinuity.Tangency)
    return blend, curve_a, curve_b


def create_anchor_to_anchor_bridge(curve_a, curve_b, radius_mm):
    """Join two adjacent anchor-ring hemispheres on their exterior side.

    Adjacent anchor rings usually overlap (neighbouring fingers share a wall - picture two
    wedding rings pressed together), so the two hemispheres physically cross, leaving a sharp
    concave crotch at the outer crossing. We round that crotch with a small fillet (radius_mm):
    pick points on each ring's far (outer) side select the exterior corner and keep each ring's
    outer arc, and the fillet arc bridges them. If the fillet will not fit, the rings fall back
    to meeting directly at the crossing (the earlier sharp behaviour). Genuinely separated rings
    mean the finger spacing is too large for this bridging approach, so we raise a ValueError
    (rather than fabricate a weak connector across the gap) - reduce all_splint_finger_circ or add
    a minimum-width bar (see TwoDFormHelper.create_two_circle_hourglass_bridge_perimeter).

    Returns (bridge, curve_a_revised, curve_b_revised) where bridge is a fillet arc, _DIRECT_JOIN
    for the sharp-crossing fallback (curves meet directly), or None on failure. Raises ValueError
    when the two anchor rings do not overlap.
    """
    events = Intersection.CurveCurve(curve_a, curve_b, _INTERSECT_TOL, _INTERSECT_TOL)
    if events is not None and events.Count > 0:
        ta_face, tb_face = _facing_endpoints(curve_a, curve_b)
        face_a = curve_a.PointAt(ta_face)
        face_b = curve_b.PointAt(tb_face)
        gap_mid = Point3d((face_a.X + face_b.X) / 2.0, (face_a.Y + face_b.Y) / 2.0,
                          (face_a.Z + face_b.Z) / 2.0)
        # Overlapping circles cross twice (one per hemisphere); take the crossing nearest the
        # facing gap so we round the correct (this-side) exterior corner.
        best = min(events, key=lambda ev: ev.PointA.DistanceTo(gap_mid))
        # Each hemisphere's outer (far, non-facing) endpoint - the side we keep.
        far_a = curve_a.PointAt(curve_a.Domain.T1 if _blend_reverse(curve_a, ta_face)
                                else curve_a.Domain.T0)
        far_b = curve_b.PointAt(curve_b.Domain.T1 if _blend_reverse(curve_b, tb_face)
                                else curve_b.Domain.T0)
        # Round the exterior crotch: fillet keeping each ring's outer arc (pick points far_a/far_b
        # sit on the retained side, steering the fillet to the exterior rather than interior).
        if radius_mm > 0.0:
            pieces = Curve.CreateFilletCurves(curve_a, far_a, curve_b, far_b, radius_mm,
                                              False, True, False, _INTERSECT_TOL, _INTERSECT_TOL)
            arc, rev_a, rev_b = _split_fillet_pieces(pieces, curve_a, curve_b, ta_face, tb_face)
            if arc is not None:
                return arc, rev_a, rev_b
            log("create_anchor_to_anchor_bridge: overlap fillet at {0:.2f} mm did not fit; "
                "meeting sharply at the crossing".format(radius_mm))
        # Fallback: meet directly at the crossing (sharp), trimming each outer arc to it.
        rev_a = _trim_keep_near(curve_a, best.ParameterA, far_a)
        rev_b = _trim_keep_near(curve_b, best.ParameterB, far_b)
        return _DIRECT_JOIN, rev_a, rev_b

    # No crossing: adjacent anchor rings are designed to overlap (they share a wall), so a gap
    # here means the finger spacing is too large for this bridging approach. Fail loudly rather
    # than fabricate a weak, degenerate connector across the gap.
    ta_face, tb_face = _facing_endpoints(curve_a, curve_b)
    gap = curve_a.PointAt(ta_face).DistanceTo(curve_b.PointAt(tb_face))
    log("create_anchor_to_anchor_bridge: adjacent anchor rings do not overlap (facing gap "
        "{0:.1f} mm); finger spacing too large. Reduce all_splint_finger_circ, or add a "
        "minimum-width connecting bar (TwoDFormHelper.create_two_circle_hourglass_bridge_"
        "perimeter).".format(gap))
    raise ValueError(
        "create_anchor_to_anchor_bridge: anchor rings too far apart to bridge (facing gap "
        "{0:.1f} mm); finger spacing (all_splint_finger_circ) too large.".format(gap))


# Minimal-non-biting-attach search bounds (normalized hemisphere arc length from the near end).
# Attaching low (near the dropped stub) makes the blend curve sharply and dip into the ring;
# raising the attach toward the apex clears it. We scan upward and take the first grazing attach.
_ATTACH_SEARCH_LO = 0.15
_ATTACH_SEARCH_HI = 0.9
_ATTACH_SEARCH_STEP = 0.05
# A blend that only grazes touches the kept ring solely at its touchdown; a bite crosses the ring
# elsewhere. Ignore intersections within this distance (mm) of the touchdown as the tangent kiss.
_ATTACH_GRAZE_TOL = 0.5


def _blend_bites_ring(blend, kept_anchor, p_attach):
    """True if blend crosses the kept anchor arc anywhere other than at its touchdown point."""
    events = Intersection.CurveCurve(blend, kept_anchor, _INTERSECT_TOL, _INTERSECT_TOL)
    if events is None:
        return False
    for ev in events:
        if ev.PointA.DistanceTo(p_attach) > _ATTACH_GRAZE_TOL:
            return True
    return False


def create_supportpath_bridge_anchor_to_support(anchor_curve, support_curve, support_center,
                                                support_param=None, min_attach_fraction=None):
    """Smooth (G1) bridge from an anchor ring hemisphere to a supported finger's support band.

    A tangent blend leaves the support band's near end as a smooth continuation of the arc and
    meets the anchor hemisphere tangentially a short way up from the end nearest the support. The
    attach point is found by search: starting low and raising it up the support-facing side of the
    hemisphere until the blend grazes the ring instead of biting into it, taking the tightest such
    join. The anchor is trimmed back to that attach point, dropping the stub that faced the
    support; the support band is left whole. Used for every anchor-to-support joint: mid-support
    arcs and both prongs of an end-support cradle (support side and return side).

    support_param pins which end of support_curve to bridge from (used for end-support cradles,
    whose two prongs sit only a band thickness apart); when None the facing endpoint is picked
    automatically.

    min_attach_fraction, when given, raises the floor of the attach search so the touchdown sits
    higher up the hemisphere (a fuller, stronger neck); when None the search starts at its default
    low bound.

    Returns (blend_curve, anchor_curve_revised, support_curve_revised); on failure the bridge is
    None and the curves are returned untrimmed.
    """
    if support_param is None:
        ta, _tb = _facing_endpoints(support_curve, anchor_curve)
    else:
        ta = support_param
    p_support = support_curve.PointAt(ta)

    # Anchor end nearest the support prong is the stub we drop; attach some way up the hemisphere
    # from it (toward the apex) so the blend meets the ring on the support-facing side. A fraction
    # measured from the near end, mapped to a normalized-length param.
    if (anchor_curve.PointAtStart.DistanceTo(p_support)
            <= anchor_curve.PointAtEnd.DistanceTo(p_support)):
        near_anchor_t = anchor_curve.Domain.T0
        to_nl = lambda frac: frac
    else:
        near_anchor_t = anchor_curve.Domain.T1
        to_nl = lambda frac: 1.0 - frac

    # Blend direction is fixed by which ends we leave from: off the support body at its prong, and
    # back toward the dropped near stub at the anchor so the kept far portion continues smoothly.
    rev_support = _blend_reverse(support_curve, ta)
    rev_anchor = near_anchor_t == anchor_curve.Domain.T0

    # Scan the attach upward and keep the first (tightest) that grazes rather than bites; remember
    # the last valid blend as a fallback if none fully clear. A caller can raise the starting floor
    # (min_attach_fraction) to force the touchdown higher up the ring for a fuller neck.
    start = _ATTACH_SEARCH_LO
    if min_attach_fraction is not None:
        start = max(_ATTACH_SEARCH_LO, min(min_attach_fraction, _ATTACH_SEARCH_HI))
    fallback = None
    frac = start
    while frac <= _ATTACH_SEARCH_HI + 1e-9:
        ok, t_attach = anchor_curve.NormalizedLengthParameter(to_nl(frac))
        if not ok:
            frac += _ATTACH_SEARCH_STEP
            continue
        blend = Curve.CreateBlendCurve(support_curve, ta, rev_support, BlendContinuity.Tangency,
                                       anchor_curve, t_attach, rev_anchor, BlendContinuity.Tangency)
        if blend is not None:
            kept = _trim_keep_far(anchor_curve, t_attach, support_center)
            p_attach = anchor_curve.PointAt(t_attach)
            if not _blend_bites_ring(blend, kept, p_attach):
                return blend, kept, support_curve
            fallback = (blend, kept)
        frac += _ATTACH_SEARCH_STEP

    if fallback is not None:
        log("create_supportpath_bridge_anchor_to_support: no non-biting attach found; using "
            "highest attach (subtle bite may remain)")
        return fallback[0], fallback[1], support_curve

    log("create_supportpath_bridge_anchor_to_support: tangent blend failed; leaving gap")
    return None, anchor_curve, support_curve


def _common_tangent_leap(hemi_a, ring_a, hemi_b, ring_b, profile_plane, elevation_ge0):
    """Plain return leap: a straight common-tangent line across the return side of the two anchor
    rings, trimming each return hemisphere at its tangent point.

    Depends only on the anchor rings (not the support height), so the profile thickness over the
    gap is whatever the elevation leaves. Used as the fallback for create_return_leap_bridge when
    no leapt-over support geometry is available. Tangency is computed against the true ring curves
    (not best-fit circles) so a skewed profile_plane is handled.
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


def create_return_leap_bridge(hemi_a, ring_a, hemi_b, ring_b, profile_plane, elevation_ge0,
                              support_pieces, return_spine_thickness_mm,
                              return_spine_end_reach, return_spine_touchdown_fraction):
    """Return-side spine across a support run: a short level bar spanning the leapt-over support,
    held return_spine_thickness_mm outward from the support's return-facing extreme and NOT
    touching either anchor ring. Each bar end bridges into the adjacent anchor's return
    hemisphere with the same grazing tangent blend used for support prongs.

    A plain common tangent to the two anchor rings ignores the support height, so it pinches thin
    at low elevation and bloats into a solid wedge at high elevation. Instead the spine tracks the
    support: its height (measured along the outward return direction) is one thickness beyond the
    tallest leapt-over support, so the profile keeps that thickness at its thinnest spot, and its
    lateral span matches the support's own width so it floats clear of the rings between them.

    Reusing create_supportpath_bridge_anchor_to_support for both ends means the spine attaches to
    each ring exactly like a support band does - the attach search lands the touchdown on the ring
    edge and keeps the ring's outer arc, so the ring is never clipped.

    return_spine_end_reach (0..1) sets the straight-span width: each spine end reaches that far
    from the support edge toward the adjacent ring apex (0 = support width only, 1 = under the
    apex, which risks touching the ring). return_spine_touchdown_fraction is the minimum attach
    fraction up each anchor return hemisphere for the end blends; raising it moves the touchdown
    higher up the ring for a fuller, stronger neck (independent of the end reach).

    support_pieces are the leapt-over support-side curves (support arcs / cradles) between the two
    anchors. Falls back to a plain common-tangent leap when none are supplied or an end bridge
    cannot be built. Returns (bridge_curve, hemi_a_revised, hemi_b_revised).
    """
    supports = [c for c in (support_pieces or []) if c is not None]
    if not supports or return_spine_thickness_mm <= 0.0:
        return _common_tangent_leap(hemi_a, ring_a, hemi_b, ring_b, profile_plane, elevation_ge0)

    # Return side is the volar (-Z) side when the finger is raised, dorsal (+Z) otherwise; the
    # spine is a level bar perpendicular to 'outward', running along the finger-row axis h.
    outward = Vector3d(0.0, 0.0, -1.0) if elevation_ge0 else Vector3d(0.0, 0.0, 1.0)
    h = _plane_horizontal_axis(profile_plane)

    def u(p):
        # Outward coordinate: how far a point reaches toward the return side.
        return p.X * outward.X + p.Y * outward.Y + p.Z * outward.Z

    # Gap centre from the two ring apexes (their most-outward points), used only as the lateral
    # origin and as the trim reference that keeps each ring's outer arc.
    apex_a = ring_a.PointAt(_extreme_point_param(ring_a, outward))
    apex_b = ring_b.PointAt(_extreme_point_param(ring_b, outward))
    gap_mid = Point3d((apex_a.X + apex_b.X) / 2.0, (apex_a.Y + apex_b.Y) / 2.0,
                      (apex_a.Z + apex_b.Z) / 2.0)

    def lat(p):
        # Lateral (finger-row) coordinate relative to the gap centre.
        return (p.X - gap_mid.X) * h.X + (p.Y - gap_mid.Y) * h.Y + (p.Z - gap_mid.Z) * h.Z

    # Spine height: one thickness beyond the tallest leapt-over support (its return-facing
    # extreme), so the profile keeps that thickness at its thinnest spot.
    u_support = max(u(c.PointAt(_extreme_point_param(c, outward))) for c in supports)
    u_spine = u_support + return_spine_thickness_mm

    # Spine lateral span = the support run's own width (its extremes along +/-h), so the bar
    # floats between the rings without reaching into them.
    lat_hi = max(lat(c.PointAt(_extreme_point_param(c, h))) for c in supports)
    lat_lo = min(lat(c.PointAt(_extreme_point_param(c, Vector3d(-h.X, -h.Y, -h.Z))))
                 for c in supports)
    if lat_hi <= lat_lo:
        return _common_tangent_leap(hemi_a, ring_a, hemi_b, ring_b, profile_plane, elevation_ge0)

    # Reach each end partway from the support edge toward the adjacent ring apex so the end blend
    # meets the anchor with a fuller neck instead of a pinch (the grazing attach still keeps the
    # ring's outer arc, so a modest reach fattens the joint without clipping the ring).
    lat_apex_hi = max(lat(apex_a), lat(apex_b))
    lat_apex_lo = min(lat(apex_a), lat(apex_b))
    lat_hi = lat_hi + return_spine_end_reach * max(0.0, lat_apex_hi - lat_hi)
    lat_lo = lat_lo - return_spine_end_reach * max(0.0, lat_lo - lat_apex_lo)

    du = u_spine - u(gap_mid)

    def spine_point(lateral):
        return Point3d(gap_mid.X + h.X * lateral + outward.X * du,
                       gap_mid.Y + h.Y * lateral + outward.Y * du,
                       gap_mid.Z + h.Z * lateral + outward.Z * du)

    # Oriented -h end (T0) to +h end (T1).
    spine = LineCurve(spine_point(lat_lo), spine_point(lat_hi))

    # Match each bar end to the ring on its lateral side, then bridge with the support-prong blend
    # (support_center=gap_mid keeps each ring's outer arc). The spine stays whole across both.
    hemi_hi, hemi_lo = (hemi_a, hemi_b) if lat(apex_a) >= lat(apex_b) else (hemi_b, hemi_a)

    blend_hi, hemi_hi_trim, spine = create_supportpath_bridge_anchor_to_support(
        hemi_hi, spine, gap_mid, support_param=spine.Domain.T1,
        min_attach_fraction=return_spine_touchdown_fraction)
    if blend_hi is None:
        log("create_return_leap_bridge: spine could not bridge to the +h anchor; using "
            "common-tangent leap")
        return _common_tangent_leap(hemi_a, ring_a, hemi_b, ring_b, profile_plane, elevation_ge0)

    blend_lo, hemi_lo_trim, spine = create_supportpath_bridge_anchor_to_support(
        hemi_lo, spine, gap_mid, support_param=spine.Domain.T0,
        min_attach_fraction=return_spine_touchdown_fraction)
    if blend_lo is None:
        log("create_return_leap_bridge: spine could not bridge to the -h anchor; using "
            "common-tangent leap")
        return _common_tangent_leap(hemi_a, ring_a, hemi_b, ring_b, profile_plane, elevation_ge0)

    hemi_a_trim, hemi_b_trim = ((hemi_hi_trim, hemi_lo_trim) if lat(apex_a) >= lat(apex_b)
                                else (hemi_lo_trim, hemi_hi_trim))

    joined = Curve.JoinCurves([blend_hi, spine, blend_lo], _JOIN_TOL)
    if joined is None or len(joined) != 1:
        log("create_return_leap_bridge: spine + end blends did not join cleanly; using "
            "common-tangent leap")
        return _common_tangent_leap(hemi_a, ring_a, hemi_b, ring_b, profile_plane, elevation_ge0)
    return joined[0], hemi_a_trim, hemi_b_trim


def _support_between(included, i, j):
    """True if any included finger strictly between indices i and j is a supported finger."""
    lo, hi = (i, j) if i < j else (j, i)
    return any(not included[k].get("is_anchor_finger") for k in range(lo + 1, hi))


def _log_walk_chain_gaps(walk_segments, work, bridge_after):
    """Diagnostic: walk the ordered chain (each slot then the bridge that follows it) and log
    any junction whose two pieces do not share an endpoint within _JOIN_TOL - i.e. exactly where
    the perimeter fails to close. Endpoints are compared in both orientations since pieces are
    not consistently oriented.
    """
    count = len(work)
    ordered = []  # (label, curve) in perimeter order
    for k in range(count):
        seg = walk_segments[k]
        if work[k] is not None:
            ordered.append(("seg[{0}] {1} f{2}".format(k, seg["kind"], seg["finger_index"]),
                            work[k]))
        if bridge_after[k] is not None:
            ordered.append(("bridge after seg[{0}] ({1})".format(k, seg["kind"]),
                            bridge_after[k]))
    if len(ordered) < 2:
        return
    # Full ordered-chain dump: every piece's endpoints in perimeter order, so a sign / side
    # mismatch (e.g. a prong on the wrong Z side) is visible at a glance.
    for label, c in ordered:
        s, e = c.PointAtStart, c.PointAtEnd
        log("  chain {0}: start=({1:.2f},{2:.2f},{3:.2f}) end=({4:.2f},{5:.2f},{6:.2f})".format(
            label, s.X, s.Y, s.Z, e.X, e.Y, e.Z))
    reported = 0
    for i in range(len(ordered)):
        label_a, ca = ordered[i]
        label_b, cb = ordered[(i + 1) % len(ordered)]
        ends_a = (ca.PointAtStart, ca.PointAtEnd)
        ends_b = (cb.PointAtStart, cb.PointAtEnd)
        gap = min(ea.DistanceTo(eb) for ea in ends_a for eb in ends_b)
        if gap > _JOIN_TOL:
            log("weld_perimeter_walk GAP {0:.3f} mm between {1} and {2}".format(
                gap, label_a, label_b))
            log("  {0} ends: start=({1:.2f},{2:.2f},{3:.2f}) end=({4:.2f},{5:.2f},{6:.2f})".format(
                label_a, ends_a[0].X, ends_a[0].Y, ends_a[0].Z,
                ends_a[1].X, ends_a[1].Y, ends_a[1].Z))
            log("  {0} ends: start=({1:.2f},{2:.2f},{3:.2f}) end=({4:.2f},{5:.2f},{6:.2f})".format(
                label_b, ends_b[0].X, ends_b[0].Y, ends_b[0].Z,
                ends_b[1].X, ends_b[1].Y, ends_b[1].Z))
            reported += 1
    if reported == 0:
        log("weld_perimeter_walk: no endpoint gaps > {0} mm found in walk order; the open end "
            "may be a self-crossing or a mis-oriented piece".format(_JOIN_TOL))


def weld_perimeter_walk(raw_data, walk_segments, profile_plane, exterior_anchor_rings,
                        anchor_bridge_radius_mm, support_bridge_radius_mm,
                        return_spine_thickness_mm, return_spine_end_reach,
                        return_spine_touchdown_fraction):
    """Bridge the ordered walk slots and join them into one closed profile perimeter (Pass 2).

    For each adjacent slot pair (including the loop-closing pair) the matching bridge is built
    and any trimmed curves are written back, then every piece is stitched with Curve.JoinCurves.
    Same-anchor hemisphere pairs (the two turn-arounds) join directly with no bridge.
    End-support fingers arrive as a single pre-capped cradle segment and bridge to the adjacent
    anchor's support and return hemispheres like any other support-to-anchor joint.

    Inputs:
      - walk_segments:            ordered slots from plan_perimeter_walk.
      - profile_plane:            the shared work plane (from build_profile_plane).
      - exterior_anchor_rings:    full rings from build_exterior_anchor_rings (for the leap's
                                  true-curve tangency), index-aligned to the included fingers.
      - anchor_bridge_radius_mm:  rounded-corner radius for structural anchor-to-anchor joints
                                  (tight - mechanical strength only).
      - support_bridge_radius_mm: rounded-corner radius for support-to-support joints (larger -
                                  the finger contacts these, so they need a smoother blend).
      - return_spine_thickness_mm: profile thickness the return-side spine holds over a support
                                  run (the strut is offset this far beyond the support's
                                  return-facing extreme).
      - return_spine_end_reach:   fraction (0..1) of the gap from the support edge to the adjacent
                                  ring apex each spine end reaches; sets the straight-span width
                                  (0 = support width only, 1 = under the apex).
      - return_spine_touchdown_fraction: minimum attach fraction up each anchor return hemisphere
                                  for the spine's end blends; higher pushes the touchdown up the
                                  ring for a fuller, stronger neck.

    The mechanism (create_rounded_corner_bridge) is radius-agnostic; this dispatcher owns the
    policy of which radius applies to which joint type.

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
    bridge_after = [None] * count  # bridge inserted after slot k in walk order (for diagnostics)

    for k in range(count):
        a = walk_segments[k]
        b = walk_segments[(k + 1) % count]
        j = (k + 1) % count
        ka, kb = a["kind"], b["kind"]
        fa, fb = a["finger_index"], b["finger_index"]
        ca, cb = work[k], work[j]
        pair_desc = "{0}->{1} (finger {2}->{3})".format(ka, kb, fa, fb)
        if ca is None or cb is None:
            log("weld_perimeter_walk: skipped {0}; a segment curve is missing".format(pair_desc))
            continue

        # Turn-around / loop closure: same anchor's two hemispheres meet at the far extreme.
        if fa == fb and set([ka, kb]) <= set(["anchor_support_side", "anchor_return_side"]):
            log("weld_perimeter_walk: turn-around at {0} (no bridge)".format(pair_desc))
            continue

        bridge = None
        if ka == "support_arc" and kb == "support_arc":
            bridge, rev_a, rev_b = create_rounded_corner_bridge(ca, cb, support_bridge_radius_mm)
            work[k] = rev_a
            work[j] = rev_b
        elif (set([ka, kb]) == set(["anchor_support_side", "support_arc"])
              or set([ka, kb]) == set(["anchor_support_side", "end_support_cradle"])):
            # Anchor support hemisphere -> support-like near end (support side).
            if ka == "anchor_support_side":
                ai, si = k, j
            else:
                ai, si = j, k
            support_center = work[si].GetBoundingBox(True).Center
            # A cradle's support prong is its start endpoint (fixed in build_end_support_cradles);
            # a plain mid-support arc uses automatic facing-endpoint detection.
            sp = work[si].Domain.T0 if walk_segments[si]["kind"] == "end_support_cradle" else None
            bridge, rev_anchor, rev_support = create_supportpath_bridge_anchor_to_support(
                work[ai], work[si], support_center, support_param=sp)
            work[ai] = rev_anchor
            work[si] = rev_support
        elif set([ka, kb]) == set(["anchor_return_side", "end_support_cradle"]):
            # End-support cradle return prong -> adjacent anchor's return hemisphere. Uses the
            # same grazing-attach tangent blend as the support prong: the attach search lands the
            # touchdown on the ring edge without denting the round outer wall - the concern that
            # once justified a separate crotch-fillet here.
            if ka == "anchor_return_side":
                ai, si = k, j
            else:
                ai, si = j, k
            support_center = work[si].GetBoundingBox(True).Center
            bridge, rev_anchor, rev_support = create_supportpath_bridge_anchor_to_support(
                work[ai], work[si], support_center, support_param=work[si].Domain.T1)
            work[ai] = rev_anchor
            work[si] = rev_support
        elif ka == "anchor_support_side" and kb == "anchor_support_side":
            bridge, rev_a, rev_b = create_anchor_to_anchor_bridge(
                ca, cb, anchor_bridge_radius_mm)
            work[k] = rev_a
            work[j] = rev_b
        elif ka == "anchor_return_side" and kb == "anchor_return_side":
            if _support_between(included, fa, fb):
                # Leapt-over support-side pieces between the two anchors set the spine height.
                lo, hi = (fa, fb) if fa < fb else (fb, fa)
                support_pieces = [
                    work[m] for m in range(count)
                    if walk_segments[m]["kind"] in ("support_arc", "end_support_cradle")
                    and lo < walk_segments[m]["finger_index"] < hi]
                bridge, rev_a, rev_b = create_return_leap_bridge(
                    ca, exterior_anchor_rings[fa], cb, exterior_anchor_rings[fb],
                    profile_plane, elevation_ge0, support_pieces,
                    return_spine_thickness_mm, return_spine_end_reach,
                    return_spine_touchdown_fraction)
                work[k] = rev_a
                work[j] = rev_b
            else:
                bridge, rev_a, rev_b = create_anchor_to_anchor_bridge(
                    ca, cb, anchor_bridge_radius_mm)
                work[k] = rev_a
                work[j] = rev_b
        else:
            log("weld_perimeter_walk: unhandled pair {0}".format(pair_desc))
            failures.append("{0}: unhandled pair".format(pair_desc))
            continue

        if bridge is None:
            log("weld_perimeter_walk: bridge FAILED for {0}".format(pair_desc))
            failures.append("{0}: bridge returned None".format(pair_desc))
            continue
        if bridge is _DIRECT_JOIN:
            log("weld_perimeter_walk: {0} joined directly (overlapping rings, no bridge)".format(
                pair_desc))
            continue
        log("weld_perimeter_walk: bridged {0} (length {1:.2f} mm)".format(
            pair_desc, bridge.GetLength()))  # type: ignore[union-attr]
        bridges.append(bridge)
        bridge_after[k] = bridge  # type: ignore[assignment]

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
        _log_walk_chain_gaps(walk_segments, work, bridge_after)
        raise ValueError(
            "weld_perimeter_walk: failed to build a closed profile perimeter "
            "({0} segment piece(s) + {1} bridge(s) joined into {2} curve(s), none closed). "
            "Failures: {3}".format(piece_count, len(bridges), joined_count, detail))
    return closed, bridges


def build_splint_solid(proximal_profile, distal_profile):
    """Loft the two profile perimeters into one closed, watertight solid slab (Phase 6).

    The proximal (-X, toward the hand) and distal (+X, toward the fingertip) perimeters from
    Phase 5 are the band's two faces. They are congruent wherever the section is an anchor (an
    anchor cylinder is uniform along X, so any X cuts the same ring) and differ only across the
    elevated-support regions, so a straight (ruled) loft between them, capped at both planar ends,
    yields the tapered band. Both faces are always lofted - never extrude one - so the
    proximal/distal difference (the support taper) is preserved.

    No fallbacks: any failure raises ValueError so the process limits stay visible. The loft is
    preconditioned to make it reliable:
      1. Require both perimeters closed.
      2. Re-seam both to their world +Y extreme so the loft's section correspondence starts at the
         same feature on each curve (an exact match on the congruent anchors), avoiding the
         twisted wall a mismatched seam produces.
      3. Match curve directions (reverse the distal curve if opposed) so the ruled wall does not
         self-cross.
      4. Straight loft -> one open tube wall.
      5. CapPlanarHoles -> fill both planar ends into a closed solid.
      6. Validate IsSolid; flip an inward-oriented solid so its normals face out.

    The finger bores are cut later by boolean-subtracting the uncapped cylinders, so this
    perimeter carries no inner holes - it is a single outer silhouette.

    Returns a single closed, outward-oriented Brep.
    """
    if proximal_profile is None or distal_profile is None:
        raise ValueError("build_splint_solid: a profile perimeter is missing (None).")

    prox = proximal_profile.DuplicateCurve()
    dist = distal_profile.DuplicateCurve()
    if not prox.IsClosed or not dist.IsClosed:
        raise ValueError(
            "build_splint_solid: both profile perimeters must be closed (proximal closed={0}, "
            "distal closed={1}).".format(prox.IsClosed, dist.IsClosed))

    # Re-seam both to the same feature (world +Y extreme). On the congruent anchor regions this is
    # an exact point correspondence, so the lofted sections align instead of shearing.
    if not prox.ChangeClosedCurveSeam(_extreme_point_param(prox, Vector3d.YAxis)):
        log("build_splint_solid: proximal re-seam had no effect (already at +Y extreme)")
    if not dist.ChangeClosedCurveSeam(_extreme_point_param(dist, Vector3d.YAxis)):
        log("build_splint_solid: distal re-seam had no effect (already at +Y extreme)")

    # Align directions so the ruled surface does not twist into a self-intersection.
    if not Curve.DoDirectionsMatch(prox, dist):
        dist.Reverse()

    lofts = Brep.CreateFromLoft([prox, dist], Point3d.Unset, Point3d.Unset,
                                LoftType.Straight, False)
    if lofts is None or len(lofts) != 1:
        raise ValueError(
            "build_splint_solid: loft did not produce exactly one wall surface (got {0}); the "
            "two perimeters are too dissimilar to ruled-loft.".format(
                0 if lofts is None else len(lofts)))
    wall = lofts[0]

    solid = wall.CapPlanarHoles(_CAP_TOL)
    if solid is None:
        raise ValueError(
            "build_splint_solid: CapPlanarHoles failed; the loft ends are not both planar closed "
            "loops.")

    if not solid.IsSolid:
        raise ValueError(
            "build_splint_solid: capped brep is not a closed solid (IsValid={0}, IsManifold={1}, "
            "face count={2}).".format(solid.IsValid, solid.IsManifold, solid.Faces.Count))

    # A capped loft can come back inward-facing; flip so the solid's normals point outward.
    if solid.SolidOrientation == BrepSolidOrientation.Inward:
        solid.Flip()

    log("build_splint_solid: built closed solid ({0} face(s), volume {1:.1f} mm^3)".format(
        solid.Faces.Count, solid.GetVolume()))
    return solid


def build_finger_bores(p1_lines_oriented, p1_circles_oriented, finger_clearance_mm=0.0):
    """Build one capped solid cylinder per included finger, for boolean-subtracting the finger
    channels from the splint solid (a later phase).

    Uses the elevated (oriented) outputs so each bore follows its finger's real axis and tilt:
      - axis / length from p1_lines_oriented,
      - radius from p1_circles_oriented (plus finger_clearance_mm for fit tolerance).

    The cylinder is doubled in length about the P1 line's own midpoint (each end extended by half
    the P1 length) so it overshoots both splint faces and always makes a clean through-cut - the
    coincident-face case that makes booleans fail. Capped into a closed solid (unlike the thin
    open p1_cylinders used earlier for visible plane cuts). A full cylinder is correct for
    supported fingers too: subtracting it carves the finger-shaped void, and whatever splint
    material exists on one side becomes the contact surface.

    Returns a list of closed Breps, index-aligned to the included fingers.
    """
    bores = []
    for i, line in enumerate(p1_lines_oriented):
        # Copy the line (value type) and double its length about its midpoint.
        axis_line = Line(line.From, line.To)
        half = axis_line.Length / 2.0
        axis_line.Extend(half, half)

        axis = axis_line.To - axis_line.From
        axis.Unitize()
        radius = p1_circles_oriented[i].Radius + finger_clearance_mm
        base_circle = Circle(Plane(axis_line.From, axis), radius)
        bore = Cylinder(base_circle, axis_line.Length).ToBrep(True, True)
        bores.append(bore)

    log("build_finger_bores: built {0} capped bore cylinder(s) (clearance {1} mm)".format(
        len(bores), finger_clearance_mm))
    return bores


def subtract_finger_bores(splint_solid, finger_bores, tolerance=None):
    """Boolean-subtract each finger bore from the splint solid, in sequence (Phase 7).

    Booleans are historically the least reliable step, so each cut goes through
    BrepDifference.robust_brep_difference, which escalates through direct / list / tolerance /
    jiggle / repair / mesh-boolean strategies and validates the result before accepting it. The
    bores are subtracted one at a time (the running result becomes the next minuend) rather than
    in a single multi-subtrahend call: it is more robust and pinpoints which finger, if any, fails.

    Inputs:
      - splint_solid:  the closed Brep from build_splint_solid.
      - finger_bores:  capped solid Breps from build_finger_bores (each overshoots both faces, so
                       every cut is a clean through-bore).
      - tolerance:     base tolerance for the booleans; None uses the document tolerance.

    Returns the bored splint Brep. Raises ValueError if the input solid is missing, and propagates
    BrepDifference's errors (BrepDifferenceError / NoIntersectionError / InvalidBrepError) if a cut
    cannot be made even after all fallback strategies - so a failure names the offending finger.
    """
    if splint_solid is None:
        raise ValueError("subtract_finger_bores: splint_solid is missing (None).")
    if not finger_bores:
        log("subtract_finger_bores: no bores supplied; returning the solid unchanged")
        return splint_solid

    result = splint_solid
    for i, bore in enumerate(finger_bores):
        bored, success, method = robust_brep_difference(result, bore, tolerance)
        log("subtract_finger_bores: finger {0} cut via {1}".format(i, method))
        result = bored

    log("subtract_finger_bores: subtracted {0} bore(s); final volume {1:.1f} mm^3".format(
        len(finger_bores), result.GetVolume()))
    return result


def generate_relative_motion_splint(raw_data, object_id="TEST"):
    """Full RelativeMotion pipeline: raw_data -> bored splint solid, with every intermediate.

    This is the single orchestration entry point (moved out of the GhPython component so the
    algorithm lives in git, is diffable/testable, and can run headless in the geo processor).
    The GH component becomes a thin shim: decode its input, call this, and fan the returned dict
    out to output wires. All GH-host plumbing (ghenv, sys.path, splintcommon init, timing,
    gh_decode_one, the try/except) stays in that shim, not here.

    The tuning coefficients (design constants, not per-patient measurements) live here as locals.
    raw_data carries only patient measurements + config that the future web form collects.

    Returns a dict of every phase's geometry so the component can still preview each stage; the
    final bored solid is result["splint_solid"].
    """
    # --- Tuning coefficients (design constants) -------------------------------------------
    support_prong_arc_deg = 55.0            # END-support cradle prong arc width (wider contact)
    support_arc_deg = 45.0                  # MID-support arc width

    radial_band_thickness_mm = 1.65
    single_sided_support_thickness_mm = radial_band_thickness_mm * 1.5
    longitudinal_band_thickness_mm = 10.0
    min_center_gap = radial_band_thickness_mm
    anchor_bridge_radius_mm = 3.0
    support_bridge_radius_mm = 10.0
    return_spine_thickness_mm = 8.0
    return_spine_end_reach = 0.2
    return_spine_touchdown_fraction = 0.35

    # --- Phase 1: finger positions --------------------------------------------------------
    mcp_points, p1_lines, p1_circles, p1_cylinders = setup_finger_positions(
        raw_data, min_center_gap=min_center_gap)

    # --- Phase 2: elevate supported fingers -----------------------------------------------
    (mcp_points_oriented, p1_lines_oriented, p1_circles_oriented,
     p1_cylinders_oriented, transforms) = elevate_supported_fingers(
        raw_data, mcp_points, p1_lines, p1_circles, p1_cylinders)

    # --- Phase 3: proximal + distal profile planes ----------------------------------------
    proximal_profile_plane, distal_profile_plane = build_profile_planes(
        raw_data, p1_lines_oriented, longitudinal_band_thickness_mm)

    # --- Phase 4: finger cross-sections on each plane -------------------------------------
    p_full_curves, p_preserved = extract_finger_cross_sections(
        raw_data, proximal_profile_plane, p1_cylinders_oriented, p1_lines_oriented,
        support_prong_arc_deg=support_prong_arc_deg, support_arc_deg=support_arc_deg)
    d_full_curves, d_preserved = extract_finger_cross_sections(
        raw_data, distal_profile_plane, p1_cylinders_oriented, p1_lines_oriented,
        support_prong_arc_deg=support_prong_arc_deg, support_arc_deg=support_arc_deg)

    # --- Phase 5: walk each profile perimeter ---------------------------------------------
    # Proximal face.
    p_rings, p_pos_hemis, p_neg_hemis = build_exterior_anchor_rings(
        raw_data, proximal_profile_plane, p_preserved, radial_band_thickness_mm)
    p_cradles = build_end_support_cradles(
        raw_data, proximal_profile_plane, p_preserved, p_rings,
        single_sided_support_thickness_mm)
    p_walk_segments = plan_perimeter_walk(
        raw_data, p_pos_hemis, p_neg_hemis, p_preserved, p_cradles)
    p_walk_preview = [s["curve"] for s in p_walk_segments]
    p_closed_profile, p_bridge_curves = weld_perimeter_walk(
        raw_data, p_walk_segments, proximal_profile_plane, p_rings,
        anchor_bridge_radius_mm, support_bridge_radius_mm, return_spine_thickness_mm,
        return_spine_end_reach, return_spine_touchdown_fraction)

    # Distal face.
    d_rings, d_pos_hemis, d_neg_hemis = build_exterior_anchor_rings(
        raw_data, distal_profile_plane, d_preserved, radial_band_thickness_mm)
    d_cradles = build_end_support_cradles(
        raw_data, distal_profile_plane, d_preserved, d_rings,
        single_sided_support_thickness_mm)
    d_walk_segments = plan_perimeter_walk(
        raw_data, d_pos_hemis, d_neg_hemis, d_preserved, d_cradles)
    d_walk_preview = [s["curve"] for s in d_walk_segments]
    d_closed_profile, d_bridge_curves = weld_perimeter_walk(
        raw_data, d_walk_segments, distal_profile_plane, d_rings,
        anchor_bridge_radius_mm, support_bridge_radius_mm, return_spine_thickness_mm,
        return_spine_end_reach, return_spine_touchdown_fraction)

    # --- Phase 6: loft the two faces, then bore the fingers -------------------------------
    splint_solid_blank = build_splint_solid(p_closed_profile, d_closed_profile)
    finger_bores = build_finger_bores(p1_lines_oriented, p1_circles_oriented)
    splint_solid = subtract_finger_bores(splint_solid_blank, finger_bores)

    log("generate_relative_motion_splint: pipeline complete (objectID {0})".format(object_id))

    # Every intermediate is returned so the GH component can still preview each phase.
    return {
        "object_id": object_id,
        # Phase 1
        "mcp_points": mcp_points, "p1_lines": p1_lines,
        "p1_circles": p1_circles, "p1_cylinders": p1_cylinders,
        # Phase 2
        "mcp_points_oriented": mcp_points_oriented, "p1_lines_oriented": p1_lines_oriented,
        "p1_circles_oriented": p1_circles_oriented,
        "p1_cylinders_oriented": p1_cylinders_oriented, "transforms": transforms,
        # Phase 3
        "proximal_profile_plane": proximal_profile_plane,
        "distal_profile_plane": distal_profile_plane,
        # Phase 4
        "p_full_curves": p_full_curves, "p_preserved": p_preserved,
        "d_full_curves": d_full_curves, "d_preserved": d_preserved,
        # Phase 5 proximal
        "p_rings": p_rings, "p_pos_hemis": p_pos_hemis, "p_neg_hemis": p_neg_hemis,
        "p_cradles": p_cradles, "p_walk_segments": p_walk_segments,
        "p_walk_preview": p_walk_preview, "p_closed_profile": p_closed_profile,
        "p_bridge_curves": p_bridge_curves,
        # Phase 5 distal
        "d_rings": d_rings, "d_pos_hemis": d_pos_hemis, "d_neg_hemis": d_neg_hemis,
        "d_cradles": d_cradles, "d_walk_segments": d_walk_segments,
        "d_walk_preview": d_walk_preview, "d_closed_profile": d_closed_profile,
        "d_bridge_curves": d_bridge_curves,
        # Phase 6
        "splint_solid_blank": splint_solid_blank, "finger_bores": finger_bores,
        "splint_solid": splint_solid,
    }
