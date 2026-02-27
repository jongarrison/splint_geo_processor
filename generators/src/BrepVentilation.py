"""
BrepVentilation.py
Cut ventilation holes in splint breps using radial cylinders.

Generates candidate hole positions along a centerline, validates each
against protected curves and spacing constraints, then batch-subtracts
all valid holes from the input brep.
"""

import Rhino.Geometry as rg
from Rhino.Geometry import Point3d, Vector3d, Line, Plane
import scriptcontext as sc
import math
import time
import random as _random_mod
from splintcommon import log


def _curve_from_centerline(centerline):
    """Convert Polyline, PolylineCurve, or Curve to NurbsCurve."""
    if isinstance(centerline, rg.Polyline):
        return centerline.ToNurbsCurve()
    if isinstance(centerline, rg.PolylineCurve):
        return centerline.ToNurbsCurve()
    if isinstance(centerline, rg.NurbsCurve):
        return centerline
    if hasattr(centerline, 'ToNurbsCurve'):
        return centerline.ToNurbsCurve()
    raise ValueError(f"Cannot convert {type(centerline)} to NurbsCurve")


def _perp_frame(curve, t):
    """Get a perpendicular frame at parameter t on the curve.

    Returns (point, tangent, dorsal, lateral) -- all unit vectors.
    Dorsal derived from world Z (consistent with FingerModel convention:
    +Z = dorsal/nail, -Z = volar/palm).
    """
    point = curve.PointAt(t)
    tangent = curve.TangentAt(t)
    tangent.Unitize()

    up = Vector3d.ZAxis
    if abs(Vector3d.Multiply(up, tangent)) > 0.95:
        up = Vector3d.YAxis
    dorsal = up - tangent * Vector3d.Multiply(up, tangent)
    dorsal.Unitize()

    lateral = Vector3d.CrossProduct(tangent, dorsal)
    lateral.Unitize()

    return point, tangent, dorsal, lateral


def _radial_direction(dorsal, lateral, angle_rad):
    """Unit vector in the plane perpendicular to the tangent at *angle_rad*
    measured from the dorsal direction."""
    c = math.cos(angle_rad)
    s = math.sin(angle_rad)
    return Vector3d(
        dorsal.X * c + lateral.X * s,
        dorsal.Y * c + lateral.Y * s,
        dorsal.Z * c + lateral.Z * s,
    )


# -- Candidate generators ---------------------------------------------------

def _generate_helical_candidates(curve, spacing_along, angular_step_deg,
                                  end_margin, angles_per_station=8):
    """Helical pattern: multiple radial candidates per station.

    At each station along the centerline, generates *angles_per_station*
    candidates evenly spaced around the circumference. Between stations
    the ring is rotated by *angular_step_deg* (default golden angle) so
    holes stagger naturally across rows.

    Candidates are interleaved: all stations at angle-index 0 first,
    then all at angle-index 1, etc. This ensures the greedy clearance
    evaluator places the first ring of holes with visible golden-angle
    rotation before filling in secondary rings.
    """
    length = curve.GetLength()
    angular_step_rad = math.radians(angular_step_deg)
    ring_step = 2 * math.pi / angles_per_station

    # Build per-station data: (point, tangent, dorsal, lateral, base_angle)
    stations = []
    dist = end_margin
    base_angle = 0.0
    while dist <= length - end_margin:
        success, t = curve.LengthParameter(dist)
        if success:
            pt, tan, dor, lat = _perp_frame(curve, t)
            stations.append((pt, tan, dor, lat, base_angle))
        dist += spacing_along
        base_angle += angular_step_rad

    # Interleave: angle index in outer loop, station in inner loop
    candidates = []
    for i in range(angles_per_station):
        for pt, tan, dor, lat, base_angle in stations:
            angle = base_angle + i * ring_step
            candidates.append(
                (pt, _radial_direction(dor, lat, angle), tan)
            )
    return candidates


# -- Clearance checks --------------------------------------------------------

def _find_surface_hits(axis_line, brep, tolerance):
    """Intersect a line with the brep surface.

    Returns list of Point3d sorted by distance from the line midpoint
    (centerline point) outward.
    """
    axis_curve = rg.LineCurve(axis_line)
    success, _, hit_points = rg.Intersect.Intersection.CurveBrep(
        axis_curve, brep, tolerance
    )

    if not success or not hit_points or len(hit_points) == 0:
        return []

    origin = Point3d(
        (axis_line.From.X + axis_line.To.X) / 2,
        (axis_line.From.Y + axis_line.To.Y) / 2,
        (axis_line.From.Z + axis_line.To.Z) / 2,
    )
    pts = [Point3d(p) for p in hit_points]
    pts.sort(key=lambda p: p.DistanceTo(origin))
    return pts


def _check_protected_clearance(axis_line, clearance_radius, protected_curves,
                                tolerance):
    """Return True if a clearance cylinder around the axis does NOT
    intersect any protected curve.

    Builds a temporary cylinder brep with *clearance_radius* and checks
    each protected curve for geometric intersection. This is robust on
    curved surfaces where point-distance checks can miss edge clipping.
    """
    if not protected_curves:
        return True

    direction = Vector3d(axis_line.Direction)
    direction.Unitize()
    base_plane = Plane(axis_line.From, direction)
    cyl = rg.Cylinder(rg.Circle(base_plane, clearance_radius), axis_line.Length)
    cyl_brep = cyl.ToBrep(True, True)
    if cyl_brep is None:
        return True  # can't build check cylinder, allow it

    for crv in protected_curves:
        success, overlap_crvs, pts = rg.Intersect.Intersection.CurveBrep(
            crv, cyl_brep, tolerance
        )
        if not success:
            continue
        # Any overlap curves or intersection points means the clearance
        # zone touches this protected curve
        has_overlap = overlap_crvs and len(overlap_crvs) > 0
        has_points = pts and len(pts) > 0
        if has_overlap or has_points:
            return False

    return True


def _check_hole_spacing(hit_points, clearance_radius, accepted_hits):
    """Return True if every hit point is farther than 2*clearance_radius
    from every already-accepted surface hit."""
    min_dist = 2.0 * clearance_radius
    for pt in hit_points:
        for acc in accepted_hits:
            if pt.DistanceTo(acc) < min_dist:
                return False
    return True


def _clearance_footprint_curves(axis_line, clearance_radius, brep, tolerance):
    """Intersect a clearance cylinder with the brep to get footprint curves.

    Returns curves where the clearance zone meets the brep surface.
    Added to active protected curves so subsequent candidates avoid
    overlapping accepted holes.
    """
    direction = Vector3d(axis_line.Direction)
    direction.Unitize()
    base_plane = Plane(axis_line.From, direction)
    cyl = rg.Cylinder(rg.Circle(base_plane, clearance_radius), axis_line.Length)
    cyl_brep = cyl.ToBrep(True, True)
    if cyl_brep is None:
        return []

    success, curves, _points = rg.Intersect.Intersection.BrepBrep(
        cyl_brep, brep, tolerance
    )
    if success and curves and len(curves) > 0:
        return list(curves)
    return []


# -- Main entry point --------------------------------------------------------

def ventilate_brep(
    input_brep,
    centerline,
    hole_radius=2.0,
    clearance_radius=3.0,
    protected_curves=None,
    strategy="helical",
    spacing_along=5.0,
    angular_step=137.5,
    angles_per_station=8,
    max_holes=None,
    max_attempts=500,
    seed=42,
    hole_length=50.0,
    tolerance=None,
):
    """Cut ventilation holes in a splint brep.

    Generates candidate hole positions along the centerline, validates
    each against protected curves and inter-hole spacing, then
    batch-subtracts all valid holes.

    Args:
        input_brep:       Closed brep to ventilate.
        centerline:       Polyline or Curve guide for hole placement.
        hole_radius:      Radius of cylindrical holes (mm).
        clearance_radius: Non-interference zone radius (mm, >= hole_radius).
        protected_curves: List of curves holes must avoid.
        strategy:         "helical" or "random".
        spacing_along:    Distance between stations along centerline (mm).
        angular_step:     Rotation offset between station rings (degrees).
                          Default 137.5 (golden angle) staggers rows.
        angles_per_station: Candidate directions per station around
                          the circumference (helical). Default 8.
        max_holes:        Stop after placing this many holes (None = no
                          limit for helical, random uses max_attempts).
        max_attempts:     Maximum candidate attempts for random strategy
                          before giving up.  Default 500.
        seed:             Random seed (random strategy).
        hole_length:      Length of cutting cylinders (mm).
        tolerance:        Geometric tolerance (mm).

    Returns:
        (ventilated_brep, hole_count)
    """
    if tolerance is None:
        tolerance = sc.doc.ModelAbsoluteTolerance

    t0 = time.time()

    if clearance_radius < hole_radius:
        clearance_radius = hole_radius
        log(f"ventilate: clearance_radius raised to hole_radius ({hole_radius})")

    if protected_curves is None:
        protected_curves = []

    log("=" * 60)
    log("VENTILATING BREP")
    log("=" * 60)
    log(f"Strategy: {strategy}")
    log(f"Hole radius: {hole_radius}mm, clearance: {clearance_radius}mm")
    log(f"Hole length: {hole_length}mm")
    log(f"Protected curves: {len(protected_curves)}")

    # Prepare centerline curve
    curve = _curve_from_centerline(centerline)
    cl_length = curve.GetLength()
    log(f"Centerline length: {cl_length:.1f}mm")

    # Keep holes away from centerline endpoints
    end_margin = clearance_radius * 2.0

    # Shared evaluation state
    accepted_axes = []
    accepted_hits = []
    rejected_no_hit = 0
    rejected_protected = 0
    rejected_spacing = 0
    half_len = hole_length / 2.0

    # Mutable copy -- accepted-hole footprints accumulate here so
    # subsequent candidates avoid overlapping placed holes.
    active_protected = list(protected_curves)

    if strategy == "helical":
        candidates = _generate_helical_candidates(
            curve, spacing_along, angular_step, end_margin,
            angles_per_station
        )
        log(f"Helical: spacing={spacing_along}mm, angle={angular_step}deg, "
            f"{angles_per_station} per station, {len(candidates)} candidates")

        for center_pt, radial_dir, _tangent in candidates:
            if max_holes is not None and len(accepted_axes) >= max_holes:
                log(f"Helical: reached target of {max_holes} holes")
                break

            axis_start = center_pt - radial_dir * half_len
            axis_end = center_pt + radial_dir * half_len
            axis_line = Line(axis_start, axis_end)

            hits = _find_surface_hits(axis_line, input_brep, tolerance)
            if not hits:
                rejected_no_hit += 1
                continue

            if not _check_protected_clearance(axis_line, clearance_radius,
                                               active_protected, tolerance):
                rejected_protected += 1
                continue

            if not _check_hole_spacing(hits, clearance_radius, accepted_hits):
                rejected_spacing += 1
                continue

            accepted_axes.append(axis_line)
            accepted_hits.extend(hits)
            active_protected.extend(
                _clearance_footprint_curves(
                    axis_line, clearance_radius, input_brep, tolerance
                )
            )

    elif strategy == "random":
        rng = _random_mod.Random(seed)
        cl_len = curve.GetLength()
        attempts = 0
        log(f"Random: max_holes={max_holes}, "
            f"max_attempts={max_attempts} (seed={seed})")

        while attempts < max_attempts:
            if max_holes is not None and len(accepted_axes) >= max_holes:
                log(f"Random: reached target of {max_holes} holes")
                break

            attempts += 1

            # Generate one random candidate on the fly
            dist = rng.uniform(end_margin, cl_len - end_margin)
            angle = rng.uniform(0, 2 * math.pi)
            success, t = curve.LengthParameter(dist)
            if not success:
                continue
            pt, tan, dor, lat = _perp_frame(curve, t)
            radial = _radial_direction(dor, lat, angle)

            axis_start = pt - radial * half_len
            axis_end = pt + radial * half_len
            axis_line = Line(axis_start, axis_end)

            hits = _find_surface_hits(axis_line, input_brep, tolerance)
            if not hits:
                rejected_no_hit += 1
                continue

            if not _check_protected_clearance(axis_line, clearance_radius,
                                               active_protected, tolerance):
                rejected_protected += 1
                continue

            if not _check_hole_spacing(hits, clearance_radius, accepted_hits):
                rejected_spacing += 1
                continue

            accepted_axes.append(axis_line)
            accepted_hits.extend(hits)
            active_protected.extend(
                _clearance_footprint_curves(
                    axis_line, clearance_radius, input_brep, tolerance
                )
            )

    else:
        raise ValueError(
            f"Unknown strategy '{strategy}'. Use 'helical' or 'random'."
        )

    log(f"Accepted: {len(accepted_axes)} holes")
    log(f"Rejected: {rejected_no_hit} no-hit, "
        f"{rejected_protected} protected, {rejected_spacing} spacing")
    if strategy == "random":
        log(f"Random attempts used: {attempts}/{max_attempts}")

    if not accepted_axes:
        elapsed = time.time() - t0
        log(f"No valid holes, returning original brep ({elapsed:.3f}s)")
        return input_brep, 0

    # Build cutting cylinders
    log(f"Creating {len(accepted_axes)} cutting cylinders...")
    cutters = []
    for axis in accepted_axes:
        direction = Vector3d(axis.Direction)
        direction.Unitize()
        base_plane = Plane(axis.From, direction)
        cyl = rg.Cylinder(rg.Circle(base_plane, hole_radius), axis.Length)
        cyl_brep = cyl.ToBrep(True, True)
        if cyl_brep:
            cutters.append(cyl_brep)

    log(f"Built {len(cutters)} cutter breps")

    # Batch boolean difference (all cutters at once)
    log("Subtracting holes (batch)...")
    result = rg.Brep.CreateBooleanDifference([input_brep], cutters, tolerance)

    if result and len(result) > 0:
        ventilated = max(result, key=lambda b: b.GetVolume())
        elapsed = time.time() - t0
        vol_before = input_brep.GetVolume()
        vol_after = ventilated.GetVolume()
        log(f"Batch OK: {len(cutters)} holes in {elapsed:.3f}s")
        log(f"Volume: {vol_before:.1f} -> {vol_after:.1f} mm^3 "
            f"(removed {vol_before - vol_after:.1f})")
        return ventilated, len(cutters)

    # Batch failed -- sequential fallback
    log("Batch failed, trying sequential subtraction...")
    ventilated = input_brep
    holes_cut = 0
    for i, cutter in enumerate(cutters):
        res = rg.Brep.CreateBooleanDifference(ventilated, cutter, tolerance)
        if res and len(res) > 0:
            ventilated = max(res, key=lambda b: b.GetVolume())
            holes_cut += 1
        else:
            log(f"  Hole {i} failed, skipping")

    elapsed = time.time() - t0
    vol_before = input_brep.GetVolume()
    vol_after = ventilated.GetVolume()
    log(f"Sequential: {holes_cut}/{len(cutters)} holes in {elapsed:.3f}s")
    log(f"Volume: {vol_before:.1f} -> {vol_after:.1f} mm^3 "
        f"(removed {vol_before - vol_after:.1f})")
    return ventilated, holes_cut
