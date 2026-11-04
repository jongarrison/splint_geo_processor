#This code was extracted from python inside the Rhino Grasshopper file: splint_geo_processor/generators/InfinityExtend.gh

from importlib import reload
import FingerModel
reload(FingerModel)

from FingerModel import create_finger_model, FingerParams, create_finger_model_result
from dataclasses import replace

import BrepDifference
reload(BrepDifference) #FOR DEV ONLY
from BrepDifference import robust_brep_difference

# Refresher, order of elements:
# metacarpel numb, mcp, proximal, pip, middle, dip, distal

# input values to work with:
# shell_thickness
# root_circ ~= mcp_circ 
# mid_circ ~=
# tip_circ
# flexion_degrees
# inter_phalanx_distance
# extent_band_width

# The model input values that we need to provide or compute:

trimDistAroundPIP = (inter_phalanx_distance/2) + (extent_band_width/2)


# Must do linear interpolation on outer joints
min_linear_int = 2.0

mcp_circ_calc = root_circ - (mid_circ - root_circ)
mcp_circ_calc = mcp_circ_calc if mcp_circ_calc > min_linear_int else min_linear_int

dip_circ_calc = tip_circ - (mid_circ - tip_circ)
dip_circ_calc = dip_circ_calc if dip_circ_calc > min_linear_int else min_linear_int


coreParams = FingerParams(
    # Segment range - generate full finger or subset
    start_at="proximal",
    end_at="middle",

    # Joint circumferences (mm)
    mcp_circ=mcp_circ_calc,
    proximal_mid_circ=root_circ,
    pip_circ=mid_circ,
    middle_mid_circ=tip_circ,
    dip_circ=dip_circ_calc,
    # distal_mid_circ=60.0,
    # tip_circ=45.0,

    # Metacarpal stub length
    metacarpal_len=20.0,

    # Phalanx lengths (mm)
    proximal_len=inter_phalanx_distance,
    middle_len=inter_phalanx_distance,
    # distal_len=None,
    
    # Joint angles (degrees) - positive = curl toward palm
    # mcp_flex=0.0,
    pip_flex=flexion_degrees,
    # dip_flex=0.0,

    # mcp_lateral=0.0,
    # pip_lateral=0.0,
    # dip_lateral=0.0,   
)

# Create the central, core finger model
c_result_finger_model = create_finger_model_result(
    coreParams,
    tolerance=None,      # Use document tolerance
)
cCenterline, cFinger_brep, cComponents, cJointPositions = (c_result_finger_model.centerline, c_result_finger_model.finger_brep,
            c_result_finger_model.components, c_result_finger_model.joint_positions)
py_finger_model = [c_result_finger_model] #list is necessary for passing it through grasshopper

# Create the shell params
shellParams = replace(coreParams, shell_thickness=shell_thickness, start_at="proximal", end_at="middle")
shellParams.trim_start=("pip", -1 * trimDistAroundPIP)  # 10mm before MCP joint
shellParams.trim_end=("pip", trimDistAroundPIP)

# Create the shell model
sCenterline, sFinger_brep, sComponents, sJointPositions = create_finger_model(
    shellParams,
    tolerance=None,      # Use document tolerance
    return_parts=True    # Return individual components
)

# Subtract inner from shell to create hollow splint
splintBlank, success, method = robust_brep_difference(
    minuend=sFinger_brep,      # subtract FROM this (larger)
    subtrahend=cFinger_brep,   # subtract this (smaller)
    base_tolerance=0.001
)

splintBlankCenterline = sCenterline

if success:
    print(f"Hollow splint created via {method}")
    # result_brep is now a hollow shell
else:
    print(f"Difference failed: {method}")