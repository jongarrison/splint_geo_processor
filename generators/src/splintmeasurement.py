"""
splintmeasurement.py
Wall thickness and geometry measurement utilities for splint meshes.

Uses ray-casting from anatomical landmark positions (via FingerModelResult
perp frames) to measure wall thickness at named probe locations.
"""

import Rhino
import Rhino.Geometry as rg
from Rhino.Geometry import Point3d, Vector3d
import scriptcontext as sc
import System
from splintcommon import log, gh_decode


def _coerce_mesh(obj):
    """Coerce a Grasshopper input (Guid, Brep, Mesh, etc.) to Rhino.Geometry.Mesh."""
    if isinstance(obj, rg.Mesh):
        return obj
    # Brep -> mesh via analysis mesh settings
    if isinstance(obj, rg.Brep):
        meshes = rg.Mesh.CreateFromBrep(obj, rg.MeshingParameters.DefaultAnalysisMesh)
        if meshes:
            joined = rg.Mesh()
            for m in meshes:
                joined.Append(m)
            return joined
        return None
    # For Guids and other GH references, use rhinoscriptsyntax to resolve
    try:
        import rhinoscriptsyntax as rs
        # Try mesh first
        mesh = rs.coercemesh(obj)
        if mesh:
            return mesh
        # Try brep, then mesh it
        brep = rs.coercebrep(obj)
        if brep:
            return _coerce_mesh(brep)
    except Exception:
        pass
    return None


def measure_wall_thickness(finger_result, splint_mesh, name, offset, local_direction, far_distance=200.0):
    """Measure wall thickness at a named location along a local direction.

    Shoots a ray outward from the centerline through the splint shell and
    finds the two intersection points (inner wall, outer wall). Returns
    the distance between them.

    Args:
        finger_result: FingerModelResult with perp frame data.
        splint_mesh: The final splint shell Mesh (after boolean difference).
        name: Joint or phalanx name ("mcp", "proximal", etc.)
        offset: Position along segment (see FingerModelResult.get_perp_frame).
        local_direction: (x, y, z) tuple in perp frame local coords.
                         (1,0,0)=lateral, (0,1,0)=dorsal, (0,-1,0)=volar,
                         (-1,0,0)=medial. Gets unitized internally.
        far_distance: Length (mm) of the constructed probe line.

    Returns:
        dict with keys:
            "thickness_mm": float or None if measurement failed
            "inner_point": Point3d of inner wall hit (or None)
            "outer_point": Point3d of outer wall hit (or None)
            "ray_origin": Point3d centerline origin of the ray
            "ray_direction": Vector3d world-space probe direction
        Returns None if the perp frame cannot be computed.
    """
    plane = finger_result.get_perp_frame(name, offset)
    if plane is None:
        log("measure_wall_thickness: no perp frame for '{}' offset={}".format(name, offset))
        return None

    lx, ly, lz = local_direction
    # Transform local direction to world space using the perp frame axes
    world_dir = plane.XAxis * lx + plane.YAxis * ly + plane.ZAxis * lz
    if not world_dir.Unitize():
        log("measure_wall_thickness: zero-length world direction for '{}' offset={} dir={}".format(
            name, offset, local_direction))
        return None

    origin = plane.Origin

    probe_end = Point3d(origin + world_dir * far_distance)
    probe_line = rg.Line(origin, probe_end)
    return measure_mesh_wall_thickness(splint_mesh, probe_line)


def measure_mesh_wall_thickness(splint_mesh, ray_line):
    """Measure wall thickness along a provided probe line/ray on a mesh.

    Args:
        splint_mesh: Mesh/Brep/Guid that can be coerced to a Mesh.
        ray_line: Probe line. Direction is From -> To.

    Returns:
        dict with keys:
            "thickness_mm": float or None if measurement failed
            "inner_point": Point3d of first hit from line.From (or None)
            "outer_point": Point3d of first hit from reverse ray at line.To (or None)
            "ray_origin": Point3d start of the probe line
            "ray_direction": Vector3d unitized direction (From -> To)
        Returns None when inputs cannot be coerced/used.
    """
    # Coerce Guid/ObjRef to actual Mesh geometry
    if not isinstance(splint_mesh, rg.Mesh):
        coerced = _coerce_mesh(splint_mesh)
        if coerced is None:
            log("measure_mesh_wall_thickness: cannot coerce splint_mesh to Mesh (got {})".format(
                type(splint_mesh).__name__))
            return None
        splint_mesh = coerced

    if isinstance(ray_line, rg.Line):
        line = ray_line
    else:
        try:
            import rhinoscriptsyntax as rs
            line = rs.coerceline(ray_line)
        except Exception:
            line = None

    if line is None:
        log("measure_mesh_wall_thickness: cannot coerce ray_line to Line (got {})".format(
            type(ray_line).__name__))
        return None

    world_dir = line.Direction
    if not world_dir.Unitize():
        log("measure_mesh_wall_thickness: ray_line has zero length")
        return None

    origin = line.From
    base = {"ray_origin": origin, "ray_direction": Vector3d(world_dir)}

    # Ray from line start along line direction: finds first wall hit
    ray_out = rg.Ray3d(origin, world_dir)
    t_inner = rg.Intersect.Intersection.MeshRay(splint_mesh, ray_out)
    if t_inner < 0:
        log("measure_mesh_wall_thickness: outward ray missed")
        base.update({"thickness_mm": None, "inner_point": None, "outer_point": None})
        return base

    inner_point = ray_out.PointAt(t_inner)

    # Reverse ray from line end to find opposite wall hit
    far_origin = line.To

    neg_dir = Vector3d(-world_dir.X, -world_dir.Y, -world_dir.Z)
    ray_in = rg.Ray3d(far_origin, neg_dir)
    t_outer = rg.Intersect.Intersection.MeshRay(splint_mesh, ray_in)
    if t_outer < 0:
        log("measure_mesh_wall_thickness: inward ray missed")
        base.update({"thickness_mm": None, "inner_point": inner_point, "outer_point": None})
        return base

    outer_point = ray_in.PointAt(t_outer)
    thickness = inner_point.DistanceTo(outer_point)

    base.update({
        "thickness_mm": round(thickness, 4),
        "inner_point": inner_point,
        "outer_point": outer_point,
    })
    return base


def measure_thickness_probes(finger_result, splint_mesh, probes, far_distance=200.0):
    """Run a batch of measurement probes and return a results dict.

    Args:
        finger_result: FingerModelResult with perp frame data.
        splint_mesh: The final splint shell Mesh (after boolean difference).
        probes: List of tuples: (probe_name, location_name, offset, local_direction)
                e.g. [("mcp_dorsal", "mcp", 0.0, (0, 1, 0)),
                      ("pip_volar",  "pip", 0.0, (0, -1, 0))]
        far_distance: How far (mm) to extend probe rays. Also sets preview line length.

    Returns:
        (results, preview) tuple:
          results: dict mapping probe_name to a dict with:
              "location": str, "offset": float, "direction": list,
              "thickness_mm": float or None
          preview: list of geometry objects for GH preview:
              Line from centerline to far_distance for each probe +
              TextDot at outer hit (with thickness) or at line end ("miss").
        results dict is suitable for passing as custom_metadata to save_job_output.
    """
    results = {}
    preview = []
    for probe_name, location, offset, local_dir in probes:
        measurement = measure_wall_thickness(finger_result, splint_mesh, location, offset, local_dir, far_distance=far_distance)
        thickness = measurement["thickness_mm"] if measurement else None
        results[probe_name] = {
            "location": location,
            "offset": offset,
            "direction": list(local_dir),
            "thickness_mm": thickness,
        }

        # Build full-length preview line and label for every probe
        if measurement:
            origin = measurement["ray_origin"]
            direction = measurement["ray_direction"]
            outer = measurement["outer_point"]
            end_pt = Point3d(origin + direction * far_distance)

            # Line always runs from centerline to far end of probe
            preview.append(rg.Line(origin, end_pt))

            if outer and thickness is not None:
                label = "{:.2f}mm".format(thickness)
                preview.append(rg.TextDot(label, outer))
            else:
                # Place "miss" at the far end
                preview.append(rg.TextDot("miss", end_pt))

        log("  probe '{}': {} mm".format(probe_name, thickness))

    measured = sum(1 for v in results.values() if v["thickness_mm"] is not None)
    log("measure_thickness_probes: {}/{} probes succeeded".format(measured, len(probes)))
    return results, preview


def measure_thickness_probes_batch(finger_results, splint_meshes, probes, far_distance=200.0):
    """Run measurement probes across multiple finger_model/splint pairs.

    Each finger_result[i] corresponds to splint_meshes[i]. The same probe
    set is applied to every pair.

    Args:
        finger_results: FingerModelResult or list of them.
        splint_meshes: Splint Mesh/Brep or list of them (same length as finger_results).
        probes: Probe definitions (same format as measure_thickness_probes).
        far_distance: How far (mm) to extend probe rays.

    Returns:
        (all_results, all_preview) tuple:
          all_results: list of dicts, one per finger/splint pair.
              Each dict maps probe_name to measurement data (same shape
              as measure_thickness_probes output).
          all_preview: list of lists, one preview list per splint.
              Each sub-list contains Lines + TextDots for that splint's probes.
    """
    # Normalize and unwrap GH wrappers
    finger_results = gh_decode(finger_results)
    splint_meshes = gh_decode(splint_meshes)

    if len(finger_results) != len(splint_meshes):
        log("measure_thickness_probes_batch: mismatched list lengths: {} finger_results vs {} splint_meshes".format(
            len(finger_results), len(splint_meshes)))
        return [], []

    all_results = []
    all_preview = []

    for i, (finger_result, splint_mesh) in enumerate(zip(finger_results, splint_meshes)):
        log("measure_thickness_probes_batch: splint {}/{}".format(i + 1, len(finger_results)))
        results, preview = measure_thickness_probes(finger_result, splint_mesh, probes, far_distance)
        all_results.append(results)
        all_preview.append(preview)

    # Build a GH DataTree so downstream components get real geometry per branch
    from Grasshopper import DataTree
    from Grasshopper.Kernel.Data import GH_Path
    preview_tree = DataTree[object]()
    for i, preview in enumerate(all_preview):
        path = GH_Path(i)
        for item in preview:
            preview_tree.Add(item, path)

    total = len(finger_results) * len(probes)
    total_measured = sum(
        sum(1 for v in r.values() if v["thickness_mm"] is not None)
        for r in all_results
    )
    log("measure_thickness_probes_batch: {}/{} total probes succeeded across {} splints".format(
        total_measured, total, len(finger_results)))
    return all_results, preview_tree
