"""
FingerModel.py
Generate anatomically-based finger geometry for splint modeling.
"""

import Rhino.Geometry as rg
from Rhino.Geometry import Point3d, Vector3d, Line, Plane, Polyline
import scriptcontext as sc
import math
from dataclasses import dataclass
from typing import Optional, List, Tuple
from splintcommon import log
from BrepGeneration import create_tapered_cylinder, create_sphere, create_cylinder
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
    proximal_circ: float
    middle_circ: float
    distal_circ: float
    
    # Phalanx lengths (mm) - from base to tip
    proximal_len: float
    middle_len: float
    distal_len: float
    
    # Joint flexion angles (degrees) - positive = flexion toward palm
    mcp_angle: float = 0.0
    pip_angle: float = 0.0
    dip_angle: float = 0.0
    
    # Metacarpal stub length (mm)
    metacarpal_len: float = 20.0
    
    # Segment range (which parts to generate)
    # Valid: "metacarpal", "mcp", "proximal", "pip", "middle", "dip", "distal", "tip"
    start_at: str = "metacarpal"
    end_at: str = "tip"
    
    # Shell mode: adds thickness to all radii (0 = off)
    shell_thickness: float = 0.0
    
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


def create_finger_model(
    params: FingerParams,
    tolerance: Optional[float] = None,
    return_parts: bool = True
):
    """
    Generate a finger model from anatomical measurements.
    
    Orientation: Finger along +X, palm faces -Z. Positive angles = flexion toward palm.
    Construction order: Metacarpal -> MCP -> Proximal -> PIP -> Middle -> DIP -> Distal -> Tip
    
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
    log(f"Angles - MCP:{params.mcp_angle}deg, PIP:{params.pip_angle}deg, DIP:{params.dip_angle}deg")
    log(f"Metacarpal stub: {params.metacarpal_len}mm")
    log(f"Segment range: {params.start_at} -> {params.end_at}")
    if shell != 0:
        log(f"Shell thickness: {shell}mm")
    
    # Convert circumferences to radii, add shell thickness
    mcp_radius = params.mcp_circ / (2 * math.pi) + shell
    pip_radius = params.pip_circ / (2 * math.pi) + shell
    dip_radius = params.dip_circ / (2 * math.pi) + shell
    tip_radius = params.tip_circ / (2 * math.pi) + shell
    
    log(f"Radii - MCP:{mcp_radius:.2f}, PIP:{pip_radius:.2f}, DIP:{dip_radius:.2f}, Tip:{tip_radius:.2f}")
    
    # Track components and centerline points
    components = []
    centerline_points = []
    
    # Current position and direction (start at origin, along +X)
    # Orientation: finger along +X, palm faces -Z, positive angles = flexion toward palm
    current_pos = Point3d.Origin
    current_dir = Vector3d.XAxis
    
    # Flexion rotation around +Y axis (curls finger toward -Z / palm)
    def apply_flexion(angle_deg):
        nonlocal current_dir
        if angle_deg != 0:
            rotation_xform = rg.Transform.Rotation(math.radians(angle_deg), Vector3d.YAxis, current_pos)
            current_dir = Vector3d(current_dir)
            current_dir.Transform(rotation_xform)
            current_dir.Unitize()
    
    # Helper to check if this is the first rendered segment (for centerline start point)
    def add_start_point_if_first():
        if not centerline_points:
            centerline_points.append(Point3d(current_pos))
    
    # --- METACARPAL STUB (cylinder) ---
    metacarpal_end = current_pos + current_dir * params.metacarpal_len
    if params.includes_segment("metacarpal"):
        log("\n--- Metacarpal Stub ---")
        add_start_point_if_first()
        metacarpal_plane = Plane(current_pos, current_dir)
        metacarpal_brep = create_cylinder(metacarpal_plane, mcp_radius, params.metacarpal_len, tolerance)
        if metacarpal_brep:
            components.append(metacarpal_brep)
            log(f"Metacarpal: length={params.metacarpal_len}mm, radius={mcp_radius:.2f}mm")
        else:
            log("ERROR: Failed to create metacarpal stub")
            return None, None, None
        centerline_points.append(Point3d(metacarpal_end))
    current_pos = metacarpal_end
    
    # --- MCP JOINT (sphere) ---
    # Always apply MCP angle for correct positioning
    apply_flexion(params.mcp_angle)
    
    if params.includes_segment("mcp"):
        log("\n--- MCP Joint ---")
        add_start_point_if_first()
        mcp_brep = create_sphere(current_pos, mcp_radius, tolerance)
        if mcp_brep:
            components.append(mcp_brep)
            log(f"MCP Joint: center={current_pos}, radius={mcp_radius:.2f}mm")
        else:
            log("ERROR: Failed to create MCP joint")
            return None, None, None
    
    # --- PROXIMAL PHALANX (tapered cylinder) ---
    proximal_end = current_pos + current_dir * params.proximal_len
    if params.includes_segment("proximal"):
        log("\n--- Proximal Phalanx ---")
        add_start_point_if_first()
        prox_line = Line(current_pos, proximal_end)
        prox_brep = create_tapered_cylinder(prox_line, mcp_radius, pip_radius, tolerance)
        if prox_brep:
            components.append(prox_brep)
            log(f"Proximal Phalanx: length={params.proximal_len}mm, r1={mcp_radius:.2f}, r2={pip_radius:.2f}")
        else:
            log("ERROR: Failed to create proximal phalanx")
            return None, None, None
        centerline_points.append(Point3d(proximal_end))
    current_pos = proximal_end
    
    # --- PIP JOINT (sphere) ---
    # Always apply PIP angle for correct positioning
    apply_flexion(params.pip_angle)
    
    if params.includes_segment("pip"):
        log("\n--- PIP Joint ---")
        add_start_point_if_first()
        pip_brep = create_sphere(current_pos, pip_radius, tolerance)
        if pip_brep:
            components.append(pip_brep)
            log(f"PIP Joint: center={current_pos}, radius={pip_radius:.2f}mm")
        else:
            log("ERROR: Failed to create PIP joint")
            return None, None, None
    
    # --- MIDDLE PHALANX (tapered cylinder) ---
    middle_end = current_pos + current_dir * params.middle_len
    if params.includes_segment("middle"):
        log("\n--- Middle Phalanx ---")
        add_start_point_if_first()
        mid_line = Line(current_pos, middle_end)
        mid_brep = create_tapered_cylinder(mid_line, pip_radius, dip_radius, tolerance)
        if mid_brep:
            components.append(mid_brep)
            log(f"Middle Phalanx: length={params.middle_len}mm, r1={pip_radius:.2f}, r2={dip_radius:.2f}")
        else:
            log("ERROR: Failed to create middle phalanx")
            return None, None, None
        centerline_points.append(Point3d(middle_end))
    current_pos = middle_end
    
    # --- DIP JOINT (sphere) ---
    # Always apply DIP angle for correct positioning
    apply_flexion(params.dip_angle)
    
    if params.includes_segment("dip"):
        log("\n--- DIP Joint ---")
        add_start_point_if_first()
        dip_brep = create_sphere(current_pos, dip_radius, tolerance)
        if dip_brep:
            components.append(dip_brep)
            log(f"DIP Joint: center={current_pos}, radius={dip_radius:.2f}mm")
        else:
            log("ERROR: Failed to create DIP joint")
            return None, None, None
    
    # --- DISTAL PHALANX (tapered cylinder) ---
    distal_end = current_pos + current_dir * params.distal_len
    if params.includes_segment("distal"):
        log("\n--- Distal Phalanx ---")
        add_start_point_if_first()
        dist_line = Line(current_pos, distal_end)
        dist_brep = create_tapered_cylinder(dist_line, dip_radius, tip_radius, tolerance)
        if dist_brep:
            components.append(dist_brep)
            log(f"Distal Phalanx: length={params.distal_len}mm, r1={dip_radius:.2f}, r2={tip_radius:.2f}")
        else:
            log("ERROR: Failed to create distal phalanx")
            return None, None, None
        centerline_points.append(Point3d(distal_end))
    current_pos = distal_end
    
    # --- FINGERTIP (sphere) ---
    if params.includes_segment("tip"):
        log("\n--- Fingertip ---")
        add_start_point_if_first()
        tip_brep = create_sphere(current_pos, tip_radius, tolerance)
        if tip_brep:
            components.append(tip_brep)
            log(f"Fingertip: center={current_pos}, radius={tip_radius:.2f}mm")
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
