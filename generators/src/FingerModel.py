"""
FingerModel.py
Generate anatomically-based finger geometry for splint modeling.
"""

from importlib import reload
import Rhino.Geometry as rg
from Rhino.Geometry import Point3d, Vector3d, Line, Plane, Polyline
import scriptcontext as sc
import math
from dataclasses import dataclass
from typing import Optional, List, Tuple
from splintcommon import log

import BrepGeneration
reload(BrepGeneration)
from BrepGeneration import create_tapered_cylinder, create_bulged_cylinder, create_sphere, create_cylinder

from BrepUnion import robust_brep_union


# Segment names in order from base to tip (joints and phalanges as separate segments)
SEGMENT_ORDER = ["metacarpal", "mcp", "proximal", "pip", "middle", "dip", "distal", "tip"]


@dataclass
class FingerParams:
    """Parameters for generating a finger model."""
    
    # Joint circumferences (mm) - from base to tip
    mcp_circ: float
    pip_circ: float
    dip_circ: float
    tip_circ: float
    
    # Phalanx circumferences (mm) at midpoint - from base to tip
    # When None, creates simple tapered cylinders instead of bulged
    proximal_circ: Optional[float] = None
    middle_circ: Optional[float] = None
    distal_circ: Optional[float] = None
    
    # Phalanx lengths (mm) - from base to tip
    proximal_len: float = 0.0
    middle_len: float = 0.0
    distal_len: float = 0.0
    
    # Joint flexion angles (degrees) - positive = flexion toward palm
    mcp_flex: float = 0.0
    pip_flex: float = 0.0
    dip_flex: float = 0.0
    
    # Joint lateral angles (degrees) - positive = toward +Y (ulnar for right hand)
    mcp_lateral: float = 0.0
    pip_lateral: float = 0.0
    dip_lateral: float = 0.0
    
    # Metacarpal stub length (mm)
    metacarpal_len: float = 20.0
    
    # Segment range (which parts to generate)
    # Valid: "metacarpal", "mcp", "proximal", "pip", "middle", "dip", "distal", "tip"
    start_at: str = "metacarpal"
    end_at: str = "tip"
    
    # Shell mode: adds thickness to all radii (0 = off)
    shell_thickness: float = 0.0
    
    # Augment joint sphere radii to improve boolean union reliability (mm)
    augment_joint_spheres: float = 0.2
    
    def get_segment_range(self) -> Tuple[int, int]:
        """Returns (start_index, end_index) for segment generation."""
        start_idx = SEGMENT_ORDER.index(self.start_at.lower())
        end_idx = SEGMENT_ORDER.index(self.end_at.lower())
        if start_idx > end_idx:
            raise ValueError(f"start_at '{self.start_at}' must come before end_at '{self.end_at}'")
        return start_idx, end_idx
    
    def includes_segment(self, segment: str) -> bool:
        """Check if a segment is within the generation range."""
        start_idx, end_idx = self.get_segment_range()
        seg_idx = SEGMENT_ORDER.index(segment.lower())
        return start_idx <= seg_idx <= end_idx


def advance_to_next_joint(
    initial_plane: Plane,
    phalanx_length: float,
    lateral_degrees: float,
    flexion_degrees: float
) -> Tuple[Plane, Line]:
    """
    Compute the coordinate frame transformation for advancing to the next joint.
    
    This function only performs the geometric math - no brep creation.
    Use create_joint_and_phalanx() afterward if geometry is needed.
    
    The plane's axes define the local coordinate system:
    - X-axis: direction the phalanx extends
    - Y-axis: flexion rotation axis (curl toward palm)
    - Z-axis: lateral rotation axis (side-to-side deviation)
    
    Rotations are applied around the initial plane's axes (before any rotation),
    with the rotation center at the initial plane's origin.
    
    Args:
        initial_plane: Plane at joint center (origin) with orientation axes
        phalanx_length: Length of the phalanx in mm
        lateral_degrees: Lateral deviation angle (rotation around Z-axis)
        flexion_degrees: Flexion angle (rotation around Y-axis)
        
    Returns:
        (new_plane, new_line)
        - new_plane: Plane at end of phalanx with updated orientation
        - new_line: Centerline of the phalanx (from joint to next joint)
    """
    # Extract axes from initial plane (these remain fixed for rotation calculations)
    origin = initial_plane.Origin
    x_axis = initial_plane.XAxis
    y_axis = initial_plane.YAxis
    z_axis = initial_plane.ZAxis
    
    # Create the phalanx line starting at origin, extending along x-axis
    phalanx_end = origin + x_axis * phalanx_length
    new_line = Line(origin, phalanx_end)
    
    # Copy initial plane to new plane (will be rotated)
    new_plane = Plane(initial_plane)
    
    # Apply flexion rotation (around initial Y-axis, centered at origin)
    if flexion_degrees != 0:
        flexion_xform = rg.Transform.Rotation(
            math.radians(flexion_degrees), y_axis, origin
        )
        new_plane.Transform(flexion_xform)
        # Transform the line's end point
        end_pt = Point3d(new_line.To)
        end_pt.Transform(flexion_xform)
        new_line = Line(origin, end_pt)
    
    # Apply lateral rotation (around initial Z-axis, centered at origin)
    if lateral_degrees != 0:
        lateral_xform = rg.Transform.Rotation(
            math.radians(lateral_degrees), z_axis, origin
        )
        new_plane.Transform(lateral_xform)
        # Transform the line's end point
        end_pt = Point3d(new_line.To)
        end_pt.Transform(lateral_xform)
        new_line = Line(origin, end_pt)
    
    # Move new_plane's origin to the end of the rotated line
    new_plane.Origin = new_line.To
    
    return new_plane, new_line


def create_joint_and_phalanx(
    phalanx_line: Line,
    joint_begin_radius: float,
    joint_end_radius: float,
    tolerance: float,
    mid_radius: Optional[float] = None,
    sphere_augment: float = 0.0
) -> Tuple[rg.Brep, rg.Brep]:
    """
    Create the joint sphere and phalanx geometry for a previously computed line.
    
    Call this after advance_to_next_joint() when geometry is actually needed.
    
    Args:
        phalanx_line: Centerline from advance_to_next_joint()
        joint_begin_radius: Radius at joint (start of phalanx)
        joint_end_radius: Radius at end of phalanx
        tolerance: Geometric tolerance for brep operations
        mid_radius: Optional radius at phalanx midpoint for bulge effect
        sphere_augment: Additional radius to add to joint sphere for union reliability
        
    Returns:
        (joint_brep, phalanx_brep)
        - joint_brep: Sphere at joint center (line start)
        - phalanx_brep: Tapered or bulged cylinder for phalanx
    """
    # Create joint sphere at the line's start point (joint center)
    # Augment radius slightly for better boolean union reliability
    sphere_radius = joint_begin_radius + sphere_augment
    joint_brep = create_sphere(phalanx_line.From, sphere_radius, tolerance)
    
    # Create phalanx - bulged if mid_radius provided, otherwise tapered
    if mid_radius is not None:
        phalanx_brep = create_bulged_cylinder(
            phalanx_line, joint_begin_radius, mid_radius, joint_end_radius, tolerance
        )
    else:
        phalanx_brep = create_tapered_cylinder(
            phalanx_line, joint_begin_radius, joint_end_radius, tolerance
        )
    
    return joint_brep, phalanx_brep


def create_finger_model(
    params: FingerParams,
    tolerance: Optional[float] = None,
    return_parts: bool = True
):
    """
    Generate a finger model from anatomical measurements.
    
    Orientation: Finger along +X, palm faces -Z. Positive angles = flexion toward palm.
    Construction order: Metacarpal -> MCP -> Proximal -> PIP -> Middle -> DIP -> Distal -> Tip
    
    The current_plane tracks position and orientation through the finger:
    - Origin: current joint/segment position
    - X-axis: direction finger extends
    - Y-axis: flexion rotation axis
    - Z-axis: lateral rotation axis (palm normal)
    
    Position is always computed from origin through all segments, but geometry is only
    created for segments within start_at..end_at range. This ensures partial models
    align with full models for boolean operations.
    
    Args:
        params: FingerParams dataclass with all measurements and options
        tolerance: Geometric tolerance for operations (defaults to document tolerance)
        return_parts: Whether to include component breps in return
        
    Returns:
        (centerline_polyline, finger_brep, component_breps)
    """
    
    if tolerance is None:
        tolerance = sc.doc.ModelAbsoluteTolerance
    
    shell = params.shell_thickness
    
    log("=" * 60)
    log("CREATING FINGER MODEL")
    log("=" * 60)
    log(f"Joints - MCP:{params.mcp_circ}mm, PIP:{params.pip_circ}mm, DIP:{params.dip_circ}mm, Tip:{params.tip_circ}mm")
    log(f"Phalanges - Prox:{params.proximal_circ}mm, Mid:{params.middle_circ}mm, Dist:{params.distal_circ}mm")
    log(f"Lengths - Prox:{params.proximal_len}mm, Mid:{params.middle_len}mm, Dist:{params.distal_len}mm")
    log(f"Flexion - MCP:{params.mcp_flex}deg, PIP:{params.pip_flex}deg, DIP:{params.dip_flex}deg")
    log(f"Lateral - MCP:{params.mcp_lateral}deg, PIP:{params.pip_lateral}deg, DIP:{params.dip_lateral}deg")
    log(f"Metacarpal stub: {params.metacarpal_len}mm")
    log(f"Segment range: {params.start_at} -> {params.end_at}")
    if shell != 0:
        log(f"Shell thickness: {shell}mm")
    
    # Convert circumferences to radii, add shell thickness
    mcp_radius = params.mcp_circ / (2 * math.pi) + shell
    pip_radius = params.pip_circ / (2 * math.pi) + shell
    dip_radius = params.dip_circ / (2 * math.pi) + shell
    tip_radius = params.tip_circ / (2 * math.pi) + shell
    
    # Convert phalanx mid-circumferences to radii (None = use tapered cylinder)
    proximal_mid_radius = (params.proximal_circ / (2 * math.pi) + shell) if params.proximal_circ else None
    middle_mid_radius = (params.middle_circ / (2 * math.pi) + shell) if params.middle_circ else None
    distal_mid_radius = (params.distal_circ / (2 * math.pi) + shell) if params.distal_circ else None
    
    log(f"Radii - MCP:{mcp_radius:.2f}, PIP:{pip_radius:.2f}, DIP:{dip_radius:.2f}, Tip:{tip_radius:.2f}")
    
    # Track components and centerline points
    components = []
    centerline_points = []
    
    # Helper to add start point on first rendered segment
    def add_start_point_if_first(pt):
        if not centerline_points:
            centerline_points.append(Point3d(pt))
    
    # Initialize current_plane at origin
    # X = finger direction, Y = flexion axis, Z = lateral axis (palm normal up)
    current_plane = Plane(Point3d.Origin, Vector3d.XAxis, Vector3d.YAxis)
    
    # --- METACARPAL STUB (cylinder, no joint) ---
    metacarpal_end = current_plane.Origin + current_plane.XAxis * params.metacarpal_len
    if params.includes_segment("metacarpal"):
        log("\n--- Metacarpal Stub ---")
        add_start_point_if_first(current_plane.Origin)
        # Cylinder axis is the plane's normal, so create plane with XAxis as normal
        metacarpal_axis_plane = Plane(current_plane.Origin, current_plane.XAxis)
        metacarpal_brep = create_cylinder(metacarpal_axis_plane, mcp_radius, params.metacarpal_len, tolerance)
        if metacarpal_brep:
            components.append(metacarpal_brep)
            log(f"Metacarpal: length={params.metacarpal_len}mm, radius={mcp_radius:.2f}mm")
        else:
            log("ERROR: Failed to create metacarpal stub")
            return None, None, None
        centerline_points.append(Point3d(metacarpal_end))
    
    # Move plane origin to end of metacarpal (MCP joint location)
    current_plane.Origin = metacarpal_end
    
    # --- MCP JOINT + PROXIMAL PHALANX ---
    log("\n--- MCP Joint + Proximal Phalanx ---")
    # Always advance the plane (coordinate math only)
    new_plane, prox_line = advance_to_next_joint(
        current_plane,
        params.proximal_len,
        params.mcp_lateral,
        params.mcp_flex
    )
    
    # Only create geometry if either segment is included
    mcp_brep = None
    prox_brep = None
    if params.includes_segment("mcp") or params.includes_segment("proximal"):
        mcp_brep, prox_brep = create_joint_and_phalanx(
            prox_line,
            mcp_radius,
            pip_radius,
            tolerance,
            proximal_mid_radius,
            params.augment_joint_spheres
        )
    
    if params.includes_segment("mcp"):
        add_start_point_if_first(prox_line.From)
        if mcp_brep:
            components.append(mcp_brep)
            log(f"MCP Joint: center={prox_line.From}, radius={mcp_radius:.2f}mm")
        else:
            log("ERROR: Failed to create MCP joint")
            return None, None, None
    
    if params.includes_segment("proximal"):
        add_start_point_if_first(prox_line.From)
        if prox_brep:
            components.append(prox_brep)
            log(f"Proximal Phalanx: length={params.proximal_len}mm, r1={mcp_radius:.2f}, r2={pip_radius:.2f}")
        else:
            log("ERROR: Failed to create proximal phalanx")
            return None, None, None
        centerline_points.append(Point3d(prox_line.To))
    
    current_plane = new_plane
    
    # --- PIP JOINT + MIDDLE PHALANX ---
    log("\n--- PIP Joint + Middle Phalanx ---")
    # Always advance the plane (coordinate math only)
    new_plane, mid_line = advance_to_next_joint(
        current_plane,
        params.middle_len,
        params.pip_lateral,
        params.pip_flex
    )
    
    # Only create geometry if either segment is included
    pip_brep = None
    mid_brep = None
    if params.includes_segment("pip") or params.includes_segment("middle"):
        pip_brep, mid_brep = create_joint_and_phalanx(
            mid_line,
            pip_radius,
            dip_radius,
            tolerance,
            middle_mid_radius,
            params.augment_joint_spheres
        )
    
    if params.includes_segment("pip"):
        add_start_point_if_first(mid_line.From)
        if pip_brep:
            components.append(pip_brep)
            log(f"PIP Joint: center={mid_line.From}, radius={pip_radius:.2f}mm")
        else:
            log("ERROR: Failed to create PIP joint")
            return None, None, None
    
    if params.includes_segment("middle"):
        add_start_point_if_first(mid_line.From)
        if mid_brep:
            components.append(mid_brep)
            log(f"Middle Phalanx: length={params.middle_len}mm, r1={pip_radius:.2f}, r2={dip_radius:.2f}")
        else:
            log("ERROR: Failed to create middle phalanx")
            return None, None, None
        centerline_points.append(Point3d(mid_line.To))
    
    current_plane = new_plane
    
    # --- DIP JOINT + DISTAL PHALANX ---
    log("\n--- DIP Joint + Distal Phalanx ---")
    # Always advance the plane (coordinate math only)
    new_plane, dist_line = advance_to_next_joint(
        current_plane,
        params.distal_len,
        params.dip_lateral,
        params.dip_flex
    )
    
    # Only create geometry if either segment is included
    dip_brep = None
    dist_brep = None
    if params.includes_segment("dip") or params.includes_segment("distal"):
        dip_brep, dist_brep = create_joint_and_phalanx(
            dist_line,
            dip_radius,
            tip_radius,
            tolerance,
            distal_mid_radius,
            params.augment_joint_spheres
        )
    
    if params.includes_segment("dip"):
        add_start_point_if_first(dist_line.From)
        if dip_brep:
            components.append(dip_brep)
            log(f"DIP Joint: center={dist_line.From}, radius={dip_radius:.2f}mm")
        else:
            log("ERROR: Failed to create DIP joint")
            return None, None, None
    
    if params.includes_segment("distal"):
        add_start_point_if_first(dist_line.From)
        if dist_brep:
            components.append(dist_brep)
            log(f"Distal Phalanx: length={params.distal_len}mm, r1={dip_radius:.2f}, r2={tip_radius:.2f}")
        else:
            log("ERROR: Failed to create distal phalanx")
            return None, None, None
        centerline_points.append(Point3d(dist_line.To))
    
    current_plane = new_plane
    
    # --- FINGERTIP (sphere at final position) ---
    if params.includes_segment("tip"):
        log("\n--- Fingertip ---")
        add_start_point_if_first(current_plane.Origin)
        tip_brep = create_sphere(current_plane.Origin, tip_radius, tolerance)
        if tip_brep:
            components.append(tip_brep)
            log(f"Fingertip: center={current_plane.Origin}, radius={tip_radius:.2f}mm")
        else:
            log("ERROR: Failed to create fingertip")
            return None, None, None
    
    # Create centerline polyline
    centerline = Polyline(centerline_points) if centerline_points else None
    log(f"\nCenterline: {len(centerline_points)} points")
    
    # Union all components
    log("\n--- Unioning Components ---")
    log(f"Component count: {len(components)}")
    
    if not components:
        log("WARNING: No components to union")
        return centerline, None, None
    
    finger_brep, success, method = robust_brep_union(components, tolerance, check_volumes=True)
    
    if not success or finger_brep is None:
        log(f"ERROR: Failed to union finger components (method attempted: {method})")
        return centerline, None, components if return_parts else None
    
    log(f"SUCCESS: Finger union complete via {method}")
    log(f"Final finger volume: {finger_brep.GetVolume():.2f} mm^3")
    log("=" * 60)
    
    return centerline, finger_brep, components if return_parts else None
