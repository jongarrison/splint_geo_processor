#!/usr/bin/env python3
"""
Script to reliably run a Rhino/Grasshopper .gh script using RhinoWIP and rhinocode CLI.

Usage:
    python run_gh_script.py /path/to/script.gh


This code was originally developed in the splint_generators repository (scripts/run_gh_script.py)
    
"""
import sys
import subprocess
import os

# Known values
RHINO_APP_PATH = "/Applications/RhinoWIP.app/"
RHINOCODE_CLI = "/Applications/RhinoWIP.app/Contents/Resources/bin/rhinocode"


def is_rhino_running():
    """Check if Rhino is running using rhinocode list."""
    import json
    print("Checking if Rhino is running (using --json)...")
    try:
        result = subprocess.run([RHINOCODE_CLI, "list", "--json"], capture_output=True, text=True)
        if result.returncode != 0:
            print("rhinocode list --json failed.")
            return False
        try:
            rhino_list = json.loads(result.stdout)
        except json.JSONDecodeError:
            print("Failed to parse JSON from rhinocode list output.")
            return False
        if isinstance(rhino_list, list) and len(rhino_list) > 0:
            print(f"Found {len(rhino_list)} running Rhino app(s).")
            return True
        else:
            print("No running Rhino apps found.")
            return False
    except Exception as e:
        print(f"Error checking Rhino status: {e}")
        return False


def launch_rhino():
    """Launch Rhino app so it stays running."""
    print("Launching Rhino application...")
    subprocess.Popen(["open", "-a", RHINO_APP_PATH, "--args", "-nosplash"])


def run_grasshopper_script(gh_script_path):
    """Run the GrasshopperPlayer command via rhinocode CLI."""
    print(f"Running GrasshopperPlayer with script: {gh_script_path}")
    # The command string for GrasshopperPlayer
    rhino_script = f'"-_GrasshopperPlayer {gh_script_path}"'
    # rhino_script = f"-_Circle 0,0,0 20"

    # Run the script using rhinocode
    result = subprocess.run([
        RHINOCODE_CLI, "command", rhino_script
    ], capture_output=True, text=True)
    print("GrasshopperPlayer command finished.")
    print("Output:")
    print(result.stdout)
    if result.stderr:
        print("Errors:")
        print(result.stderr)


def main():
    print("Starting Rhino/Grasshopper automation script...")
    if len(sys.argv) != 2:
        print("Usage: python3 run_gh_script.py /path/to/script.gh")
        sys.exit(1)
    gh_script_path = sys.argv[1]
    print(f"Grasshopper script path provided: {gh_script_path}")
    if not os.path.isfile(gh_script_path):
        print(f"Error: File not found: {gh_script_path}")
        sys.exit(1)
    if not is_rhino_running():
        launch_rhino()
        print("Waiting for Rhino to launch...")
        import time
        time.sleep(5)
    run_grasshopper_script(gh_script_path)
    print("Script execution complete.")


if __name__ == "__main__":
    main()
