#!/bin/bash

# These are the PROCESSsettings as of 10/8/2025

# The goal here is to preserve the information about:
# * where settings originated from (the paths linked in this script)  
# * the actual setting values as they were found
# * which specific settings were changed (see jq commands below)

# The original PROCESS file: /Users/jon/Library/Application Support/BambuStudio/system/BBL/process/0.20mm Standard @BBL X1C.json

rm ./*.json

cp "/Users/jon/Library/Application Support/BambuStudio/system/BBL/process/0.20mm Standard @BBL X1C.json" base0.json # child
cp "/Users/jon/Library/Application Support/BambuStudio/system/BBL/process/fdm_process_single_0.20.json" base1.json   # parent
cp "/Users/jon/Library/Application Support/BambuStudio/system/BBL/process/fdm_process_single_common.json" base2.json       # grandparent
cp "/Users/jon/Library/Application Support/BambuStudio/system/BBL/process/fdm_process_common.json" base3.json  # great-grandparent

jq -s '.[0] * .[1] * .[2] * .[3] | del(.inherits)' base3.json base2.json base1.json base0.json > process-expanded-original.json

# Now change the individual keys as needed:

jq '.' process-expanded-original.json > process-final.json