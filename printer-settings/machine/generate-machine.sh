#!/bin/bash

# These are the MACHINE settings as of 10/8/2025

# The goal here is to preserve the information about:
# * where settings originated from (the paths linked in this script)  
# * the actual setting values as they were found
# * which specific settings were changed (see jq commands below)

# The original MACHINE file: /Users/jon/Library/Application Support/BambuStudio/system/BBL/machine/Bambu Lab P1S 0.4 nozzle.json

rm ./*.json

cp "/Users/jon/Library/Application Support/BambuStudio/system/BBL/machine/Bambu Lab P1S 0.4 nozzle.json" base0.json # child
cp "/Users/jon/Library/Application Support/BambuStudio/system/BBL/machine/fdm_bbl_3dp_001_common.json" base1.json   # parent
cp "/Users/jon/Library/Application Support/BambuStudio/system/BBL/machine/fdm_machine_common.json" base2.json       # grandparent

jq -s '.[0] * .[1] * .[2] | del(.inherits)' base2.json base1.json base0.json > machine-expanded-original.json

# Now change the individual keys as needed:

jq '.' machine-expanded-original.json > machine-final.json

