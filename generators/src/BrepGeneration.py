"""
Reusable geometry generation functions for Grasshopper/Rhino

These functions create clean, validated brep geometry suitable for boolean operations.
Uses splintcommon.log for output.
"""

import Rhino.Geometry as rg
import scriptcontext as sc
import math
from splintcommon import log

def create_tapered_cylinder(center_line, radius_start, radius_end, tolerance=None):
    """
    Create a clean tapered cylinder using surface of revolution.
    More reliable than lofting - avoids seam misalignment and self-intersections.
    
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
        
        # Validate inputs
        if not center_line or center_line.Length < tolerance:
            log("ERROR: Invalid center line (too short or None)")
            return None
        
        if radius_start <= 0 or radius_end <= 0:
            log("ERROR: Invalid radii (must be > 0)")
            return None
        
        # Get line endpoints
        pt_start = center_line.From
        pt_end = center_line.To
        
        # Create axis vector
        axis_vector = rg.Vector3d(pt_end - pt_start)
        axis_vector.Unitize()
        
        # Find a perpendicular vector to the axis
        # Use a robust method that works for any axis orientation
        if abs(axis_vector.Z) < 0.9:
            # Axis is not vertical - use Z cross product
            perp = rg.Vector3d.CrossProduct(axis_vector, rg.Vector3d.ZAxis)
        else:
            # Axis is vertical - use X cross product
            perp = rg.Vector3d.CrossProduct(axis_vector, rg.Vector3d.XAxis)
        
        perp.Unitize()
        
        # Create profile line (outer edge of cone/cylinder)
        profile_start = pt_start + perp * radius_start
        profile_end = pt_end + perp * radius_end
        profile_line = rg.Line(profile_start, profile_end)
        
        # Create revolution surface
        rev_surface = rg.RevSurface.Create(
            profile_line.ToNurbsCurve(),
            center_line,
            0.0,  # start angle
            2.0 * math.pi  # end angle (full circle)
        )
        
        if not rev_surface:
            log("ERROR: Failed to create revolution surface")
            log("  Profile line: {} to {}".format(profile_start, profile_end))
            log("  Axis: {} to {}".format(pt_start, pt_end))
            log("  Radii: r1={:.3f}, r2={:.3f}".format(radius_start, radius_end))
            return None
        
        # Convert to brep
        brep = rev_surface.ToBrep()
        if not brep:
            log("ERROR: Failed to convert revolution surface to brep")
            return None
        
        # Cap both ends to create solid
        # Method 1: Try CapPlanarHoles
        capped = brep.CapPlanarHoles(tolerance)
        
        # Method 2: If still not solid, try adding planar caps manually
        if not brep.IsSolid:
            log("  Trying manual planar caps...")
            # Create planar caps at each end
            # Start cap - create plane at start with axis as normal
            start_plane = rg.Plane(pt_start, axis_vector)
            start_circle = rg.Circle(start_plane, radius_start)
            start_curve = start_circle.ToNurbsCurve()
            start_cap = rg.Brep.CreatePlanarBreps([start_curve], tolerance)
            
            # End cap - create plane at end with axis as normal
            end_plane = rg.Plane(pt_end, axis_vector)
            end_circle = rg.Circle(end_plane, radius_end)
            end_curve = end_circle.ToNurbsCurve()
            end_cap = rg.Brep.CreatePlanarBreps([end_curve], tolerance)
            
            if start_cap and len(start_cap) > 0 and end_cap and len(end_cap) > 0:
                # Join all breps
                all_breps = [brep, start_cap[0], end_cap[0]]
                joined = rg.Brep.JoinBreps(all_breps, tolerance)
                if joined and len(joined) > 0:
                    brep = joined[0]
                    log("  Joined revolution surface with planar caps")
        
        # Final cleanup and validation
        if brep.IsSolid:
            # Clean up the brep
            brep.Faces.SplitKinkyFaces(sc.doc.ModelAngleToleranceRadians, True)
            brep.Compact()
            
            # Try to remove any micro-edges or degeneracies
            brep.MergeCoplanarFaces(tolerance)
            
            # If not valid, try to repair
            if not brep.IsValid:
                log("  Attempting repair (IsValid=False)...")
                repaired = brep.Repair(tolerance)
                if repaired:
                    log("  Repair successful")
                else:
                    log("  Repair failed, but continuing anyway")
            
            # Final validation - accept if solid and manifold, even if repair needed
            if brep.IsSolid and brep.IsManifold:
                naked_edges = sum(1 for e in brep.Edges if e.Valence == rg.EdgeAdjacency.Naked)
                log("Created tapered cylinder: r1={:.3f}, r2={:.3f}, length={:.3f}, faces={}, edges={}, naked={}, valid={}".format(
                    radius_start, radius_end, center_line.Length, brep.Faces.Count, brep.Edges.Count, naked_edges, brep.IsValid))
                return brep
            else:
                log("ERROR: Tapered cylinder failed final validation")
                log("  IsValid: {}, IsSolid: {}, IsManifold: {}".format(
                    brep.IsValid, brep.IsSolid, brep.IsManifold))
                return None
        else:
            log("ERROR: Tapered cylinder not solid after capping attempts")
            log("  IsValid: {}, IsSolid: {}, Faces: {}".format(
                brep.IsValid, brep.IsSolid, brep.Faces.Count))
            return None
            
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
