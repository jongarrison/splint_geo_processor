# See: https://discourse.mcneel.com/t/how-to-optimise-ghpython-obj-export/87598/76

# This code is intended to be run inside a Grasshopper Python component
# It exports a given mesh to an OBJ or STL file using Rhino's export functionality
# Node inputs (these are available as locals):
#   geo_input: The input mesh to be exported (Rhino.Geometry.Mesh)
#   output_file_dir: The directory where the exported file will be saved (string)
#   jobname: The base name for the exported file (string)

# Start standard gh python imports
from pathlib import Path
import sys
from importlib import reload #FOR DEV ONLY

ghFileDir = str(Path.joinpath(Path(ghenv.Component.OnPingDocument().FilePath).resolve().parents[0], "src"))
if ghFileDir not in sys.path:
    print("ghFileDir needed to be included")
    sys.path.append(ghFileDir)
import splintcommon
reload(splintcommon)
# End standard gh python imports

import time
splintcommon.log(f'Save Mesh Running at: ({time.strftime("%H:%M:%S", time.localtime())})')

import scriptcontext as sc
# import rhinoscriptsyntax as rs
import System
import Rhino
import random
import string
import os
import traceback

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
            raise ValueError("{} is not a valid layer name."\
                .format(layer_name))
        # Check whether a layer with the same name already exists
        layer_index = sc.doc.Layers.Find(layer_name, True)
        if layer_index >= 0:
            raise ValueError("A layer with the name {} already exists."\
                .format(layer_name))
    else:
        layer_name = sc.doc.Layers.GetUnusedLayerName(False)
    
    # Check whether the layer color is valid
    if layer_color != None:
        if not isinstance(layer_color, System.Drawing.Color):
            raise ValueError("{} is not a valid layer color."\
                .format(layer_color))
    else:
        layer_color = System.Drawing.Color.Black # default layer color
    
    # Add a new layer to the active document
    layer_index = sc.doc.Layers.Add(layer_name, layer_color)
    if layer_index < 0:
        raise ValueError("Unable to add layer {} to document."\
            .format(layer_name))
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

    splintcommon.log(f"mesh type:{type(mesh)}")
    # mesh = rs.coercemesh(mesh)
    # splintcommon.log(f"mesh type:{type(mesh)}")

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
    splintcommon.log("Using OBJ mesh settings")
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
    cfg += "_Enter _Enter" # remove the last _Enter to check if density is set
    return cfg, "obj"

def get_stl_settings_default():
    #[ANGLE,ASPECT RATIO,MAX DIST TO SRF,GRID,DENSITY,MAX EDGE LEN,MIN EDGE LEN]
    #Change list below as needed
    setting_list=[15,0,0.01,16,0,0,0.001]   
    return get_stl_settings(*setting_list)

def get_stl_settings(ang,ar,dist,grid,den,maxL=0,minL=.0001):
    splintcommon.log("Using STL mesh settings")
    e_str = "_ExportFileAs=_Binary "
    e_str+= "_ExportUnfinishedObjects=_Yes "
    e_str+= "_UseSimpleDialog=_No "
    e_str+= "_Enter _DetailedOptions "
    e_str+= "_JaggedSeams=_No "
    e_str+= "_PackTextures=_No "
    e_str+= "_Refine=_Yes "
    e_str+= "_SimplePlane=_Yes "
    # e_str+= "_AdvancedOptions "
    # e_str+= "_Angle={} ".format(ang)
    # e_str+= "_AspectRatio={} ".format(ar)
    # e_str+= "_Distance={} ".format(dist)
    # e_str+= "_Density={} ".format(den)
    # e_str+= "_Grid={} ".format(grid)
    # e_str+= "_MaxEdgeLength={} ".format(maxL)
    # e_str+= "_MinEdgeLength={} ".format(minL)
    e_str+= "_Enter _Enter"
    return e_str, "stl"

def export_mesh_files(meshes, path, filename, debug=False, asObj=True):
    """Exports a collection of meshes to an OBJ file.
    
    Args:
      meshes (list): A list of Rhino.Geometry.Mesh objects.
      path (str): An absolute path pointing to a directory.
      filename (str): A filename (without extension).
      debug (bool): Optional True to print debug information.
    Returns:
      True or False, indicating success or failure.
    """

    sc.doc = Rhino.RhinoDoc.ActiveDoc
    sc.doc.Views.RedrawEnabled = True # JG - False
    if debug: 
        now = time.process_time()
        elapsed = 0.0
        
    # Create a temporary layer
    splintcommon.log(f"export_mesh_files: Creating temp layer")
    layer = "".join(random.choice(string.ascii_uppercase) for _ in range(9))
    layer_index = add_layer(layer)
    if debug: 
        then, now = now, time.process_time()
        elapsed += now - then
        splintcommon.log("Creating temporary layer: {0:.4f} seconds".format(now-then))
        
    # Bake the temporary mesh(es)
    splintcommon.log(f"export_mesh_files: Baking temp mesh(es)")
    mesh_ids = [bake_mesh(layer, mesh) for mesh in meshes]
    if debug: 
        then, now = now, time.process_time()
        elapsed += now - then
        splintcommon.log("Creating temporary mesh(es): {0:.4f} seconds".format(now-then))
    
    # Unselect all objects in the scene
    sc.doc.Objects.UnselectAll()
    # Select the baked mesh object(s)
    for mid in mesh_ids:
        sc.doc.Objects.Select(mid)
    if debug: 
        then, now = now, time.process_time()
        elapsed += now - then
        splintcommon.log("Unselecting and selecting: {0:.4f} seconds".format(now-then))
    
    # Export the selected mesh object(s) to mesh file
    splintcommon.log(f"export_mesh_files: exporting to file")
    export_config, export_extension = get_obj_settings() if asObj else get_stl_settings_default()
    export_fname = "{}.{}".format(filename, export_extension)
    export_fpath = Path(os.path.join(path, export_fname))
    splintcommon.log(f"export_mesh_files: Export Values: {export_fname=} {export_fpath=}")
    
    if export_fpath.exists():
        splintcommon.log(f"export_mesh_files: REMOVING EXISTING MESH FILE: {export_fpath}")
        export_fpath.unlink()
        time.sleep(1.0)
        splintcommon.log(f"export_mesh_files: testing... MeshStillExists:{export_fpath.exists()}")

    splintcommon.log(f"export_mesh_files: exporting to file")

    cmd = '_-Export _Pause "{}" {} _Enter'.format(export_fpath, export_config) # _Enter
    # see: https://discourse.mcneel.com/t/export-command-line-options/87537/5
    splintcommon.log(f"export_mesh_files: RUNNING cmd: '{cmd}'")
    
    #start = Rhino.DocObjects.RhinoObject.NextRuntimeSerialNumber
    rc = Rhino.RhinoApp.RunScript(cmd, True)

    splintcommon.log(f"export_mesh_files: Completion {rc=}")

    #end = Rhino.DocObjects.RhinoObject.NextRuntimeSerialNumber
    #global __command_serial_numbers
    #__command_serial_numbers = None
    #if start != end:
        #__command_serial_numbers = (start, end)
    
    if debug: 
        then, now = now, time.process_time()
        elapsed += now - then
        splintcommon.log("Exporting Mesh Result: {0:.4f} seconds result={1}".format(now-then, rc))
    
    # Delete the temporary layer and meshes
    splintcommon.log(f"export_mesh_files: cleaning up temp layer")
    delete_layer(layer)
    
    if debug: 
        then, now = now, time.process_time()
        elapsed += now - then
        splintcommon.log("Cleaning up: {0:.4f} seconds\n".format(now-then))
        splintcommon.log("Total elapsed: {0:.4f} seconds".format(elapsed))

    sc.doc.Views.RedrawEnabled = True

    splintcommon.log(f"export_mesh_files: Returning result: {rc=}")

    return rc

def saveMeshToFile(inputMesh, directory, rootFilename):
    try:
        splintcommon.log(f"inputMesh type: {inputMesh}")
        splintcommon.log(f"{directory=}")
        splintcommon.log(f"{rootFilename=}")
        
        if inputMesh is None or directory is None or rootFilename is None:        
            raise ValueError("Missing required inputs for saving file")

        #TODO, Validate the path more completely
        if "." in directory or "." in rootFilename:
            raise ValueError(f"Path must not contain dot character: {directory}")

        result = export_mesh_files([inputMesh], directory, rootFilename, True, False)

        if not result:
            raise ValueError(f"FAIL: mesh export failed. {result=}")
        else:
            splintcommon.log(f'SUCCESS: Saved to {rootFilename} in {directory}')
            splintcommon.confirm_job_is_processed_and_exit(jobname, True, "mesh exported normally")

    except Exception as e:
        errMsg = f"Exception: {traceback.format_exc()}"
        splintcommon.log(errMsg)
        splintcommon.confirm_job_is_processed_and_exit(jobname, False, errMsg)

if geo_input is None:
    message = f"No mesh input To Process {geo_input=}"
    splintcommon.log(message)
    splintcommon.confirm_job_is_processed_and_exit(jobname, False, message)
else:
    saveMeshToFile(geo_input, output_file_dir, jobname)