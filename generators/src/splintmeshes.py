"""
Shared mesh export functionality for Rhino/Grasshopper
This module provides functions to export meshes to various formats (STL, OBJ, 3MF)
"""

import scriptcontext as sc
import System
import Rhino
import random
import string
import os
import time
from pathlib import Path


def add_layer(layer_name=None, layer_color=None):
    """Adds a new layer to the active Rhino document.
    
    Args:
      layer_name (str): An optional layer name.
      layer_color (System.Drawing.Color): An optional layer color.
    Returns:
      The index of the new layer.
    """
    sc.doc = Rhino.RhinoDoc.ActiveDoc
    if layer_name != None:
        # Check whether the layer name is valid
        if not Rhino.DocObjects.Layer.IsValidName(layer_name):
            raise ValueError("{} is not a valid layer name.".format(layer_name))
        # Check whether a layer with the same name already exists
        layer_index = sc.doc.Layers.Find(layer_name, True)
        if layer_index >= 0:
            raise ValueError("A layer with the name {} already exists.".format(layer_name))
    else:
        layer_name = sc.doc.Layers.GetUnusedLayerName(False)
    
    # Check whether the layer color is valid
    if layer_color != None:
        if not isinstance(layer_color, System.Drawing.Color):
            raise ValueError("{} is not a valid layer color.".format(layer_color))
    else:
        layer_color = System.Drawing.Color.Black # default layer color
    
    # Add a new layer to the active document
    layer_index = sc.doc.Layers.Add(layer_name, layer_color)
    if layer_index < 0:
        raise ValueError("Unable to add layer {} to document.".format(layer_name))
    return layer_index


def delete_layer(layer):
    """Deletes an existing layer from the active Rhino document. 
    The layer to be removed cannot be the current layer and 
    it will be deleted even if it contains objects.
    
    Args:
      layer (str\id): A name or id of an existing layer.
    Returns:
      True or False, indicating success or failure.
    """
    sc.doc = Rhino.RhinoDoc.ActiveDoc
    layer_index = sc.doc.Layers.Find(layer, True)
    if layer_index < 0:
        raise ValueError("The layer {} does not exist.".format(layer))
    rc = sc.doc.Layers.Purge(layer_index, True)
    sc.doc.Views.Redraw()
    return rc


def bake_mesh(layer, mesh, mesh_name=None):
    """Bakes a mesh object to the active Rhino document.
    
    Args:
      layer (str\id): The name or id of an existing layer.
      mesh (Rhino.Geometry.Mesh): A mesh object to bake.
      mesh_name (str): An optional mesh object name.
    Returns:
      The GUID of the baked mesh.
    """
    sc.doc = Rhino.RhinoDoc.ActiveDoc
    # Check whether the mesh is a mesh object
    if mesh.ObjectType != Rhino.DocObjects.ObjectType.Mesh:
        raise ValueError("{} is not a mesh.".format(mesh))
    # Create the mesh object attributes
    attr = Rhino.DocObjects.ObjectAttributes()
    # Set the mesh object layer index attribute
    layer_index = sc.doc.Layers.Find(layer, True)
    if layer_index < 0:
        if layer == "Default":
            raise ValueError("The layer {} can't be baked to.".format(layer))
        else:
            raise ValueError("The layer {} does not exist.".format(layer))    
    else:
        attr.LayerIndex = layer_index
    # Set the mesh object name attribute
    if mesh_name != None:
        attr.Name = mesh_name
    # Bake the mesh to the active Rhino documemnt
    mesh_id = sc.doc.Objects.AddMesh(mesh, attr)
    return mesh_id


def get_obj_settings():
    """Returns the settings for the OBJ export command as a string."""
    # Formatting options
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
    # Detailed options
    cfg += "_DetailedOptions "
    cfg += "_JaggedSeams=_No "
    cfg += "_PackTextures=_No "
    cfg += "_Refine=_No "
    cfg += "_SimplePlane=_No "
    # Advanced options
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


def get_stl_settings_default():
    """Returns default STL export settings."""
    #[ANGLE,ASPECT RATIO,MAX DIST TO SRF,GRID,DENSITY,MAX EDGE LEN,MIN EDGE LEN]
    setting_list=[15,0,0.01,16,0,0,0.001]   
    return get_stl_settings(*setting_list)


def get_stl_settings(ang,ar,dist,grid,den,maxL=0,minL=.0001):
    """Returns the settings for binary STL export command as a string."""
    e_str = "_ExportFileAs=_Binary "
    e_str+= "_ExportUnfinishedObjects=_Yes "
    e_str+= "_UseSimpleDialog=_No "
    e_str+= "_Enter _DetailedOptions "
    e_str+= "_JaggedSeams=_No "
    e_str+= "_PackTextures=_No "
    e_str+= "_Refine=_Yes "
    e_str+= "_SimplePlane=_Yes "
    e_str+= "_Enter _Enter"
    return e_str, "stl"


def get_3mf_settings():
    """Returns the settings for 3MF export command as a string."""
    e_str = "_ExportUnfinishedObjects=_Yes "
    e_str+= "_UseSimpleDialog=_No "
    e_str+= "_Enter"
    return e_str, "3mf"


def export_mesh_files(meshes, path, filename, logger=None, debug=False, format_type="stl"):
    """Exports a collection of meshes to a file.
    
    Args:
      meshes (list): A list of Rhino.Geometry.Mesh objects.
      path (str): An absolute path pointing to a directory.
      filename (str): A filename (without extension).
      logger (callable): Optional logging function that takes a string message.
      debug (bool): Optional True to print debug information.
      format_type (str): Export format - "stl", "obj", or "3mf". Default is "stl".
    Returns:
      True or False, indicating success or failure.
    """
    
    def log(msg):
        if logger:
            logger(msg)
        else:
            print(msg)
    
    sc.doc = Rhino.RhinoDoc.ActiveDoc
    sc.doc.Views.RedrawEnabled = True
    if debug: 
        now = time.process_time()
        elapsed = 0.0
        
    # Create a temporary layer
    log(f"export_mesh_files: Creating temp layer")
    layer = "".join(random.choice(string.ascii_uppercase) for _ in range(9))
    layer_index = add_layer(layer)
    if debug: 
        then, now = now, time.process_time()
        elapsed += now - then
        log("Creating temporary layer: {0:.4f} seconds".format(now-then))
        
    # Bake the temporary mesh(es)
    log(f"export_mesh_files: Baking temp mesh(es)")
    mesh_ids = [bake_mesh(layer, mesh) for mesh in meshes]
    if debug: 
        then, now = now, time.process_time()
        elapsed += now - then
        log("Creating temporary mesh(es): {0:.4f} seconds".format(now-then))
    
    # Unselect all objects in the scene
    sc.doc.Objects.UnselectAll()
    # Select the baked mesh object(s)
    for mid in mesh_ids:
        sc.doc.Objects.Select(mid)
    if debug: 
        then, now = now, time.process_time()
        elapsed += now - then
        log("Unselecting and selecting: {0:.4f} seconds".format(now-then))
    
    # Export the selected mesh object(s) to mesh file
    log(f"export_mesh_files: Exporting to {format_type.upper()} format")
    
    # Get export settings based on format
    if format_type.lower() == "obj":
        export_config, export_extension = get_obj_settings()
        log("Using OBJ mesh settings")
    elif format_type.lower() == "3mf":
        export_config, export_extension = get_3mf_settings()
        log("Using 3MF mesh settings")
    else:  # Default to STL
        export_config, export_extension = get_stl_settings_default()
        log("Using STL mesh settings")
    
    export_fname = "{}.{}".format(filename, export_extension)
    export_fpath = Path(os.path.join(path, export_fname))
    log(f"export_mesh_files: Export Values: {export_fname=} {export_fpath=}")
    
    if export_fpath.exists():
        log(f"export_mesh_files: REMOVING EXISTING MESH FILE: {export_fpath}")
        export_fpath.unlink()
        time.sleep(1.0)
        log(f"export_mesh_files: testing... MeshStillExists:{export_fpath.exists()}")

    log(f"export_mesh_files: Exporting to file")

    cmd = '_-Export _Pause "{}" {} _Enter'.format(export_fpath, export_config)
    log(f"export_mesh_files: RUNNING cmd: '{cmd}'")
    
    rc = Rhino.RhinoApp.RunScript(cmd, True)

    log(f"export_mesh_files: Completion {rc=}")
    
    if debug: 
        then, now = now, time.process_time()
        elapsed += now - then
        log("Exporting Mesh Result: {0:.4f} seconds result={1}".format(now-then, rc))
    
    # Delete the temporary layer and meshes
    log(f"export_mesh_files: Cleaning up temp layer")
    delete_layer(layer)
    
    if debug: 
        then, now = now, time.process_time()
        elapsed += now - then
        log("Cleaning up: {0:.4f} seconds\n".format(now-then))
        log("Total elapsed: {0:.4f} seconds".format(elapsed))

    sc.doc.Views.RedrawEnabled = True

    log(f"export_mesh_files: Returning result: {rc=}")

    return rc


def save_mesh(input_mesh, directory, root_filename, logger=None, format_type="stl"):
    """High-level function to save a mesh to a file.
    
    Args:
      input_mesh (Rhino.Geometry.Mesh): The mesh to export.
      directory (str): Directory path where file will be saved.
      root_filename (str): Base filename (without extension).
      logger (callable): Optional logging function.
      format_type (str): Export format - "stl", "obj", or "3mf". Default is "stl".
    
    Returns:
      bool: True if successful, False otherwise.
    
    Raises:
      ValueError: If inputs are invalid or export fails.
    """
    
    def log(msg):
        if logger:
            logger(msg)
        else:
            print(msg)
    
    log(f"save_mesh: inputMesh type: {type(input_mesh)}")
    log(f"save_mesh: {directory=}")
    log(f"save_mesh: {root_filename=}")
    log(f"save_mesh: {format_type=}")
    
    if input_mesh is None or directory is None or root_filename is None:        
        raise ValueError("Missing required inputs for saving file")

    # Validate the path (no dots in directory or filename for safety)
    if "." in directory or "." in root_filename:
        raise ValueError(f"Path must not contain dot character: {directory}")

    result = export_mesh_files([input_mesh], directory, root_filename, logger=logger, debug=True, format_type=format_type)

    if not result:
        raise ValueError(f"FAIL: mesh export failed. {result=}")
    else:
        log(f'SUCCESS: Saved {format_type.upper()} to {root_filename} in {directory}')
        return True
