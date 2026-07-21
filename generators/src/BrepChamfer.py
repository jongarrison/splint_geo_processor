"""
BrepChamfer.py
Thin wrapper over Rhino's native Brep.CreateFilletEdges with BlendType.Chamfer, the approach
proven out in dev/RelativeMotion/harness.py (2026-07-10 PROD CANDIDATE): flat bevels on a closed
solid, driven by edge indices resolved via BrepEdgeLocator. Uniform distance per call; run the
wrapper twice (rims, then perimeter) for the two-radius policy.

Why "chamfer" not "fillet": the flat bevel is more robust than a rolling-ball fillet at small
distances (<= 0.5 mm), simpler surface, and at 0.2 mm layer height the visual difference is
negligible. Rail policy is DistanceFromEdge to match how the Rhino ChamferEdge command behaves
(distance measured on each face from the edge, not a rolling-ball radius).

Fail loud: raises BrepChamferError on empty result, non-solid result, or invalid brep. The
pipeline's fail-loud contract means a chamfer regression must surface immediately, not silently
ship a corrupted solid.

Superseded modules: BrepEdgeRound.py (hand-built wedge cutter) - retired 2026-07-11 in favor of
this native path. BrepFillet.py (older CreateFilletEdges wrapper for BlendType.Fillet) still
lives in the tree but is not used by the RelativeMotion pipeline.
"""

import Rhino.Geometry as rg
import scriptcontext as sc
from splintcommon import log

# .NET generic containers - CreateFilletEdgesVariableRadius expects
#   IDictionary[int, IList[BrepEdgeFilletDistance]].
# .NET generics are invariant, so Dictionary<int, List<T>> is NOT IDictionary<int, IList<T>>.
# We declare the dict's value type as IList<T> and store concrete List<T> instances in it
# (that direction is legal because List<T> implements IList<T>).
from System.Collections.Generic import Dictionary, List, IList


class BrepChamferError(Exception):
    """Raised when a chamfer call fails to produce a valid closed solid."""
    pass


def chamfer_edges(brep, edge_indices, distance_mm, tolerance=None):
    """Chamfer the given edges of a closed solid brep with a uniform bevel distance.

    Args:
        brep: rg.Brep, a closed solid.
        edge_indices: iterable of int, indices into brep.Edges (typically resolved from
            construction curves via BrepEdgeLocator.find_edges_for_curve or
            find_edge_containing_curve).
        distance_mm: float, chamfer distance measured on each face from the edge. Uniform
            start=end along every edge in this call.
        tolerance: optional float; defaults to sc.doc.ModelAbsoluteTolerance.

    Returns:
        rg.Brep: the chamfered closed solid.

    Raises:
        BrepChamferError: on empty result, non-solid result, or invalid brep.
    """
    edges = [int(i) for i in edge_indices]
    if not edges:
        raise BrepChamferError("chamfer_edges: edge_indices was empty.")
    if tolerance is None:
        tolerance = sc.doc.ModelAbsoluteTolerance
    distance = float(distance_mm)
    starts = [distance] * len(edges)
    ends = [distance] * len(edges)

    try:
        results = rg.Brep.CreateFilletEdges(
            brep, edges, starts, ends,
            rg.BlendType.Chamfer, rg.RailType.DistanceFromEdge, tolerance)
    except Exception as exc:
        raise BrepChamferError(
            "chamfer_edges: CreateFilletEdges raised on edges {0} at d={1}mm: {2}".format(
                edges, distance, exc))

    if not results or len(results) == 0:
        raise BrepChamferError(
            "chamfer_edges: CreateFilletEdges returned no breps for edges {0} at d={1}mm.".format(
                edges, distance))

    result = results[0]
    if not result.IsSolid or not result.IsValid:
        raise BrepChamferError(
            "chamfer_edges: result not a valid closed solid (edges={0}, d={1}mm, "
            "IsSolid={2}, IsValid={3}).".format(edges, distance, result.IsSolid, result.IsValid))

    log("chamfer_edges: chamfered {0} edge(s) at d={1:.3f}mm".format(len(edges), distance))
    return result


def chamfer_edges_variable(brep, edge_handles, tolerance=None, angle_tolerance=None,
                            setback_fillets=False):
    """Variable-distance chamfer on one or more brep edges via CreateFilletEdgesVariableRadius.

    Each edge gets its own list of (edge_parameter, distance_mm) handles; Rhino linearly
    interpolates the chamfer distance between consecutive handles. Use this when the chamfer
    needs to taper along an edge - e.g. drop to near-zero at both ends of a support-perimeter
    rail so the chamfer strip does not run into the corners of an already-chamfered anchor rim.

    Args:
        brep: rg.Brep, a closed solid.
        edge_handles: dict of {edge_index: [(edge_param, distance_mm), ...]}. Handle list must
            span the edge parameter range you care about; handles are used verbatim, no clamping.
            For a closed edge, include matching handles at T0 and T1 so the ramp wraps cleanly.
        tolerance: optional float; defaults to sc.doc.ModelAbsoluteTolerance.
        angle_tolerance: optional float (radians); defaults to sc.doc.ModelAngleToleranceRadians.
        setback_fillets: whether Rhino should compute setback corners at vertex meets. Off by
            default (matches Rhino's Chamfer command behavior).

    Returns:
        rg.Brep: the chamfered closed solid.

    Raises:
        BrepChamferError: on empty input, empty result, non-solid result, or invalid brep.
    """
    if not edge_handles:
        raise BrepChamferError("chamfer_edges_variable: edge_handles was empty.")
    if tolerance is None:
        tolerance = sc.doc.ModelAbsoluteTolerance
    if angle_tolerance is None:
        angle_tolerance = sc.doc.ModelAngleToleranceRadians

    # Build the .NET generic dictionary expected by the API binding.
    edge_dist = Dictionary[int, IList[rg.BrepEdgeFilletDistance]]()
    edge_indices = []
    for edge_index, handles in edge_handles.items():
        if not handles:
            raise BrepChamferError(
                "chamfer_edges_variable: edge {0} has no handles.".format(edge_index))
        handle_list = List[rg.BrepEdgeFilletDistance]()
        for t, d in handles:
            handle_list.Add(rg.BrepEdgeFilletDistance(float(t), float(d)))
        edge_dist[int(edge_index)] = handle_list
        edge_indices.append(int(edge_index))

    try:
        results = rg.Brep.CreateFilletEdgesVariableRadius(
            brep, edge_indices, edge_dist,
            rg.BlendType.Chamfer, rg.RailType.DistanceFromEdge,
            setback_fillets, tolerance, angle_tolerance)
    except Exception as exc:
        raise BrepChamferError(
            "chamfer_edges_variable: CreateFilletEdgesVariableRadius raised on edges "
            "{0}: {1}".format(edge_indices, exc))

    if not results or len(results) == 0:
        raise BrepChamferError(
            "chamfer_edges_variable: CreateFilletEdgesVariableRadius returned no breps for "
            "edges {0}.".format(edge_indices))

    result = results[0]
    if not result.IsSolid or not result.IsValid:
        raise BrepChamferError(
            "chamfer_edges_variable: result not a valid closed solid (edges={0}, "
            "IsSolid={1}, IsValid={2}).".format(
                edge_indices, result.IsSolid, result.IsValid))

    log("chamfer_edges_variable: chamfered {0} edge(s) with variable distance".format(
        len(edge_indices)))
    return result
