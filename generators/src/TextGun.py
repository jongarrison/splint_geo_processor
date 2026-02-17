"""
TextGun.py
Emboss text on the inside or outside wall of a brep.

Algorithm:
1. Get brep bounding box centroid
2. Create text outline geometry centered at the centroid
3. Orient the text to align with text_projection_vector
4. If embossing outside, mirror text horizontally and move outward first
5. Extrude each letter to create solid breps
6. For EACH letter separately:
   - Get the letter's centroid
   - Project from that centroid along projection vector until it hits the surface
   - Move the letter to that intersection point
   - Subtract the letter from the brep
"""

import Rhino.Geometry as rg
import Rhino
import rhinoscriptsyntax as rs
import scriptcontext as sc
import math
from splintcommon import log


class TextGunError(Exception):
    """Raised when text embossing operation fails."""
    pass


class InvalidInputError(Exception):
    """Raised when input parameters are invalid."""
    pass


def emboss_text(
    target_brep,
    text_content,
    wall_thickness_mm,
    text_size=3.3,
    text_projection_vector=None,
    text_up_vector=None,
    projection_origin=None,
    extrusion_depth_factor=0.8,
    emboss_inside=True
):
    """
    Emboss text on the inside or outside wall of a brep.
    
    Args:
        target_brep: The Brep to emboss text onto
        text_content: Text string to emboss
        wall_thickness_mm: Wall thickness in mm
        text_size: Text height (default 3.3)
        text_projection_vector: Vector3d direction to project text onto surface.
                               If None, defaults to (0, 0.883, -0.469) which is
                               equivalent to the old -28 degree angle.
        text_up_vector: Vector3d hint for "up" direction of text.
                       If None (default), computed via vector rejection of +Z
                       from text_projection_vector (falls back to +Y if +Z is parallel).
                       If provided, used directly without modification - this gives
                       full control for special cases like text on vertical walls.
        projection_origin: Point3d where text is centered before projection.
                          If None, uses the bounding box center of target_brep.
        extrusion_depth_factor: Fraction of wall thickness for extrusion depth (default 0.8)
        emboss_inside: If True, emboss on inside surface (default).
                      If False, emboss on outside surface (text will be mirrored).
    
    Returns:
        tuple: (result_brep, text_breps_before_projection, projected_letter_breps, final_text_plane)
            - result_brep: The brep with embossed text
            - text_breps_before_projection: List of extruded letter Breps, centered and oriented
                         but before projection to the surface (useful for debug)
            - projected_letter_breps: List of letter Breps after projection to surface
            - final_text_plane: The plane used for text orientation
        
    Raises:
        InvalidInputError: If inputs are invalid (including text_up_vector parallel
                          to text_projection_vector)
        TextGunError: If embossing operation fails
    """
    # Validate inputs
    if target_brep is None:
        raise InvalidInputError("target_brep is None")
    if not target_brep.IsValid:
        raise InvalidInputError("target_brep is not valid")
    if not text_content or len(text_content.strip()) == 0:
        raise InvalidInputError("text_content is empty")
    if wall_thickness_mm <= 0:
        raise InvalidInputError("wall_thickness_mm must be positive")
    if text_size <= 0:
        raise InvalidInputError("text_size must be positive")
    
    # Calculate extrusion depth (handle None for extrusion_depth_factor)
    if extrusion_depth_factor is None:
        extrusion_depth_factor = 0.8
    extrusion_depth = wall_thickness_mm * extrusion_depth_factor
    log("  Extrusion depth: {:.2f} mm".format(extrusion_depth))

    # Default projection vector (equivalent to old -28 degree angle around X axis)
    if text_projection_vector is None:
        raise InvalidInputError("text_projection_vector is None")
    
    # Ensure it's a unit vector
    projection_direction = rg.Vector3d(text_projection_vector)
    projection_direction.Unitize()
    
    # Determine actual_up vector for text orientation
    if text_up_vector is None:
        # Default: compute via vector rejection of +Z from projection_direction
        # This automatically handles most cases and gracefully handles near-vertical projections
        up_vec = rg.Vector3d.ZAxis
        dot = rg.Vector3d.Multiply(up_vec, projection_direction)
        actual_up = up_vec - projection_direction * dot
        
        # Check if +Z is parallel to projection (rejection is zero)
        if actual_up.Length < 0.001:
            # Fall back to +Y as the up hint
            up_vec = rg.Vector3d.YAxis
            dot = rg.Vector3d.Multiply(up_vec, projection_direction)
            actual_up = up_vec - projection_direction * dot
            
            if actual_up.Length < 0.001:
                raise InvalidInputError(
                    "Cannot determine text orientation: projection is along both +Z and +Y")
        
        actual_up.Unitize()
        log("text_up_vector: Using auto-computed text_up (from +Z rejection)")
    else:
        # User provided explicit text_up_vector - use it directly without rejection
        # This gives full control for special cases (e.g., text on vertical walls)
        actual_up = rg.Vector3d(text_up_vector)
        if actual_up.Length < 0.001:
            raise InvalidInputError("text_up_vector has zero length")
        actual_up.Unitize()
        log("text_up_vector: Using user-provided text_up_vector directly")
    
    tolerance = 0.01  # Rhino document tolerance
    
    log("TextGun: Starting emboss for '{}' (inside={})".format(text_content, emboss_inside))
    
    # Step 1: Determine projection origin (text center point)
    if projection_origin is not None:
        centroid = rg.Point3d(projection_origin)
        log("  Using provided projection_origin: ({:.2f}, {:.2f}, {:.2f})".format(
            centroid.X, centroid.Y, centroid.Z))
    else:
        # Default to bounding box center
        bbox = target_brep.GetBoundingBox(True)
        if not bbox.IsValid:
            raise TextGunError("Failed to get bounding box of target brep")
        centroid = bbox.Center
        log("  Using bbox center as projection_origin: ({:.2f}, {:.2f}, {:.2f})".format(
            centroid.X, centroid.Y, centroid.Z))
    
    log("  Projection vector: ({:.3f}, {:.3f}, {:.3f})".format(
        projection_direction.X, projection_direction.Y, projection_direction.Z))
    log("  Text up vector (actual): ({:.3f}, {:.3f}, {:.3f})".format(
        actual_up.X, actual_up.Y, actual_up.Z))
    
    # Step 2: Build text plane
    plane_normal = projection_direction #Z analog
    text_y =  actual_up
    text_X = rg.Vector3d.CrossProduct(plane_normal, text_y)

    text_plane = rg.Plane(centroid, text_X, text_y)
    
    log("text_plane: Origin=({:.2f}, {:.2f}, {:.2f}), X=({:.3f}, {:.3f}, {:.3f}), Y=({:.3f}, {:.3f}, {:.3f})".format(
        text_plane.Origin.X, text_plane.Origin.Y, text_plane.Origin.Z,
        text_plane.XAxis.X, text_plane.XAxis.Y, text_plane.XAxis.Z,
        text_plane.YAxis.X, text_plane.YAxis.Y, text_plane.YAxis.Z))
    
    # Create text breps directly using CreatePolysurfacesGrouped
    # Double the depth so letters are centered on the surface (extend equally on both sides)
    final_text_plane = rg.Plane(text_plane)
    letter_extrusion_depth = extrusion_depth * 2
    
    letter_breps = create_text_breps(text_content, text_plane, text_size, letter_extrusion_depth)
    log("  Letter extrusion depth (2x): {:.2f} mm".format(letter_extrusion_depth))
    
    if not letter_breps or len(letter_breps) == 0:
        raise TextGunError("Failed to create letter breps via CreatePolysurfacesGrouped")
    
    log("  Created {} letter breps via CreatePolysurfacesGrouped".format(len(letter_breps)))
    
    # Step 2b: Center the letter breps on the centroid
    text_bbox = rg.BoundingBox.Empty
    for brep in letter_breps:
        text_bbox.Union(brep.GetBoundingBox(True))
    
    if text_bbox.IsValid:
        text_center = text_bbox.Center
        center_offset = centroid - text_center
        for brep in letter_breps:
            brep.Translate(center_offset)
        log("  Centered letter breps by offset ({:.2f}, {:.2f}, {:.2f})".format(
            center_offset.X, center_offset.Y, center_offset.Z))
    
    # Step 2c: If embossing outside, mirror text horizontally so it reads correctly
    if not emboss_inside:
        log(" =========== MIRRORING ===========")
        mirror_plane = rg.Plane(centroid, rg.Vector3d.XAxis)
        mirror_xform = rg.Transform.Mirror(mirror_plane)
        for brep in letter_breps:
            brep.Transform(mirror_xform)
        log("  Mirrored letter breps for outside embossing")
    
    # Keep a copy of the centered/oriented text before projection
    text_breps_before_projection = [b.DuplicateBrep() for b in letter_breps]
    
    # Create mesh from target brep for ray intersection
    mesh_params = rg.MeshingParameters.FastRenderMesh
    meshes = rg.Mesh.CreateFromBrep(target_brep, mesh_params)
    if not meshes or len(meshes) == 0:
        raise TextGunError("Failed to create mesh from target brep")
    
    target_mesh = rg.Mesh()
    for m in meshes:
        target_mesh.Append(m)
    
    log("  Created mesh with {} faces for intersection".format(target_mesh.Faces.Count))
    
    # Step 4: For each letter, project it onto the surface and subtract
    result_brep = target_brep.DuplicateBrep()
    
    # Collect projected letters for debug output
    projected_letter_breps = []
    
    # For outside embossing, we need to project from far outside back inward
    outside_offset_distance = 1000.0  # mm
    
    for i, letter_brep in enumerate(letter_breps):
        # Get the letter's centroid
        letter_centroid = get_brep_centroid(letter_brep)
        if letter_centroid is None:
            log("  Warning: Could not get centroid for letter {}, skipping".format(i))
            continue
        
        log("  Letter {} centroid: ({:.2f}, {:.2f}, {:.2f})".format(
            i, letter_centroid.X, letter_centroid.Y, letter_centroid.Z))
        
        # Determine ray origin and direction based on inside/outside
        if emboss_inside:
            # Project outward from centroid to find inside surface
            ray_origin = letter_centroid
            ray_direction = projection_direction
        else:
            # Move far out along projection direction, then shoot back inward
            ray_origin = letter_centroid + projection_direction * outside_offset_distance
            ray_direction = -projection_direction
        
        ray = rg.Ray3d(ray_origin, ray_direction)
        intersection_param = rg.Intersect.Intersection.MeshRay(target_mesh, ray)
        
        if intersection_param < 0:
            # Try opposite direction as fallback
            ray = rg.Ray3d(ray_origin, -ray_direction)
            intersection_param = rg.Intersect.Intersection.MeshRay(target_mesh, ray)
        
        if intersection_param < 0:
            log("  Warning: Could not find intersection for letter {}, skipping".format(i))
            continue
        
        # Calculate the intersection point
        surface_point = ray.PointAt(intersection_param)
        log("  Letter {} surface point: ({:.2f}, {:.2f}, {:.2f})".format(
            i, surface_point.X, surface_point.Y, surface_point.Z))
        
        # Calculate move vector to bring letter centroid to surface
        # Letters are extruded at 2x depth and centered, so they extend equally on both sides
        move_vector = surface_point - letter_centroid
        
        # Move the letter brep
        moved_letter = letter_brep.DuplicateBrep()
        moved_letter.Translate(move_vector)
        
        log("  Moved letter {} by ({:.2f}, {:.2f}, {:.2f})".format(
            i, move_vector.X, move_vector.Y, move_vector.Z))
        
        # Fix inverted normals if volume is negative (required for boolean to work)
        letter_volume = get_brep_volume(moved_letter)
        if letter_volume and letter_volume < 0:
            moved_letter.Flip()
        
        # Save for debug output
        projected_letter_breps.append(moved_letter.DuplicateBrep())
        
        # Subtract this letter from the result
        diff_result = rg.Brep.CreateBooleanDifference(result_brep, moved_letter, tolerance)
        
        if diff_result and len(diff_result) > 0:
            # Select the largest piece (main brep with letter carved out)
            result_brep = max(diff_result, key=lambda b: get_brep_volume(b) or 0)
            log("  Subtracted letter {}".format(i))
        else:
            log("  Warning: Boolean difference failed for letter {}".format(i))
    
    if not result_brep.IsValid:
        raise TextGunError("Result brep is not valid")
    
    log("TextGun: Successfully embossed '{}'".format(text_content))
    return (result_brep, text_breps_before_projection, projected_letter_breps, final_text_plane)


def create_text_breps(text, plane, height, depth):
    """
    Create 3D letter breps directly using TextEntity.CreatePolysurfacesGrouped.
    
    Workaround for Rhino TextEntity orientation issues: create text on World XY
    plane where it behaves predictably, then transform to the desired plane.
    
    Args:
        text: The text string
        plane: Plane for text placement (text will be centered on plane origin)
        height: Text height
        depth: Extrusion depth for the letters
        
    Returns:
        list of Brep objects (one per letter/character group)
    """
    result_breps = []
    
    try:
        doc = Rhino.RhinoDoc.ActiveDoc
        if doc:
            dim_style = doc.DimStyles.Current
            
            # Create text on World XY plane at origin (predictable behavior)
            xy_plane = rg.Plane.WorldXY
            log("create_text_breps: Creating TextEntity on World XY, will transform to target plane")
            
            text_entity = rg.TextEntity.Create(
                text, xy_plane, dim_style, False, 0, 0
            )
            if text_entity:
                text_entity.TextHeight = height
                log("  Text height set to: {:.2f}".format(height))
                
                # CreatePolysurfacesGrouped returns an array of Brep arrays
                small_caps_scale = 1.0
                spacing = 0.0
                
                brep_groups = text_entity.CreatePolysurfacesGrouped(
                    dim_style, small_caps_scale, depth, spacing
                )
                
                if brep_groups:
                    for group in brep_groups:
                        if group:
                            for brep in group:
                                if brep and brep.IsValid:
                                    result_breps.append(brep)
                    
                    log("  Created {} breps on World XY".format(len(result_breps)))
                    
                    if len(result_breps) > 0:
                        # Compute bounding box of all text breps
                        text_bbox = rg.BoundingBox.Empty
                        for brep in result_breps:
                            text_bbox.Union(brep.GetBoundingBox(True))
                        
                        # Center point of text on XY plane
                        text_center = text_bbox.Center
                        log("  Text center on XY: ({:.2f}, {:.2f}, {:.2f})".format(
                            text_center.X, text_center.Y, text_center.Z))
                        
                        # Build transform: XY plane centered at text_center -> target plane
                        source_plane = rg.Plane(text_center, rg.Vector3d.XAxis, rg.Vector3d.YAxis)
                        xform = rg.Transform.PlaneToPlane(source_plane, plane)
                        
                        # Apply transform to all breps
                        for brep in result_breps:
                            brep.Transform(xform)
                        
                        log("  Transformed text to target plane: Origin=({:.2f}, {:.2f}, {:.2f})".format(
                            plane.Origin.X, plane.Origin.Y, plane.Origin.Z))
                    
                    return result_breps
                else:
                    log("  CreatePolysurfacesGrouped returned None or empty")
    except Exception as e:
        log("  TextEntity.CreatePolysurfacesGrouped failed: {}".format(str(e)))
    
    return result_breps


def get_brep_centroid(brep):
    """
    Get the volume centroid of a brep.
    
    Args:
        brep: A Brep
        
    Returns:
        Point3d or None
    """
    vmp = rg.VolumeMassProperties.Compute(brep)
    if vmp:
        return vmp.Centroid
    
    # Fallback to area centroid
    amp = rg.AreaMassProperties.Compute(brep)
    if amp:
        return amp.Centroid
    
    # Last resort: bounding box center
    bbox = brep.GetBoundingBox(True)
    if bbox.IsValid:
        return bbox.Center
    
    return None


def get_brep_volume(brep):
    """
    Get the volume of a brep.
    
    Args:
        brep: A Brep
        
    Returns:
        float or None
    """
    try:
        vmp = rg.VolumeMassProperties.Compute(brep)
        if vmp:
            return vmp.Volume
    except:
        pass
    return None
