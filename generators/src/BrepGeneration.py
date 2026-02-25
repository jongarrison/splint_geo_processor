"""
Reusable geometry generation functions for Grasshopper/Rhino

These functions create clean, validated brep geometry suitable for boolean operations.
Uses splintcommon.log for output.
"""

import Rhino.Geometry as rg
import scriptcontext as sc
import math
from splintcommon import log


def _revolve_profile(profile_curve, axis_line, radius_start, radius_end, tolerance):
    """Create a capped solid of revolution from a profile curve.
    
    Args:
        profile_curve: NurbsCurve to revolve
        axis_line: Line defining the revolution axis
        radius_start: Radius at start (for manual cap fallback)
        radius_end: Radius at end (for manual cap fallback)
        tolerance: Geometric tolerance
        
    Returns:
        Brep: Capped solid, or None on failure
    """
    pt_start = axis_line.From
    pt_end = axis_line.To
    axis_vector = rg.Vector3d(pt_end - pt_start)
    axis_vector.Unitize()
    
    rev_surface = rg.RevSurface.Create(profile_curve, axis_line, 0.0, 2.0 * math.pi)
    if not rev_surface:
        log("ERROR: Failed to create revolution surface")
        return None
    
    brep = rev_surface.ToBrep()
    if not brep:
        log("ERROR: Failed to convert revolution surface to brep")
        return None
    
    # CapPlanarHoles returns a NEW brep
    capped = brep.CapPlanarHoles(tolerance)
    if capped and capped.IsSolid:
        brep = capped
    
    # Fallback: manual planar caps
    if not brep.IsSolid:
        log("  Trying manual planar caps...")
        start_plane = rg.Plane(pt_start, axis_vector)
        start_circle = rg.Circle(start_plane, radius_start)
        start_cap = rg.Brep.CreatePlanarBreps([start_circle.ToNurbsCurve()], tolerance)
        
        end_plane = rg.Plane(pt_end, axis_vector)
        end_circle = rg.Circle(end_plane, radius_end)
        end_cap = rg.Brep.CreatePlanarBreps([end_circle.ToNurbsCurve()], tolerance)
        
        if start_cap and len(start_cap) > 0 and end_cap and len(end_cap) > 0:
            all_breps = [brep, start_cap[0], end_cap[0]]
            joined = rg.Brep.JoinBreps(all_breps, tolerance)
            if joined and len(joined) > 0:
                brep = joined[0]
                log("  Joined with manual planar caps")
    
    if not brep.IsSolid:
        log("ERROR: Revolution brep not solid after capping")
        return None
    
    # Cleanup
    brep.Faces.SplitKinkyFaces(sc.doc.ModelAngleToleranceRadians, True)
    brep.Compact()
    brep.MergeCoplanarFaces(tolerance)
    
    if not brep.IsValid:
        log("  Attempting repair (IsValid=False)...")
        brep.Repair(tolerance)
    
    if brep.IsSolid and brep.IsManifold:
        return brep
    
    log("ERROR: Revolution brep failed final validation")
    log("  IsValid: {}, IsSolid: {}, IsManifold: {}".format(
        brep.IsValid, brep.IsSolid, brep.IsManifold))
    return None

def create_tapered_cylinder(center_line, radius_start, radius_end, tolerance=None):
    """
    Create a clean tapered cylinder using surface of revolution.
    
    Args:
        center_line: Line defining the cylinder axis
        radius_start: Radius at start of line
        radius_end: Radius at end of line
        tolerance: Optional tolerance (uses doc tolerance if None)
    
    Returns:
        Brep: Capped tapered cylinder, or None if creation fails
    """
    try:
        if tolerance is None or tolerance <= 0:
            tolerance = sc.doc.ModelAbsoluteTolerance
        
        if not center_line or center_line.Length < tolerance:
            log("ERROR: Invalid center line (too short or None)")
            return None
        
        if radius_start <= 0 or radius_end <= 0:
            log("ERROR: Invalid radii (must be > 0)")
            return None
        
        pt_start = center_line.From
        pt_end = center_line.To
        axis_vector = rg.Vector3d(pt_end - pt_start)
        axis_vector.Unitize()
        
        if abs(axis_vector.Z) < 0.9:
            perp = rg.Vector3d.CrossProduct(axis_vector, rg.Vector3d.ZAxis)
        else:
            perp = rg.Vector3d.CrossProduct(axis_vector, rg.Vector3d.XAxis)
        perp.Unitize()
        
        profile_start = pt_start + perp * radius_start
        profile_end = pt_end + perp * radius_end
        profile_curve = rg.Line(profile_start, profile_end).ToNurbsCurve()
        
        brep = _revolve_profile(profile_curve, center_line, radius_start, radius_end, tolerance)
        
        if brep:
            naked_edges = sum(1 for e in brep.Edges if e.Valence == rg.EdgeAdjacency.Naked)
            log("Created tapered cylinder: r1={:.3f}, r2={:.3f}, length={:.3f}, faces={}, edges={}, naked={}, valid={}".format(
                radius_start, radius_end, center_line.Length, brep.Faces.Count, brep.Edges.Count, naked_edges, brep.IsValid))
        return brep
            
    except Exception as e:
        log("ERROR creating tapered cylinder: {}".format(str(e)))
        return None


def create_sphere(center, radius, tolerance=None):
    """
    Create a clean solid sphere.
    
    Args:
        center: Point3d center of sphere
        radius: Sphere radius
        tolerance: Optional tolerance (uses doc tolerance if None)
    
    Returns:
        Brep: Solid sphere, or None if creation fails
    """
    try:
        if tolerance is None or tolerance <= 0:
            tolerance = sc.doc.ModelAbsoluteTolerance
        
        if radius <= 0:
            log("ERROR: Invalid radius (must be > 0)")
            return None
        
        # Create sphere
        sphere = rg.Sphere(center, radius)
        brep = sphere.ToBrep()
        
        if brep and brep.IsValid and brep.IsSolid:
            log("Created sphere: r={:.3f} at {}".format(radius, center))
            return brep
        else:
            log("ERROR: Failed to create valid sphere")
            return None
            
    except Exception as e:
        log("ERROR creating sphere: {}".format(str(e)))
        return None


def create_cylinder(base_plane, radius, height, tolerance=None):
    """
    Create a clean solid cylinder.
    
    Args:
        base_plane: Plane defining base center and axis direction
        radius: Cylinder radius
        height: Cylinder height (can be negative for opposite direction)
        tolerance: Optional tolerance (uses doc tolerance if None)
    
    Returns:
        Brep: Solid cylinder, or None if creation fails
    """
    try:
        if tolerance is None or tolerance <= 0:
            tolerance = sc.doc.ModelAbsoluteTolerance
        
        if radius <= 0:
            log("ERROR: Invalid radius (must be > 0)")
            return None
        
        if abs(height) < tolerance:
            log("ERROR: Invalid height (too small)")
            return None
        
        # Create base circle
        circle = rg.Circle(base_plane, radius)
        
        # Create cylinder
        cylinder = rg.Cylinder(circle, height)
        brep = cylinder.ToBrep(True, True)  # cap both ends
        
        if brep and brep.IsValid and brep.IsSolid:
            log("Created cylinder: r={:.3f}, h={:.3f}".format(radius, height))
            return brep
        else:
            log("ERROR: Failed to create valid cylinder")
            return None
            
    except Exception as e:
        log("ERROR creating cylinder: {}".format(str(e)))
        return None


def create_bulged_cylinder(center_line, radius_start, radius_mid, radius_end, tolerance=None):
    """
    Create a solid tube with a bulge (or waist) at the midpoint using surface of revolution.
    
    Uses a quadratic bezier curve as the profile, revolved around the axis.
    
    Args:
        center_line: Line defining the tube axis
        radius_start: Radius at start of line
        radius_mid: Radius at midpoint (can be larger or smaller than ends)
        radius_end: Radius at end of line
        tolerance: Optional tolerance (uses doc tolerance if None)
    
    Returns:
        Brep: Capped tube, or None if creation fails
    """
    try:
        if tolerance is None or tolerance <= 0:
            tolerance = sc.doc.ModelAbsoluteTolerance
        
        if not center_line or center_line.Length < tolerance:
            log("ERROR: Invalid center line (too short or None)")
            return None
        
        if radius_start <= 0 or radius_mid <= 0 or radius_end <= 0:
            log("ERROR: Invalid radii (must be > 0)")
            return None
        
        length = center_line.Length
        pt_start = center_line.From
        pt_end = center_line.To
        axis_vector = rg.Vector3d(pt_end - pt_start)
        axis_vector.Unitize()
        
        if abs(axis_vector.Z) < 0.9:
            perp = rg.Vector3d.CrossProduct(axis_vector, rg.Vector3d.ZAxis)
        else:
            perp = rg.Vector3d.CrossProduct(axis_vector, rg.Vector3d.XAxis)
        perp.Unitize()
        
        profile_start = pt_start + perp * radius_start
        profile_mid = pt_start + axis_vector * (length / 2.0) + perp * radius_mid
        profile_end = pt_end + perp * radius_end
        
        # Quadratic bezier control point so curve passes through profile_mid at t=0.5
        control_pt = rg.Point3d(
            2.0 * profile_mid.X - 0.5 * profile_start.X - 0.5 * profile_end.X,
            2.0 * profile_mid.Y - 0.5 * profile_start.Y - 0.5 * profile_end.Y,
            2.0 * profile_mid.Z - 0.5 * profile_start.Z - 0.5 * profile_end.Z
        )
        
        profile_curve = rg.NurbsCurve.Create(False, 2, [profile_start, control_pt, profile_end])
        if not profile_curve:
            log("ERROR: Failed to create bezier profile curve")
            return None
        
        brep = _revolve_profile(profile_curve, center_line, radius_start, radius_end, tolerance)
        
        if brep:
            naked_edges = sum(1 for e in brep.Edges if e.Valence == rg.EdgeAdjacency.Naked)
            log("Created bulged cylinder (revolution): r1={:.3f}, r_mid={:.3f}, r2={:.3f}, length={:.3f}, faces={}, naked={}".format(
                radius_start, radius_mid, radius_end, length, brep.Faces.Count, naked_edges))
        return brep
            
    except Exception as e:
        log("ERROR creating bulged cylinder: {}".format(str(e)))
        return None
