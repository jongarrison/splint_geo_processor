# This file represents a stub for a GhPython component in Grasshopper.
# It sets up the environment to import and use the splintcommon or other external modules.

from pathlib import Path
import sys
from importlib import reload #FOR DEV
import time

# In normal GH doc
ghFileDir = None
try:
    ghFileDir = str(Path.joinpath(Path(ghenv.Component.OnPingDocument().FilePath).resolve().parents[0], "src"))
except:
    # In GH Cluster
    ghFileDir = str(Path.joinpath(Path(ghenv.Component.OnPingDocument().Owner.OnPingDocument().FilePath).resolve().parents[0], "src"))

print(f"{ghFileDir=}")

if ghFileDir not in sys.path:
    print("ghFileDir needed to be included")
    sys.path.append(ghFileDir)

import splintcommon
reload(splintcommon) #FOR DEV

#splintcommon module is now available for use in this GhPython component
splintcommon.log(f'GhPython Node initialized at {time.ctime()}')

#Your code here