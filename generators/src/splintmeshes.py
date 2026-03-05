"""
splintmeshes.py
Mesh export for Rhino/Grasshopper.
Exports one or more meshes to a single STL, OBJ, or 3MF file
via Rhino's bake-select-export pipeline.
"""

import scriptcontext as sc
import System
import Rhino
import random
import string
import os
import json
import time
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
    e_str += "_Refine=_Yes "
    e_str += "_SimplePlane=_Yes "
    e_str += "_Enter _Enter"
    return e_str, "stl"


def _get_3mf_settings():
    """3MF export command-line settings."""
    e_str = "_ExportUnfinishedObjects=_Yes "
    e_str += "_UseSimpleDialog=_No "
    e_str += "_Enter"
    return e_str, "3mf"


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
        mesh_meta_list = []
        for i, mesh in enumerate(meshes):
            bbox = mesh.GetBoundingBox(True)
            dims = [bbox.Max.X - bbox.Min.X, bbox.Max.Y - bbox.Min.Y, bbox.Max.Z - bbox.Min.Z]
            mesh_meta_list.append({
                "index": i,
                "volume_mm3": round(mesh.Volume(), 4) if hasattr(mesh, 'Volume') else None,
                "is_closed": bool(mesh.IsClosed) if hasattr(mesh, 'IsClosed') else None,
                "bbox_dimensions": [round(d, 4) for d in dims],
            })

        elapsed = get_generation_elapsed()
        metadata = {
            "mesh_count": len(meshes),
            "meshes": mesh_meta_list,
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
        sc.doc.Views.RedrawEnabled = True
