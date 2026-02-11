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
    
    # Endpoint circumferences (mm) - at joints and fingertip
    # Only required for segments being rendered (validated by validate_for_segment_range)
    mcp_circ: float = 0.0
    pip_circ: float = 0.0
    dip_circ: float = 0.0
    tip_circ: float = 0.0
    
    # Phalanx mid-circumferences (mm) - optional bulge/waist at bone midpoints
    # When None, creates simple tapered cylinders instead of bulged
    proximal_mid_circ: Optional[float] = None
    middle_mid_circ: Optional[float] = None
    distal_mid_circ: Optional[float] = None
    
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
    metacarpal_len: float = 0.0
    
    # Segment range (which parts to generate)
    # Valid: "metacarpal", "mcp", "proximal", "pip", "middle", "dip", "distal", "tip"
    start_at: str = "metacarpal"
    end_at: str = "tip"
    
    # Shell mode: adds thickness to all radii (0 = off)
    shell_thickness: float = 0.0
    
    # Augment joint sphere radii to improve boolean union reliability (mm)
    augment_joint_spheres: float = 0.2
    
    # Trim region specification: (joint_name, offset_mm)
    # offset is negative for before joint, positive for after
    # Example: ("mcp", -20.0) means 20mm before MCP joint
    # When None, no trimming is performed on that end
    trim_start: Optional[Tuple[str, float]] = None
    trim_end: Optional[Tuple[str, float]] = None
    
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
    
    def validate_for_segment_range(self) -> List[str]:
        """
        Validate that required parameters are set for the specified segment range.
        
        Returns list of error messages (empty if valid).
        """
        errors = []
        
        # Circumference requirements based on which segments are rendered
        # Each circ is needed if we're rendering geometry that uses it
        
        # mcp_circ: needed for metacarpal, mcp joint, or proximal phalanx
        if self.includes_segment("metacarpal") or self.includes_segment("mcp") or self.includes_segment("proximal"):
            if self.mcp_circ <= 0:
                errors.append(f"mcp_circ must be > 0 when rendering metacarpal/mcp/proximal (got {self.mcp_circ})")
        
        # pip_circ: needed for proximal phalanx, pip joint, or middle phalanx
        if self.includes_segment("proximal") or self.includes_segment("pip") or self.includes_segment("middle"):
            if self.pip_circ <= 0:
                errors.append(f"pip_circ must be > 0 when rendering proximal/pip/middle (got {self.pip_circ})")
        
        # dip_circ: needed for middle phalanx, dip joint, or distal phalanx
        if self.includes_segment("middle") or self.includes_segment("dip") or self.includes_segment("distal"):
            if self.dip_circ <= 0:
                errors.append(f"dip_circ must be > 0 when rendering middle/dip/distal (got {self.dip_circ})")
        
        # tip_circ: needed for distal phalanx or tip sphere
        if self.includes_segment("distal") or self.includes_segment("tip"):
            if self.tip_circ <= 0:
                errors.append(f"tip_circ must be > 0 when rendering distal/tip (got {self.tip_circ})")
        
        # Length requirements - phalanx lengths needed if rendering that phalanx
        if self.includes_segment("proximal") and self.proximal_len <= 0:
            errors.append(f"proximal_len must be > 0 when rendering proximal (got {self.proximal_len})")
        
        if self.includes_segment("middle") and self.middle_len <= 0:
            errors.append(f"middle_len must be > 0 when rendering middle (got {self.middle_len})")
        
        if self.includes_segment("distal") and self.distal_len <= 0:
            errors.append(f"distal_len must be > 0 when rendering distal (got {self.distal_len})")
        
        if self.includes_segment("metacarpal") and self.metacarpal_len <= 0:
            errors.append(f"metacarpal_len must be > 0 when rendering metacarpal (got {self.metacarpal_len})")
        
        return errors


def get_trim_point_and_plane(
    trim_spec: Tuple[str, float],
    joint_positions: dict,
    params: 'FingerParams'
) -> Tuple[Point3d, Plane]:
    """
    Convert a trim specification (joint, offset) to a 3D point and perpendicular plane.
    
    Args:
        trim_spec: (joint_name, offset_mm) where offset is negative for before, positive for after
        joint_positions: dict from compute_joint_positions()
        params: FingerParams for segment lengths
        
    Returns:
        (point, plane) where plane is perpendicular to centerline at point
    """
    joint_name, offset = trim_spec
    joint_name = joint_name.lower()
    
    if joint_name not in joint_positions:
        raise ValueError(f"Unknown joint name: {joint_name}. Valid: {list(joint_positions.keys())}")
    
    joint_pos, joint_dir, joint_dist = joint_positions[joint_name]
    
    # Target distance along centerline
    target_dist = joint_dist + offset
    
    # Find which segment this falls in and compute the point
    # Segments: origin->mcp (metacarpal), mcp->pip (proximal), pip->dip (middle), dip->tip (distal)
    segments = [
        ("origin", "mcp", params.metacarpal_len),
        ("mcp", "pip", params.proximal_len),
        ("pip", "dip", params.middle_len),
        ("dip", "tip", params.distal_len),
    ]
    
    cumulative = 0.0
    for start_joint, end_joint, seg_len in segments:
        seg_start_dist = cumulative
        seg_end_dist = cumulative + seg_len
        
        if seg_start_dist <= target_dist <= seg_end_dist:
            # Target is within this segment
            start_pos, _, _ = joint_positions[start_joint]
            end_pos, end_dir, _ = joint_positions[end_joint]
            
            # Compute direction for this segment
            seg_dir = Vector3d(end_pos - start_pos)
            seg_dir.Unitize()
            
            # Interpolate position within segment
            t = (target_dist - seg_start_dist) / seg_len if seg_len > 0 else 0
            trim_point = start_pos + seg_dir * (t * seg_len)
            
            # Create plane perpendicular to segment direction
            trim_plane = Plane(trim_point, seg_dir)
            
            return trim_point, trim_plane
        
        cumulative = seg_end_dist
    
    # If we get here, target_dist is outside the finger bounds
    # Clamp to the nearest end
    if target_dist < 0:
        origin_pos, origin_dir, _ = joint_positions["origin"]
        trim_point = origin_pos + origin_dir * target_dist  # extend backward
        trim_plane = Plane(trim_point, origin_dir)
    else:
        tip_pos, tip_dir, _ = joint_positions["tip"]
        overshoot = target_dist - cumulative
        trim_point = tip_pos + tip_dir * overshoot  # extend forward
        trim_plane = Plane(trim_point, tip_dir)
    
    return trim_point, trim_plane


def trim_finger_model(
    finger_brep: rg.Brep,
    centerline: Polyline,
    params: 'FingerParams',
    joint_positions: dict,
    tolerance: float
) -> Tuple[rg.Brep, Polyline]:
    """
    Trim the finger model to the region specified by params.trim_start and params.trim_end.
    
    Args:
        finger_brep: The unioned finger brep to trim
        centerline: The centerline polyline
        params: FingerParams with trim_start and/or trim_end specified
        joint_positions: Dict from create_finger_model mapping joint names to
                        (position, direction, cumulative_distance)
        tolerance: Geometric tolerance
        
    Returns:
        (trimmed_brep, trimmed_centerline)
    """
    if params.trim_start is None and params.trim_end is None:
        return finger_brep, centerline
    
    log("\n--- Trimming Finger Model ---")
    
    # Get bounding box to size the cutting planes appropriately
    bbox = finger_brep.GetBoundingBox(True)
    plane_extent = bbox.Diagonal.Length * 2  # ensure plane is large enough
    
    trimmed_brep = finger_brep
    
    # Process trim_start (remove material before this plane)
    if params.trim_start is not None:
        start_point, start_plane = get_trim_point_and_plane(params.trim_start, joint_positions, params)
        log(f"Trim start: {params.trim_start} -> point={start_point}")
        
        # Create a PlaneSurface large enough to cut through the brep
        plane_srf = rg.PlaneSurface(start_plane, 
                                     rg.Interval(-plane_extent, plane_extent),
                                     rg.Interval(-plane_extent, plane_extent))
        
        # Split brep with plane and keep the part on the positive side (toward tip)
        split_result = trimmed_brep.Split([plane_srf.ToBrep()], tolerance)
        if split_result and len(split_result) > 0:
            # Find the piece(s) on the positive side of the plane (toward fingertip)
            # The positive side is where plane.Normal points
            kept_pieces = []
            for piece in split_result:
                centroid = rg.VolumeMassProperties.Compute(piece).Centroid
                # Check which side of plane the centroid is on
                dist = start_plane.DistanceTo(centroid)
                if dist > 0:  # On positive side (toward tip)
                    kept_pieces.append(piece)
            
            if kept_pieces:
                if len(kept_pieces) == 1:
                    trimmed_brep = kept_pieces[0]
                else:
                    # Union the kept pieces
                    unioned = rg.Brep.CreateBooleanUnion(kept_pieces, tolerance)
                    if unioned and len(unioned) > 0:
                        trimmed_brep = unioned[0]
                
                # Cap the planar hole created by the split
                capped = trimmed_brep.CapPlanarHoles(tolerance)
                if capped:
                    trimmed_brep = capped
                    log(f"Trim start applied and capped, kept {len(kept_pieces)} piece(s)")
                else:
                    log(f"Trim start applied (cap failed), kept {len(kept_pieces)} piece(s)")
            else:
                log("WARNING: No pieces kept after trim_start split")
        else:
            log("WARNING: Brep split for trim_start produced no result")
    
    # Process trim_end (remove material after this plane)
    if params.trim_end is not None:
        end_point, end_plane = get_trim_point_and_plane(params.trim_end, joint_positions, params)
        log(f"Trim end: {params.trim_end} -> point={end_point}")
        
        # Create a PlaneSurface large enough to cut through the brep
        bbox = trimmed_brep.GetBoundingBox(True)
        plane_extent = bbox.Diagonal.Length * 2
        plane_srf = rg.PlaneSurface(end_plane,
                                     rg.Interval(-plane_extent, plane_extent),
                                     rg.Interval(-plane_extent, plane_extent))
        
        # Split brep with plane and keep the part on the negative side (toward origin)
        split_result = trimmed_brep.Split([plane_srf.ToBrep()], tolerance)
        if split_result and len(split_result) > 0:
            kept_pieces = []
            for piece in split_result:
                centroid = rg.VolumeMassProperties.Compute(piece).Centroid
                dist = end_plane.DistanceTo(centroid)
                if dist < 0:  # On negative side (toward origin)
                    kept_pieces.append(piece)
            
            if kept_pieces:
                if len(kept_pieces) == 1:
                    trimmed_brep = kept_pieces[0]
                else:
                    unioned = rg.Brep.CreateBooleanUnion(kept_pieces, tolerance)
                    if unioned and len(unioned) > 0:
                        trimmed_brep = unioned[0]
                
                # Cap the planar hole created by the split
                capped = trimmed_brep.CapPlanarHoles(tolerance)
                if capped:
                    trimmed_brep = capped
                    log(f"Trim end applied and capped, kept {len(kept_pieces)} piece(s)")
                else:
                    log(f"Trim end applied (cap failed), kept {len(kept_pieces)} piece(s)")
            else:
                log("WARNING: No pieces kept after trim_end split")
        else:
            log("WARNING: Brep split for trim_end produced no result")
    
    # Trim the centerline at the same points
    trimmed_centerline = centerline
    if centerline is not None and centerline.Count >= 2:
        # Convert polyline to curve for trimming operations
        centerline_curve = centerline.ToNurbsCurve()
        
        # Get trim parameters on the curve
        start_param = centerline_curve.Domain.Min
        end_param = centerline_curve.Domain.Max
        
        if params.trim_start is not None:
            start_point, _ = get_trim_point_and_plane(params.trim_start, joint_positions, params)
            success, t = centerline_curve.ClosestPoint(start_point)
            if success:
                start_param = t
        
        if params.trim_end is not None:
            end_point, _ = get_trim_point_and_plane(params.trim_end, joint_positions, params)
            success, t = centerline_curve.ClosestPoint(end_point)
            if success:
                end_param = t
        
        # Trim the curve and convert back to polyline
        if start_param < end_param:
            trimmed_curve = centerline_curve.Trim(start_param, end_param)
            if trimmed_curve:
                # Convert back to polyline by sampling points
                trimmed_centerline = Polyline()
                # Add start point
                trimmed_centerline.Add(trimmed_curve.PointAtStart)
                # Add intermediate points from original polyline that fall within range
                for i in range(centerline.Count):
                    pt = centerline[i]
                    success, t = trimmed_curve.ClosestPoint(pt)
                    if success:
                        dist = pt.DistanceTo(trimmed_curve.PointAt(t))
                        if dist < tolerance and t > trimmed_curve.Domain.Min and t < trimmed_curve.Domain.Max:
                            trimmed_centerline.Add(pt)
                # Add end point
                trimmed_centerline.Add(trimmed_curve.PointAtEnd)
                log(f"Trimmed centerline: {trimmed_centerline.Count} points")
    
    if trimmed_brep:
        log(f"Trimmed finger volume: {trimmed_brep.GetVolume():.2f} mm^3")
    
    return trimmed_brep, trimmed_centerline


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
        (centerline_polyline, finger_brep, component_breps, joint_positions)
        joint_positions maps joint names to (position, direction, cumulative_distance)
    """
    
    if tolerance is None:
        tolerance = sc.doc.ModelAbsoluteTolerance
    
    # Validate parameters for the specified segment range
    validation_errors = params.validate_for_segment_range()
    if validation_errors:
        for err in validation_errors:
            log(f"VALIDATION ERROR: {err}")
        raise ValueError(f"Invalid FingerParams: {'; '.join(validation_errors)}")
    
    shell = params.shell_thickness
    
    log("=" * 60)
    log("CREATING FINGER MODEL")
    log("=" * 60)
    log(f"Endpoints - MCP:{params.mcp_circ}mm, PIP:{params.pip_circ}mm, DIP:{params.dip_circ}mm, Tip:{params.tip_circ}mm")
    log(f"Mid-phalanx - Prox:{params.proximal_mid_circ}mm, Mid:{params.middle_mid_circ}mm, Dist:{params.distal_mid_circ}mm")
    log(f"Lengths - Prox:{params.proximal_len}mm, Mid:{params.middle_len}mm, Dist:{params.distal_len}mm")
    log(f"Flexion - MCP:{params.mcp_flex}deg, PIP:{params.pip_flex}deg, DIP:{params.dip_flex}deg")
    log(f"Lateral - MCP:{params.mcp_lateral}deg, PIP:{params.pip_lateral}deg, DIP:{params.dip_lateral}deg")
    log(f"Metacarpal stub: {params.metacarpal_len}mm")
    log(f"Segment range: {params.start_at} -> {params.end_at}")
    if shell != 0:
        log(f"Shell thickness: {shell}mm")
    
    # Convert endpoint circumferences to radii, add shell thickness
    mcp_radius = params.mcp_circ / (2 * math.pi) + shell
    pip_radius = params.pip_circ / (2 * math.pi) + shell
    dip_radius = params.dip_circ / (2 * math.pi) + shell
    tip_radius = params.tip_circ / (2 * math.pi) + shell
    
    # Convert phalanx mid-circumferences to radii (None = use tapered cylinder)
    proximal_mid_radius = (params.proximal_mid_circ / (2 * math.pi) + shell) if params.proximal_mid_circ else None
    middle_mid_radius = (params.middle_mid_circ / (2 * math.pi) + shell) if params.middle_mid_circ else None
    distal_mid_radius = (params.distal_mid_circ / (2 * math.pi) + shell) if params.distal_mid_circ else None
    
    log(f"Radii - MCP:{mcp_radius:.2f}, PIP:{pip_radius:.2f}, DIP:{dip_radius:.2f}, Tip:{tip_radius:.2f}")
    
    # Track components and centerline points
    components = []
    centerline_points = []
    
    # Track joint positions: maps joint name to (position, direction, cumulative_distance)
    joint_positions = {}
    cumulative_dist = 0.0
    
    # Helper to add start point on first rendered segment
    def add_start_point_if_first(pt):
        if not centerline_points:
            centerline_points.append(Point3d(pt))
    
    # Initialize current_plane at origin
    # X = finger direction, Y = flexion axis, Z = lateral axis (palm normal up)
    current_plane = Plane(Point3d.Origin, Vector3d.XAxis, Vector3d.YAxis)
    
    # Record origin position
    joint_positions["origin"] = (Point3d(current_plane.Origin), Vector3d(current_plane.XAxis), cumulative_dist)
    
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
    cumulative_dist += params.metacarpal_len
    joint_positions["mcp"] = (Point3d(current_plane.Origin), Vector3d(current_plane.XAxis), cumulative_dist)
    
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
    cumulative_dist += params.proximal_len
    joint_positions["pip"] = (Point3d(current_plane.Origin), Vector3d(prox_line.Direction), cumulative_dist)
    
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
    cumulative_dist += params.middle_len
    joint_positions["dip"] = (Point3d(current_plane.Origin), Vector3d(mid_line.Direction), cumulative_dist)
    
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
    cumulative_dist += params.distal_len
    joint_positions["tip"] = (Point3d(current_plane.Origin), Vector3d(dist_line.Direction), cumulative_dist)
    
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
        return centerline, None, None, joint_positions
    
    finger_brep, success, method = robust_brep_union(components, tolerance, check_volumes=True)
    
    if not success or finger_brep is None:
        log(f"ERROR: Failed to union finger components (method attempted: {method})")
        return centerline, None, components if return_parts else None, joint_positions
    
    log(f"SUCCESS: Finger union complete via {method}")
    log(f"Final finger volume: {finger_brep.GetVolume():.2f} mm^3")
    
    # Apply trimming if specified
    if params.trim_start is not None or params.trim_end is not None:
        finger_brep, centerline = trim_finger_model(finger_brep, centerline, params, joint_positions, tolerance)
        if finger_brep is None:
            log("ERROR: Trimming failed")
            return None, None, components if return_parts else None, joint_positions
    
    log("=" * 60)
    
    return centerline, finger_brep, components if return_parts else None, joint_positions
