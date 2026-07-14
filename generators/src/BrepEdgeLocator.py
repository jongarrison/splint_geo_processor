"""Locate the Brep edges that correspond to a known construction curve.

The native Rhino fillet/chamfer methods (Brep.CreateFilletEdges,
Brep.CreateFilletEdgesVariableRadius, etc.) all take EDGE INDICES into brep.Edges. But what we
usually own during splint construction are the CURVES we built the solid from (bore rims, walk
segments, offset rails, etc.). This module is the bridge: given a construction curve, find the
brep edge(s) whose geometry coincides with it.

Strategy (fast-then-strict):
    1. Midpoint filter: for each brep edge, compute distance from its midpoint to the target
       curve (Curve.ClosestPoint). Reject if the gap exceeds the tolerance. This kills most
       candidates in O(1) per edge, so noisy solids (embossed text can add 200+ short edges) stay
       cheap to search.
    2. Endpoint check: for surviving candidates, also verify the edge's start AND end points sit
       on the target curve. Requires the whole edge to lie on the curve, not just cross it.
    3. Length-based coverage classification: compare edge length to target curve length so
       callers can tell 'one edge covers the whole curve' from 'this edge is a fragment of it'.

Coverage semantics returned by find_edges_for_curve:
    * 'exact'   - one edge, its length matches the target curve length within length_tol.
    * 'partial' - one or more edges whose combined length is less than the target's length.
    * 'over'    - matched edges' combined length exceeds the target length (unexpected, worth
                  investigating; usually means the target and the edge overlap only partially and
                  the target extends beyond the edge).
    * 'none'    - no edges matched within the point tolerance.

Endpoint sharing note: adjacent brep edges share endpoint vertices. That's fine for our purposes
because we require the WHOLE edge (mid + start + end) to sit on the target curve; a neighbor edge
that merely touches the target at one shared endpoint fails the midpoint test.
"""

import Rhino.Geometry as rg


DEFAULT_POINT_TOL_MM = 0.10
DEFAULT_LENGTH_TOL_MM = 0.10


class EdgeMatch(object):
    """One brep edge that lies on a target curve.

    Attributes:
        edge_index: index into brep.Edges.
        gap: worst-case point-to-curve distance among (start, mid, end) samples, in mm.
        edge_length: length of the brep edge, in mm.
        target_t_start / target_t_mid / target_t_end: parameter on the target curve where the
            edge's start / midpoint / end project. Useful for placing variable-radius handles or
            for detecting the coverage span on the target.
    """
    __slots__ = ("edge_index", "gap", "edge_length",
                 "target_t_start", "target_t_mid", "target_t_end")

    def __init__(self, edge_index, gap, edge_length,
                 target_t_start, target_t_mid, target_t_end):
        self.edge_index = edge_index
        self.gap = gap
        self.edge_length = edge_length
        self.target_t_start = target_t_start
        self.target_t_mid = target_t_mid
        self.target_t_end = target_t_end

    def __repr__(self):
        return ("EdgeMatch(index={0}, gap={1:.4f}mm, len={2:.2f}mm, "
                "t=[{3:.3f}..{4:.3f}])".format(
                    self.edge_index, self.gap, self.edge_length,
                    self.target_t_start, self.target_t_end))


class EdgeMatchResult(object):
    """Result of matching a target curve to a brep's edges."""
    __slots__ = ("matches", "coverage", "target_length", "matched_length")

    def __init__(self, matches, coverage, target_length, matched_length):
        self.matches = matches                       # list[EdgeMatch] sorted by target_t_start
        self.coverage = coverage                     # 'exact' | 'partial' | 'over' | 'none'
        self.target_length = target_length
        self.matched_length = matched_length

    @property
    def edge_indices(self):
        return [m.edge_index for m in self.matches]

    def __repr__(self):
        return ("EdgeMatchResult(coverage={0!r}, edges={1}, "
                "target_len={2:.2f}mm, matched_len={3:.2f}mm)".format(
                    self.coverage, self.edge_indices, self.target_length, self.matched_length))


def _project(target_curve, point):
    """Return (ok, target_parameter, gap_mm) for a point vs the target curve."""
    ok, t = target_curve.ClosestPoint(point)
    if not ok:
        return False, 0.0, float("inf")
    return True, t, point.DistanceTo(target_curve.PointAt(t))


def find_edges_for_curve(brep, target_curve,
                         point_tol=DEFAULT_POINT_TOL_MM,
                         length_tol=DEFAULT_LENGTH_TOL_MM):
    """Locate every brep edge that lies on target_curve.

    Args:
        brep: rg.Brep to search.
        target_curve: rg.Curve to match against (open or closed).
        point_tol: max allowed distance (mm) from any of an edge's (start, mid, end) samples to
            the target curve for that edge to count as a match.
        length_tol: tolerance for classifying coverage as 'exact' (single match whose length
            equals target length within this).

    Returns:
        EdgeMatchResult. Matches are sorted by their start parameter on the target curve so the
        list reads in order along the curve (natural for placing variable-radius fillet handles).
    """
    matches = []
    n = brep.Edges.Count
    for i in range(n):
        edge = brep.Edges[i]
        mid = edge.PointAtNormalizedLength(0.5)

        # Step 1: midpoint filter (fast reject).
        ok_m, tm, gap_m = _project(target_curve, mid)
        if not ok_m or gap_m > point_tol:
            continue

        # Step 2: endpoint check. Adjacent edges share vertices; requiring the whole edge to sit
        # on the target curve keeps neighbors that only touch at one vertex from false-matching.
        ok_s, ts, gap_s = _project(target_curve, edge.PointAtStart)
        if not ok_s or gap_s > point_tol:
            continue
        ok_e, te, gap_e = _project(target_curve, edge.PointAtEnd)
        if not ok_e or gap_e > point_tol:
            continue

        worst = max(gap_m, gap_s, gap_e)
        matches.append(EdgeMatch(
            edge_index=i,
            gap=worst,
            edge_length=edge.GetLength(),
            target_t_start=ts,
            target_t_mid=tm,
            target_t_end=te,
        ))

    # Sort matches by where they START on the target curve (natural order along the target).
    matches.sort(key=lambda m: m.target_t_start)

    target_length = target_curve.GetLength()
    matched_length = sum(m.edge_length for m in matches)

    if not matches:
        coverage = "none"
    elif len(matches) == 1 and abs(matches[0].edge_length - target_length) <= length_tol:
        coverage = "exact"
    elif matched_length > target_length + length_tol:
        coverage = "over"
    else:
        coverage = "partial"

    return EdgeMatchResult(matches, coverage, target_length, matched_length)


def nearest_edge(brep, target_curve):
    """Diagnostic helper: return (edge_index, midpoint_gap_mm) for the edge whose midpoint is
    closest to target_curve. Used to explain a 'none' match ("nearest was edge 6 at 0.184mm off,
    tolerance was 0.10mm").
    """
    best_i, best_d = -1, float("inf")
    for i in range(brep.Edges.Count):
        mid = brep.Edges[i].PointAtNormalizedLength(0.5)
        ok, t = target_curve.ClosestPoint(mid)
        if not ok:
            continue
        d = mid.DistanceTo(target_curve.PointAt(t))
        if d < best_d:
            best_d = d
            best_i = i
    return best_i, best_d


class EdgeContainment(object):
    """A brep edge that CONTAINS the target curve as a sub-span.

    This is the reverse-direction match of EdgeMatch: instead of "edge lies on curve", this is
    "curve lies on edge". Needed when placing variable-radius handles on a long perimeter edge
    at parameters derived from short construction rails that only cover part of that edge.

    Attributes:
        edge_index: index into brep.Edges.
        gap: worst-case point-to-edge distance among (start, mid, end) samples of the target.
        edge_length: length of the containing brep edge, in mm.
        edge_t_start / edge_t_mid / edge_t_end: parameter on the EDGE where the target curve's
            start / midpoint / end project. These are what CreateFilletEdgesVariableRadius wants
            (its edgeParameter argument is a parameter on the brep edge).
        edge_arc_start / edge_arc_end: arc-length distance (mm) along the edge from its T0 to
            where the target starts / ends. Useful when the ramp width is defined in mm.
    """
    __slots__ = ("edge_index", "gap", "edge_length",
                 "edge_t_start", "edge_t_mid", "edge_t_end",
                 "edge_arc_start", "edge_arc_end")

    def __init__(self, edge_index, gap, edge_length,
                 edge_t_start, edge_t_mid, edge_t_end,
                 edge_arc_start, edge_arc_end):
        self.edge_index = edge_index
        self.gap = gap
        self.edge_length = edge_length
        self.edge_t_start = edge_t_start
        self.edge_t_mid = edge_t_mid
        self.edge_t_end = edge_t_end
        self.edge_arc_start = edge_arc_start
        self.edge_arc_end = edge_arc_end

    def __repr__(self):
        return ("EdgeContainment(index={0}, gap={1:.4f}mm, edge_len={2:.2f}mm, "
                "arc=[{3:.2f}..{4:.2f}]mm)".format(
                    self.edge_index, self.gap, self.edge_length,
                    self.edge_arc_start, self.edge_arc_end))


def _project_onto_edge(edge, point):
    """Return (ok, edge_parameter, gap_mm, arc_length_from_T0) for a point vs a brep edge."""
    ok, t = edge.ClosestPoint(point)
    if not ok:
        return False, 0.0, float("inf"), 0.0
    gap = point.DistanceTo(edge.PointAt(t))
    # Arc length from edge.Domain.T0 to t: sub-domain length.
    arc = edge.GetLength(rg.Interval(edge.Domain.T0, t))
    return True, t, gap, arc


def find_edge_containing_curve(brep, target_curve, point_tol=DEFAULT_POINT_TOL_MM):
    """Find the single brep edge that CONTAINS target_curve as a sub-span.

    The reverse of find_edges_for_curve: use this when target_curve is shorter than the edge it
    lies on (e.g. one 30mm support-path rail sitting inside a 184mm outer-perimeter edge loop).

    Strategy: for each brep edge, project target_curve's (start, mid, end) onto the edge; if all
    three project within tolerance, target lies on this edge. Return the containment with the
    best worst-case gap.

    Returns:
        EdgeContainment on success, or None if no edge fully contains the target within tolerance.
    """
    best = None
    n = brep.Edges.Count
    t_start_pt = target_curve.PointAtStart
    t_mid_pt = target_curve.PointAtNormalizedLength(0.5)
    t_end_pt = target_curve.PointAtEnd
    for i in range(n):
        edge = brep.Edges[i]
        ok_m, tm, gap_m, arc_m = _project_onto_edge(edge, t_mid_pt)
        if not ok_m or gap_m > point_tol:
            continue
        ok_s, ts, gap_s, arc_s = _project_onto_edge(edge, t_start_pt)
        if not ok_s or gap_s > point_tol:
            continue
        ok_e, te, gap_e, arc_e = _project_onto_edge(edge, t_end_pt)
        if not ok_e or gap_e > point_tol:
            continue
        worst = max(gap_m, gap_s, gap_e)
        cand = EdgeContainment(
            edge_index=i,
            gap=worst,
            edge_length=edge.GetLength(),
            edge_t_start=ts,
            edge_t_mid=tm,
            edge_t_end=te,
            edge_arc_start=arc_s,
            edge_arc_end=arc_e,
        )
        if best is None or worst < best.gap:
            best = cand
    return best


def nearest_containing_edge(brep, target_curve):
    """Diagnostic helper for find_edge_containing_curve failures: return (edge_index, worst_gap)
    for the edge that MINIMIZES the worst-case (start, mid, end) projection gap. Explains why a
    'no containing edge' failure happened - is target off by 0.11mm (bump tolerance) or 2mm
    (real geometry problem)? Also reports the individual gap components so you can see whether
    one endpoint is the outlier (T-junction) vs the whole rail being off (wrong edge)."""
    t_start_pt = target_curve.PointAtStart
    t_mid_pt = target_curve.PointAtNormalizedLength(0.5)
    t_end_pt = target_curve.PointAtEnd
    best = None  # (edge_index, worst_gap, gap_start, gap_mid, gap_end)
    for i in range(brep.Edges.Count):
        edge = brep.Edges[i]
        _, _, gap_s, _ = _project_onto_edge(edge, t_start_pt)
        _, _, gap_m, _ = _project_onto_edge(edge, t_mid_pt)
        _, _, gap_e, _ = _project_onto_edge(edge, t_end_pt)
        worst = max(gap_s, gap_m, gap_e)
        if best is None or worst < best[1]:
            best = (i, worst, gap_s, gap_m, gap_e)
    return best
