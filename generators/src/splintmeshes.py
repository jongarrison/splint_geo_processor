"""
splintmeshes.py
Mesh export for Rhino/Grasshopper.
Exports one or more meshes to a single STL, OBJ, or 3MF file
via Rhino's bake-select-export pipeline.
"""

import scriptcontext as sc
import System
import Rhino
import Rhino.Geometry as rg
import random
import string
import os
import json
import time
import math
from pathlib import Path
from splintcommon import log, get_generation_elapsed


class MeshExportError(Exception):
    """Raised when mesh export fails."""
    pass


def _add_layer(layer_name):
    """Add a temporary layer to the active Rhino document."""
    sc.doc = Rhino.RhinoDoc.ActiveDoc
    if not Rhino.DocObjects.Layer.IsValidName(layer_name):
        raise MeshExportError("'{}' is not a valid layer name".format(layer_name))
    layer_index = sc.doc.Layers.Find(layer_name, True)
    if layer_index >= 0:
        raise MeshExportError("Layer '{}' already exists".format(layer_name))
    layer_index = sc.doc.Layers.Add(layer_name, System.Drawing.Color.Black)
    if layer_index < 0:
        raise MeshExportError("Unable to add layer '{}'".format(layer_name))
    return layer_index


def _delete_layer(layer_name):
    """Delete a layer and all its objects from the active Rhino document."""
    sc.doc = Rhino.RhinoDoc.ActiveDoc
    layer_index = sc.doc.Layers.Find(layer_name, True)
    if layer_index < 0:
        log("  Warning: layer '{}' not found during cleanup".format(layer_name))
        return False
    rc = sc.doc.Layers.Purge(layer_index, True)
    sc.doc.Views.Redraw()
    return rc


def _bake_mesh(layer_name, mesh, mesh_name=None):
    """Bake a mesh object onto a layer in the active Rhino document."""
    sc.doc = Rhino.RhinoDoc.ActiveDoc
    if mesh.ObjectType != Rhino.DocObjects.ObjectType.Mesh:
        raise MeshExportError("Object is not a mesh: {}".format(type(mesh).__name__))
    attr = Rhino.DocObjects.ObjectAttributes()
    layer_index = sc.doc.Layers.Find(layer_name, True)
    if layer_index < 0:
        raise MeshExportError("Layer '{}' does not exist".format(layer_name))
    attr.LayerIndex = layer_index
    if mesh_name is not None:
        attr.Name = mesh_name
    return sc.doc.Objects.AddMesh(mesh, attr)


def _get_obj_settings():
    """OBJ export command-line settings."""
    cfg = "_Geometry=_Mesh "
    cfg += "_EndOfLine=CRLF "
    cfg += "_ExportRhinoObjectNames=_ExportObjectsAsOBJGroups "
    cfg += "_ExportMeshTextureCoordinates=_Yes "
    cfg += "_ExportMeshVertexNormals=_Yes "
    cfg += "_ExportMeshVertexColors=_Yes "
    cfg += "_CreateNGons=_No "
    cfg += "_ExportMaterialDefinitions=_No "
    cfg += "_YUp=_Yes "
    cfg += "_WrapLongLines=_No "
    cfg += "_VertexWelding=_Unmodified "
    cfg += "_WritePrecision=4 "
    cfg += "_Enter "
    cfg += "_DetailedOptions "
    cfg += "_JaggedSeams=_No "
    cfg += "_PackTextures=_No "
    cfg += "_Refine=_No "
    cfg += "_SimplePlane=_No "
    cfg += "_AdvancedOptions "
    cfg += "_Angle=0 "
    cfg += "_AspectRatio=0 "
    cfg += "_Distance=0.0 "
    cfg += "_Density=0.5 "
    cfg += "_Grid=0 "
    cfg += "_MaxEdgeLength=0 "
    cfg += "_MinEdgeLength=0.0001 "
    cfg += "_Enter _Enter"
    return cfg, "obj"


def _get_stl_settings():
    """Binary STL export command-line settings."""
    e_str = "_ExportFileAs=_Binary "
    e_str += "_ExportUnfinishedObjects=_Yes "
    e_str += "_UseSimpleDialog=_No "
    e_str += "_Enter _DetailedOptions "
    e_str += "_JaggedSeams=_No "
    e_str += "_PackTextures=_No "
    # Refine/SimplePlane only affect NURBS->mesh conversion; we always feed
    # pre-baked meshes. Leaving these on caused silent macro abort on a
    # ~1M-face SizingRings export (RunScript returned True in 0.0005s, no file).
    e_str += "_Refine=_No "
    e_str += "_SimplePlane=_No "
    e_str += "_Enter _Enter"
    return e_str, "stl"


def _get_3mf_settings():
    """3MF export command-line settings."""
    e_str = "_ExportUnfinishedObjects=_Yes "
    e_str += "_UseSimpleDialog=_No "
    e_str += "_Enter"
    return e_str, "3mf"


def _coerce_geometry_for_meshing(obj):
    """Coerce GH/Rhino references to Mesh or Brep for meshing/export prep.

    Returns:
        tuple: (kind, geometry, method)
               kind is "mesh", "brep", or None.
    """
    if obj is None:
        return None, None, "None"

    if isinstance(obj, rg.Mesh):
        return "mesh", obj, "AlreadyMesh"
    if isinstance(obj, rg.Brep):
        return "brep", obj, "AlreadyBrep"

    if isinstance(obj, rg.Extrusion):
        try:
            brep = obj.ToBrep()
            if brep is not None:
                return "brep", brep, "Extrusion.ToBrep"
        except Exception:
            pass

    if isinstance(obj, rg.Surface):
        try:
            brep = obj.ToBrep()
            if brep is not None:
                return "brep", brep, "Surface.ToBrep"
        except Exception:
            pass

    try:
        import rhinoscriptsyntax as rs
        mesh = rs.coercemesh(obj)
        if mesh is not None:
            return "mesh", mesh, "rhinoscriptsyntax.coercemesh"
        brep = rs.coercebrep(obj)
        if brep is not None:
            return "brep", brep, "rhinoscriptsyntax.coercebrep"
    except Exception:
        pass

    return None, None, "Uncoercible:{}".format(type(obj).__name__)


def _build_meshing_parameters(
        quality="high",
        target_edge_length=None,
        min_edge_length=0.01,
        max_edge_length=None,
    jagged_seams=False,
        simple_planes=True,
        refine_grid=False):
    """Build meshing parameters for Brep-to-mesh conversion."""
    q = (quality or "high").lower()
    if q == "fast":
        params = rg.MeshingParameters.FastRenderMesh
    elif q == "analysis":
        params = rg.MeshingParameters.DefaultAnalysisMesh
    else:
        params = rg.MeshingParameters.Default

    try:
        # Explicit max_edge_length wins if both are provided.
        if max_edge_length is not None and hasattr(params, "MaximumEdgeLength"):
            params.MaximumEdgeLength = float(max_edge_length)
        elif target_edge_length is not None and hasattr(params, "MaximumEdgeLength"):
            params.MaximumEdgeLength = float(target_edge_length)
        if min_edge_length is not None and hasattr(params, "MinimumEdgeLength"):
            params.MinimumEdgeLength = float(min_edge_length)

        if hasattr(params, "JaggedSeams"):
            params.JaggedSeams = bool(jagged_seams)
        if hasattr(params, "SimplePlanes"):
            params.SimplePlanes = bool(simple_planes)
        if hasattr(params, "RefineGrid"):
            params.RefineGrid = bool(refine_grid)
    except Exception as err:
        log("  Warning: failed to apply meshing edge length settings: {}".format(err))

    return params


def _topology_edge_counts(mesh):
    """Return topology edge counts for mesh quality diagnostics."""
    naked_count = 0
    interior_count = 0
    non_manifold_count = 0

    topo = getattr(mesh, "TopologyEdges", None)
    if topo is None:
        return {
            "naked_edges": None,
            "interior_edges": None,
            "non_manifold_edges": None,
        }

    for i in range(topo.Count):
        try:
            connected_faces = topo.GetConnectedFaces(i)
            face_count = len(connected_faces) if connected_faces is not None else 0
        except Exception:
            face_count = 0

        if face_count == 2:
            interior_count += 1
        elif face_count == 1:
            naked_count += 1
        else:
            non_manifold_count += 1

    return {
        "naked_edges": naked_count,
        "interior_edges": interior_count,
        "non_manifold_edges": non_manifold_count,
    }


def _mesh_quality(mesh):
    """Return mesh quality summary used for gating export readiness."""
    edge_counts = _topology_edge_counts(mesh)

    triangle_count = 0
    try:
        faces = mesh.Faces
        for fi in range(faces.Count):
            triangle_count += 2 if faces[fi].IsQuad else 1
    except Exception:
        # Fallback estimate if face accessor differs across runtime versions.
        triangle_count = int(mesh.Faces.Count)

    return {
        "is_valid": bool(mesh.IsValid),
        "is_closed": bool(mesh.IsClosed),
        "vertex_count": int(mesh.Vertices.Count),
        "face_count": int(mesh.Faces.Count),
        "triangle_count": int(triangle_count),
        "naked_edges": edge_counts["naked_edges"],
        "non_manifold_edges": edge_counts["non_manifold_edges"],
    }


def inspect_mesh_quality(
        input_meshes,
        require_valid=True,
        require_closed=True,
        require_manifold=True,
        max_naked_edges=0):
    """Return a JSON-serializable quality report for one or more meshes.

    This does not mutate or convert geometry. It is intended for quality
    reporting in metadata and for server-side observability.

    Args:
        input_meshes: Mesh or iterable of meshes.
        require_valid: If True, invalid meshes fail criteria.
        require_closed: If True, open meshes fail criteria.
        require_manifold: If True, meshes with non-manifold topology edges fail.
        max_naked_edges: Maximum allowed naked edge count.

    Returns:
        dict: JSON-ready report with per-mesh and aggregate metrics.
    """
    if input_meshes is None:
        raise ValueError("No meshes provided")

    if hasattr(input_meshes, '__iter__') and not isinstance(input_meshes, (str, bytes)):
        meshes = list(input_meshes)
    else:
        meshes = [input_meshes]

    if len(meshes) == 0:
        raise ValueError("No meshes provided")

    report = {
        "version": 1,
        "criteria": {
            "require_valid": bool(require_valid),
            "require_closed": bool(require_closed),
            "require_manifold": bool(require_manifold),
            "max_naked_edges": int(max_naked_edges),
        },
        "mesh_count": len(meshes),
        "mesh_pass_count": 0,
        "mesh_fail_count": 0,
        "overall_pass": True,
        "totals": {
            "total_vertices": 0,
            "total_faces": 0,
            "total_triangles": 0,
            "total_naked_edges": 0,
            "total_non_manifold_edges": 0,
            "invalid_meshes": 0,
            "open_meshes": 0,
        },
        "meshes": [],
    }

    for i, mesh in enumerate(meshes):
        mesh_report = {
            "index": i,
            "input_type": type(mesh).__name__,
            "is_valid": None,
            "is_closed": None,
            "watertight": None,
            "vertex_count": None,
            "face_count": None,
            "triangle_count": None,
            "naked_edges": None,
            "non_manifold_edges": None,
            "passes_criteria": False,
            "fail_reasons": [],
        }

        if mesh is None:
            mesh_report["fail_reasons"].append("MeshIsNone")
        elif not isinstance(mesh, rg.Mesh):
            mesh_report["fail_reasons"].append("NotMesh")
        else:
            q = _mesh_quality(mesh)
            mesh_report.update({
                "is_valid": q["is_valid"],
                "is_closed": q["is_closed"],
                "vertex_count": q["vertex_count"],
                "face_count": q["face_count"],
                "triangle_count": q["triangle_count"],
                "naked_edges": q["naked_edges"],
                "non_manifold_edges": q["non_manifold_edges"],
            })

            if q["naked_edges"] is not None and q["non_manifold_edges"] is not None:
                mesh_report["watertight"] = bool(
                    q["is_closed"] and q["naked_edges"] == 0 and q["non_manifold_edges"] == 0)

            report["totals"]["total_vertices"] += q["vertex_count"]
            report["totals"]["total_faces"] += q["face_count"]
            report["totals"]["total_triangles"] += q["triangle_count"]
            report["totals"]["total_naked_edges"] += q["naked_edges"] or 0
            report["totals"]["total_non_manifold_edges"] += q["non_manifold_edges"] or 0

            if require_valid and not q["is_valid"]:
                mesh_report["fail_reasons"].append("NotValid")
                report["totals"]["invalid_meshes"] += 1
            if require_closed and not q["is_closed"]:
                mesh_report["fail_reasons"].append("NotClosed")
                report["totals"]["open_meshes"] += 1
            if require_manifold and (q["non_manifold_edges"] or 0) > 0:
                mesh_report["fail_reasons"].append("NonManifoldEdges={}".format(
                    q["non_manifold_edges"]))
            if (q["naked_edges"] or 0) > max_naked_edges:
                mesh_report["fail_reasons"].append("NakedEdges={}".format(q["naked_edges"]))

        mesh_report["passes_criteria"] = len(mesh_report["fail_reasons"]) == 0
        if mesh_report["passes_criteria"]:
            report["mesh_pass_count"] += 1
        else:
            report["mesh_fail_count"] += 1
            report["overall_pass"] = False

        report["meshes"].append(mesh_report)

    report["estimated_binary_stl_size_mb"] = round(
        (84 + 50 * report["totals"]["total_triangles"]) / (1024.0 * 1024.0), 3)

    return report


def _log_mesh_quality(prefix, quality):
    """Log mesh quality details in one compact line."""
    log("{} valid={} closed={} verts={} faces={} tris={} naked_edges={} non_manifold_edges={}".format(
        prefix,
        quality["is_valid"],
        quality["is_closed"],
        quality["vertex_count"],
        quality["face_count"],
        quality["triangle_count"],
        quality["naked_edges"],
        quality["non_manifold_edges"],
    ))


def _apply_mesh_cleanup(mesh, weld_angle_radians, smoothing_iterations=0, fill_holes=False):
    """Apply conservative cleanup/repair operations on a mesh copy."""
    cleaned = mesh.DuplicateMesh()

    try:
        cleaned.Faces.CullDegenerateFaces()
    except Exception:
        pass

    try:
        cleaned.Vertices.CombineIdentical(True, True)
    except Exception:
        pass

    try:
        cleaned.Vertices.CullUnused()
    except Exception:
        pass

    if fill_holes:
        try:
            cleaned.FillHoles()
        except Exception:
            pass

    if weld_angle_radians is not None:
        try:
            cleaned.Weld(weld_angle_radians)
        except Exception:
            pass

    try:
        cleaned.UnifyNormals()
    except Exception:
        pass

    try:
        cleaned.Normals.ComputeNormals()
    except Exception:
        pass

    if smoothing_iterations > 0:
        for _ in range(int(smoothing_iterations)):
            smoothed = False
            try:
                cleaned.Smooth(0.35, True, True, True, True, rg.SmoothingCoordinateSystem.World)
                smoothed = True
            except Exception:
                pass

            if not smoothed:
                try:
                    cleaned.Smooth(0.35)
                    smoothed = True
                except Exception:
                    # Mesh smoothing overload differs across Rhino versions.
                    break

    try:
        cleaned.Compact()
    except Exception:
        pass

    return cleaned


def _extract_mesh_from_result(result):
    """Normalize shrinkwrap API returns to a single mesh if possible."""
    if isinstance(result, rg.Mesh):
        return result
    if result is None:
        return None

    try:
        items = list(result)
    except Exception:
        return None

    if not items:
        return None
    if isinstance(items[0], rg.Mesh):
        joined = rg.Mesh()
        for item in items:
            joined.Append(item)
        return joined
    return None


def _try_shrinkwrap_fallback(source_geometry, target_edge_length=None):
    """Best-effort ShrinkWrap fallback across RhinoCommon API variations.

    Rhino 8+ exposes Mesh.ShrinkWrap overloads. This helper attempts those
    first, then keeps legacy probes for compatibility.
    """
    if source_geometry is None:
        return None

    shrink_params = None
    if hasattr(rg, "ShrinkWrapParameters"):
        try:
            shrink_params = rg.ShrinkWrapParameters()
            if target_edge_length is not None and hasattr(shrink_params, "TargetEdgeLength"):
                shrink_params.TargetEdgeLength = float(target_edge_length)
        except Exception:
            shrink_params = None

    if shrink_params is None:
        log("  ShrinkWrap fallback unavailable: ShrinkWrapParameters not found")
        return None

    meshing_params = _build_meshing_parameters(
        quality="analysis",
        target_edge_length=target_edge_length,
        min_edge_length=0.01,
        max_edge_length=target_edge_length,
        jagged_seams=False,
        simple_planes=True,
        refine_grid=False,
    )

    token_none = None
    try:
        token_none = getattr(System.Threading.CancellationToken, "None")
    except Exception:
        pass

    geometry_sequences = []
    if isinstance(source_geometry, rg.GeometryBase):
        geometry_sequences.append([source_geometry])
        try:
            geometry_sequences.append(System.Array[rg.GeometryBase]([source_geometry]))
        except Exception:
            pass

    mesh_sequences = []
    if isinstance(source_geometry, rg.Mesh):
        mesh_sequences.append([source_geometry])
        try:
            mesh_sequences.append(System.Array[rg.Mesh]([source_geometry]))
        except Exception:
            pass

    attempts = []

    # Rhino 8 static API: Mesh.ShrinkWrap(...)
    if hasattr(rg.Mesh, "ShrinkWrap"):
        mesh_shrinkwrap = rg.Mesh.ShrinkWrap

        for seq in geometry_sequences:
            attempts.append((
                "Mesh.ShrinkWrap(IEnumerable<GeometryBase>, params, meshing)",
                mesh_shrinkwrap,
                (seq, shrink_params, meshing_params),
            ))
            if token_none is not None:
                attempts.append((
                    "Mesh.ShrinkWrap(IEnumerable<GeometryBase>, params, meshing, token)",
                    mesh_shrinkwrap,
                    (seq, shrink_params, meshing_params, token_none),
                ))

        for seq in mesh_sequences:
            attempts.append((
                "Mesh.ShrinkWrap(IEnumerable<Mesh>, params)",
                mesh_shrinkwrap,
                (seq, shrink_params),
            ))
            if token_none is not None:
                attempts.append((
                    "Mesh.ShrinkWrap(IEnumerable<Mesh>, params, token)",
                    mesh_shrinkwrap,
                    (seq, shrink_params, token_none),
                ))

    # Rhino 8 mesh instance API: mesh.ShrinkWrap(...)
    if isinstance(source_geometry, rg.Mesh) and hasattr(source_geometry, "ShrinkWrap"):
        attempts.append((
            "mesh.ShrinkWrap(params)",
            source_geometry.ShrinkWrap,
            (shrink_params,),
        ))
        if token_none is not None:
            attempts.append((
                "mesh.ShrinkWrap(params, token)",
                source_geometry.ShrinkWrap,
                (shrink_params, token_none),
            ))

    # Legacy probes retained for compatibility.
    if hasattr(rg.Mesh, "CreateFromShrinkWrap"):
        attempts.append(("Mesh.CreateFromShrinkWrap", rg.Mesh.CreateFromShrinkWrap, ([source_geometry], shrink_params)))

    if hasattr(rg, "ShrinkWrap"):
        sw = rg.ShrinkWrap
        if hasattr(sw, "Create"):
            attempts.append(("ShrinkWrap.Create", sw.Create, ([source_geometry], shrink_params)))

    if not attempts:
        log("  ShrinkWrap fallback unavailable in this RhinoCommon runtime")
        return None

    for method_name, method, args in attempts:
        try:
            result = method(*args)
            mesh = _extract_mesh_from_result(result)
            if mesh is not None:
                log("  ShrinkWrap fallback succeeded via {}".format(method_name))
                return mesh
        except Exception:
            pass

    log("  ShrinkWrap fallback attempted but failed")
    return None


def convert_to_export_meshes(
        input_geometry,
        quality="high",
        target_edge_length=None,
        min_edge_length=0.01,
        max_edge_length=None,
    jagged_seams=False,
    simple_planes=True,
    refine_grid=False,
        weld_angle_degrees=180.0,
        smoothing_iterations=0,
        repair_if_needed=True,
        require_closed=True,
        require_manifold=True,
        shrinkwrap_fallback=False):
    """Convert Brep/Mesh/Guid inputs to export-ready meshes with tunable quality.

    This function is intended as the final conversion step before save_mesh.

    Args:
        input_geometry: Mesh/Brep/Guid or iterable of them.
        quality: "high" (default), "analysis", or "fast" meshing profile.
        target_edge_length: Preferred edge length for meshing (mm), used as
            max edge length when max_edge_length is not provided.
        min_edge_length: Lower bound for generated mesh edges.
        max_edge_length: Optional explicit max edge length (overrides target).
        jagged_seams: Allow non-matching seams during meshing. This can help
            local shape fidelity but may produce open seam edges.
        simple_planes: Use planar-aware meshing to preserve flat surfaces.
        refine_grid: Extra meshing refinement (usually increases file size).
        weld_angle_degrees: Welding angle for cleanup (default 180 deg).
        smoothing_iterations: Optional smoothing passes after meshing.
        repair_if_needed: Apply extra cleanup/hole-fill if quality gate fails.
        require_closed: Require closed meshes.
        require_manifold: Require zero non-manifold topology edges.
        shrinkwrap_fallback: Try ShrinkWrap if mesh still fails quality gate.

    Returns:
        list[Rhino.Geometry.Mesh]: Export-ready meshes.

    Raises:
        MeshExportError: If conversion fails or quality requirements are not met.
        ValueError: If no geometry is provided.
    """
    if input_geometry is None:
        raise ValueError("No input geometry provided")

    if hasattr(input_geometry, '__iter__') and not isinstance(input_geometry, (str, bytes)):
        items = list(input_geometry)
    else:
        items = [input_geometry]

    if not items:
        raise ValueError("No input geometry provided")

    if require_closed and jagged_seams:
        log("  Warning: jagged_seams=True can produce open seam edges when require_closed=True")
        log("           Consider jagged_seams=False for watertight output")

    params = _build_meshing_parameters(
        quality=quality,
        target_edge_length=target_edge_length,
        min_edge_length=min_edge_length,
        max_edge_length=max_edge_length,
        jagged_seams=jagged_seams,
        simple_planes=simple_planes,
        refine_grid=refine_grid,
    )
    weld_angle_radians = math.radians(float(weld_angle_degrees)) if weld_angle_degrees is not None else None

    log("convert_to_export_meshes: {} item(s), quality={}, target_edge_length={}, max_edge_length={}, min_edge_length={}, smoothing={}, repair_if_needed={}, shrinkwrap_fallback={}".format(
        len(items), quality, target_edge_length, max_edge_length, min_edge_length,
        smoothing_iterations, repair_if_needed, shrinkwrap_fallback))
    log("  meshing_flags: jagged_seams={}, simple_planes={}, refine_grid={}".format(
        jagged_seams, simple_planes, refine_grid))
    if weld_angle_degrees is not None and float(weld_angle_degrees) >= 120.0:
        log("  Note: high weld_angle_degrees={} may visually soften sharp creases; try 30-60 for edge preservation".format(
            weld_angle_degrees))

    output_meshes = []
    total_triangles = 0
    for i, item in enumerate(items):
        kind, geometry, method = _coerce_geometry_for_meshing(item)
        if kind is None or geometry is None:
            raise MeshExportError("Item {} could not be coerced to Brep/Mesh ({})".format(i, method))

        log("  item {}: kind={} via {}".format(i, kind, method))

        if kind == "mesh":
            mesh = geometry.DuplicateMesh()
        else:
            parts = rg.Mesh.CreateFromBrep(geometry, params)
            if not parts or len(parts) == 0:
                raise MeshExportError("Item {} Brep meshing returned no meshes".format(i))
            mesh = rg.Mesh()
            for part in parts:
                mesh.Append(part)

        mesh = _apply_mesh_cleanup(
            mesh,
            weld_angle_radians=weld_angle_radians,
            smoothing_iterations=smoothing_iterations,
            fill_holes=False,
        )
        q = _mesh_quality(mesh)
        _log_mesh_quality("  item {} mesh quality:".format(i), q)

        fails_closed = require_closed and (not q["is_closed"])
        fails_manifold = require_manifold and ((q["non_manifold_edges"] or 0) > 0)
        fails_basic = (not q["is_valid"]) or fails_closed or fails_manifold

        if fails_basic and repair_if_needed:
            log("  item {}: attempting mesh repair pass".format(i))
            mesh = _apply_mesh_cleanup(
                mesh,
                weld_angle_radians=weld_angle_radians,
                smoothing_iterations=smoothing_iterations,
                fill_holes=True,
            )
            q = _mesh_quality(mesh)
            _log_mesh_quality("  item {} post-repair quality:".format(i), q)
            fails_closed = require_closed and (not q["is_closed"])
            fails_manifold = require_manifold and ((q["non_manifold_edges"] or 0) > 0)
            fails_basic = (not q["is_valid"]) or fails_closed or fails_manifold

        if fails_basic and shrinkwrap_fallback:
            log("  item {}: attempting ShrinkWrap fallback".format(i))
            shrink_source = geometry if kind == "brep" else mesh
            shrink_mesh = _try_shrinkwrap_fallback(shrink_source, target_edge_length=target_edge_length)
            if shrink_mesh is not None:
                mesh = _apply_mesh_cleanup(
                    shrink_mesh,
                    weld_angle_radians=weld_angle_radians,
                    smoothing_iterations=smoothing_iterations,
                    fill_holes=True,
                )
                q = _mesh_quality(mesh)
                _log_mesh_quality("  item {} shrinkwrap quality:".format(i), q)
                fails_closed = require_closed and (not q["is_closed"])
                fails_manifold = require_manifold and ((q["non_manifold_edges"] or 0) > 0)
                fails_basic = (not q["is_valid"]) or fails_closed or fails_manifold

        if fails_basic:
            raise MeshExportError(
                "Item {} mesh failed quality gate: valid={}, closed={}, non_manifold_edges={}".format(
                    i, q["is_valid"], q["is_closed"], q["non_manifold_edges"]))

        total_triangles += q["triangle_count"]
        output_meshes.append(mesh)

    est_binary_stl_mb = (84 + 50 * total_triangles) / (1024.0 * 1024.0)
    log("convert_to_export_meshes: total_triangles={} est_binary_stl_size_mb={:.2f}".format(
        total_triangles, est_binary_stl_mb))

    return output_meshes


def save_mesh(input_meshes, directory, root_filename, format_type="stl"):
    """Export one or more meshes to a single file.
    
    Uses Rhino's bake-select-export pipeline: bakes meshes to a temp layer,
    selects them, runs the export command, then cleans up.
    
    Args:
        input_meshes: A single Rhino.Geometry.Mesh or a list of them.
                      All meshes are combined into one output file.
        directory: Directory path (no dots allowed).
        root_filename: Base filename without extension (no dots allowed).
        format_type: "stl" (default), "obj", or "3mf".
        
    Returns:
        bool: True if export succeeded.
        
    Raises:
        MeshExportError: If export fails.
        ValueError: If inputs are invalid.
    """
    # Normalize single mesh to list
    if not hasattr(input_meshes, '__iter__'):
        meshes = [input_meshes]
    else:
        meshes = list(input_meshes)

    log("save_mesh: {} mesh(es), format={}, filename='{}'".format(
        len(meshes), format_type.upper(), root_filename))
    log("save_mesh: directory='{}'".format(directory))

    if len(meshes) == 0:
        raise ValueError("No meshes provided")
    for i, m in enumerate(meshes):
        if m is None:
            raise ValueError("Mesh {} is None".format(i))
        log("  mesh {}: type={}, vertices={}, faces={}".format(
            i, type(m).__name__,
            m.Vertices.Count if hasattr(m, 'Vertices') else '?',
            m.Faces.Count if hasattr(m, 'Faces') else '?'))

    if directory is None or root_filename is None:
        raise ValueError("directory and root_filename are required")
    if "." in directory or "." in root_filename:
        raise ValueError("directory and root_filename must not contain dots")

    # Pick export settings
    fmt = format_type.lower()
    if fmt == "obj":
        export_config, export_extension = _get_obj_settings()
    elif fmt == "3mf":
        export_config, export_extension = _get_3mf_settings()
    else:
        export_config, export_extension = _get_stl_settings()
    log("  Using {} export settings".format(fmt.upper()))

    export_fname = "{}.{}".format(root_filename, export_extension)
    export_fpath = Path(os.path.join(directory, export_fname))
    log("  Output path: {}".format(export_fpath))

    t_start = time.process_time()
    sc.doc = Rhino.RhinoDoc.ActiveDoc
    sc.doc.Views.RedrawEnabled = True
    temp_layer = None

    try:
        # Create temp layer
        temp_layer = "".join(random.choice(string.ascii_uppercase) for _ in range(9))
        _add_layer(temp_layer)
        t_layer = time.process_time()
        log("  Created temp layer '{}' ({:.4f}s)".format(temp_layer, t_layer - t_start))

        # Bake all meshes
        mesh_ids = []
        for i, mesh in enumerate(meshes):
            mid = _bake_mesh(temp_layer, mesh)
            mesh_ids.append(mid)
        t_bake = time.process_time()
        log("  Baked {} mesh(es) ({:.4f}s)".format(len(mesh_ids), t_bake - t_layer))

        # Select only the baked meshes
        sc.doc.Objects.UnselectAll()
        for mid in mesh_ids:
            sc.doc.Objects.Select(mid)
        t_select = time.process_time()
        log("  Selected mesh(es) ({:.4f}s)".format(t_select - t_bake))

        # Remove existing file if present
        if export_fpath.exists():
            log("  Removing existing file: {}".format(export_fpath))
            export_fpath.unlink()
            time.sleep(1.0)
            if export_fpath.exists():
                raise MeshExportError("Failed to remove existing file: {}".format(export_fpath))

        # Run the Rhino export command
        cmd = '_-Export _Pause "{}" {} _Enter'.format(export_fpath, export_config)
        log("  Running export command...")
        rc = Rhino.RhinoApp.RunScript(cmd, True)
        t_export = time.process_time()
        log("  Export RunScript returned: {} ({:.4f}s)".format(rc, t_export - t_select))

        # RunScript return value is unreliable -- verify by checking the file
        if not export_fpath.exists():
            raise MeshExportError("Export file not found after RunScript: {}".format(export_fpath))

        file_size = os.path.getsize(str(export_fpath))
        if file_size < 100:
            raise MeshExportError("Export file suspiciously small ({} bytes): {}".format(
                file_size, export_fpath))
        log("  File created: {} ({} bytes)".format(export_fpath.name, file_size))

        # Collect per-mesh metadata
        mesh_quality = None
        quality_by_index = {}
        try:
            mesh_quality = inspect_mesh_quality(meshes)
            log("  Mesh quality summary: pass={}/{} fail={} naked_edges={} non_manifold_edges={}".format(
                mesh_quality["mesh_pass_count"],
                mesh_quality["mesh_count"],
                mesh_quality["mesh_fail_count"],
                mesh_quality["totals"]["total_naked_edges"],
                mesh_quality["totals"]["total_non_manifold_edges"],
            ))
            quality_by_index = {m["index"]: m for m in mesh_quality.get("meshes", [])}
        except Exception as quality_err:
            log("  Warning: mesh quality report failed: {}".format(quality_err))

        mesh_meta_list = []
        for i, mesh in enumerate(meshes):
            bbox = mesh.GetBoundingBox(True)
            dims = [bbox.Max.X - bbox.Min.X, bbox.Max.Y - bbox.Min.Y, bbox.Max.Z - bbox.Min.Z]
            item_meta = {
                "index": i,
                "volume_mm3": round(mesh.Volume(), 4) if hasattr(mesh, 'Volume') else None,
                "is_closed": bool(mesh.IsClosed) if hasattr(mesh, 'IsClosed') else None,
                "bbox_dimensions": [round(d, 4) for d in dims],
            }

            item_quality = quality_by_index.get(i)
            if item_quality is not None:
                item_meta.update({
                    "quality_pass": item_quality.get("passes_criteria"),
                    "watertight": item_quality.get("watertight"),
                    "naked_edges": item_quality.get("naked_edges"),
                    "non_manifold_edges": item_quality.get("non_manifold_edges"),
                })

            mesh_meta_list.append(item_meta)

        elapsed = get_generation_elapsed()
        metadata = {
            "mesh_count": len(meshes),
            "meshes": mesh_meta_list,
            "mesh_quality": mesh_quality,
            "file_size_bytes": file_size,
            "elapsed_time_seconds": round(elapsed, 2) if elapsed is not None else None,
        }

        meta_path = Path(os.path.join(directory, "{}.meta.json".format(root_filename)))
        try:
            with open(str(meta_path), 'w', encoding='utf-8') as mf:
                json.dump(metadata, mf, indent=2)
            log("  Metadata written: {}".format(meta_path.name))
        except Exception as meta_err:
            log("  Warning: failed to write metadata: {}".format(meta_err))

        t_total = time.process_time() - t_start
        log("save_mesh: Complete ({:.4f}s total)".format(t_total))
        return True

    finally:
        # Always clean up the temp layer
        if temp_layer is not None:
            try:
                _delete_layer(temp_layer)
                log("  Cleaned up temp layer")
            except Exception as cleanup_err:
                log("  Warning: cleanup failed: {}".format(cleanup_err))


def save_job_output(input_meshes, directory, root_filename, format_type="stl", custom_metadata=None):
    """Export meshes and attach arbitrary per-job metadata.

    Calls save_mesh, then merges custom_metadata into the .meta.json file
    under a "custom" key. The extra data rides through the existing pipeline
    with no changes to the processor or server.

    Args:
        input_meshes, directory, root_filename, format_type: passed to save_mesh.
        custom_metadata: Optional dict of arbitrary key/value pairs to include
                         in the metadata JSON (e.g. wall thicknesses, angles).

    Returns:
        bool: True if export succeeded.

    Raises:
        Same as save_mesh.
    """
    result = save_mesh(input_meshes, directory, root_filename, format_type)

    if custom_metadata and isinstance(custom_metadata, dict):
        meta_path = Path(os.path.join(directory, "{}.meta.json".format(root_filename)))
        try:
            with open(str(meta_path), 'r', encoding='utf-8') as f:
                metadata = json.load(f)
            metadata["custom"] = custom_metadata
            with open(str(meta_path), 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2)
            log("  Custom metadata merged ({} keys)".format(len(custom_metadata)))
        except Exception as err:
            log("  Warning: failed to merge custom metadata: {}".format(err))

    return result
