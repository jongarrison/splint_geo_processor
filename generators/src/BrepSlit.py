"""
BrepSlit.py
Cut a sizing slit in a splint brep and fillet the exposed edges.

The slit is defined by an untrimmed planar surface (the slit panel) that
intersects the splint. The panel is thickened symmetrically along its normal
to create a solid cutter, which is boolean-subtracted from the splint.
The sharp edges revealed by the cut are then filleted for skin comfort.
"""

import Rhino.Geometry as rg
import scriptcontext as sc
from splintcommon import log
from BrepDifference import robust_brep_difference


class BrepSlitError(Exception):
    """Raised when the slit operation fails."""
    pass


def _thicken_surface(surface, thickness):
    """Extrude an untrimmed surface symmetrically along its normal to create a solid cutter.

    Translates two copies of the surface boundary +/- half thickness along the
    surface normal, straight-lofts the walls, and caps to form a solid.

    Args:
        surface: Untrimmed planar Surface or single-face Brep.
        thickness: Total slit width in mm. Half is extruded each direction.

    Returns:
        Solid Brep cutter, or None on failure.
    """
    # Normalize input to a Surface
    if isinstance(surface, rg.Brep):
        if surface.Faces.Count != 1:
            log("ERROR: slit panel brep has {} faces, expected 1".format(surface.Faces.Count))
            return None
        srf = surface.Faces[0].UnderlyingSurface()
    elif isinstance(surface, rg.Surface):
        srf = surface
    else:
        log("ERROR: unexpected slit panel type: {}".format(type(surface).__name__))
        return None

    # Get normal at surface center
    normal = srf.NormalAt(srf.Domain(0).Mid, srf.Domain(1).Mid)
    normal.Unitize()
    half = thickness / 2.0
    log("Thickening panel: thickness={:.4f}mm, normal=({:.3f},{:.3f},{:.3f})".format(
        thickness, normal.X, normal.Y, normal.Z))

    # Translate two copies of the surface boundary, loft walls, cap to solid
    brep_a = srf.ToBrep()
    brep_b = srf.ToBrep()
    brep_a.Transform(rg.Transform.Translation(normal * half))
    brep_b.Transform(rg.Transform.Translation(normal * (-half)))

    curve_a = brep_a.Faces[0].OuterLoop.To3dCurve()
    curve_b = brep_b.Faces[0].OuterLoop.To3dCurve()

    lofted = rg.Brep.CreateFromLoft(
        [curve_a, curve_b],
        rg.Point3d.Unset, rg.Point3d.Unset,
        rg.LoftType.Straight, False
    )
    if not lofted or len(lofted) == 0:
        log("ERROR: Loft between translated boundaries failed")
        return None

    capped = lofted[0].CapPlanarHoles(sc.doc.ModelAbsoluteTolerance)
    if capped and capped.IsSolid:
        log("Cutter built: solid={}, valid={}, faces={}".format(
            capped.IsSolid, capped.IsValid, capped.Faces.Count))
        return capped

    log("ERROR: CapPlanarHoles failed on lofted walls")
    return None


def _dist_to_plane(point, plane_origin, normal):
    """Signed distance from a point to a plane defined by origin + unit normal."""
    vec = point - plane_origin
    return abs(vec.X * normal.X + vec.Y * normal.Y + vec.Z * normal.Z)


def _find_slit_edges(brep, original_brep, slit_normal, slit_center, slit_thickness, tolerance):
    """Find edges created by the slit cut that lie on the two cut planes.

    Only edges coplanar with one of the two offset planes are returned.
    Wall/intersection edges that pass through the slit region are excluded.

    An edge is considered coplanar with a plane if its midpoint and both
    endpoints are within tolerance of that plane.

    Args:
        brep: The brep after the slit cut.
        original_brep: The brep before the slit cut.
        slit_normal: Unit normal of the slit panel.
        slit_center: A point on the slit center plane.
        slit_thickness: Total slit width in mm.
        tolerance: Distance tolerance for midpoint matching.

    Returns:
        Tuple of (side_a_indices, side_b_indices) - edge indices coplanar
        with the + and - normal offset planes respectively.
    """
    match_tol = tolerance * 10
    half = slit_thickness / 2.0
    plane_tol = tolerance * 5  # coplanarity tolerance

    # Offset plane origins
    plane_a_origin = slit_center + slit_normal * half   # + normal side
    plane_b_origin = slit_center + slit_normal * (-half) # - normal side

    # Snapshot original edge midpoints
    original_midpoints = []
    for edge in original_brep.Edges:
        original_midpoints.append(edge.PointAt(edge.Domain.Mid))

    side_a = []
    side_b = []
    skipped = 0
    for edge in brep.Edges:
        mid_pt = edge.PointAt(edge.Domain.Mid)
        is_original = any(mid_pt.DistanceTo(op) < match_tol for op in original_midpoints)
        if is_original:
            continue

        # Test coplanarity: midpoint + both endpoints must be on the plane
        start_pt = edge.PointAtStart
        end_pt = edge.PointAtEnd

        # Check plane A
        if (_dist_to_plane(mid_pt, plane_a_origin, slit_normal) < plane_tol and
                _dist_to_plane(start_pt, plane_a_origin, slit_normal) < plane_tol and
                _dist_to_plane(end_pt, plane_a_origin, slit_normal) < plane_tol):
            side_a.append(edge.EdgeIndex)
        # Check plane B
        elif (_dist_to_plane(mid_pt, plane_b_origin, slit_normal) < plane_tol and
                _dist_to_plane(start_pt, plane_b_origin, slit_normal) < plane_tol and
                _dist_to_plane(end_pt, plane_b_origin, slit_normal) < plane_tol):
            side_b.append(edge.EdgeIndex)
        else:
            skipped += 1

    log("Slit edge detection: {} + {} coplanar edges, {} wall/split edges skipped".format(
        len(side_a), len(side_b), skipped))
    return side_a, side_b


def _fillet_edges_by_index(brep, edge_indices, radius, tolerance):
    """Fillet specific edges by index. Tries batch first, then one-at-a-time.

    Args:
        brep: Input brep.
        edge_indices: List of edge indices to fillet.
        radius: Fillet radius in mm.
        tolerance: Model tolerance.

    Returns:
        Filleted brep (or original if filleting fails).
    """
    if not edge_indices:
        log("No edges to fillet")
        return brep

    blend_type = rg.BlendType.Fillet
    rail_type = rg.RailType.DistanceFromEdge
    radii = [radius] * len(edge_indices)

    # Try batch first
    log("Attempting batch fillet on {} edges, radius={:.3f}".format(len(edge_indices), radius))
    result = rg.Brep.CreateFilletEdges(
        brep, edge_indices, radii, radii, blend_type, rail_type, tolerance
    )
    if result and len(result) > 0:
        log("Batch fillet succeeded: {} brep(s)".format(len(result)))
        return result[0]

    log("Batch fillet failed, trying one-at-a-time")

    # One-at-a-time with re-detection each pass.
    # Snapshot midpoints of target edges; entries set to None once consumed.
    target_points = []
    for idx in edge_indices:
        edge = brep.Edges[idx]
        target_points.append(edge.PointAt(edge.Domain.Mid))

    current = brep
    match_tol = tolerance * 10
    max_passes = len(edge_indices) + 5
    total_filleted = 0

    for pass_num in range(max_passes):
        # Find current edges matching unconsumed target midpoints
        candidates = []  # (edge_index, target_index)
        for edge in current.Edges:
            mid_pt = edge.PointAt(edge.Domain.Mid)
            for ti, tp in enumerate(target_points):
                if tp is not None and mid_pt.DistanceTo(tp) < match_tol:
                    candidates.append((edge.EdgeIndex, ti))
                    break

        if not candidates:
            log("Fillet pass {}: no target edges remain, done ({} filleted)".format(
                pass_num, total_filleted))
            break

        filleted = False
        for idx, ti in candidates:
            res = rg.Brep.CreateFilletEdges(
                current, [idx], [radius], [radius], blend_type, rail_type, tolerance
            )
            if res and len(res) > 0:
                log("  Fillet pass {}: edge {} succeeded".format(pass_num, idx))
                current = res[0]
                total_filleted += 1
                target_points[ti] = None  # consumed, prevent re-match
                filleted = True
                break
            else:
                log("  Fillet pass {}: edge {} FAILED".format(pass_num, idx))

        if not filleted:
            # Try remaining as batch
            remaining_indices = [ei for ei, _ in candidates]
            batch_radii = [radius] * len(remaining_indices)
            res = rg.Brep.CreateFilletEdges(
                current, remaining_indices, batch_radii, batch_radii,
                blend_type, rail_type, tolerance
            )
            if res and len(res) > 0:
                log("  Batch fallback succeeded for remaining {} edges".format(len(candidates)))
                current = res[0]
                total_filleted += len(candidates)
                break
            log("No more edges could be filleted ({} of {} done)".format(
                total_filleted, len(edge_indices)))
            break

    return current


def cut_slit(splint_brep, slit_panel, slit_thickness, fillet_radius,
             tolerance=None):
    """Cut a sizing slit in a splint brep and fillet the exposed edges.

    Args:
        splint_brep: The solid splint Brep.
        slit_panel: Untrimmed planar surface defining the slit center plane.
                    Its intersection with splint_brep defines the slit location.
        slit_thickness: Total slit width in mm (removed symmetrically from panel).
        fillet_radius: Radius in mm for filleting the exposed slit edges.
        tolerance: Model tolerance (uses doc tolerance if None).

    Returns:
        Tuple of (brep, side_a_edges, side_b_edges, surface_a, surface_b).
        side_a/b are edge index lists for each cut plane (+ and - normal).
        surface_a/b are single-face Breps of the two offset cut planes.

    Raises:
        BrepSlitError: If the boolean difference fails.
    """
    if tolerance is None:
        tolerance = sc.doc.ModelAbsoluteTolerance

    log("=" * 60)
    log("CUT SLIT: thickness={:.3f}mm, fillet_r={:.3f}mm, tol={:.4f}".format(
        slit_thickness, fillet_radius, tolerance))
    log("=" * 60)

    # Validate inputs
    if splint_brep is None or not splint_brep.IsValid:
        raise BrepSlitError("Input splint brep is None or invalid")
    if slit_panel is None:
        raise BrepSlitError("Slit panel is None")

    log("Splint: solid={}, valid={}, faces={}, edges={}".format(
        splint_brep.IsSolid, splint_brep.IsValid,
        splint_brep.Faces.Count, splint_brep.Edges.Count))

    # Step 1: Thicken the slit panel into a solid cutter
    cutter = _thicken_surface(slit_panel, slit_thickness)
    if cutter is None:
        raise BrepSlitError("Failed to create slit cutter from panel")

    # Verify bounding box overlap between splint and cutter
    bb_splint = splint_brep.GetBoundingBox(True)
    bb_cutter = cutter.GetBoundingBox(True)
    overlap = not (
        bb_splint.Min.X > bb_cutter.Max.X or bb_cutter.Min.X > bb_splint.Max.X or
        bb_splint.Min.Y > bb_cutter.Max.Y or bb_cutter.Min.Y > bb_splint.Max.Y or
        bb_splint.Min.Z > bb_cutter.Max.Z or bb_cutter.Min.Z > bb_splint.Max.Z
    )
    if not overlap:
        raise BrepSlitError("Slit cutter does not overlap splint brep")

    # Step 2: Boolean difference using robust multi-strategy approach
    log("Subtracting slit cutter from splint via robust_brep_difference...")
    slit_brep, success, method = robust_brep_difference(
        splint_brep, cutter, base_tolerance=tolerance
    )
    log("Boolean difference: success={}, method={}, faces={}, edges={}".format(
        success, method, slit_brep.Faces.Count, slit_brep.Edges.Count))

    # Heal naked edges left by boolean difference
    naked_before = sum(1 for e in slit_brep.Edges if e.Valence == rg.EdgeAdjacency.Naked)
    if naked_before > 0:
        slit_brep.JoinNakedEdges(tolerance)
        naked_after = sum(1 for e in slit_brep.Edges if e.Valence == rg.EdgeAdjacency.Naked)
        log("Healed naked edges: {} -> {} (solid={})".format(
            naked_before, naked_after, slit_brep.IsSolid))

    # Compute slit plane info for edge classification and surface output
    if isinstance(slit_panel, rg.Brep):
        srf = slit_panel.Faces[0].UnderlyingSurface()
    else:
        srf = slit_panel
    slit_center = srf.PointAt(srf.Domain(0).Mid, srf.Domain(1).Mid)
    slit_normal = srf.NormalAt(srf.Domain(0).Mid, srf.Domain(1).Mid)
    slit_normal.Unitize()
    half = slit_thickness / 2.0

    # Build the two offset surface breps (single-face)
    surface_a = srf.ToBrep()
    surface_b = srf.ToBrep()
    surface_a.Transform(rg.Transform.Translation(slit_normal * half))
    surface_b.Transform(rg.Transform.Translation(slit_normal * (-half)))

    # Step 3: Find edges created by the cut, coplanar with each cut plane
    side_a, side_b = _find_slit_edges(
        slit_brep, splint_brep, slit_normal, slit_center, slit_thickness, tolerance)

    if not side_a and not side_b:
        log("WARNING: no new edges detected from cut")
        return slit_brep, [], [], surface_a, surface_b

    # Step 4: Fillet the new edges
    if fillet_radius > 0:
        all_new = side_a + side_b
        slit_brep = _fillet_edges_by_index(slit_brep, all_new, fillet_radius, tolerance)
        log("After fillet: solid={}, valid={}, faces={}, edges={}".format(
            slit_brep.IsSolid, slit_brep.IsValid,
            slit_brep.Faces.Count, slit_brep.Edges.Count))

    log("=" * 60)
    log("SLIT COMPLETE")
    log("=" * 60)
    return slit_brep, side_a, side_b, surface_a, surface_b
