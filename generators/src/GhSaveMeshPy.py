# Minimal Grasshopper Python Component code for saving meshes
# This code should be pasted into a GhPython component
# Component inputs (available as locals):
#   geo_input: The input mesh to be exported (Rhino.Geometry.Mesh)
#   output_file_dir: The directory where the exported file will be saved (string)
#   jobname: The base name for the exported file (string)

from pathlib import Path
import sys
from importlib import reload
import traceback

# Standard gh python imports to access external modules
ghFileDir = str(Path.joinpath(Path(ghenv.Component.OnPingDocument().FilePath).resolve().parents[0], "src"))
if ghFileDir not in sys.path:
    sys.path.append(ghFileDir)

import splintcommon
import splintmeshes
reload(splintcommon)
reload(splintmeshes)

# Main execution
if geo_input is None:
    message = f"No mesh input to process {geo_input=}"
    splintcommon.log(message)
    splintcommon.confirm_job_is_processed_and_exit(jobname, False, message)
else:
    try:
        # Save mesh using the shared module
        # Format can be "stl", "obj", or "3mf"
        splintmeshes.save_mesh(
            input_mesh=geo_input,
            directory=output_file_dir,
            root_filename=jobname,
            logger=splintcommon.log,
            format_type="stl"  # Change to "obj" or "3mf" as needed
        )
        splintcommon.confirm_job_is_processed_and_exit(jobname, True, "mesh exported normally")
        
    except Exception as e:
        errMsg = f"Exception: {traceback.format_exc()}"
        splintcommon.log(errMsg)
        splintcommon.confirm_job_is_processed_and_exit(jobname, False, errMsg)
