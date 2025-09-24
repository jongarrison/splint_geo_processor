See: ../../README.md for an overview of all the connected systems

# Overview

* The splint_geo_processor polls the geometry queue api provided by ../../splint_factory/splint_factory/src/app/api/geometry-processing looking for geometry queue jobs that need to be processed
* The splint_geo_processor uses ~/SplintFactoryFiles/ as a local working directory for passing meta data and generated files to and from Rhino3D/Grasshopper scripts. 
  * Once the json input file is received, it will be written into the ~/SplintFactoryFiles/inbox directory
  * An example job file that would be provided by the geometry queue api, "cylinder_1.json" (~/SplintFactoryFiles/inbox/cylinder_1.json) that will be used by the correct gh script to generate an stl file:
    {
        "cylinder_radius": "75.0",
        "cylinder_height": "11.1"
    }
    The contents of these json input files will vary according to the associated Geometry Input Parameter Schema associated with the "Named Geometry Designs" 
* The splint_geo_processor then verifies that the Rhino App (/Applications/RhinoWIP.app/) is running using the rhinocode cli ("/Applications/RhinoWIP.app/Contents/Resources/bin/rhinocode") via the shell command: `rhinocode list --json`. If Rhino is not running, it is launched in a way that will leave the program running, for example by running: `open -a {RHINO_APP_PATH} --args -nosplash`
* The splint_geoprocessor uses the rhinocode cli to launch the correct gh script file as specified by the geometry que api. For example, "simple_cylinder_generator.gh". The command would look like this: `rhinocode command "-_GrasshopperPlayer {gh_script_path}"`. All generator files will be found in the ./generators directory.
* Rhino/Grasshopper will take a few seconds to process and will then put its output in ~/SplintFactoryFiles/outbox. All output files will start with the same root name as the input json file and there will be two files an stl mesh file and json file summarizing the run result. For example the outbox will contain: cylinder_13.stl and cylinder_13.json
  * The outbox json file will contain something that looks like this:
    {
      "result": "SUCCESS",
      "message": "mesh exported normally"
    }
    If key "result" is "SUCCESS" we can proceed on with processing, otherwise an error should be reported back to the geometry queue api
* The resulting stl file then needs to be processed by BambuStudio to prepare it for 3D printing.
  * Assuming current working directory: /Applications/BambuStudio.app/Contents/MacOS
  * Example successful command:
  ./BambuStudio --orient 1 --arrange 1 --load-settings "/Users/jon/Library/Application Support/BambuStudio/system/BBL/machine/Bambu Lab P1S 0.4 nozzle.json;/Users/jon/Library/Application Support/BambuStudio/system/BBL/process/0.20mm Standard @BBL X1C.json"  --load-filaments "/Users/jon/Library/Application Support/BambuStudio/system/BBL/filament/Generic PLA.json" --slice 0 --debug 2 --export-3mf /Users/jon/SplintFactoryFiles/outbox/cylinder_1.gcode.3mf /Users/jon/SplintFactoryFiles/outbox/cylinder_1.stl
  * That command results in a .gcode.3mf file containing the gcode necessary to print the object. The stl file output from Grasshopper and the .gcode.3mf should be uploaded back to the 