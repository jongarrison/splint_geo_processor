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


## First Layer Adhesion — Review and Recommendations (2026-06-04)

Our splints have small footprints, making first-layer adhesion critical. No brims — finish quality matters.
Active files used by the slicer pipeline: `filament-final.json`, `machine-final.json`, `process-final.json`.

### Changes applied

| File | Setting | Old | New | Reason |
|---|---|---|---|---|
| process | `initial_layer_speed` | 50 | 25 | Slow first layer improves adhesion; 15 mm/s was too slow — caused poor flow and worse adhesion |
| process | `initial_layer_infill_speed` | 60 | 25 | Match perimeter speed on first layer |
| process | `initial_layer_acceleration` | 500 | 200 | Reduces jerk/vibration that lifts small parts before they bond |
| process | `skirt_loops` | 0 | 1 | Single skirt loop primes nozzle without touching the part |

### What didn't work (2026-06-04)

Tried simultaneously: `nozzle_temperature_initial_layer` 225°C, `filament_flow_ratio` 1.0, `initial_layer_speed` 15 mm/s.
Result: **worse adhesion** — even the purge line failed to stick. Likely cause: PLA at higher temp moving very slowly becomes too fluid, slumps instead of pressing into the bed texture, reducing actual contact area. Hotter + slower is not always better.

Reverted nozzle temp and flow to originals. Settled on 25 mm/s as a middle ground for first layer speed.

### Temperatures confirmed correct for PLA on PEI textured plate
- Nozzle: 220°C (225°C first layer after change above)
- Bed: 60°C — appropriate for PLA on textured PEI

---

## Let's make fully expanded files, based on the system settings, so that we have full control over the files

  * JQ command for merging files:

    jq -s '.[0] * .[1]' base.json override.json

    so, the goal is to create fully expanded files to run...

  * MACHINE:

    jq -s '.[0] * .[1]' "/Users/jon/Library/Application Support/BambuStudio/system/BBL/machine/fdm_bbl_3dp_001_common.json" "/Users/jon/Library/Application Support/BambuStudio/system/BBL/machine/Bambu Lab P1S 0.4 nozzle.json" > machine.json

  * PROCESS:

  * FILAMENT:

