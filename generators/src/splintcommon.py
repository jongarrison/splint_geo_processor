import os
import json
from pathlib import Path
import traceback
import rhinoscriptsyntax as rs
import shutil
from Rhino.Geometry import Brep
import Rhino.Geometry as rg
import scriptcontext as sc

splint_home_dir = Path("~/SplintFactoryFiles/").expanduser()
Path(splint_home_dir).mkdir(parents=True, exist_ok=True)
splint_inbox_dir = os.path.join(splint_home_dir, "inbox")
Path(splint_inbox_dir).mkdir(parents=True, exist_ok=True)
splint_outbox_dir = os.path.join(splint_home_dir, "outbox")
Path(splint_outbox_dir).mkdir(parents=True, exist_ok=True)
splint_archive_dir = os.path.join(splint_home_dir, "archive")
Path(splint_archive_dir).mkdir(parents=True, exist_ok=True)

def inclusionTest():
    log("You got it!")

def get_output_mesh_filename(jobname, extension):
    return os.path.join(splint_outbox_dir, f"{jobname}.{extension}") #extension is probably obj

def get_inbox_job_filepath(jobname):
    return os.path.join(splint_inbox_dir, f"{jobname}.json")

def get_outbox_job_confirmation_filepath(jobname):
    return os.path.join(splint_outbox_dir, f"{jobname}.json")

def get_log_filepath():
    return os.path.join(splint_outbox_dir, "log.txt")

def get_generator_filepath():
    return Path(__file__).parent.parent.resolve()

def log(message):
    print(f"log:{message}")
    with open(get_log_filepath(), "a") as f:
        f.write(f"{message}\n")

def log_clear(message=""):
    print(f"log_clear:{message}")
    with open(get_log_filepath(), "a") as f:
        f.write(f"\n\n============================================")
        f.write(f"{message}\n")

log(f"Splint Home Dirs verified: {splint_home_dir=}")

def confirm_job_is_processed_and_exit(jobname, is_success, message):
    job_path = Path(get_inbox_job_filepath(jobname))

    conf_path = Path(get_outbox_job_confirmation_filepath(jobname))
    try:
        #Remove the job queue file that started all of this work
        # if job_path.exists():
        #     shutil.move(job_path, splint_archive_dir) # This is now handled by the splint_geo_processor
        #     # job_path.unlink()
        #     if job_path.exists():
        #         log(f"FAILED TO Archive JOB FILE. previous message:{message}: {job_path}")

        log(f"RESULT: {"SUCCESS" if is_success else "FAILURE"} {message=} {jobname=}")

    except Exception as e:
        conf_data = {"result": "FAILURE", "phase": "during confirmation", "exception": f"{traceback.format_exc()}", "message": message}
        with open(conf_path, "w") as f:
            json.dump(conf_data, f, indent=2)        

def load_oldest_json_job_file(directory, algorithm_name):
    """
    List all JSON files in the specified directory and process the oldest one.
    
    Args:
        directory_path (str): Path to the directory containing JSON files
    
    Returns:
        dict: Contents of the oldest JSON file, or None if no files found
    """
    # Check if directory exists
    directory = Path(directory)

    if not directory.exists():
        log(f"Directory {directory} does not exist.")
        return None
    
    # Find all JSON files in the directory
    json_files = list(directory.glob(f"{algorithm_name}*.json"))
    
    log(f"{json_files=}")

    if not json_files:
        log(f"No JSON files found in {directory}")
        return None
    
    log(f"Found {len(json_files)} JSON file(s):")
    for file in json_files:
        log(f"  - {file.name}")
    
    # Find the oldest file by creation time
    oldest_file = min(json_files, key=lambda f: f.stat().st_ctime)
    
    log(f"\nProcessing oldest file: {oldest_file.name}")
    
    try:
        # Read and parse the JSON file
        with open(oldest_file, 'r', encoding='utf-8') as file:
            data = json.load(file)
        
        data["jobname"] = oldest_file.stem #name
        log("Successfully loaded JSON data.")
        return data
    
    except json.JSONDecodeError as e:
        log(f"Error parsing JSON file {oldest_file.name}: {e}")
        return None
    except Exception as e:
        log(f"Error reading file {oldest_file.name}: {e}")
        return None

def extract_server_params_data(json_data):
    result_data = None

    if json_data is not None:
        log("\nJSON data loaded successfully:")
        log(f"Data type: {type(json_data)}")
        
        for key in list(json_data.keys()):
            log(f"INPUT: {key}: {json_data[key]}")

        result_data = json.loads(json_data["params"]) if "params" in json_data else None

        if result_data is None:
            log("No 'params' key found in JSON data.")
        else:
            result_data["jobname"] = json_data["jobname"]
            result_data["objectID"] = json_data.get("metadata", {}).get("objectID", "NA")

            for key in list(result_data.keys()):
                log(f"RESULT: {key}: {result_data[key]}")
            return result_data
    raise Exception("No data found to extract correctly")

def get_next_geo_job(algorithm_name):
    try:
        log(f"get_next_geo_job {algorithm_name=}")
        # Process the oldest JSON file
        json_data = load_oldest_json_job_file(splint_inbox_dir, algorithm_name)

        return extract_server_params_data(json_data)
    except Exception as e:
        log(f"Exception in get_next_geo_job: {traceback.format_exc()}")
        return None

def load_dev_data(geo_algorithm_name):
    dev_data_path = Path.joinpath(get_generator_filepath(), f"{geo_algorithm_name}.json")
    log(f"Loading dev data from {dev_data_path}")
    if not dev_data_path.exists():
        log(f"Dev data file does not exist: {dev_data_path}")
        raise Exception(f"Dev data file does not exist: {dev_data_path}")

    try:
        with open(dev_data_path, 'r', encoding='utf-8') as file:
            data = json.load(file)
            data["jobname"] = dev_data_path.stem #name

            log(f"Loaded dev data from {dev_data_path}")
            return extract_server_params_data(data)
    except json.JSONDecodeError as e:
        log(f"Error parsing dev data JSON file {dev_data_path}: {e}")
        raise e
    except Exception as e:
        log(f"Error reading dev data file {dev_data_path}: {e}")
        raise e

def checkGeometryExists(geo):
    """
    Check if in various forms of geometry input, there is valid geometry present.
    geo could be a list or a single geometry item.
    could be a brep or something else
    """
    print(f"{geo=} type={type(geo)}")

    isExisting = False

    if geo is None:
        print("geo is None (root)")
        isExisting = False
    elif type(geo) is Brep:
        print("geo is Brep. Yay.")
        isExisting = True
    elif hasattr(geo, '__getitem__'):
        if len(geo) == 0 or geo[0] is None:
            print(f"geo[0] is None len={len(geo)}")
            isExisting = False
        else:
            isExisting = len(geo) == 1
            print(f"geo is list of {len(geo)} len. {isExisting=}")
    else:
        print("fall through no recognized type")

    doesExist = isExisting
    doesntExist = not isExisting
    print(f"{doesExist=} {doesntExist=}") #Sure this looks funny, but it's useful for debugging and keeping component code minimal
    return doesExist

def trim_solid_robust(brep_to_trim, cutting_brep, tolerance=None):
    """
    Robust brep trimming with fallbacks for tangent/overlapping surfaces.
    Returns open brep with naked edges (like GH TrimSolid component).
    """
    brep_to_trim = rs.coercebrep(brep_to_trim)
    cutting_brep = rs.coercebrep(cutting_brep)
    log(f"trim_solid_robust brep_to_trim={type(brep_to_trim)} cutting_brep={type(cutting_brep)} tolerance={tolerance}")
    if tolerance is None:
        log(f"{sc.doc.ModelAbsoluteTolerance=}")
        tolerance = sc.doc.ModelAbsoluteTolerance
    
    # Method 1: Try direct Split
    log("Method 1: Try direct Split")
    try:
        pieces = brep_to_trim.Split(cutting_brep, tolerance)
        log(f"trim_solid_robust 1 {pieces=}")
        if pieces and len(pieces) > 0:
            # Return the piece(s) - you may need logic to pick the right one
            return pieces
    except Exception as e:
        log(f"Exception in trim_solid_robust Method 1: {traceback.format_exc()}")
    
    # Method 2: Try with slightly larger tolerance
    log("Method 2: Try with slightly larger tolerance")
    try:
        pieces = brep_to_trim.Split(cutting_brep, tolerance * 10)
        #Can we find the piece with significant overlap with brep_to_trim?

        log(f"trim_solid_robust 2 {pieces=}")
        if pieces and len(pieces) > 0:
            return pieces
    except Exception as e:
        log(f"Exception in trim_solid_robust Method 2: {traceback.format_exc()}")
    
    # Method 3: Try intersecting first, then trim
    # (more robust for tangent surfaces)
    log("Method 3: Try intersecting first, then trim")
    try:
        # Get intersection curves
        intersection = rg.Intersect.Intersection.BrepBrep(
            brep_to_trim, cutting_brep, tolerance
        )
        log(f"trim_solid_robust 3 {intersection=}")

        if intersection[1]:  # if curves exist
            # Use curves to split
            # This is more complex - may need curve-based splitting
            pass
    except Exception as e:
        log(f"Exception in trim_solid_robust Method 3: {traceback.format_exc()}")

    
    # Return None or original brep if all methods fail
    return None