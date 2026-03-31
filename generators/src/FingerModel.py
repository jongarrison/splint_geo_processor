"""
FingerModel.py
Generate anatomically-based finger geometry for splint modeling.
"""

from importlib import reload
import Rhino.Geometry as rg
from Rhino.Geometry import Point3d, Vector3d, Line, Plane, Polyline
import scriptcontext as sc
import math
import time
from dataclasses import dataclass
from typing import Optional, List, Tuple
from splintcommon import log

class FingerModelError(Exception):
    """Raised when finger model creation fails."""
    pass


class GeometryCreationError(Exception):
    """Raised when a geometry primitive (sphere, cylinder, etc.) fails to create."""
    pass


class TrimError(Exception):
    """Raised when trimming operation fails."""
    pass
import BrepGeneration
reload(BrepGeneration)
from BrepGeneration import create_tapered_cylinder, create_bulged_cylinder, create_sphere, create_cylinder

import BrepUnion
reload(BrepUnion)
from BrepUnion import robust_brep_union, BrepUnionError, InvalidBrepError


# Segment names in order from base to tip (joints and phalanges as separate segments)
SEGMENT_ORDER = ["metacarpal", "mcp", "proximal", "pip", "middle", "dip", "distal", "tip"]

# Phalanx and joint names for perp frame lookups
PHALANX_NAMES = ["metacarpal", "proximal", "middle", "distal"]
JOINT_NAMES = ["mcp", "pip", "dip", "tip"]

# Maps each joint to (proximal_phalanx, distal_phalanx) for perp frame resolution
JOINT_ADJACENCY = {
    "mcp": ("metacarpal", "proximal"),
    "pip": ("proximal", "middle"),
    "dip": ("middle", "distal"),
    "tip": ("distal", None),
}


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
    
    # Phalanx lengths (mm) - measured joint-to-joint
    # distal_len is DIP-to-fingertip end (tip sphere radius subtracted internally)
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
    
    # Pad rise: shifts volar (palm-side) vertices dorsally from DIP to tip
    # 0.0 = symmetric (current behavior), 0.3-0.5 = realistic fingertip shape
    # Value is fraction of tip radius used as max dorsal shift at the tip
    pad_rise: float = 0.0
    
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
    
    # Derive segment lengths from actual joint positions
    # This accounts for tip sphere adjustment (distal_bone_len != distal_len)
    segment_pairs = [("origin", "mcp"), ("mcp", "pip"), ("pip", "dip"), ("dip", "tip")]
    segments = []
    for s_joint, e_joint in segment_pairs:
        if s_joint in joint_positions and e_joint in joint_positions:
            s_pos = joint_positions[s_joint][0]
            e_pos = joint_positions[e_joint][0]
            segments.append((s_joint, e_joint, s_pos.DistanceTo(e_pos)))
    
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
                    else:
                        raise TrimError(
                            f"Failed to union {len(kept_pieces)} pieces after trim_start split"
                        )
                
                # Cap the planar hole created by the split
                capped = trimmed_brep.CapPlanarHoles(tolerance)
                if capped:
                    trimmed_brep = capped
                    log(f"Trim start applied and capped, kept {len(kept_pieces)} piece(s)")
                else:
                    log(f"Trim start applied (cap failed), kept {len(kept_pieces)} piece(s)")
            else:
                raise TrimError(
                    f"No geometry pieces on positive side of trim_start plane at {params.trim_start}"
                )
        else:
            raise TrimError(
                f"Brep.Split() returned no result for trim_start at {params.trim_start}"
            )
    
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
                    else:
                        raise TrimError(
                            f"Failed to union {len(kept_pieces)} pieces after trim_end split"
                        )
                
                # Cap the planar hole created by the split
                capped = trimmed_brep.CapPlanarHoles(tolerance)
                if capped:
                    trimmed_brep = capped
                    log(f"Trim end applied and capped, kept {len(kept_pieces)} piece(s)")
                else:
                    log(f"Trim end applied (cap failed), kept {len(kept_pieces)} piece(s)")
            else:
                raise TrimError(
                    f"No geometry pieces on negative side of trim_end plane at {params.trim_end}"
                )
        else:
            raise TrimError(
                f"Brep.Split() returned no result for trim_end at {params.trim_end}"
            )
    
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


class PadRiseMorph(rg.SpaceMorph):
    """SpaceMorph that reshapes the fingertip sphere region for anatomical realism.
    
    Five influences control the deformation, all shifts are in the dorsal
    (nail/upward) direction:
    
    1. Axial ramp: 0 at 2*R behind tip center, raised-cosine ramp to 1.0
       at the very tip (+R). Ensures the phalanx tube is unaffected while
       the tip sphere and its transition get the full effect.
    
    2. North (nail) hemisphere profile: cos falloff from 1.0 at the equator
       to 0.0 at the north pole (pi/2 range). Keeps the nail surface flat.
    
    3. South (pad) hemisphere profile: gentler cos falloff from 1.0 at the
       equator to 0.5 at the south pole (pi/3 range). The pad bottom still
       rises substantially, producing the characteristic fingertip shape.
    
    4. Lateral falloff: quadratic reduction (1.0 center to 0.7 at full
       lateral extent). The lateral nail edges sit slightly lower than the
       nail midline, matching real anatomy.
    
    5. North pole cap: hard limit preventing any point from shifting above
       the original north pole position.
    """
    
    def __init__(self, tip_center, finger_dir, dorsal, tip_radius, pad_rise, tolerance):
        rg.SpaceMorph.__init__(self)
        self.Tolerance = tolerance
        self._tip_center = tip_center
        self._finger_dir = finger_dir  # unit vector along finger axis
        self._dorsal = dorsal
        self._lateral = rg.Vector3d.CrossProduct(finger_dir, dorsal)
        self._lateral.Unitize()
        self._tip_radius = tip_radius
        self._max_shift = pad_rise * tip_radius
    
    def MorphPoint(self, point):
        v = rg.Vector3d(point - self._tip_center)
        R = self._tip_radius
        
        # Decompose into axial, dorsal, and lateral components
        axial_dist = rg.Vector3d.Multiply(v, self._finger_dir)
        dorsal_comp = rg.Vector3d.Multiply(v, self._dorsal)
        lateral_comp = rg.Vector3d.Multiply(v, self._lateral)
        
        # Skip points outside the influence zone
        if axial_dist < -R * 2.0 or axial_dist > R * 1.5:
            return point
        
        # 1. Axial ramp: 0 at -2R, 1.0 at +R (raised cosine)
        t = (axial_dist + 2.0 * R) / (3.0 * R)
        t = max(0.0, min(1.0, t))
        axial_factor = 0.5 * (1.0 - math.cos(math.pi * t))
        
        # 2. Equatorial influence (asymmetric by hemisphere)
        if dorsal_comp >= 0:
            # North (nail) hemisphere: standard falloff to zero at north pole
            eq_t = min(dorsal_comp / R, 1.0)
            equatorial_factor = math.cos(eq_t * math.pi / 2.0)
        else:
            # South (pad) hemisphere: gentler falloff, keeps influence deeper
            # cos(60 deg) = 0.5 at south pole, so pad bottom still rises
            eq_t = min(abs(dorsal_comp) / R, 1.0)
            equatorial_factor = math.cos(eq_t * math.pi / 3.0)
        
        # 3. Lateral falloff: gently reduce at sides
        lat_t = min(abs(lateral_comp) / R, 1.0)
        lateral_factor = 1.0 - 0.3 * lat_t * lat_t  # quadratic, subtle
        
        shift = self._max_shift * axial_factor * equatorial_factor * lateral_factor
        
        # 4. North pole cap: never shift above original north pole
        if shift > 0 and dorsal_comp + shift > R:
            shift = max(0.0, R - dorsal_comp)
        
        if shift <= 0:
            return point
        
        return rg.Point3d(
            point.X + self._dorsal.X * shift,
            point.Y + self._dorsal.Y * shift,
            point.Z + self._dorsal.Z * shift
        )


def apply_pad_rise(finger_brep, joint_positions, tip_radius, pad_rise, tolerance):
    """
    Reshape the fingertip region for realistic pad-rise anatomy.
    
    Applies a SpaceMorph that shifts volar control points dorsally in the
    tip sphere zone only. The dorsal (nail) surface stays flat, the volar
    (pad) surface rises toward the tip. Smooth falloff draws up the distal
    phalanx edge naturally.
    
    Works directly on brep NURBS control points -- no mesh conversion.
    
    Args:
        finger_brep: Input brep to deform
        joint_positions: Dict with position/direction info
        tip_radius: Radius at fingertip (defines deformation zone)
        pad_rise: Shift fraction (0.0 = no-op, 0.3-0.5 typical)
        tolerance: Geometric tolerance
        
    Returns:
        Brep: Deformed brep, or original if not applicable
    """
    if pad_rise <= 0:
        return finger_brep
    
    if "tip" not in joint_positions:
        log("pad_rise: tip position not available, skipping")
        return finger_brep
    
    tip_center = joint_positions["tip"][0]
    finger_dir = joint_positions["tip"][1]  # direction at tip
    finger_dir.Unitize()
    
    # Dorsal direction: perpendicular to finger axis, away from palm (+Z approx)
    up_candidate = rg.Vector3d.ZAxis
    if abs(rg.Vector3d.Multiply(up_candidate, finger_dir)) > 0.95:
        up_candidate = rg.Vector3d.YAxis
    dorsal = up_candidate - finger_dir * rg.Vector3d.Multiply(up_candidate, finger_dir)
    dorsal.Unitize()
    
    max_shift = pad_rise * tip_radius
    log(f"pad_rise: tip_r={tip_radius:.2f}, shift={max_shift:.2f}mm, center={tip_center}")
    
    morph = PadRiseMorph(tip_center, finger_dir, dorsal, tip_radius, pad_rise, tolerance)
    
    t0 = time.time()
    deformed = finger_brep.Duplicate()
    success = morph.Morph(deformed)
    elapsed = time.time() - t0
    
    if success and deformed.IsValid:
        log(f"pad_rise: morph succeeded in {elapsed:.3f}s, volume={deformed.GetVolume():.2f} mm^3")
        return deformed
    
    log(f"pad_rise: morph failed after {elapsed:.3f}s, returning original")
    return finger_brep


class FingerModelResult:
    """Wraps finger model output and enables perp frame / cross-section queries.
    
    Location names for get_perp_frame / get_cross_section:
    
    Phalanx names: "metacarpal", "proximal", "middle", "distal"
        offset 0.0 = proximal end (toward metacarpal)
        offset 1.0 = distal end (toward tip)
        Perp frame normal = phalanx centerline direction
    
    Joint names: "mcp", "pip", "dip", "tip"
        offset 0.0 = bisector of adjoining phalanx directions
        offset > 0 = distal phalanx (toward tip), maps to that phalanx offset
        offset < 0 = proximal phalanx (toward metacarpal), offset = 1.0 + value
        "tip" with offset > 0 extends along distal direction into tip sphere
    
    Perp frame axes: ZAxis = finger direction, YAxis = dorsal, XAxis = lateral.
    """
    
    def __init__(self, params, tolerance, finger_brep, centerline,
                 components, joint_positions, phalanx_lines, radii,
                 distal_bone_len=None, success=True, error=None):
        self.params = params
        self.tolerance = tolerance
        self.finger_brep = finger_brep
        self.centerline = centerline
        self.components = components
        self.joint_positions = joint_positions
        self.phalanx_lines = phalanx_lines  # dict: phalanx name -> Line
        self.radii = radii  # dict: joint name -> float
        self.distal_bone_len = distal_bone_len  # distal_len minus tip_radius
        self.success = success  # False when union or post-processing failed
        self.error = error  # error message when success is False
        self._mesh = None  # lazy-cached mesh for cross-section queries
    
    def get_perp_frame(self, name, offset=0.0):
        """Get a perpendicular frame (plane) at a named location.
        
        Args:
            name: Phalanx name ("metacarpal", "proximal", "middle", "distal")
                  or joint name ("mcp", "pip", "dip", "tip")
            offset: For phalanx: 0.0 (proximal end) to 1.0 (distal end)
                    For joint: 0.0 = bisector, positive = toward tip,
                    negative = toward metacarpal. Range: -1.0 to 1.0
        
        Returns:
            Plane with ZAxis = finger direction, YAxis = dorsal, XAxis = lateral.
            None if location is outside the generated geometry.
        """
        name = name.lower()
        
        if name in PHALANX_NAMES:
            return self._phalanx_perp_frame(name, offset)
        elif name in JOINT_NAMES:
            return self._joint_perp_frame(name, offset)
        else:
            raise ValueError(
                f"Unknown location '{name}'. "
                f"Valid: phalanx {PHALANX_NAMES}, joint {JOINT_NAMES}"
            )
    
    def get_cross_section(self, name, offset=0.0):
        """Get the cross-section curve at a named location.
        
        Intersects finger_brep with the perpendicular frame plane and
        returns a single joined closed planar curve.
        
        Uses mesh-plane intersection for robustness -- avoids NURBS seam
        and self-intersection artifacts that fragment BrepPlane results.
        
        Args:
            name: Location name (see get_perp_frame)
            offset: Offset parameter (see get_perp_frame)
        
        Returns:
            A single closed Curve, or None if the plane does not
            intersect the brep.
        """
        plane = self.get_perp_frame(name, offset)
        if plane is None:
            log(f"get_cross_section('{name}', {offset}): perp frame is None")
            return None
        
        mesh = self._get_mesh()
        if mesh is None:
            log(f"get_cross_section('{name}', {offset}): failed to mesh brep")
            return None
        
        # MeshPlane returns clean closed Polyline[]
        polylines = rg.Intersect.Intersection.MeshPlane(mesh, plane)
        if not polylines or len(polylines) == 0:
            log(f"get_cross_section('{name}', {offset}): MeshPlane returned no polylines")
            return None
        
        log(f"get_cross_section('{name}', {offset}): {len(polylines)} polyline(s)")
        
        # Pick the longest closed polyline
        best = None
        best_len = 0
        for pl in polylines:
            if pl.IsClosed and pl.Length > best_len:
                best = pl
                best_len = pl.Length
        
        if best is None:
            log(f"get_cross_section('{name}', {offset}): no closed polylines")
            return None
        
        # Convert polyline to NURBS curve for consistent return type
        crv = best.ToNurbsCurve()
        log(f"get_cross_section('{name}', {offset}): returning closed curve, "
            f"length={crv.GetLength():.2f}mm, {best.Count} points")
        return crv
    
    def _get_mesh(self):
        """Lazy-create and cache a mesh of finger_brep for cross-section queries."""
        if self._mesh is None:
            mesh_params = rg.MeshingParameters.DefaultAnalysisMesh
            meshes = rg.Mesh.CreateFromBrep(self.finger_brep, mesh_params)
            if meshes:
                self._mesh = rg.Mesh()
                for m in meshes:
                    self._mesh.Append(m)
                log(f"Cached mesh: {self._mesh.Faces.Count} faces, "
                    f"{self._mesh.Vertices.Count} vertices")
        return self._mesh
    
    def _phalanx_perp_frame(self, name, offset):
        """Perp frame at a point along a phalanx centerline."""
        if name not in self.phalanx_lines:
            return None
        
        line = self.phalanx_lines[name]
        offset = max(0.0, min(1.0, offset))
        
        point = line.PointAt(offset)
        direction = Vector3d(line.Direction)
        direction.Unitize()
        
        return self._make_dorsal_plane(point, direction)
    
    def _joint_perp_frame(self, name, offset):
        """Perp frame at a joint: bisector at 0, resolved to phalanx otherwise."""
        if name not in self.joint_positions:
            return None
        
        prox_name, dist_name = JOINT_ADJACENCY[name]
        
        # "tip" only supports offset=0.0 (sphere center, not a true joint)
        if name == "tip" and offset != 0.0:
            raise ValueError(
                "Tip does not support offset. Use ('distal', offset) "
                "to get cross-sections along the distal phalanx and tip sphere."
            )
        
        if offset == 0.0:
            # Bisector of adjoining phalanx directions
            joint_pos = self.joint_positions[name][0]
            
            if name == "tip":
                # Only one adjoining phalanx, use its direction
                if prox_name not in self.phalanx_lines:
                    return None
                direction = Vector3d(self.phalanx_lines[prox_name].Direction)
                direction.Unitize()
            else:
                # Average the two adjoining phalanx directions
                if prox_name not in self.phalanx_lines or dist_name not in self.phalanx_lines:
                    return None
                d1 = Vector3d(self.phalanx_lines[prox_name].Direction)
                d1.Unitize()
                d2 = Vector3d(self.phalanx_lines[dist_name].Direction)
                d2.Unitize()
                direction = d1 + d2
                if direction.Length < 1e-10:
                    direction = Vector3d(d1)  # parallel case
                direction.Unitize()
            
            return self._make_dorsal_plane(joint_pos, direction)
        
        elif offset > 0:
            # Positive offset resolves to distal phalanx
            return self._phalanx_perp_frame(dist_name, offset)
        
        else:
            # Negative offset resolves to proximal phalanx at 1.0 + offset
            if prox_name not in self.phalanx_lines:
                return None
            return self._phalanx_perp_frame(prox_name, 1.0 + offset)
    
    def _make_dorsal_plane(self, origin, normal):
        """Construct perp frame: ZAxis = normal (finger dir), YAxis = dorsal."""
        up = Vector3d.ZAxis
        if abs(Vector3d.Multiply(up, normal)) > 0.95:
            up = Vector3d.YAxis
        # Project up onto the plane perpendicular to normal
        dorsal = up - normal * Vector3d.Multiply(up, normal)
        dorsal.Unitize()
        # XAxis = lateral, completing right-hand frame (X cross Y = Z = normal)
        lateral = Vector3d.CrossProduct(dorsal, normal)
        lateral.Unitize()
        return Plane(origin, lateral, dorsal)


def _build_finger_model(
    params: FingerParams,
    tolerance: Optional[float] = None,
    raise_on_union_failure: bool = True,
):
    """Internal: build finger model and return FingerModelResult.
    
    When raise_on_union_failure=False, catches BrepUnionError and returns a
    partial FingerModelResult with finger_brep=None, success=False, and all
    pre-union data (components, centerline, joint_positions, radii) populated.
    
    Orientation: Finger along +X, palm faces -Z. Positive angles = flexion toward palm.
    Construction order: Metacarpal -> MCP -> Proximal -> PIP -> Middle -> DIP -> Distal -> Tip
    
    Position is always computed from origin through all segments, but geometry is only
    created for segments within start_at..end_at range.
    """
    
    if tolerance is None:
        tolerance = sc.doc.ModelAbsoluteTolerance
    
    start_time = time.time()
    
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
    if params.pad_rise != 0:
        log(f"Pad rise: {params.pad_rise}")
    
    # Convert endpoint circumferences to radii, add shell thickness
    mcp_radius = params.mcp_circ / (2 * math.pi) + shell
    pip_radius = params.pip_circ / (2 * math.pi) + shell
    dip_radius = params.dip_circ / (2 * math.pi) + shell
    base_tip_radius = params.tip_circ / (2 * math.pi)  # anatomical radius before shell
    tip_radius = base_tip_radius + shell
    
    # Convert phalanx mid-circumferences to radii (None = use tapered cylinder)
    proximal_mid_radius = (params.proximal_mid_circ / (2 * math.pi) + shell) if params.proximal_mid_circ else None
    middle_mid_radius = (params.middle_mid_circ / (2 * math.pi) + shell) if params.middle_mid_circ else None
    distal_mid_radius = (params.distal_mid_circ / (2 * math.pi) + shell) if params.distal_mid_circ else None
    
    log(f"Radii - MCP:{mcp_radius:.2f}, PIP:{pip_radius:.2f}, DIP:{dip_radius:.2f}, Tip:{tip_radius:.2f} (base:{base_tip_radius:.2f})")
    
    # Distal bone length: measured distal_len includes tip sphere
    # Use base_tip_radius (not shell-augmented) so sphere center stays at the
    # same anatomical position regardless of shell_thickness. This ensures
    # uniform wall thickness at the fingertip after boolean difference.
    distal_bone_len = params.distal_len - base_tip_radius
    if (params.includes_segment("distal") or params.includes_segment("tip")) and distal_bone_len <= 0:
        raise ValueError(
            f"distal_len ({params.distal_len}mm) must be greater than "
            f"base tip_radius ({base_tip_radius:.2f}mm) derived from tip_circ ({params.tip_circ}mm)"
        )
    log(f"Distal bone: {distal_bone_len:.2f}mm (measured {params.distal_len}mm - base_tip_r {base_tip_radius:.2f}mm)")
    
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
    metacarpal_line = Line(Point3d(current_plane.Origin), metacarpal_end)
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
            raise GeometryCreationError(
                f"Failed to create metacarpal stub (len={params.metacarpal_len}, r={mcp_radius:.2f})"
            )
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
            raise GeometryCreationError(
                f"Failed to create MCP joint sphere (center={prox_line.From}, r={mcp_radius:.2f})"
            )
    
    if params.includes_segment("proximal"):
        add_start_point_if_first(prox_line.From)
        if prox_brep:
            components.append(prox_brep)
            log(f"Proximal Phalanx: length={params.proximal_len}mm, r1={mcp_radius:.2f}, r2={pip_radius:.2f}")
        else:
            raise GeometryCreationError(
                f"Failed to create proximal phalanx (len={params.proximal_len}, r1={mcp_radius:.2f}, r2={pip_radius:.2f})"
            )
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
            raise GeometryCreationError(
                f"Failed to create PIP joint sphere (center={mid_line.From}, r={pip_radius:.2f})"
            )
    
    if params.includes_segment("middle"):
        add_start_point_if_first(mid_line.From)
        if mid_brep:
            components.append(mid_brep)
            log(f"Middle Phalanx: length={params.middle_len}mm, r1={pip_radius:.2f}, r2={dip_radius:.2f}")
        else:
            raise GeometryCreationError(
                f"Failed to create middle phalanx (len={params.middle_len}, r1={pip_radius:.2f}, r2={dip_radius:.2f})"
            )
        centerline_points.append(Point3d(mid_line.To))
    
    current_plane = new_plane
    cumulative_dist += params.middle_len
    joint_positions["dip"] = (Point3d(current_plane.Origin), Vector3d(mid_line.Direction), cumulative_dist)
    
    # --- DIP JOINT + DISTAL PHALANX ---
    log("\n--- DIP Joint + Distal Phalanx ---")
    # advance_to_next_joint uses distal_bone_len (to sphere center, not fingertip)
    new_plane, dist_line = advance_to_next_joint(
        current_plane,
        distal_bone_len,
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
            raise GeometryCreationError(
                f"Failed to create DIP joint sphere (center={dist_line.From}, r={dip_radius:.2f})"
            )
    
    if params.includes_segment("distal"):
        add_start_point_if_first(dist_line.From)
        if dist_brep:
            components.append(dist_brep)
            log(f"Distal Phalanx: bone_len={distal_bone_len:.2f}mm (measured={params.distal_len}mm), r1={dip_radius:.2f}, r2={tip_radius:.2f}")
        else:
            raise GeometryCreationError(
                f"Failed to create distal phalanx (len={distal_bone_len:.2f}, r1={dip_radius:.2f}, r2={tip_radius:.2f})"
            )
    
    current_plane = new_plane
    cumulative_dist += distal_bone_len
    joint_positions["tip"] = (Point3d(current_plane.Origin), Vector3d(dist_line.Direction), cumulative_dist)
    
    # Fingertip end point: sphere center + tip_radius along distal direction
    tip_dir = Vector3d(dist_line.Direction)
    tip_dir.Unitize()
    tip_end_point = Point3d(current_plane.Origin) + tip_dir * tip_radius
    
    # Add tip endpoint to centerline (full measured distal_len from DIP)
    if params.includes_segment("distal") or params.includes_segment("tip"):
        centerline_points.append(Point3d(tip_end_point))
    
    # --- FINGERTIP (sphere at final position) ---
    if params.includes_segment("tip"):
        log("\n--- Fingertip ---")
        add_start_point_if_first(current_plane.Origin)
        tip_brep = create_sphere(current_plane.Origin, tip_radius, tolerance)
        if tip_brep:
            components.append(tip_brep)
            log(f"Fingertip: center={current_plane.Origin}, radius={tip_radius:.2f}mm")
        else:
            raise GeometryCreationError(
                f"Failed to create fingertip sphere (center={current_plane.Origin}, r={tip_radius:.2f})"
            )
    
    # Create centerline polyline
    centerline = Polyline(centerline_points) if centerline_points else None
    log(f"\nCenterline: {len(centerline_points)} points")
    
    # Union all components
    log("\n--- Unioning Components ---")
    log(f"Component count: {len(components)}")
    
    if not components:
        raise FingerModelError(
            f"No components generated for segment range {params.start_at} to {params.end_at}"
        )
    
    # robust_brep_union will raise BrepUnionError on failure
    try:
        finger_brep, union_ok, method = robust_brep_union(components, tolerance, check_volumes=True)
    except BrepUnionError as e:
        if raise_on_union_failure:
            raise
        # Return partial result with all pre-union data
        log(f"Union failed (non-fatal): {e}")
        distal_full_line = Line(dist_line.From, tip_end_point)
        elapsed = time.time() - start_time
        log(f"create_finger_model completed in {elapsed:.3f}s (union failed)")
        log("=" * 60)
        return FingerModelResult(
            params=params,
            tolerance=tolerance,
            finger_brep=None,
            centerline=centerline,
            components=components,
            joint_positions=joint_positions,
            phalanx_lines={
                "metacarpal": metacarpal_line,
                "proximal": prox_line,
                "middle": mid_line,
                "distal": distal_full_line,
            },
            radii={
                "mcp": mcp_radius,
                "pip": pip_radius,
                "dip": dip_radius,
                "tip": tip_radius,
            },
            distal_bone_len=distal_bone_len,
            success=False,
            error=str(e),
        )
    
    log(f"SUCCESS: Finger union complete via {method}")
    log(f"Final finger volume: {finger_brep.GetVolume():.2f} mm^3")
    
    # Apply trimming if specified (trim_finger_model raises TrimError on failure)
    if params.trim_start is not None or params.trim_end is not None:
        finger_brep, centerline = trim_finger_model(finger_brep, centerline, params, joint_positions, tolerance)
    
    # Apply pad rise deformation (no-op when pad_rise=0.0)
    # Use tip_radius (includes shell) so morph influence zone matches actual
    # sphere geometry. This avoids a groove artifact where the morph's zone
    # boundary falls inside the outer shell's sphere surface.
    if params.pad_rise > 0 and params.includes_segment("tip"):
        finger_brep = apply_pad_rise(finger_brep, joint_positions, tip_radius, params.pad_rise, tolerance)
    
    # Collect phalanx centerlines and radii for perp frame queries
    # distal_full_line spans DIP to fingertip end (full measured distal_len)
    distal_full_line = Line(dist_line.From, tip_end_point)
    phalanx_lines = {
        "metacarpal": metacarpal_line,
        "proximal": prox_line,
        "middle": mid_line,
        "distal": distal_full_line,
    }
    radii = {
        "mcp": mcp_radius,
        "pip": pip_radius,
        "dip": dip_radius,
        "tip": tip_radius,
    }
    
    elapsed = time.time() - start_time
    log(f"create_finger_model completed in {elapsed:.3f}s")
    log("=" * 60)
    
    return FingerModelResult(
        params=params,
        tolerance=tolerance,
        finger_brep=finger_brep,
        centerline=centerline,
        components=components,
        joint_positions=joint_positions,
        phalanx_lines=phalanx_lines,
        radii=radii,
        distal_bone_len=distal_bone_len,
    )


def create_finger_model_result(
    params: FingerParams,
    tolerance: Optional[float] = None,
) -> 'FingerModelResult':
    """Generate a finger model, returning a FingerModelResult for further queries.
    
    Raises BrepUnionError if the boolean union fails.
    See _build_finger_model for full documentation.
    """
    return _build_finger_model(params, tolerance)


def create_finger_model_safe(
    params: FingerParams,
    tolerance: Optional[float] = None,
) -> 'FingerModelResult':
    """Generate a finger model, returning partial results on failure.
    
    Never raises on union failure. Check result.success and result.error.
    On failure, result.finger_brep is None but components, centerline,
    joint_positions, phalanx_lines, and radii are still populated.
    """
    return _build_finger_model(params, tolerance, raise_on_union_failure=False)


def create_finger_model(
    params: FingerParams,
    tolerance: Optional[float] = None,
    return_parts: bool = True,
):
    """Generate a finger model from anatomical measurements (backward-compatible).
    
    Returns:
        (centerline_polyline, finger_brep, component_breps, joint_positions)
        joint_positions maps joint names to (position, direction, cumulative_distance)
    """
    result = _build_finger_model(params, tolerance)
    return (result.centerline, result.finger_brep,
            result.components if return_parts else None, result.joint_positions)
