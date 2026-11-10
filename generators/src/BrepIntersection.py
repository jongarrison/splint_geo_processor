"""Validated solid Boolean intersection with diagnostics and an explicit strategy boundary."""

import math
from typing import Optional

from Rhino import RhinoDoc
import Rhino.Geometry as rg
from splintcommon import log


class BrepIntersectionError(RuntimeError):
    """Raised when an intersection cannot be accepted as a nonempty solid result."""


def _solid_volume(brep: rg.Brep, label: str) -> float:
    """Log structural checks and require a finite, positive signed solid volume."""
    if brep is None:
        raise BrepIntersectionError("{0}: Brep is None".format(label))
    naked_edges = sum(edge.Valence == rg.EdgeAdjacency.Naked for edge in brep.Edges)
    log("{0}: valid={1}, solid={2}, manifold={3}, faces={4}, naked_edges={5}".format(
        label, brep.IsValid, brep.IsSolid, brep.IsManifold, brep.Faces.Count, naked_edges))
    if not brep.IsValid or not brep.IsSolid or not brep.IsManifold or naked_edges:
        raise BrepIntersectionError("{0}: invalid, open, or non-manifold solid".format(label))
    properties = rg.VolumeMassProperties.Compute(brep)
    if properties is None:
        raise BrepIntersectionError("{0}: volume computation failed".format(label))
    volume = properties.Volume
    properties.Dispose()
    log("{0}: signed volume={1:.9g} mm^3".format(label, volume))
    if not math.isfinite(volume) or volume <= 0.0:
        raise BrepIntersectionError("{0}: nonpositive or nonfinite signed volume".format(label))
    return volume


def attempt_boolean_intersection(
    brep_a: rg.Brep, brep_b: rg.Brep, tolerance: float,
) -> list[rg.Brep]:
    """Direct native strategy; preserve every returned piece and leave inputs untouched."""
    native_result = rg.Brep.CreateBooleanIntersection(
        brep_a.DuplicateBrep(), brep_b.DuplicateBrep(), tolerance)
    return list(native_result) if native_result is not None else []


def robust_brep_intersection(
    brep_a: rg.Brep,
    brep_b: rg.Brep,
    base_tolerance: Optional[float] = None,
) -> tuple[list[rg.Brep], bool, str]:
    """Return (all_pieces, True, method) only after accepting a nonempty intersection.

    First pass uses one native strategy; future fallbacks belong here and must pass the
    same validation. Empty output is deliberately an error, not proof of disjoint inputs.
    Structural, volume, bounds, and boundary-sample checks detect gross corruption but
    are not a mathematical proof of exact Boolean equivalence or completeness.
    """
    document = RhinoDoc.ActiveDoc
    tolerance = base_tolerance
    if tolerance is None:
        tolerance = document.ModelAbsoluteTolerance if document is not None else 0.001
    if not math.isfinite(tolerance) or tolerance <= 0.0:
        raise BrepIntersectionError("Intersection tolerance must be positive and finite")
    method = "Intersection(tol={0:.6g})".format(tolerance)
    log("ROBUST BOOLEAN INTERSECTION: {0}".format(method))
    try:
        volumes = [_solid_volume(brep_a, "Input A"), _solid_volume(brep_b, "Input B")]
        try:
            pieces = attempt_boolean_intersection(brep_a, brep_b, tolerance)
        except Exception as exc:
            raise BrepIntersectionError("{0}: Rhino raised {1}: {2}".format(
                method, type(exc).__name__, exc)) from exc
        log("{0}: returned {1} piece(s)".format(method, len(pieces)))
        if not pieces:
            raise BrepIntersectionError(
                "{0}: no solid pieces; inputs may be disjoint or Rhino may have failed".format(method))

        total_volume = sum(_solid_volume(piece, "Result[{0}]".format(index))
                           for index, piece in enumerate(pieces))
        volume_limit = min(volumes)
        log("Result total volume={0:.9g}; maximum input bound={1:.9g} mm^3".format(
            total_volume, volume_limit))
        if total_volume > volume_limit * 1.001:
            raise BrepIntersectionError("Intersection volume exceeds the smaller input by over 0.1%")

        containment_tolerance = 5.0 * tolerance
        for piece_index, piece in enumerate(pieces):
            bounds = piece.GetBoundingBox(True)
            samples = [vertex.Location for vertex in piece.Vertices]
            samples.extend(edge.PointAtNormalizedLength(0.5) for edge in piece.Edges)
            for label, source in (("A", brep_a), ("B", brep_b)):
                source_bounds = source.GetBoundingBox(True)
                source_bounds.Inflate(containment_tolerance)
                if not source_bounds.Contains(bounds):
                    raise BrepIntersectionError("Result[{0}] bounds escape input {1}".format(
                        piece_index, label))
                for sample in samples:
                    if source.IsPointInside(sample, containment_tolerance, False):
                        continue
                    nearest = source.ClosestPoint(sample)
                    if not nearest.IsValid or sample.DistanceTo(nearest) > containment_tolerance:
                        raise BrepIntersectionError("Result[{0}] boundary sample escapes input {1}".format(
                            piece_index, label))
        log("SUCCESS: {0}, {1} validated piece(s), total volume={2:.9g} mm^3".format(
            method, len(pieces), total_volume))
        return pieces, True, method
    except Exception as exc:
        log("FAILED: {0}: {1}".format(method, exc))
        if isinstance(exc, BrepIntersectionError):
            raise
        raise BrepIntersectionError("{0}: validation raised {1}: {2}".format(
            method, type(exc).__name__, exc)) from exc