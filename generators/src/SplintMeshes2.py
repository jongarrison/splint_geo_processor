"""
SplintMeshes2.py

Simple mesh conversion and export for the Python-native splint pipeline. Replaces
splintmeshes.py's over-engineered approach for new code (RelativeMotion and future designs).
Older designs still use splintmeshes.py and should migrate here when refactored.

Design principles (learned from splintmeshes.py's issues):
  - Use Rhino's "Smooth and slower" meshing preset directly. It just works on valid closed
    solids - no franken-parameter-set layered on top of MeshingParameters.Default.
  - No quality gating that rejects meshes. The brep is already validated upstream (IsSolid,
    IsValid, _log_splint_health). If meshing produces naked edges, log them as a warning -
    don't raise. The slicer (Bambu Studio) handles minor mesh imperfections fine.
  - No repair pipeline. If the mesh isn't closed, log it. Don't FillHoles/Weld/ShrinkWrap -
    those mutations can make things worse and hide the real problem (which is always upstream
    in the brep, not in the meshing).
  - Separate concerns: mesh_brep() meshes, export_mesh() exports, inspect_mesh() logs.
    No single function does all three.
"""

import os
import json
import time
import random
import string
import scriptcontext as sc
import System
import Rhino
import Rhino.Geometry as rg
from pathlib import Path
from splintcommon import log, get_generation_elapsed, confirm_job_is_processed_and_exit


class MeshExportError(Exception):
    """Raised when mesh export fails (file I/O, Rhino command abort, etc.)."""
    pass


def mesh_brep(brep):
    """Mesh a brep using Rhino's 'Smooth and slower' quality preset.

    This matches the settings from Rhino's Mesh command with the 'Smooth and slower' preset:
    Density=0.8, MaxAngle=20, MinEdgeLength=0.0001, SimplePlanes=True, RefineGrid=True,
    MinInitialGridQuads=16, ClosedObjectPostProcess=True.

    No quality gating - the caller decides what to do with the result. Use inspect_mesh()
    to log diagnostics if needed.

    Args:
        brep: a single rg.Brep (should be a valid closed solid for best results).

    Returns:
        rg.Mesh: the meshed result (single joined mesh).

    Raises:
        ValueError: brep is None or meshing returned nothing.
    """
    if brep is None:
        raise ValueError("mesh_brep: brep is None")

    # Rhino's "Smooth and slower" preset parameters
    params = rg.MeshingParameters()
    params.RelativeTolerance = 0.8       # Density
    params.MaximumAngle = 20.0           # degrees (Rhino converts internally)
    params.MinimumEdgeLength = 0.0001
    params.MaximumEdgeLength = 0.0       # 0 = no limit
    params.SimplePlanes = True
    params.RefineGrid = True
    params.GridMinCount = 16             # MinInitialGridQuads
    params.JaggedSeams = False
    if hasattr(params, "ClosedObjectPostProcess"):
        params.ClosedObjectPostProcess = True

    parts = rg.Mesh.CreateFromBrep(brep, params)
    if parts is None or len(parts) == 0:
        raise ValueError("mesh_brep: Mesh.CreateFromBrep returned nothing")

    mesh = rg.Mesh()
    for part in parts:
        mesh.Append(part)

    # Basic cleanup (non-destructive, no hole filling)
    mesh.Vertices.CombineIdentical(True, True)
    mesh.Vertices.CullUnused()
    mesh.Faces.CullDegenerateFaces()
    mesh.UnifyNormals()
    mesh.Normals.ComputeNormals()
    mesh.Compact()

    return mesh


def inspect_mesh(mesh, label=""):
    """Log mesh diagnostics without gating. Returns a dict of the metrics for callers
    that want to inspect programmatically.

    Logs: valid, closed, vertex/face/triangle count, naked edges, non-manifold edges.
    """
    naked_count = 0
    non_manifold_count = 0
    topo = getattr(mesh, "TopologyEdges", None)
    if topo is not None:
        for i in range(topo.Count):
            try:
                fc = len(topo.GetConnectedFaces(i))
            except Exception:
                fc = 0
            if fc == 1:
                naked_count += 1
            elif fc > 2:
                non_manifold_count += 1

    tri_count = 0
    try:
        for fi in range(mesh.Faces.Count):
            tri_count += 2 if mesh.Faces[fi].IsQuad else 1
    except Exception:
        tri_count = mesh.Faces.Count

    prefix = "inspect_mesh"
    if label:
        prefix = "inspect_mesh [{0}]".format(label)
    log("{0}: valid={1} closed={2} verts={3} faces={4} tris={5} "
        "naked_edges={6} non_manifold={7}".format(
            prefix, mesh.IsValid, mesh.IsClosed, mesh.Vertices.Count,
            mesh.Faces.Count, tri_count, naked_count, non_manifold_count))

    return {
        "is_valid": bool(mesh.IsValid),
        "is_closed": bool(mesh.IsClosed),
        "vertex_count": int(mesh.Vertices.Count),
        "face_count": int(mesh.Faces.Count),
        "triangle_count": int(tri_count),
        "naked_edges": naked_count,
        "non_manifold_edges": non_manifold_count,
    }


def export_mesh(meshes, directory, root_filename, format_type="3mf",
                emit_pipeline_signal=True):
    """Export one or more meshes to a single file via Rhino's bake-select-export pipeline.

    Args:
        meshes: a single rg.Mesh or a list of rg.Mesh.
        directory: output directory path (no dots allowed).
        root_filename: base filename without extension (no dots allowed).
        format_type: "3mf" (default), "stl", or "obj".
        emit_pipeline_signal: when True, emits [PIPELINE_RESULT:SUCCESS/FAILURE] sentinel
            for the geo processor's log scanner.

    Returns:
        dict: {"file_path": str, "file_size_bytes": int, "mesh_quality": dict}

    Raises:
        MeshExportError: export command failed or produced no/invalid file.
        ValueError: invalid inputs.
    """
    def _signal(is_success, reason):
        if not emit_pipeline_signal:
            return
        try:
            confirm_job_is_processed_and_exit(root_filename or "unknown", is_success, reason)
        except Exception:
            pass

    if not hasattr(meshes, '__iter__') or isinstance(meshes, (str, bytes)):
        meshes = [meshes]
    else:
        meshes = list(meshes)

    if not meshes or any(m is None for m in meshes):
        _signal(False, "No meshes or None mesh")
        raise ValueError("export_mesh: no valid meshes provided")

    if directory is None or root_filename is None:
        _signal(False, "directory and root_filename required")
        raise ValueError("export_mesh: directory and root_filename required")

    # Pick export settings
    fmt = (format_type or "3mf").lower()
    if fmt == "stl":
        ext = "stl"
        settings = ("_ExportFileAs=_Binary _ExportUnfinishedObjects=_Yes "
                     "_UseSimpleDialog=_No _Enter _DetailedOptions "
                     "_JaggedSeams=_No _PackTextures=_No _Refine=_No "
                     "_SimplePlane=_No _Enter _Enter")
    elif fmt == "obj":
        ext = "obj"
        settings = ("_Geometry=_Mesh _EndOfLine=CRLF "
                     "_ExportRhinoObjectNames=_ExportObjectsAsOBJGroups "
                     "_ExportMeshTextureCoordinates=_Yes _ExportMeshVertexNormals=_Yes "
                     "_YUp=_Yes _WrapLongLines=_No _VertexWelding=_Unmodified "
                     "_WritePrecision=4 _Enter _DetailedOptions _JaggedSeams=_No "
                     "_PackTextures=_No _Refine=_No _SimplePlane=_No _Enter _Enter")
    else:
        ext = "3mf"
        settings = "_ExportUnfinishedObjects=_Yes _UseSimpleDialog=_Yes _Enter"

    export_path = Path(os.path.join(directory, "{0}.{1}".format(root_filename, ext)))
    log("export_mesh: {0} mesh(es) -> {1}".format(len(meshes), export_path))

    sc.doc = Rhino.RhinoDoc.ActiveDoc
    temp_layer = "".join(random.choice(string.ascii_uppercase) for _ in range(9))
    layer_idx = sc.doc.Layers.Add(temp_layer, System.Drawing.Color.Black)
    if layer_idx < 0:
        _signal(False, "Could not create temp layer")
        raise MeshExportError("export_mesh: could not create temp layer")

    try:
        # Bake
        attr = Rhino.DocObjects.ObjectAttributes()
        attr.LayerIndex = layer_idx
        mesh_ids = []
        for m in meshes:
            mid = sc.doc.Objects.AddMesh(m, attr)
            mesh_ids.append(mid)

        # Select
        sc.doc.Objects.UnselectAll()
        for mid in mesh_ids:
            sc.doc.Objects.Select(mid)

        # Remove existing file
        if export_path.exists():
            export_path.unlink()
            time.sleep(0.5)

        # Export
        cmd = '_-Export _Pause "{0}" {1} _Enter'.format(export_path, settings)
        Rhino.RhinoApp.RunScript(cmd, True)

        if not export_path.exists():
            _signal(False, "Export file not found after RunScript")
            raise MeshExportError("export_mesh: file not created: {0}".format(export_path))

        file_size = os.path.getsize(str(export_path))
        if file_size < 100:
            _signal(False, "Export file too small ({0} bytes)".format(file_size))
            raise MeshExportError("export_mesh: file too small ({0} bytes)".format(file_size))

        log("export_mesh: wrote {0} ({1} bytes)".format(export_path.name, file_size))

        # Inspect the exported meshes for the metadata
        quality = inspect_mesh(meshes[0], "export") if len(meshes) == 1 else {}

        # Write metadata sidecar
        elapsed = get_generation_elapsed()
        metadata = {
            "mesh_count": len(meshes),
            "file_size_bytes": file_size,
            "elapsed_time_seconds": round(elapsed, 2) if elapsed is not None else None,
            "mesh_quality": quality,
        }
        meta_path = Path(os.path.join(directory, "{0}.meta.json".format(root_filename)))
        try:
            with open(str(meta_path), 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2)
        except Exception as err:
            log("export_mesh: warning - failed to write metadata: {0}".format(err))

        _signal(True, "export complete: {0}".format(export_path.name))
        return {
            "file_path": str(export_path),
            "file_size_bytes": file_size,
            "mesh_quality": quality,
        }

    except MeshExportError:
        raise
    except Exception as exc:
        _signal(False, str(exc))
        raise MeshExportError("export_mesh: {0}".format(exc))
    finally:
        # Cleanup temp layer
        try:
            layer_idx = sc.doc.Layers.Find(temp_layer, True)
            if layer_idx >= 0:
                sc.doc.Layers.Purge(layer_idx, True)
        except Exception:
            pass


def export_mesh_with_metadata(meshes, directory, root_filename, format_type="3mf",
                               custom_metadata=None, emit_pipeline_signal=True):
    """Export meshes and merge custom metadata into the sidecar .meta.json.

    Thin wrapper over export_mesh that adds arbitrary per-job metadata under a "custom" key,
    matching the interface of splintmeshes.save_job_output for callers that need it.
    """
    result = export_mesh(meshes, directory, root_filename, format_type,
                         emit_pipeline_signal=emit_pipeline_signal)

    if custom_metadata and isinstance(custom_metadata, dict):
        meta_path = Path(os.path.join(directory, "{0}.meta.json".format(root_filename)))
        try:
            with open(str(meta_path), 'r', encoding='utf-8') as f:
                metadata = json.load(f)
            metadata["custom"] = custom_metadata
            with open(str(meta_path), 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2)
            log("export_mesh_with_metadata: merged {0} custom key(s)".format(
                len(custom_metadata)))
        except Exception as err:
            log("export_mesh_with_metadata: warning - failed to merge custom metadata: "
                "{0}".format(err))

    return result
