#!/bin/bash

# These are the Filament settings as of 10/8/2025

# The goal here is to preserve the information about:
# * where settings originated from (the paths linked in this script)  
# * the actual setting values as they were found
# * which specific settings were changed (see jq commands below)

# The original FILAMENT file: /Users/jon/Library/Application Support/BambuStudio/system/BBL/filament/Generic PLA.json

rm ./*.json

cp "/Users/jon/Library/Application Support/BambuStudio/system/BBL/filament/Generic PLA.json" base0.json          # child
cp "/Users/jon/Library/Application Support/BambuStudio/system/BBL/filament/Generic PLA @base.json" base1.json    # parent
cp "/Users/jon/Library/Application Support/BambuStudio/system/BBL/filament/fdm_filament_pla.json" base2.json     # grandparent
cp "/Users/jon/Library/Application Support/BambuStudio/system/BBL/filament/fdm_filament_common.json" base3.json  # great-grandparent

jq -s '.[0] * .[1] * .[2] * .[3] | del(.inherits)' base3.json base2.json base1.json base0.json > filament-expanded-original.json

# Now change the individual keys as needed:

jq '.' filament-expanded-original.json > filament-final.json



