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
from BrepFillet import fillet_edges as safe_fillet_edges  # unused until chamfer is re-enabled


class BrepSlitError(Exception):
    """Raised when the slit operation fails."""
    pass


def _coerce_mesh(obj):
    """Best-effort conversion of GH/Rhino references to Mesh."""
    if obj is None:
        return None
    if isinstance(obj, rg.Mesh):
        return obj

    try:
        import rhinoscriptsyntax as rs
        mesh = rs.coercemesh(obj)
        if mesh is not None:
            return mesh
    except Exception:
        pass

    return None


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


def _count_naked_topology_edges(mesh):
    """Count naked topology edges (connected to exactly one face)."""
    topo = getattr(mesh, "TopologyEdges", None)
    if topo is None:
        return None

    naked_count = 0
    for i in range(topo.Count):
        try:
            connected = topo.GetConnectedFaces(i)
            count = len(connected) if connected is not None else 0
        except Exception:
            count = 0
        if count == 1:
            naked_count += 1
    return naked_count


def _mesh_from_brep(brep, max_edge_length):
    """Create a single joined mesh from a brep with explicit edge-length control."""
    tol = sc.doc.ModelAbsoluteTolerance

    params = rg.MeshingParameters.DefaultAnalysisMesh
    try:
        max_len = max(float(max_edge_length), tol * 4.0)
        min_len = max(max_len * 0.15, tol)
        if hasattr(params, "MaximumEdgeLength"):
            params.MaximumEdgeLength = max_len
        if hasattr(params, "MinimumEdgeLength"):
            params.MinimumEdgeLength = min_len
        if hasattr(params, "SimplePlanes"):
            params.SimplePlanes = False
        if hasattr(params, "RefineGrid"):
            params.RefineGrid = True
        if hasattr(params, "JaggedSeams"):
            params.JaggedSeams = False
    except Exception:
        pass

    parts = rg.Mesh.CreateFromBrep(brep, params)
    if not parts or len(parts) == 0:
        return None

    out_mesh = rg.Mesh()
    for part in parts:
        out_mesh.Append(part)

    try:
        out_mesh.Faces.CullDegenerateFaces()
    except Exception:
        pass
    try:
        out_mesh.Vertices.CullUnused()
    except Exception:
        pass
    try:
        out_mesh.Normals.ComputeNormals()
    except Exception:
        pass
    try:
        out_mesh.Compact()
    except Exception:
        pass

    if out_mesh.Faces.Count == 0:
        return None
    return out_mesh


def _mesh_boolean_difference(minuend_mesh, subtrahend_mesh):
    """Attempt mesh boolean difference across common RhinoCommon overloads."""
    attempts = [
        ("MeshBoolean(list,list)", ( [minuend_mesh], [subtrahend_mesh] )),
        ("MeshBoolean(mesh,mesh)", ( minuend_mesh, subtrahend_mesh )),
    ]

    for method_name, args in attempts:
        try:
            result = rg.Mesh.CreateBooleanDifference(*args)
            if not result:
                continue

            if isinstance(result, rg.Mesh):
                return result, method_name

            meshes = list(result)
            if not meshes:
                continue

            joined = rg.Mesh()
            for m in meshes:
                if isinstance(m, rg.Mesh):
                    joined.Append(m)

            if joined.Faces.Count > 0:
                return joined, method_name
        except Exception:
            pass

    return None, None


def _find_slit_topology_edges(mesh, original_mesh, slit_normal, slit_center, slit_thickness, tolerance):
    """Find new mesh topology edges that lie on the two slit cut planes."""
    match_tol = tolerance * 10
    half = slit_thickness / 2.0
    plane_tol = tolerance * 5

    plane_a_origin = slit_center + slit_normal * half
    plane_b_origin = slit_center + slit_normal * (-half)

    original_midpoints = []
    original_topo = getattr(original_mesh, "TopologyEdges", None)
    if original_topo is not None:
        for i in range(original_topo.Count):
            try:
                original_midpoints.append(original_topo.EdgeLine(i).PointAt(0.5))
            except Exception:
                pass

    topo = getattr(mesh, "TopologyEdges", None)
    if topo is None:
        return [], []

    side_a = []
    side_b = []
    skipped = 0

    for i in range(topo.Count):
        try:
            line = topo.EdgeLine(i)
        except Exception:
            skipped += 1
            continue

        mid_pt = line.PointAt(0.5)
        is_original = any(mid_pt.DistanceTo(op) < match_tol for op in original_midpoints)
        if is_original:
            continue

        start_pt = line.From
        end_pt = line.To

        if (_dist_to_plane(mid_pt, plane_a_origin, slit_normal) < plane_tol and
                _dist_to_plane(start_pt, plane_a_origin, slit_normal) < plane_tol and
                _dist_to_plane(end_pt, plane_a_origin, slit_normal) < plane_tol):
            side_a.append(i)
        elif (_dist_to_plane(mid_pt, plane_b_origin, slit_normal) < plane_tol and
                _dist_to_plane(start_pt, plane_b_origin, slit_normal) < plane_tol and
                _dist_to_plane(end_pt, plane_b_origin, slit_normal) < plane_tol):
            side_b.append(i)
        else:
            skipped += 1

    log("Slit topology edge detection: {} + {} coplanar edges, {} wall/split edges skipped".format(
        len(side_a), len(side_b), skipped))
    return side_a, side_b


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
    """Apply chamfer/fillet to slit edges. Currently unused -- see cut_slit Step 4.

    DEFERRED: Applying CreateFilletEdges post-boolean reliably breaks the solid
    state of the result. The chamfer radius (0.3mm) is large relative to the slit
    geometry, and the fillet engine leaves naked edges at shared vertices where
    multiple slit-plane edges meet. This turns a clean solid into a non-solid,
    which then causes the *next* slit's boolean to fall back to MeshBoolean,
    compounding fragmentation across chained slit operations.

    Future approach: embed the bevel geometry into the cutter shape itself
    in _thicken_surface (e.g. hexagonal cross-section cutter) so no post-boolean
    edge operations are needed.

    Args:
        brep: Input brep.
        edge_indices: List of edge indices to fillet.
        radius: Fillet radius in mm.
        tolerance: Model tolerance.

    Returns:
        Treated brep (or original if operation fails).
    """
    if not edge_indices:
        log("No edges to fillet")
        return brep

    # Delegate to BrepFillet stable implementation (single call, no retry loop).
    return safe_fillet_edges(brep, edge_indices, radius, tolerance)


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

    # Step 4: Edge chamfer/fillet -- skipped for now (breaks solid state)
    # TODO: replace with cutter-embedded bevel geometry in _thicken_surface

    log("=" * 60)
    log("SLIT COMPLETE")
    log("=" * 60)
    return slit_brep, side_a, side_b, surface_a, surface_b


def cut_slit_mesh(splint_mesh, slit_panel, slit_thickness, fillet_radius,
                  tolerance=None):
    """Cut a sizing slit directly on a mesh using a mesh boolean difference.

    Signature and return tuple mirror cut_slit for easier drop-in usage.

    Args:
        splint_mesh: The input splint Mesh (or mesh-coercible reference).
        slit_panel: Untrimmed planar surface defining the slit center plane.
        slit_thickness: Total slit width in mm (removed symmetrically from panel).
        fillet_radius: Reserved for API parity with cut_slit; currently unused.
        tolerance: Model tolerance (uses doc tolerance if None).

    Returns:
        Tuple of (mesh, side_a_edges, side_b_edges, surface_a, surface_b).
        side_a/b are mesh topology edge indices on each cut plane.
        surface_a/b are single-face Breps of the two offset cut planes.

    Raises:
        BrepSlitError: If mesh boolean difference fails.
    """
    if tolerance is None:
        tolerance = sc.doc.ModelAbsoluteTolerance

    mesh_in = _coerce_mesh(splint_mesh)
    if mesh_in is None or not mesh_in.IsValid:
        raise BrepSlitError("Input splint mesh is None/uncoercible or invalid")
    if slit_panel is None:
        raise BrepSlitError("Slit panel is None")

    log("=" * 60)
    log("CUT SLIT MESH: thickness={:.3f}mm, fillet_r={:.3f}mm, tol={:.4f}".format(
        slit_thickness, fillet_radius, tolerance))
    log("=" * 60)
    log("Mesh input: closed={}, verts={}, faces={}, naked_topo_edges={}".format(
        bool(mesh_in.IsClosed), mesh_in.Vertices.Count, mesh_in.Faces.Count,
        _count_naked_topology_edges(mesh_in)))

    if fillet_radius not in (None, 0.0):
        log("Note: cut_slit_mesh currently ignores fillet_radius (kept for API parity)")

    # Step 1: Build a solid cutter from the slit panel, same as Brep path.
    cutter_brep = _thicken_surface(slit_panel, slit_thickness)
    if cutter_brep is None:
        raise BrepSlitError("Failed to create slit cutter from panel")

    bb_mesh = mesh_in.GetBoundingBox(True)
    bb_cutter = cutter_brep.GetBoundingBox(True)
    overlap = not (
        bb_mesh.Min.X > bb_cutter.Max.X or bb_cutter.Min.X > bb_mesh.Max.X or
        bb_mesh.Min.Y > bb_cutter.Max.Y or bb_cutter.Min.Y > bb_mesh.Max.Y or
        bb_mesh.Min.Z > bb_cutter.Max.Z or bb_cutter.Min.Z > bb_mesh.Max.Z
    )
    if not overlap:
        raise BrepSlitError("Slit cutter does not overlap splint mesh")

    # Step 2: Mesh the cutter at a resolution that respects narrow slit widths.
    cutter_edge_length = max(slit_thickness * 0.25, tolerance * 4.0)
    cutter_mesh = _mesh_from_brep(cutter_brep, cutter_edge_length)
    if cutter_mesh is None:
        raise BrepSlitError("Failed to mesh slit cutter for mesh boolean")

    # Step 3: Mesh boolean difference.
    log("Subtracting slit cutter from mesh via mesh boolean...")
    result_mesh, method = _mesh_boolean_difference(mesh_in, cutter_mesh)
    if result_mesh is None:
        raise BrepSlitError("Mesh boolean difference failed for slit operation")

    try:
        result_mesh.Faces.CullDegenerateFaces()
    except Exception:
        pass
    try:
        result_mesh.Vertices.CullUnused()
    except Exception:
        pass
    try:
        result_mesh.Normals.ComputeNormals()
    except Exception:
        pass
    try:
        result_mesh.Compact()
    except Exception:
        pass

    log("Mesh boolean: method={}, closed={}, verts={}, faces={}, naked_topo_edges={}".format(
        method,
        bool(result_mesh.IsClosed),
        result_mesh.Vertices.Count,
        result_mesh.Faces.Count,
        _count_naked_topology_edges(result_mesh),
    ))

    # Step 4: Build offset panel surfaces and classify new slit edges by plane.
    if isinstance(slit_panel, rg.Brep):
        srf = slit_panel.Faces[0].UnderlyingSurface()
    elif isinstance(slit_panel, rg.Surface):
        srf = slit_panel
    else:
        raise BrepSlitError("Slit panel must be a Surface or single-face Brep")

    slit_center = srf.PointAt(srf.Domain(0).Mid, srf.Domain(1).Mid)
    slit_normal = srf.NormalAt(srf.Domain(0).Mid, srf.Domain(1).Mid)
    slit_normal.Unitize()
    half = slit_thickness / 2.0

    surface_a = srf.ToBrep()
    surface_b = srf.ToBrep()
    surface_a.Transform(rg.Transform.Translation(slit_normal * half))
    surface_b.Transform(rg.Transform.Translation(slit_normal * (-half)))

    side_a, side_b = _find_slit_topology_edges(
        result_mesh, mesh_in, slit_normal, slit_center, slit_thickness, tolerance)

    if not side_a and not side_b:
        log("WARNING: no new slit topology edges detected from mesh cut")

    log("=" * 60)
    log("SLIT MESH COMPLETE")
    log("=" * 60)
    return result_mesh, side_a, side_b, surface_a, surface_b
