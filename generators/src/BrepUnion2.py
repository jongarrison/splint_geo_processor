"""
BrepUnion2.py

Simple, transparent boolean union wrapper. Does the obvious thing first
(Brep.CreateBooleanUnion at document tolerance) and raises cleanly on failure.

This replaces the over-engineered BrepUnion.py for new code. Existing older splint
designs still use BrepUnion.py and should migrate here when refactored.

Fallback strategies will be added incrementally only when real failures demand them
(same incremental spirit as BrepDifference.py's history).
"""

import Rhino.Geometry as rg
import scriptcontext as sc
from splintcommon import log


class BrepUnionError(Exception):
    """Raised when the boolean union fails."""
    pass


def robust_brep_union(breps, tolerance=None):
    """Union a list of breps into one solid.

    Args:
        breps: list of rg.Brep (at least 2, all must be valid closed solids).
        tolerance: float or None (defaults to doc model absolute tolerance).

    Returns:
        tuple: (result_brep, True, method_string) on success.

    Raises:
        BrepUnionError: union failed (returned None, empty, or multiple pieces).
        ValueError: fewer than 2 valid breps provided.
    """
    if not breps or len(breps) < 2:
        raise ValueError("robust_brep_union requires at least 2 breps (got {0})".format(
            0 if not breps else len(breps)))

    tol = tolerance
    if tol is None or tol <= 0.0:
        tol = sc.doc.ModelAbsoluteTolerance

    # Log input diagnostics
    for i, b in enumerate(breps):
        vol = b.GetVolume() if b is not None else 0
        log("BrepUnion2: input[{0}] IsValid={1} IsSolid={2} faces={3} vol={4:.1f}".format(
            i, b.IsValid if b else False, b.IsSolid if b else False,
            b.Faces.Count if b else 0, vol))

    # The obvious call - same thing Rhino's UI BooleanUnion does.
    result = rg.Brep.CreateBooleanUnion(breps, tol)

    if result is None or len(result) == 0:
        raise BrepUnionError(
            "Brep.CreateBooleanUnion returned nothing (tolerance={0})".format(tol))

    if len(result) == 1:
        log("BrepUnion2: SUCCESS - 1 result brep, faces={0} vol={1:.1f}".format(
            result[0].Faces.Count, result[0].GetVolume()))
        return result[0], True, "CreateBooleanUnion(tol={0})".format(tol)

    # Multiple results means the inputs didn't actually merge (returned as separate pieces).
    vols = [b.GetVolume() for b in result]
    log("BrepUnion2: FAILED - CreateBooleanUnion returned {0} separate pieces "
        "(volumes: {1}). Inputs likely don't volumetrically overlap.".format(
            len(result), ["%.1f" % v for v in vols]))
    raise BrepUnionError(
        "CreateBooleanUnion returned {0} separate breps instead of 1 merged solid "
        "(volumes: {1}) - insufficient volumetric overlap between inputs".format(
            len(result), ["%.1f" % v for v in vols]))
