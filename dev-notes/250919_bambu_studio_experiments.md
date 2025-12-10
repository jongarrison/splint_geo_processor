Docs found here:
https://github.com/bambulab/BambuStudio/wiki/Command-Line-Usage


Example:

/bambu-studio --orient --arrange 1 --load-settings "test_data/machine.json;test_data/process.json" --load-filaments "test_data/filament.json" --slice 2 --debug 2 --export-3mf output.3mf test_data/boat.stl


# Local info, script runnable BambuStudio is here:

/Applications/BambuStudio.app/Contents/MacOS/BambuStudio

-h dumps a lot of help info that looks similar to the docs info

# Getting settings files

I am *hoping* that a normal 3mf project save contains all of the settings files that I need

To get the following files, I proceeded as if I was going to print a simple cylinder and saved the project as two different files:

  * Question: bambustudio "slice plate" vs "slice all"

cylinder_13.3mf (Just by saving the project after doing basic settings and slicing)
cylinder_13.gcode.3mf (Slice plate > Export plate sliced file)

I then unzipped them like so:

    unzip cylinder_13.3mf -d cylinder_13_zip

Consider the differences:

cylinder_13_zip
cylinder_13_zip/[Content_Types].xml
cylinder_13_zip/3D
cylinder_13_zip/3D/3dmodel.model
cylinder_13_zip/3D/Objects
cylinder_13_zip/3D/Objects/object_1.model
cylinder_13_zip/3D/_rels
cylinder_13_zip/3D/_rels/3dmodel.model.rels
cylinder_13_zip/_rels
cylinder_13_zip/_rels/.rels
cylinder_13_zip/Metadata
cylinder_13_zip/Metadata/plate_1.png
cylinder_13_zip/Metadata/plate_no_light_1.png
cylinder_13_zip/Metadata/plate_1_small.png
cylinder_13_zip/Metadata/plate_1.json
cylinder_13_zip/Metadata/model_settings.config
cylinder_13_zip/Metadata/pick_1.png
cylinder_13_zip/Metadata/cut_information.xml
cylinder_13_zip/Metadata/project_settings.config
cylinder_13_zip/Metadata/slice_info.config
cylinder_13_zip/Metadata/top_1.png

cylinder_13_gcode_zip
cylinder_13_gcode_zip/[Content_Types].xml
cylinder_13_gcode_zip/3D
cylinder_13_gcode_zip/3D/3dmodel.model
cylinder_13_gcode_zip/.DS_Store
cylinder_13_gcode_zip/_rels
cylinder_13_gcode_zip/_rels/.rels
cylinder_13_gcode_zip/Metadata
cylinder_13_gcode_zip/Metadata/plate_1.png
cylinder_13_gcode_zip/Metadata/plate_no_light_1.png
cylinder_13_gcode_zip/Metadata/plate_1_small.png
cylinder_13_gcode_zip/Metadata/plate_1.gcode
cylinder_13_gcode_zip/Metadata/plate_1.gcode.md5
cylinder_13_gcode_zip/Metadata/plate_1.json
cylinder_13_gcode_zip/Metadata/model_settings.config
cylinder_13_gcode_zip/Metadata/_rels
cylinder_13_gcode_zip/Metadata/_rels/model_settings.config.rels
cylinder_13_gcode_zip/Metadata/pick_1.png
cylinder_13_gcode_zip/Metadata/cut_information.xml
cylinder_13_gcode_zip/Metadata/project_settings.config
cylinder_13_gcode_zip/Metadata/slice_info.config
cylinder_13_gcode_zip/Metadata/top_1.png



The important files seem to be:
cylinder_13_zip/Metadata/project_settings.config

less interesting?:
cylinder_13_zip/Metadata/plate_1.json
cylinder_13_zip/Metadata/model_settings.config
cylinder_13_zip/Metadata/cut_information.xml
cylinder_13_zip/Metadata/slice_info.config


# Attempting a slice run in the outbox:

* example:
/bambu-studio --orient --arrange 1 --load-settings "test_data/machine.json;test_data/process.json" --load-filaments "test_data/filament.json" --slice 2 --debug 2 --export-3mf output.3mf test_data/boat.stl

* Attempts to Slice! (Run from within: /Applications/BambuStudio.app/Contents/MacOS)

* attempt 1
./BambuStudio --orient 1 --arrange 1 --load-settings "/Users/jon/SplintFactoryFiles/outbox/cylinder_13_zip/Metadata/project_settings.config" --slice 0 --debug 2 --export-3mf /Users/jon/SplintFactoryFiles/outbox/cylinder-output-jg1.3mf /Users/jon/SplintFactoryFiles/outbox/cylinder_13.stl

fails with "from project unsupportedrun found error, exit"

* attempt 2
./BambuStudio --orient 1 --arrange 1 --slice 0 --debug 3 --export-3mf /Users/jon/SplintFactoryFiles/outbox/cylinder-output-jg1.3mf /Users/jon/SplintFactoryFiles/outbox/cylinder_13.stl

>>> [0x000000020a2a1f00] [info]    store_to_3mf_structure:stored 1 plates!
>>> zsh: segmentation fault

* attempt 3 - Using a 3mf saved with likely print settings, maybe?
./BambuStudio --orient 1 --arrange 1 --slice 0 --debug 3 --load-settings "/Users/jon/SplintFactoryFiles/outbox/250924_no_obj_just_print.3mf" --export-3mf /Users/jon/SplintFactoryFiles/outbox/cylinder-output-jg1.3mf /Users/jon/SplintFactoryFiles/outbox/cylinder_13.stl

>>> [2025-09-24 14:18:13.585512] [0x000000020a2a1f00] [error]   operator():Can not load config from file /Users/jon/SplintFactoryFiles/outbox/250924_no_obj_just_print.3mf
>>> [2025-09-24 14:18:13.585521] [0x000000020a2a1f00] [info]    record_exit_reson:465 Slicer_Info_Report: plate_id=0, return_code=-5, error_message=The input preset file is invalid and can not be parsed.

* attempt 4 - going with the idea that the settings file cannot be 3mf

attempt 3 - Using a 3mf saved with likely print settings, maybe?
./BambuStudio --orient 1 --arrange 1 --slice 0 --debug 3 --load-settings "/Users/jon/SplintFactoryFiles/outbox/250924_no_obj_just_print.3mf" --export-3mf /Users/jon/SplintFactoryFiles/outbox/cylinder-output-jg1.3mf /Users/jon/SplintFactoryFiles/outbox/cylinder_13.stl

* attempt 5 - finding all of the system settings files under /Users/jon/Library/Application Support/BambuStudio/system/BBL/

- machine:
/Users/jon/Library/Application Support/BambuStudio/system/BBL/machine/Bambu Lab P1S 0.4 nozzle.json
- filament:
/Users/jon/Library/Application Support/BambuStudio/system/BBL/filament/Generic PLA.json
- process (NOTE SELECTION OF X1C, which is supposed to be same as P1S):
/Users/jon/Library/Application Support/BambuStudio/system/BBL/process/0.20mm Standard @BBL X1C.json


./BambuStudio --orient 1 --arrange 1 --load-settings "/Users/jon/Library/Application Support/BambuStudio/system/BBL/machine/Bambu Lab P1S 0.4 nozzle.json;/Users/jon/Library/Application Support/BambuStudio/system/BBL/process/0.20mm Standard @BBL X1C.json"  --load-filaments "/Users/jon/Library/Application Support/BambuStudio/system/BBL/filament/Generic PLA.json" --slice 0 --debug 2 --export-3mf /Users/jon/SplintFactoryFiles/outbox/cylinder-output-jg1.gcode.3mf /Users/jon/SplintFactoryFiles/outbox/cylinder_13.stl

WORKS!!!! Yeah?

