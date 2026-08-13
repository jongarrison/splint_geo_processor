"""
BoreChamfer.py
Frustum-based bore rim chamfer: constructs an elliptical frustum from the bore's inner ring
curve and boolean-subtracts it to create a bevel. Topology-independent (does not rely on edge
indices), so adjacent anchor bores can be chamfered in any order without interference.
"""

import Rhino.Geometry as rg
from Rhino.Geometry import (Point3d, Vector3d, Plane, Curve, CurveOffsetCornerStyle,
                            Brep, LoftType, BrepSolidOrientation, AreaMassProperties)
from splintcommon import log

import BrepDifference
from BrepDifference import robust_brep_difference


class BoreChamferError(Exception):
    """Raised when the frustum chamfer cannot be constructed or subtracted."""
    pass


def _curve_plane(curve):
    """Derive the plane a closed planar curve lives in. Returns None if non-planar."""
    ok, plane = curve.TryGetPlane()
    if not ok:
        return None
    return plane


def _offset_curve(curve, plane, distance, outward):
    """Offset a closed planar curve by distance. outward=True picks the longer (enclosing)
    result, outward=False picks the shorter (enclosed) result."""
    input_len = curve.GetLength()
    best = None
    best_len = None
    for signed in (distance, -distance):
        pieces = curve.Offset(plane, signed, 1e-6, CurveOffsetCornerStyle.Round)
        if pieces is None or len(pieces) == 0:
            continue
        candidate = pieces[0]
        if len(pieces) > 1:
            joined = Curve.JoinCurves(pieces, 1e-2)
            if joined is None or len(joined) == 0:
                continue
            candidate = joined[0]
        if not candidate.IsClosed:
            continue
        clen = candidate.GetLength()
        if outward and clen <= input_len:
            continue
        if not outward and clen >= input_len:
            continue
        if best_len is None:
            best = candidate
            best_len = clen
        elif outward and clen > best_len:
            best = candidate
            best_len = clen
        elif not outward and clen < best_len:
            best = candidate
            best_len = clen
    return best


def chamfer_bore_rim(ring_curve, input_brep, push_dir, chamfer_dist,
                     nudge_dist=0.1, debug=None):
    """Chamfer a bore rim by subtracting an elliptical frustum.

    Args:
        ring_curve:  Closed planar curve of the bore's inner wall cross-section (the rim edge).
        input_brep:  Solid Brep to subtract from.
        push_dir:    Unit vector pointing INTO the bore from the face (bore centerline direction
                     from the face inward).
        chamfer_dist: Radial width of the bevel (how far it extends outward from the rim).
        nudge_dist:  Small offset to prevent coincident surfaces (default 0.1mm).
        debug:       Optional dict; populated with construction geometry for harness preview.

    Returns:
        The input_brep with the frustum chamfer subtracted.

    Raises:
        BoreChamferError if the frustum cannot be constructed or the boolean fails.
    """
    if debug is None:
        debug = {}

    plane = _curve_plane(ring_curve)
    if plane is None:
        raise BoreChamferError("ring_curve is not planar")

    # Outer ring (base of frustum): offset outward by chamfer_dist + nudge_dist.
    # Sits on the face plane, widened beyond the bore rim.
    outer_curve = _offset_curve(ring_curve, plane, chamfer_dist + nudge_dist, outward=True)
    if outer_curve is None:
        raise BoreChamferError("failed to offset ring_curve outward for frustum base")

    # Inner ring (top of frustum): offset inward by nudge_dist to avoid coincident bore wall.
    # Translated along push_dir by chamfer_dist + nudge_dist (the bevel depth).
    inner_curve = _offset_curve(ring_curve, plane, nudge_dist, outward=False)
    if inner_curve is None:
        raise BoreChamferError("failed to offset ring_curve inward for frustum top")

    # Translate the inner ring into the bore along push_dir.
    push = Vector3d(push_dir)
    push.Unitize()
    translate_dist = chamfer_dist + nudge_dist
    inner_curve.Translate(push * translate_dist)

    debug["frustum_outer_curve"] = outer_curve.DuplicateCurve()
    debug["frustum_inner_curve"] = inner_curve.DuplicateCurve()

    # Nudge the whole assembly back by nudge_dist so the base bites past the face.
    nudge_vec = push * (-nudge_dist)
    outer_curve.Translate(nudge_vec)
    inner_curve.Translate(nudge_vec)

    # Match directions for a clean loft (no twist).
    if not Curve.DoDirectionsMatch(outer_curve, inner_curve):
        inner_curve.Reverse()

    # Re-seam both to a deterministic geometric feature (extreme +Y point) so the loft
    # maps corresponding locations. Centroid-based seaming fails because both rings are
    # concentric and ClosestPoint lands at arbitrary angular positions.
    def _extreme_param(crv, direction):
        """Param maximizing dot(point, direction) by dense sampling."""
        dom = crv.Domain
        steps = 240
        span = dom.T1 - dom.T0
        best_t = dom.T0
        best_v = None
        for i in range(steps + 1):
            t = dom.T0 + span * (float(i) / steps)
            p = crv.PointAt(t)
            v = p.X * direction.X + p.Y * direction.Y + p.Z * direction.Z
            if best_v is None or v > best_v:
                best_v = v
                best_t = t
        return best_t

    seam_dir = Vector3d.YAxis
    outer_curve.ChangeClosedCurveSeam(_extreme_param(outer_curve, seam_dir))
    inner_curve.ChangeClosedCurveSeam(_extreme_param(inner_curve, seam_dir))

    # Rebuild both to matching control-point structure so the ruled loft maps
    # proportionally instead of twisting through mismatched knot vectors.
    max_len = max(outer_curve.GetLength(), inner_curve.GetLength())
    rebuild_pts = max(80, int(max_len))
    outer_rebuilt = outer_curve.Rebuild(rebuild_pts, 3, False)
    inner_rebuilt = inner_curve.Rebuild(rebuild_pts, 3, False)
    if outer_rebuilt is None or inner_rebuilt is None:
        raise BoreChamferError("Curve.Rebuild failed (outer={0}, inner={1})".format(
            outer_rebuilt is not None, inner_rebuilt is not None))
    outer_curve = outer_rebuilt
    inner_curve = inner_rebuilt

    # Loft the frustum wall, then cap into a solid.
    lofts = Brep.CreateFromLoft([outer_curve, inner_curve], Point3d.Unset, Point3d.Unset,
                                LoftType.Straight, False)
    if lofts is None or len(lofts) == 0:
        raise BoreChamferError("loft failed to produce a frustum wall")
    wall = lofts[0]

    frustum = wall.CapPlanarHoles(1e-2)
    if frustum is None:
        raise BoreChamferError("CapPlanarHoles failed on frustum loft")

    if not frustum.IsSolid:
        raise BoreChamferError(
            "frustum is not a closed solid (faces={0})".format(frustum.Faces.Count))

    if frustum.SolidOrientation == BrepSolidOrientation.Inward:
        frustum.Flip()

    debug["frustum_solid"] = frustum.DuplicateBrep()

    # Boolean subtract.
    try:
        result, _success, method = robust_brep_difference(input_brep, frustum)
        #result = input_brep #disabling for dev JG
        #method = "skipped"
    except Exception as exc:
        raise BoreChamferError("boolean subtraction failed: {0}".format(exc))

    log("chamfer_bore_rim: subtracted frustum via {0} (chamfer_dist={1:.2f}mm)".format(
        method, chamfer_dist))
    return result
