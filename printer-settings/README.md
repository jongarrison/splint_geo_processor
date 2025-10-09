# Bambu Process Settings Directory

* These settings represent the machine, process, and filament settings


* The first working version of this software used these settings:
  * /Users/jon/Library/Application Support/BambuStudio/system/BBL/machine/Bambu Lab P1S 0.4 nozzle.json
  * /Users/jon/Library/Application Support/BambuStudio/system/BBL/process/0.20mm Standard @BBL X1C.json
  * /Users/jon/Library/Application Support/BambuStudio/system/BBL/filament/Generic PLA.json


* A deeper dive on each of these:


  * /Users/jon/Library/Application Support/BambuStudio/system/BBL/machine/Bambu Lab P1S 0.4 nozzle.json

    Claims to inherit from: "fdm_bbl_3dp_001_common"
    ls /Users/jon/Library/Application\ Support/BambuStudio/system/BBL/machine/fdm_bbl_3dp_001_common*

    found:
    /Users/jon/Library/Application Support/BambuStudio/system/BBL/machine/fdm_bbl_3dp_001_common.json

  * /Users/jon/Library/Application Support/BambuStudio/system/BBL/process/0.20mm Standard @BBL X1C.json

    Claims to inherit from: "fdm_process_single_0.20"

    Does the ancestor file need to be in the same directory, or can we count on BambuStudio looking for those settings from the system?

    Notice the space escape to reveal the inherited file:
    ls /Users/jon/Library/Application\ Support/BambuStudio/system/BBL/process/fdm_process_single_0.20*

    Found!:
    /Users/jon/Library/Application Support/BambuStudio/system/BBL/process/fdm_process_single_0.20.json


  * /Users/jon/Library/Application Support/BambuStudio/system/BBL/filament/Generic PLA.json

    * Inherits from: "Generic PLA @base"

    ls /Users/jon/Library/Application\ Support/BambuStudio/system/BBL/filament/Generic\ PLA\ @base*

    which found:
    /Users/jon/Library/Application Support/BambuStudio/system/BBL/filament/Generic PLA @base.json


## Let's make fully expanded files, based on the system settings, so that we have full control over the files

  * JQ command for merging files:

    jq -s '.[0] * .[1]' base.json override.json

    so, the goal is to create fully expanded files to run...

  * MACHINE:

    jq -s '.[0] * .[1]' "/Users/jon/Library/Application Support/BambuStudio/system/BBL/machine/fdm_bbl_3dp_001_common.json" "/Users/jon/Library/Application Support/BambuStudio/system/BBL/machine/Bambu Lab P1S 0.4 nozzle.json" > machine.json

  * PROCESS:

  * FILAMENT:

