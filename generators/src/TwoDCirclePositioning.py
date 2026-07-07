import math

def two_circle_positioning(c1, c2, full_perimeter, min_center_gap):
  # Inputs (from GH component)
  circumference_1 = c1  # larger circle circumference
  circumference_2 = c2  # smaller circle circumference
  belt_perimeter = full_perimeter    # convex hull perimeter measurement
  is_radius_reversed = False

  # Derive radii
  r1 = circumference_1 / (2 * math.pi)
  r2 = circumference_2 / (2 * math.pi)

  # Ensure r1 >= r2
  if r2 > r1:
      is_radius_reversed = True
      r1, r2 = r2, r1

  def hull_perimeter(d, r1, r2):
      """Convex hull perimeter of two disks with center distance d."""
      tangent_len = 2 * math.sqrt(d**2 - (r1 - r2)**2)
      arc_base = math.pi * (r1 + r2)
      arc_correction = 2 * (r1 - r2) * math.asin((r1 - r2) / d)
      return tangent_len + arc_base + arc_correction

  # Bisection solve for center-to-center distance
  d_lo = r1 + r2 + 1e-9  # circles just touching
  d_hi = belt_perimeter / 2.0
  tolerance = 1e-9

  for _ in range(200):
      d_mid = (d_lo + d_hi) / 2.0
      if hull_perimeter(d_mid, r1, r2) < belt_perimeter:
          d_lo = d_mid
      else:
          d_hi = d_mid
      if (d_hi - d_lo) < tolerance:
          break

  center_distance = (d_lo + d_hi) / 2.0
  gap = center_distance - r1 - r2

  if (gap < min_center_gap):
      gap = min_center_gap

  #Now unreverse the radius if necessary
  if is_radius_reversed:
      r1, r2 = r2, r1

  center_to_center = r1 + r2 + gap
  
  return center_to_center, gap, r1, r2


def multiple_circle_positioning(circumferences, full_perimeter, min_gap):
    """ 
    Imagine n number of circles with given circumferences each resting one side on a straight line
    The circles are spaced to have even gaps between them, with at least the minimum gap specified by min_gap.
    The full_perimeter parameter specifies the total perimeter available for positioning the circles.
    Returns:
    - center to center distance between first and last circle
    - list of center to center distances between adjacent circles
    - gap between adjacent circles (at least min_gap and only a single value because gaps between circles are evenly spaced)
    - list of radius for each circle.
    - length of the common bottom line that touches the tangent of each circle
    - list of distances from the start of that bottom line to each circle's tangent
      point (first is always 0, last equals the bottom line length).
    """
    # Derive radii from circumferences; preserve the given ordering.
    radii = [c / (2.0 * math.pi) for c in circumferences]
    n = len(radii)

    # Fewer than two circles: no gaps to solve for.
    if n < 2:
        return 0.0, [], min_gap, radii, 0.0, [0.0] * n

    def horizontal_spacings(gap):
        # Horizontal distance between adjacent centers when both circles rest on
        # the baseline y=0 and are separated edge-to-edge by `gap`. The Euclidean
        # center distance is D = r_i + r_{i+1} + gap; because the centers sit at
        # heights r_i and r_{i+1}, the horizontal span is sqrt(D^2 - (dr)^2).
        spans = []
        for i in range(n - 1):
            d = radii[i] + radii[i + 1] + gap
            dr = radii[i] - radii[i + 1]
            spans.append(math.sqrt(max(d * d - dr * dr, 0.0)))
        return spans

    def belt_perimeter(gap):
        # Convex-hull perimeter of the circles resting on a common baseline.
        # The bottom is one straight tangent segment (length = sum of horizontal
        # spacings); the top is the chain of upper external tangents (each also
        # equal in length to the horizontal spacing) joined by arcs. Every
        # transition is tangent, so the arc central angles sum to 2*pi.
        spans = horizontal_spacings(gap)

        # Upper-tangent contact angle (from +x axis) shared by each adjacent pair.
        phi = [2.0 * math.atan2(radii[i + 1] - radii[i], spans[i]) + math.pi / 2.0
               for i in range(n - 1)]

        # Leftmost circle wraps from its upper contact around the left down to the
        # baseline tangent point (angle -pi/2 == 3*pi/2); rightmost mirrors it.
        arc = radii[0] * (1.5 * math.pi - phi[0])
        arc += radii[-1] * (phi[-1] + math.pi / 2.0)
        # Interior circles contribute only a small upper wedge between their two
        # upper tangents; clamp guards against the rare swallowed-circle case.
        for i in range(1, n - 1):
            arc += radii[i] * max(phi[i - 1] - phi[i], 0.0)

        # Straight baseline + upper tangents both total sum(spans).
        return 2.0 * sum(spans) + arc

    # Solve for the even gap whose belt perimeter matches full_perimeter.
    if belt_perimeter(0.0) >= full_perimeter:
        # Circles already fill the belt when touching; fall back to the minimum.
        solved_gap = 0.0
    else:
        # Bracket an upper bound where the belt exceeds full_perimeter.
        g_lo = 0.0
        g_hi = max(1.0, min_gap)
        for _ in range(200):
            if belt_perimeter(g_hi) >= full_perimeter:
                break
            g_hi *= 2.0
        # Bisection (belt perimeter is monotonically increasing in gap).
        for _ in range(200):
            g_mid = 0.5 * (g_lo + g_hi)
            if belt_perimeter(g_mid) < full_perimeter:
                g_lo = g_mid
            else:
                g_hi = g_mid
            if (g_hi - g_lo) < 1e-9:
                break
        solved_gap = 0.5 * (g_lo + g_hi)

    gap = max(solved_gap, min_gap)

    # Placement metrics at the chosen gap.
    adjacent_center_distances = [radii[i] + radii[i + 1] + gap for i in range(n - 1)]
    spans = horizontal_spacings(gap)
    horizontal_span = sum(spans)
    # Tangent-point offset of each circle along the bottom line (cumulative
    # horizontal spacings): first is 0, last equals the bottom line length.
    tangent_offsets = [0.0]
    for h in spans:
        tangent_offsets.append(tangent_offsets[-1] + h)
    # Straight-line center-to-center distance between the first and last circles
    # (their centers sit at heights r_first and r_last above the baseline).
    first_last_center_distance = math.hypot(horizontal_span, radii[0] - radii[-1])

    return (first_last_center_distance, adjacent_center_distances, gap, radii,
            horizontal_span, tangent_offsets)
