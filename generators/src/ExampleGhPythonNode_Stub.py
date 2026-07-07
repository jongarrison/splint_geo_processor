# This file represents a stub for a GhPython component in Grasshopper.
# It sets up the environment to import and use the splintcommon or other external modules.

from pathlib import Path
import sys
from importlib import reload #FOR DEV
import time

# Walk up through nested clusters to find the root GH document (necessary for use in nested clusters)
doc = ghenv.Component.OnPingDocument()
while not doc.FilePath and doc.Owner:
    doc = doc.Owner.OnPingDocument()
ghFileDir = str(Path(doc.FilePath).resolve().parent / "src")

print(f"{ghFileDir=}")

if ghFileDir not in sys.path:
    print("ghFileDir needed to be included")
    sys.path.append(ghFileDir)

import splintcommon
reload(splintcommon) #FOR DEV
from splintcommon import mark_generation_start
mark_generation_start()

#splintcommon module is now available for use in this GhPython component
component_label = ghenv.Component.NickName or ghenv.Component.Name
component_start_time = time.time()
component_run_token = str(int(component_start_time * 1000))
splintcommon.log(f'[{component_label} run={component_run_token}] GhPython Node initialized at {time.ctime(component_start_time)}')


#Component code here


component_elapsed_seconds = time.time() - component_start_time
splintcommon.log(f'[{component_label} run={component_run_token}] GhPython Node completed in {component_elapsed_seconds:.3f}s')