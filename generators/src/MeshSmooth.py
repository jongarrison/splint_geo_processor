"""
MeshSmooth.py
Targeted Taubin smoothing of a mesh along user-supplied "smooth" curves,
with optional "protect" curves that veto smoothing locally.

Intended use:
  - During Brep construction, callers collect sharp edge curves they
    want softened (e.g. via find_sharp_edges + edge.DuplicateCurve()).
  - Just before mesh export, call smooth_mesh_near_curves(mesh, smooth_curves,
    protect_curves=...) to round those bands without disturbing other
    features (embossed text, branding, intentional sharp transitions).
"""

import math
import time

import Rhino.Geometry as rg
import rhinoscriptsyntax as rs

from splintcommon import log


# ---------- input coercion ----------

def _coerce_curves(items, label):
    """Accept curves as rg.Curve or GH-style Guid/object refs; skip invalid."""
    out = []
    for i, item in enumerate(items or []):
        if item is None:
            continue
        if isinstance(item, rg.Curve):
            out.append(item)
            continue
        try:
            c = rs.coercecurve(item)
        except Exception:
            c = None
        if c is None:
            log("  {}: item {} not a curve, skipping".format(label, i))
            continue
        out.append(c)
    return out


# ---------- curve sampling + spatial index ----------

def _sample_curves(curves, sample_mm):
    """Densely sample curves into 3D points for nearest-distance queries."""
    pts = []
    total_len = 0.0
    for c in curves:
        L = c.GetLength()
        total_len += L
        if L <= 0.0:
            continue
        # +1 so we include both endpoints; min 2 samples per curve.
        n = max(2, int(math.ceil(L / sample_mm)) + 1)
        # DivideByCount handles closed curves correctly.
        ts = c.DivideByCount(n - 1, True)
        if ts is None:
            continue
        for t in ts:
            pts.append(c.PointAt(t))
    return pts, total_len


def _build_rtree(points):
    """RTree of point indices for fast nearest-point queries."""
    tree = rg.RTree()
    for i, p in enumerate(points):
        tree.Insert(rg.BoundingBox(p, p), i)
    return tree


def _min_distance_to_points(query_pt, points, tree, search_radius):
    """Return min distance from query_pt to any sample point within search_radius,
    or search_radius+1 if no sample is in range (treated as "out of band")."""
    closest = [search_radius + 1.0]  # mutable for closure

    def cb(sender, e):
        d = query_pt.DistanceTo(points[e.Id])
        if d < closest[0]:
            closest[0] = d

    sphere = rg.Sphere(query_pt, search_radius)
    tree.Search(sphere, cb)
    return closest[0]


def _closest_point_index(query_pt, points, tree, search_radius):
    """Like _min_distance_to_points but also returns the index of the closest
    sample. Returns (distance, index) or (search_radius+1, -1) if out of range."""
    state = [search_radius + 1.0, -1]  # [distance, index]

    def cb(sender, e):
        d = query_pt.DistanceTo(points[e.Id])
        if d < state[0]:
            state[0] = d
            state[1] = e.Id

    sphere = rg.Sphere(query_pt, search_radius)
    tree.Search(sphere, cb)
    return state[0], state[1]


# ---------- weight field ----------

def _vertex_weights(mesh, smooth_pts, smooth_tree,
                    protect_pts, protect_tree,
                    band_mm, falloff_mm):
    """Per-vertex smoothing weight in [0,1]. Cosine falloff across the
    transition shell. Protect curves force weight to 0 within band_mm.
    Naked-edge vertices also forced to 0."""
    n = mesh.Vertices.Count
    weights = [0.0] * n
    influence = band_mm + falloff_mm
    naked = mesh.GetNakedEdgePointStatus() if mesh.Vertices.Count else None

    in_core = 0
    in_falloff = 0
    protected = 0
    pinned_naked = 0

    for vi in range(n):
        if naked is not None and naked[vi]:
            pinned_naked += 1
            continue

        v = mesh.Vertices[vi]
        # rg.MeshVertexList yields Point3f; convert to Point3d for distance calls.
        p = rg.Point3d(v.X, v.Y, v.Z)

        d = _min_distance_to_points(p, smooth_pts, smooth_tree, influence)
        if d > influence:
            continue

        if protect_tree is not None:
            dp = _min_distance_to_points(p, protect_pts, protect_tree, band_mm)
            if dp <= band_mm:
                protected += 1
                continue

        if d <= band_mm:
            weights[vi] = 1.0
            in_core += 1
        else:
            # Cosine ramp: 1.0 at band_mm, 0.0 at band_mm + falloff_mm.
            t = (d - band_mm) / falloff_mm
            weights[vi] = 0.5 * (1.0 + math.cos(math.pi * t))
            in_falloff += 1

    log("  weights: core={}, falloff={}, protected={}, naked_pinned={}, total_verts={}".format(
        in_core, in_falloff, protected, pinned_naked, n))
    return weights


# ---------- 1-ring adjacency ----------

def _build_vertex_neighbors(mesh):
    """List of neighbor index sets, one per vertex. Derived from face topology
    so it works on triangle and quad meshes."""
    n = mesh.Vertices.Count
    nbrs = [set() for _ in range(n)]
    faces = mesh.Faces
    for fi in range(faces.Count):
        f = faces[fi]
        if f.IsQuad:
            quad = [f.A, f.B, f.C, f.D]
            for k in range(4):
                a = quad[k]
                b = quad[(k + 1) % 4]
                nbrs[a].add(b)
                nbrs[b].add(a)
        else:
            tri = [f.A, f.B, f.C]
            for k in range(3):
                a = tri[k]
                b = tri[(k + 1) % 3]
                nbrs[a].add(b)
                nbrs[b].add(a)
    return [list(s) for s in nbrs]


# ---------- Taubin step ----------

def _laplacian_step(positions, neighbors, weights, factor):
    """One weighted uniform-Laplacian step. Returns new positions list.
    Vertices with weight 0 are not moved (early-out)."""
    new_positions = [None] * len(positions)
    for vi, p in enumerate(positions):
        w = weights[vi]
        if w == 0.0:
            new_positions[vi] = p
            continue
        nbs = neighbors[vi]
        if not nbs:
            new_positions[vi] = p
            continue
        # Centroid of 1-ring neighbors.
        sx = sy = sz = 0.0
        for j in nbs:
            q = positions[j]
            sx += q.X
            sy += q.Y
            sz += q.Z
        inv = 1.0 / len(nbs)
        cx = sx * inv
        cy = sy * inv
        cz = sz * inv
        # Move toward centroid by (factor * weight). factor is signed (lam>0, mu<0).
        a = factor * w
        new_positions[vi] = rg.Point3d(
            p.X + a * (cx - p.X),
            p.Y + a * (cy - p.Y),
            p.Z + a * (cz - p.Z),
        )
    return new_positions


# ---------- public entry point ----------

def smooth_mesh_near_curves(
    mesh,
    smooth_curves,
    protect_curves=None,
    band_mm=0.6,
    falloff_mm=0.6,
    iterations=15,
    lam=0.5,
    mu=-0.53,
    curve_sample_mm=None,
    clamp_displacement=False,
    clamp_fraction=0.1,
):
    """Return a new Mesh with vertices near `smooth_curves` Taubin-smoothed.

    Args:
        mesh: Input rg.Mesh (not mutated; copied).
        smooth_curves: list of curves marking bands to smooth. Accepts
            rg.Curve or GH Guid/object refs.
        protect_curves: optional list of curves that lock nearby vertices.
            Protection wins over smoothing where they overlap.
        band_mm: full-strength half-width of the smoothing band.
        falloff_mm: cosine transition shell beyond the core band.
        iterations: number of Taubin pairs (each pair = 1 lambda + 1 mu step).
            Larger -> more rounded feel. Width of perceived rounding
            scales with sqrt(iterations) * band_mm roughly.
        lam, mu: Taubin coefficients. |mu| should be slightly > lam for
            volume preservation. Defaults are standard.
        curve_sample_mm: spacing for curve sampling into the spatial index.
            Defaults to band_mm/4 (fine enough to not miss the band).
        clamp_displacement: If True, cap per-iteration vertex displacement
            relative to the prior iteration. Suppresses large-scale bending
            modes (e.g. helical warping in slender bridges) caused by
            asymmetric smoothing accumulating over many iterations. Local
            rounding still develops; just slower per iteration. Bump
            `iterations` to compensate when enabling.
        clamp_fraction: Max per-Taubin-pair displacement as a fraction of
            band_mm. Default 0.1 (e.g. 0.06 mm cap at band_mm=0.6). Smaller
            = stricter (more stable, more iterations to reach same effect).
    """
    t_start = time.time()

    if mesh is None or mesh.Vertices.Count == 0:
        log("smooth_mesh_near_curves: empty mesh, returning original")
        return mesh

    smooth_curves = _coerce_curves(smooth_curves, "smooth_curves")
    protect_curves = _coerce_curves(protect_curves, "protect_curves") if protect_curves else []

    if not smooth_curves:
        log("smooth_mesh_near_curves: no smooth_curves provided, returning original")
        return mesh

    if curve_sample_mm is None:
        curve_sample_mm = max(0.05, band_mm * 0.25)

    log("smooth_mesh_near_curves: verts={}, faces={}, smooth_curves={}, protect_curves={}".format(
        mesh.Vertices.Count, mesh.Faces.Count, len(smooth_curves), len(protect_curves)))
    log("  band_mm={:.3f}, falloff_mm={:.3f}, iterations={}, sample_mm={:.3f}, clamp={}{}".format(
        band_mm, falloff_mm, iterations, curve_sample_mm,
        clamp_displacement,
        " (max={:.3f}mm/pair)".format(band_mm * clamp_fraction) if clamp_displacement else ""))

    # Sample curves and build spatial indices once.
    t = time.time()
    smooth_pts, smooth_len = _sample_curves(smooth_curves, curve_sample_mm)
    smooth_tree = _build_rtree(smooth_pts) if smooth_pts else None
    if smooth_tree is None:
        log("smooth_mesh_near_curves: smooth curves yielded no samples, returning original")
        return mesh

    protect_pts = []
    protect_tree = None
    if protect_curves:
        protect_pts, _plen = _sample_curves(protect_curves, curve_sample_mm)
        protect_tree = _build_rtree(protect_pts) if protect_pts else None
    log("  sampled smooth_pts={}, protect_pts={}, smooth_len={:.1f}mm, t={:.2f}s".format(
        len(smooth_pts), len(protect_pts), smooth_len, time.time() - t))

    # Per-vertex weights (computed once; geometry of band doesn't drift meaningfully
    # over a few iterations of small displacements).
    t = time.time()
    weights = _vertex_weights(
        mesh, smooth_pts, smooth_tree,
        protect_pts, protect_tree,
        band_mm, falloff_mm,
    )
    log("  weights computed in {:.2f}s".format(time.time() - t))

    active_count = sum(1 for w in weights if w > 0.0)
    if active_count == 0:
        log("smooth_mesh_near_curves: no vertices in influence region, returning original")
        return mesh

    # 1-ring neighbors from face topology.
    t = time.time()
    neighbors = _build_vertex_neighbors(mesh)
    log("  adjacency built in {:.2f}s".format(time.time() - t))

    # Pull initial positions into a flat Python list for speed (avoid repeated
    # MeshVertexList indexing inside the inner loop).
    positions = [
        rg.Point3d(v.X, v.Y, v.Z) for v in mesh.Vertices
    ]

    # Taubin iterations: alternate lambda (shrink) and mu (un-shrink).
    # When clamp_displacement is on, each Taubin pair's net per-vertex
    # displacement is capped at (band_mm * clamp_fraction). Suppresses
    # large-scale bending modes on slender features driven by asymmetric
    # smoothing accumulating over many iterations.
    t = time.time()
    prev_positions = positions
    max_disp_log = []
    clamp_max = band_mm * clamp_fraction if clamp_displacement else 0.0
    clamp_max_sq = clamp_max * clamp_max
    total_clamped = 0
    for it in range(iterations):
        pair_start = positions
        positions = _laplacian_step(positions, neighbors, weights, lam)
        positions = _laplacian_step(positions, neighbors, weights, mu)

        if clamp_displacement:
            clamped_this_iter = 0
            new_positions = [None] * len(positions)
            for j in range(len(positions)):
                if weights[j] == 0.0:
                    new_positions[j] = positions[j]
                    continue
                a = pair_start[j]
                b = positions[j]
                dx = b.X - a.X
                dy = b.Y - a.Y
                dz = b.Z - a.Z
                d_sq = dx * dx + dy * dy + dz * dz
                if d_sq > clamp_max_sq and d_sq > 0.0:
                    scale = clamp_max / math.sqrt(d_sq)
                    new_positions[j] = rg.Point3d(
                        a.X + dx * scale,
                        a.Y + dy * scale,
                        a.Z + dz * scale,
                    )
                    clamped_this_iter += 1
                else:
                    new_positions[j] = b
            positions = new_positions
            total_clamped += clamped_this_iter

        # Cheap convergence telemetry every few iterations.
        if it == 0 or (it + 1) % 5 == 0 or it == iterations - 1:
            max_d = 0.0
            for j in range(len(positions)):
                if weights[j] == 0.0:
                    continue
                d = positions[j].DistanceTo(prev_positions[j])
                if d > max_d:
                    max_d = d
            max_disp_log.append((it + 1, max_d))
            prev_positions = positions
    log("  iterations done in {:.2f}s; max_disp samples: {}{}".format(
        time.time() - t,
        ", ".join("it{}={:.3f}mm".format(i, d) for i, d in max_disp_log),
        "; clamped_total={}".format(total_clamped) if clamp_displacement else ""))

    # Build the output mesh: copy topology, write new vertex positions.
    out = mesh.DuplicateMesh()
    for vi, p in enumerate(positions):
        out.Vertices.SetVertex(vi, p)

    out.Normals.ComputeNormals()
    out.FaceNormals.ComputeFaceNormals()
    out.Compact()

    # Final manifold sanity check (these meshes must be printable).
    naked_after = 0
    try:
        naked_status = out.GetNakedEdgePointStatus()
        if naked_status is not None:
            for s in naked_status:
                if s:
                    naked_after += 1
    except Exception:
        pass
    log("smooth_mesh_near_curves: done in {:.2f}s, active_verts={}, naked_verts_after={}".format(
        time.time() - t_start, active_count, naked_after))

    return out


# ---------- fillet (tube projection) ----------

def fillet_mesh_near_curves(
    mesh,
    smooth_curves,
    radius,
    protect_curves=None,
    falloff_mm=0.4,
    cleanup_iterations=3,
    curve_sample_mm=None,
):
    """Return a new Mesh with a fillet-like rounding carved/built along
    `smooth_curves`. Unlike smooth_mesh_near_curves (gentle Taubin blur),
    this projects band vertices onto a tube of `radius` around each curve,
    producing a predictable fillet profile.

    Algorithm (per vertex within radius + falloff_mm of a smooth curve):
      1. Find closest sample point cp on the curve set.
      2. dir = (vertex - cp) normalized.  (Concave edges: dir points inward,
         which fills the corner. Convex edges: dir points outward, which
         carves the edge. Both yield a fillet.)
      3. target = cp + dir * radius      (point on the tube).
      4. blend vertex toward target by the per-vertex weight (full inside
         the core, cosine falloff in the transition shell).
      5. Run a small number of cleanup Taubin passes restricted to the same
         weights to smooth tessellation seams.

    Args:
        mesh: Input rg.Mesh (not mutated).
        smooth_curves: Curves marking edges to fillet. rg.Curve or GH refs.
        radius: Fillet radius in mm. Vertices on the curve are pushed by
            roughly this distance.
        protect_curves: Optional curves whose neighborhoods are locked.
            Protection wins over filleting within `radius` of a protect curve.
        falloff_mm: Width of the cosine falloff shell beyond the core radius.
            Larger -> softer blend into surrounding surface.
        cleanup_iterations: Taubin pairs run after projection to clean up
            seams. 2-4 is usually plenty.
        curve_sample_mm: Spacing for curve sampling. Defaults to radius/6.
            Finer sampling = better projection direction accuracy.

    Returns:
        New rg.Mesh. Original returned unchanged on empty input.

    Notes:
        - Needs mesh resolution finer than ~radius/4 in the band, otherwise
          the tube becomes faceted. Mesh accordingly.
        - At junctions where multiple smooth_curves meet at a point the tube
          surfaces self-intersect; result will look pinched there, as a
          conventional CAD fillet would also fail. Avoid placing curves
          that intersect at sharp angles.
    """
    t_start = time.time()

    if mesh is None or mesh.Vertices.Count == 0:
        log("fillet_mesh_near_curves: empty mesh, returning original")
        return mesh

    smooth_curves = _coerce_curves(smooth_curves, "smooth_curves")
    protect_curves = _coerce_curves(protect_curves, "protect_curves") if protect_curves else []

    if not smooth_curves:
        log("fillet_mesh_near_curves: no smooth_curves provided, returning original")
        return mesh

    if radius <= 0.0:
        log("fillet_mesh_near_curves: radius must be > 0")
        return mesh

    if curve_sample_mm is None:
        curve_sample_mm = max(0.03, radius / 6.0)

    influence = radius + falloff_mm

    log("fillet_mesh_near_curves: verts={}, faces={}, smooth_curves={}, protect_curves={}".format(
        mesh.Vertices.Count, mesh.Faces.Count, len(smooth_curves), len(protect_curves)))
    log("  radius={:.3f}, falloff_mm={:.3f}, cleanup_iter={}, sample_mm={:.3f}".format(
        radius, falloff_mm, cleanup_iterations, curve_sample_mm))

    # Sample smooth curves and build the spatial index.
    t = time.time()
    smooth_pts, smooth_len = _sample_curves(smooth_curves, curve_sample_mm)
    smooth_tree = _build_rtree(smooth_pts) if smooth_pts else None
    if smooth_tree is None:
        log("fillet_mesh_near_curves: smooth curves yielded no samples, returning original")
        return mesh

    protect_pts = []
    protect_tree = None
    if protect_curves:
        protect_pts, _plen = _sample_curves(protect_curves, curve_sample_mm)
        protect_tree = _build_rtree(protect_pts) if protect_pts else None
    log("  sampled smooth_pts={}, protect_pts={}, smooth_len={:.1f}mm, t={:.2f}s".format(
        len(smooth_pts), len(protect_pts), smooth_len, time.time() - t))

    # Pull positions to a flat list once.
    positions = [rg.Point3d(v.X, v.Y, v.Z) for v in mesh.Vertices]
    n = mesh.Vertices.Count
    naked = mesh.GetNakedEdgePointStatus() if n else None

    # Per-vertex projection + weight pass. Weights are reused by the cleanup
    # Taubin pass below so vertices outside the influence shell never move.
    t = time.time()
    weights = [0.0] * n
    in_core = 0
    in_falloff = 0
    protected = 0
    pinned_naked = 0
    degenerate_dir = 0
    max_push = 0.0

    for vi in range(n):
        if naked is not None and naked[vi]:
            pinned_naked += 1
            continue

        p = positions[vi]
        d, cp_idx = _closest_point_index(p, smooth_pts, smooth_tree, influence)
        if cp_idx < 0:
            continue

        # Protect curves veto filleting within their own band.
        if protect_tree is not None:
            dp = _min_distance_to_points(p, protect_pts, protect_tree, radius)
            if dp <= radius:
                protected += 1
                continue

        # Per-vertex influence weight (cosine falloff outside the core).
        if d <= radius:
            w = 1.0
            in_core += 1
        else:
            t_blend = (d - radius) / falloff_mm
            w = 0.5 * (1.0 + math.cos(math.pi * t_blend))
            in_falloff += 1
        weights[vi] = w

        # Project vertex onto the tube of `radius` around the nearest curve sample.
        cp = smooth_pts[cp_idx]
        dx = p.X - cp.X
        dy = p.Y - cp.Y
        dz = p.Z - cp.Z
        dist = math.sqrt(dx * dx + dy * dy + dz * dz)
        if dist < 1e-9:
            # Vertex sits exactly on the curve; no defined outward direction.
            # Leave it for the cleanup Taubin pass to nudge.
            degenerate_dir += 1
            continue

        inv = 1.0 / dist
        tx = cp.X + dx * inv * radius
        ty = cp.Y + dy * inv * radius
        tz = cp.Z + dz * inv * radius

        # Blend toward the tube surface by the falloff weight.
        new_x = p.X + w * (tx - p.X)
        new_y = p.Y + w * (ty - p.Y)
        new_z = p.Z + w * (tz - p.Z)
        push = abs(radius - dist) * w
        if push > max_push:
            max_push = push
        positions[vi] = rg.Point3d(new_x, new_y, new_z)

    log("  projection: core={}, falloff={}, protected={}, naked_pinned={}, on_curve={}, max_push={:.3f}mm, t={:.2f}s".format(
        in_core, in_falloff, protected, pinned_naked, degenerate_dir, max_push, time.time() - t))

    active_count = in_core + in_falloff
    if active_count == 0:
        log("fillet_mesh_near_curves: no vertices in influence region, returning original")
        return mesh

    # Optional cleanup Taubin passes: smooth the tessellation seam produced
    # by per-vertex projection without giving up the fillet shape. Same
    # weight field ensures we only touch already-active vertices.
    if cleanup_iterations and cleanup_iterations > 0:
        t = time.time()
        neighbors = _build_vertex_neighbors(mesh)
        lam = 0.5
        mu = -0.53
        for _ in range(cleanup_iterations):
            positions = _laplacian_step(positions, neighbors, weights, lam)
            positions = _laplacian_step(positions, neighbors, weights, mu)
        log("  cleanup iterations done in {:.2f}s".format(time.time() - t))

    # Build the output mesh: copy topology, write new vertex positions.
    out = mesh.DuplicateMesh()
    for vi, p in enumerate(positions):
        out.Vertices.SetVertex(vi, p)

    out.Normals.ComputeNormals()
    out.FaceNormals.ComputeFaceNormals()
    out.Compact()

    # Final manifold sanity check.
    naked_after = 0
    try:
        naked_status = out.GetNakedEdgePointStatus()
        if naked_status is not None:
            for s in naked_status:
                if s:
                    naked_after += 1
    except Exception:
        pass
    log("fillet_mesh_near_curves: done in {:.2f}s, active_verts={}, naked_verts_after={}".format(
        time.time() - t_start, active_count, naked_after))

    return out
