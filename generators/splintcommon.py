import os
import json
from pathlib import Path
import traceback
import rhinoscriptsyntax as rs
import shutil

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

def confirm_job_is_processed_and_exit(jobname, is_success, message, is_kill_on_completion):
    job_path = Path(get_inbox_job_filepath(jobname))

    conf_path = Path(get_outbox_job_confirmation_filepath(jobname))
    try:
        #Remove the job queue file that started all of this work
        if job_path.exists():
            shutil.move(job_path, splint_archive_dir)
            # job_path.unlink()
            if job_path.exists():
                message = f"FAILED TO Archive JOB FILE. previous message:{message}"

        conf_data = {"result": "SUCCESS" if is_success else "FAILURE", "message": message}
        with open(conf_path, "w") as f:
            json.dump(conf_data, f, indent=2)        

        if (is_kill_on_completion):
            log("Killing Rhino is not currently allowed...")
            # rs.Exit()
        else:
            log("Rhino exit disabled via: is_kill_on_completion")
    except Exception as e:
        conf_data = {"result": "FAILURE", "phase": "during confirmation", "exception": f"{traceback.format_exc()}", "message": message}
        with open(conf_path, "w") as f:
            json.dump(conf_data, f, indent=2)        
        if (is_kill_on_completion):
            log("Attempting to exit Rhino after exception")
            rs.Exit()

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

def get_next_geo_job(algorithm_name):

    log(f"get_next_geo_job {algorithm_name=}")
    # Process the oldest JSON file
    json_data = load_oldest_json_job_file(splint_inbox_dir, algorithm_name)
    
    if json_data is not None:
        log("\nJSON data loaded successfully:")
        log(f"Data type: {type(json_data)}")
        
        for key in list(json_data.keys()):
            log(f"{key}: {json_data[key]}")
   
    return json_data
