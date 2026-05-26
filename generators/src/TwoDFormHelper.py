"""
TwoDFormHelper.py
Helpers for constructing 2D perimeter curves around simple primitives
(circles, etc.) in a shared plane.
"""

import Rhino.Geometry as rg
import math


def _compute_tangent_offset(dist, r1, r2, hg):
    """Solve for the perpendicular offset y of the externally-tangent
    construction circles whose centers lie on the perpendicular bisector
    of c1-c2 (well, on the perp through `base`).

    Returns (x, y) where x is offset along the c1->c2 axis from c1, and y
    is the perpendicular offset to each tangent circle center. Returns
    (None, None) if no real solution exists.
    """
    R1 = r1 + hg
    R2 = r2 + hg
    x = (dist * dist + R1 * R1 - R2 * R2) / (2.0 * dist)
    y_sq = R1 * R1 - x * x
    if y_sq < 0:
        return None, None
    return x, math.sqrt(y_sq)


def _isthmus_width_for_hg(dist, r1, r2, hg):
    """Resulting isthmus width given a hypothetical hourglass radius.
    Returns None if no tangent solution exists at this hg."""
    _, y = _compute_tangent_offset(dist, r1, r2, hg)
    if y is None:
        return None
    return 2.0 * (y - hg)


def _solve_hourglass_r_for_min_isthmus(dist, r1, r2, hg_start, min_isthmus_width,
                                       max_iter=60, tol=1e-5):
    """Find the smallest hourglass_r >= hg_start such that the resulting
    isthmus width >= min_isthmus_width. Uses bisection; isthmus_width is
    monotonically non-decreasing in hg for valid configurations.
    Returns the adjusted hg, or None if no solution can be found.
    """
    lo = hg_start
    w_lo = _isthmus_width_for_hg(dist, r1, r2, lo)
    if w_lo is not None and w_lo >= min_isthmus_width:
        return lo  # input already satisfies the constraint

    # Bracket: grow hi until the constraint is met (or we give up)
    hi = max(hg_start, 1.0)
    for _ in range(max_iter):
        w_hi = _isthmus_width_for_hg(dist, r1, r2, hi)
        if w_hi is not None and w_hi >= min_isthmus_width:
            break
        hi *= 2.0
    else:
        return None

    # Bisect
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        w_mid = _isthmus_width_for_hg(dist, r1, r2, mid)
        if w_mid is None or w_mid < min_isthmus_width:
            lo = mid
        else:
            hi = mid
        if (hi - lo) < tol:
            break
    return hi


def _tangent_pt(circ_center, circ_r, tang_center):
    """Point on a circle (center, radius) along the ray toward `tang_center`."""
    v = tang_center - circ_center
    v.Unitize()
    return circ_center + v * circ_r


def _build_pure_arc_hourglass(c1, c2, r1, r2, dist, axis, perp, hg, tolerance, verbose):
    """Original 4-arc hourglass build at a given hourglass_r `hg`.
    Returns the joined curve (or list of arcs / None) and the resulting
    isthmus width.
    """
    x, y = _compute_tangent_offset(dist, r1, r2, hg)
    if y is None:
        if verbose:
            print("ERROR: no tangent circles exist at hourglass_r={:.3f}".format(hg))
        return None, None

    base = c1 + axis * x
    t1_center = base + perp * y
    t2_center = base - perp * y

    p_c1_t1 = _tangent_pt(c1, r1, t1_center)
    p_c1_t2 = _tangent_pt(c1, r1, t2_center)
    p_c2_t1 = _tangent_pt(c2, r2, t1_center)
    p_c2_t2 = _tangent_pt(c2, r2, t2_center)

    c1_far = c1 - axis * r1
    c2_far = c2 + axis * r2
    t1_inner = t1_center - perp * hg
    t2_inner = t2_center + perp * hg

    arc_c1 = rg.Arc(p_c1_t2, c1_far, p_c1_t1)
    arc_t1 = rg.Arc(p_c1_t1, t1_inner, p_c2_t1)
    arc_c2 = rg.Arc(p_c2_t1, c2_far, p_c2_t2)
    arc_t2 = rg.Arc(p_c2_t2, t2_inner, p_c1_t2)

    curves = [
        rg.ArcCurve(arc_c1),
        rg.ArcCurve(arc_t1),
        rg.ArcCurve(arc_c2),
        rg.ArcCurve(arc_t2),
    ]

    isthmus_w = 2.0 * (y - hg)
    joined = rg.Curve.JoinCurves(curves, tolerance)
    if joined and len(joined) > 0:
        result = joined[0]
        if verbose:
            print("Joined hourglass (pure arcs): closed={}, length={:.3f}, isthmus={:.3f}".format(
                result.IsClosed, result.GetLength(), isthmus_w))
        return result, isthmus_w

    if verbose:
        print("WARNING: JoinCurves failed; returning individual arcs.")
    return curves, isthmus_w


def _build_straight_bar_hourglass(c1, c2, r1, r2, dist, axis, perp, hg,
                                  bar_width, tolerance, verbose):
    """Build an hourglass perimeter where each waist is `arc -> straight bar
    -> arc`, preserving the requested `hg`. Both waist arcs remain radius
    `hg` and are tangent to a straight bar at perpendicular distance
    `bar_width / 2` from the c1-c2 axis.

    Returns (curve_or_list, achieved_isthmus_width) or (None, None) on
    geometric infeasibility.
    """
    half_w = 0.5 * bar_width
    # Arc centers sit at perpendicular distance (hg + half_w) from the axis.
    perp_off = hg + half_w
    R1 = r1 + hg
    R2 = r2 + hg

    # Axial offset of each arc center from its parent circle center,
    # solved from R^2 = x_axial^2 + perp_off^2.
    sq1 = R1 * R1 - perp_off * perp_off
    sq2 = R2 * R2 - perp_off * perp_off
    if sq1 < 0 or sq2 < 0:
        if verbose:
            print("Straight-bar mode infeasible: perp_off={:.3f} exceeds R1 or R2".format(
                perp_off))
        return None, None
    x1 = math.sqrt(sq1)
    x2 = math.sqrt(sq2)

    # The two top arc centers must leave room for a positive-length bar:
    # axial position of c1-side arc center = x1; c2-side = dist - x2.
    bar_len = dist - x2 - x1
    if bar_len <= 0:
        if verbose:
            print("Straight-bar mode infeasible: bar length {:.3f} <= 0".format(bar_len))
        return None, None

    # Also reject if the tangent point on each input circle would leave the
    # circle (i.e. half_w > r). Geometrically this is implied by sq >= 0 only
    # when hg >= 0, but check directly for clarity.
    if half_w > r1 or half_w > r2:
        if verbose:
            print("Straight-bar mode infeasible: half_width {:.3f} exceeds a circle radius".format(
                half_w))
        return None, None

    # Top-side (perp positive) construction.
    top_arc1_center = c1 + axis * x1 + perp * perp_off
    top_arc2_center = c2 - axis * x2 + perp * perp_off
    # Tangent points on the input circles (ray from circle center toward arc center).
    p_c1_top = _tangent_pt(c1, r1, top_arc1_center)
    p_c2_top = _tangent_pt(c2, r2, top_arc2_center)
    # Tangent points on the straight bar (directly "below" each arc center).
    bar_top_left = top_arc1_center - perp * hg
    bar_top_right = top_arc2_center - perp * hg
    # Mid points for 3-pt arc construction (on the arc, on the far side from
    # the bar/circle chord). The arc is small, so the midpoint of the arc is
    # roughly the midpoint of the two endpoints projected outward to radius hg.
    def _arc_midpoint(center, p_a, p_b, radius):
        mid_chord = (p_a + p_b) * 0.5
        v = mid_chord - center
        v.Unitize()
        return center + v * radius

    top_arc1_mid = _arc_midpoint(top_arc1_center, p_c1_top, bar_top_left, hg)
    top_arc2_mid = _arc_midpoint(top_arc2_center, p_c2_top, bar_top_right, hg)

    # Bottom-side mirrors.
    bot_arc1_center = c1 + axis * x1 - perp * perp_off
    bot_arc2_center = c2 - axis * x2 - perp * perp_off
    p_c1_bot = _tangent_pt(c1, r1, bot_arc1_center)
    p_c2_bot = _tangent_pt(c2, r2, bot_arc2_center)
    bar_bot_left = bot_arc1_center + perp * hg
    bar_bot_right = bot_arc2_center + perp * hg
    bot_arc1_mid = _arc_midpoint(bot_arc1_center, p_c1_bot, bar_bot_left, hg)
    bot_arc2_mid = _arc_midpoint(bot_arc2_center, p_c2_bot, bar_bot_right, hg)

    # Outer circle arcs (far side of each input circle).
    c1_far = c1 - axis * r1
    c2_far = c2 + axis * r2
    arc_c1 = rg.Arc(p_c1_bot, c1_far, p_c1_top)   # walk around c1 outer
    arc_c2 = rg.Arc(p_c2_top, c2_far, p_c2_bot)   # walk around c2 outer

    # Waist arcs (small, concave toward axis).
    arc_top_left = rg.Arc(p_c1_top, top_arc1_mid, bar_top_left)
    arc_top_right = rg.Arc(bar_top_right, top_arc2_mid, p_c2_top)
    arc_bot_right = rg.Arc(p_c2_bot, bot_arc2_mid, bar_bot_right)
    arc_bot_left = rg.Arc(bar_bot_left, bot_arc1_mid, p_c1_bot)

    # Straight bars.
    line_top = rg.LineCurve(bar_top_left, bar_top_right)
    line_bot = rg.LineCurve(bar_bot_right, bar_bot_left)

    # Walk: c1 outer -> top waist left -> top bar -> top waist right
    #     -> c2 outer -> bot waist right -> bot bar -> bot waist left -> back
    curves = [
        rg.ArcCurve(arc_c1),
        rg.ArcCurve(arc_top_left),
        line_top,
        rg.ArcCurve(arc_top_right),
        rg.ArcCurve(arc_c2),
        rg.ArcCurve(arc_bot_right),
        line_bot,
        rg.ArcCurve(arc_bot_left),
    ]

    joined = rg.Curve.JoinCurves(curves, tolerance)
    if joined and len(joined) > 0:
        result = joined[0]
        if verbose:
            print("Joined hourglass (straight bar): closed={}, length={:.3f}, "
                  "bar_len={:.3f}, isthmus={:.3f}".format(
                      result.IsClosed, result.GetLength(), bar_len, bar_width))
        return result, bar_width

    if verbose:
        print("WARNING: JoinCurves failed; returning individual segments.")
    return curves, bar_width


def create_two_circle_hourglass_bridge_perimeter(circle1, circle2, hourglass_r,
                                                 min_isthmus_width=0.0,
                                                 attempt_preserve_hourglass_r=False,
                                                 tolerance=0.01,
                                                 verbose=False):
    """Create a closed hourglass-shaped perimeter curve around two co-planar,
    non-overlapping circles.

    Two modes for enforcing `min_isthmus_width`:

    * `attempt_preserve_hourglass_r=False` (default): grow `hourglass_r` via
      bisection until the natural arc-to-arc gap at the waist meets
      `min_isthmus_width`. The waist remains two pure arcs.

    * `attempt_preserve_hourglass_r=True`: keep the requested `hourglass_r`
      and instead form each waist from two small arcs joined by a straight
      bar of width `min_isthmus_width`. If this construction is
      geometrically infeasible (e.g. `min_isthmus_width / 2` exceeds a
      circle radius, or the circles are too close to fit a positive-length
      bar), falls back to the bisection mode.

    Args:
        circle1, circle2: Rhino.Geometry.Circle, co-planar.
        hourglass_r: Requested radius for the concave waist arcs.
        min_isthmus_width: Minimum gap between the two waist regions at the
            isthmus. Ignored when 0.
        attempt_preserve_hourglass_r: Mode selector, see above.
        tolerance: Join tolerance passed to Curve.JoinCurves.
        verbose: If True, print diagnostic info.

    Returns:
        A single closed Curve on success, a list of segment curves if
        joining fails, or None if no valid configuration can be built.
    """
    c1 = circle1.Center
    c2 = circle2.Center
    r1 = circle1.Radius
    r2 = circle2.Radius

    axis = c2 - c1
    dist = axis.Length
    if dist <= 0:
        if verbose:
            print("ERROR: coincident circle centers.")
        return None
    axis.Unitize()

    normal = circle1.Plane.ZAxis
    perp = rg.Vector3d.CrossProduct(normal, axis)
    perp.Unitize()

    if verbose:
        print("C1 r={:.3f}, C2 r={:.3f}, dist={:.3f}, hourglass_r={:.3f}, "
              "min_isthmus_width={:.3f}, preserve_hg={}".format(
                  r1, r2, dist, hourglass_r, min_isthmus_width,
                  attempt_preserve_hourglass_r))

    hg = hourglass_r

    # Decide whether we even need to engage min_isthmus_width logic.
    natural_isthmus = _isthmus_width_for_hg(dist, r1, r2, hg)
    needs_intervention = (min_isthmus_width > 0.0 and
                          (natural_isthmus is None or
                           natural_isthmus < min_isthmus_width))

    if needs_intervention and attempt_preserve_hourglass_r:
        result, _ = _build_straight_bar_hourglass(
            c1, c2, r1, r2, dist, axis, perp, hg,
            min_isthmus_width, tolerance, verbose)
        if result is not None:
            return result
        if verbose:
            print("Falling back to bisection mode.")

    if needs_intervention:
        adjusted = _solve_hourglass_r_for_min_isthmus(
            dist, r1, r2, hg, min_isthmus_width)
        if adjusted is None:
            if verbose:
                print("ERROR: could not satisfy min_isthmus_width={:.3f}".format(
                    min_isthmus_width))
            return None
        if adjusted > hg and verbose:
            print("Adjusted hourglass_r {:.3f} -> {:.3f} to meet min_isthmus_width={:.3f}".format(
                hg, adjusted, min_isthmus_width))
        hg = adjusted

    result, _ = _build_pure_arc_hourglass(
        c1, c2, r1, r2, dist, axis, perp, hg, tolerance, verbose)
    return result
