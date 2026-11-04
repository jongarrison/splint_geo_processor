"""
Orienteering.py

Additive splint features that help the wearer orient the splint on their finger (which way is
volar/distal). All functions here graft a small, unambiguous tactile/visual landmark onto a
finished splint solid.

positive_sphere_direction_indicator is a pure-Python port of the "Direction Indicator"
Grasshopper definition (generators/InfinityFlex.gh family). It drops a small protruding
partial sphere ("nub") onto the splint surface to mark the volar/distal orientation:

  1. Extend the reference band curve slightly past both ends (north_star_nudge_mm), take its
     two endpoints, and project each onto the splint surface -> two on-surface points.
  2. The nub sits at the point evaluated along the line between those two surface points
     (nub_position_param, 0.5 = midpoint).
  3. Nub radius = wall_thickness_mm * ball_r_to_wall_ratio, so the ball scales with the wall.
  4. Nudge the sphere along the local outward surface normal by nub_sinking_factor * radius,
     then boolean-union it into the splint. With the center on the surface this leaves roughly
     a hemisphere protruding.

Debug observability follows the RingSlit/SupportPathRamp out-parameter pattern: pass debug={}
to receive every intermediate construction artifact, which survives whether the call succeeds
or raises.
"""

import scriptcontext as sc
from Rhino.Geometry import (Point3d, Vector3d, LineCurve, Sphere, CurveEnd,
                            CurveExtensionStyle)
from splintcommon import log

import BrepUnion
from BrepUnion import robust_brep_union, BrepUnionError, InvalidBrepError


class OrienteeringError(Exception):
    """Raised when a direction-indicator feature cannot be constructed or grafted. Construction
    geometry is still exposed via the optional `debug` dict, which survives the raise."""
    pass


# Distance (mm) to probe off the surface along the closest-point normal when deciding which way
# is outward. Small but well above document tolerance.
_NORMAL_PROBE_MM = 0.05


def _dput(debug, key, value):
    """Populate a key on the optional debug out-dict; no-op if debug is None."""
    if debug is not None:
        debug[key] = value


def _outward_normal_at(brep, test_point, tolerance):
    """Closest point on `brep` to `test_point` plus the surface normal there, flipped so it
    points OUT of the solid. Returns (closest_point, outward_normal) or (None, None) if the
    projection fails. Face normals do not reliably point outward on a joined solid, so the
    direction is resolved by an inside/outside test a short step along the normal."""
    ok, closest, _ci, _s, _t, normal = brep.ClosestPoint(test_point, 0.0)
    if not ok:
        return None, None
    normal.Unitize()
    probe = Point3d(closest.X + normal.X * _NORMAL_PROBE_MM,
                    closest.Y + normal.Y * _NORMAL_PROBE_MM,
                    closest.Z + normal.Z * _NORMAL_PROBE_MM)
    # If a step along the normal lands inside the solid, the normal points inward -> flip it.
    if brep.IsPointInside(probe, tolerance, False):
        normal.Reverse()
    return closest, normal


def positive_sphere_direction_indicator(splint_brep, tip_top_band_curve, wall_thickness_mm,
                                        ball_r_to_wall_ratio=0.65,
                                        nub_position_param=0.5,
                                        nub_sinking_factor=0.1,
                                        north_star_nudge_mm=5.0,
                                        tolerance=None,
                                        debug=None):
    """Union a small protruding partial sphere onto the splint to indicate its volar/distal
    orientation.

    Args:
        splint_brep (rg.Brep): finished splint solid. Not mutated; a new unioned Brep is
            returned.
        tip_top_band_curve (rg.Curve): reference curve near the splint tip whose span sets
            where the nub lands (its extended endpoints project onto the surface).
        wall_thickness_mm (float): splint wall thickness; scales the nub radius. Must be > 0.
        ball_r_to_wall_ratio (float): nub_radius = wall_thickness_mm * this. Default 0.65 keeps
            the embedded half of the ball inside the wall.
        nub_position_param (float): 0..1 position along the line between the two projected
            surface points where the nub sits. 0.5 = midpoint.
        nub_sinking_factor (float): fraction of the nub radius to shift the sphere along the
            OUTWARD surface normal. Positive nudges the nub further out (more protrusion);
            negative sinks it deeper. The reference .gh used a positive value for a slightly
            fuller-than-hemisphere bump.
        north_star_nudge_mm (float): distance to extend the band curve past each end before
            taking its endpoints, nudging the projected points outward along the curve.
        tolerance (float): boolean/containment tolerance; None uses the document tolerance.
        debug (dict): optional out-dict populated with construction geometry.

    Returns:
        rg.Brep: the splint unioned with the nub.

    Raises:
        OrienteeringError: on invalid inputs or if any construction/union step fails.
    """
    if splint_brep is None:
        raise OrienteeringError("splint_brep is None.")
    if tip_top_band_curve is None:
        raise OrienteeringError("tip_top_band_curve is None.")
    if wall_thickness_mm is None or wall_thickness_mm <= 0.0:
        raise OrienteeringError(
            "wall_thickness_mm must be > 0 (got {0}).".format(wall_thickness_mm))

    tol = tolerance if (tolerance is not None and tolerance > 0.0) else sc.doc.ModelAbsoluteTolerance

    # --- 1. Extend the band curve past both ends, then take its endpoints ---------------------
    # A short linear nudge is enough here: the endpoints get reprojected onto the brep, so the
    # extension style barely matters.
    extended = tip_top_band_curve.Extend(
        CurveEnd.Both, north_star_nudge_mm, CurveExtensionStyle.Line)
    if extended is None:
        log("positive_sphere_direction_indicator: curve extend failed; using original curve")
        extended = tip_top_band_curve
    _dput(debug, "extended_band_curve", extended)

    end_a = extended.PointAtStart
    end_b = extended.PointAtEnd

    # --- 2. Project each endpoint onto the splint surface ------------------------------------
    proj_a, _n_a = _outward_normal_at(splint_brep, end_a, tol)
    proj_b, _n_b = _outward_normal_at(splint_brep, end_b, tol)
    if proj_a is None or proj_b is None:
        raise OrienteeringError("could not project band-curve endpoints onto the splint.")
    _dput(debug, "proj_point_start", proj_a)
    _dput(debug, "proj_point_end", proj_b)

    # --- 3. Nub sits along the line between the two projected points -------------------------
    placement_line = LineCurve(proj_a, proj_b)
    _dput(debug, "placement_line", placement_line)
    nub_point = placement_line.PointAtNormalizedLength(nub_position_param)

    # Outward normal at the placement point drives the sink/protrusion nudge.
    surface_point, outward_normal = _outward_normal_at(splint_brep, nub_point, tol)
    if surface_point is None:
        raise OrienteeringError("could not find the surface normal at the nub location.")

    # --- 4. Size, place, and build the sphere ------------------------------------------------
    nub_radius = wall_thickness_mm * ball_r_to_wall_ratio
    _dput(debug, "nub_radius", nub_radius)

    nudge = nub_radius * nub_sinking_factor
    nub_center = Point3d(nub_point.X + outward_normal.X * nudge,
                         nub_point.Y + outward_normal.Y * nudge,
                         nub_point.Z + outward_normal.Z * nudge)
    _dput(debug, "nub_center", nub_center)
    _dput(debug, "nub_normal_line",
          LineCurve(surface_point, Point3d(surface_point.X + outward_normal.X * nub_radius,
                                           surface_point.Y + outward_normal.Y * nub_radius,
                                           surface_point.Z + outward_normal.Z * nub_radius)))

    nub_sphere = Sphere(nub_center, nub_radius).ToBrep()
    if nub_sphere is None or not nub_sphere.IsValid:
        raise OrienteeringError("failed to build a valid nub sphere brep.")
    _dput(debug, "nub_sphere", nub_sphere)

    # --- 5. Union the nub into the splint ----------------------------------------------------
    try:
        result, success, method = robust_brep_union([splint_brep, nub_sphere], tol)
    except (BrepUnionError, InvalidBrepError) as exc:
        raise OrienteeringError("nub union failed: {0}".format(exc))
    if not success or result is None:
        raise OrienteeringError("nub union did not produce a valid result.")

    log("positive_sphere_direction_indicator: nub (r={0:.2f}mm) unioned via {1}".format(
        nub_radius, method))
    _dput(debug, "result", result)
    return result
