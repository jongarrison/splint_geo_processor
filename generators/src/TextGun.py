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
        extrusion_depth_factor: Fraction of wall thickness for extrusion depth (default 0.8)
        emboss_inside: If True, emboss on inside surface (default).
                      If False, emboss on outside surface (text will be mirrored).
    
    Returns:
        Brep: The brep with embossed text
        
    Raises:
        InvalidInputError: If inputs are invalid
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
    
    # Default projection vector (equivalent to old -28 degree angle around X axis)
    if text_projection_vector is None:
        # This matches: Y rotated -28 degrees around X axis
        angle_rad = math.radians(-28.0)
        text_projection_vector = rg.Vector3d(
            0,
            math.cos(angle_rad),
            math.sin(angle_rad)
        )
    
    # Ensure it's a unit vector
    projection_direction = rg.Vector3d(text_projection_vector)
    projection_direction.Unitize()
    
    tolerance = 0.01  # Rhino document tolerance
    
    log("TextGun: Starting emboss for '{}' (inside={})".format(text_content, emboss_inside))
    
    # Step 1: Get bounding box centroid of the target brep
    bbox = target_brep.GetBoundingBox(True)
    if not bbox.IsValid:
        raise TextGunError("Failed to get bounding box of target brep")
    
    centroid = bbox.Center
    log("  Target centroid: ({:.2f}, {:.2f}, {:.2f})".format(centroid.X, centroid.Y, centroid.Z))
    log("  Projection vector: ({:.3f}, {:.3f}, {:.3f})".format(
        projection_direction.X, projection_direction.Y, projection_direction.Z))
    
    # Step 2: Create text outline geometry centered at the centroid
    # Create a plane perpendicular to the projection direction
    # The plane's normal should be the projection direction
    # X axis should be roughly world X (or perpendicular to projection in XY plane)
    # Y axis (text vertical) should be roughly world Z
    
    # Build an orthonormal basis for the text plane
    # We want text to be readable when looking along -projection_direction
    plane_normal = projection_direction
    
    # Try to use world Z as the "up" reference for text
    world_z = rg.Vector3d.ZAxis
    
    # If projection is nearly vertical, use world Y as reference instead
    if abs(rg.Vector3d.Multiply(plane_normal, world_z)) > 0.9:
        up_ref = rg.Vector3d.YAxis
    else:
        up_ref = world_z
    
    # Plane X axis = normal cross up_ref (horizontal direction, pointing right)
    plane_x = rg.Vector3d.CrossProduct(plane_normal, up_ref)
    plane_x.Unitize()
    
    # Plane Y axis = X cross normal (vertical direction for text, pointing up)
    plane_y = rg.Vector3d.CrossProduct(plane_x, plane_normal)
    plane_y.Unitize()
    
    # For text to read correctly, we may need to flip based on orientation
    # Text plane: X = horizontal, Y = vertical (up for text)
    text_plane = rg.Plane(centroid, plane_x, plane_y)
    
    log("  Text plane origin: ({:.2f}, {:.2f}, {:.2f})".format(
        text_plane.Origin.X, text_plane.Origin.Y, text_plane.Origin.Z))
    
    # Create text curves at this plane
    text_curves = create_text_curves(text_content, text_plane, text_size, bold=True)
    if not text_curves or len(text_curves) == 0:
        raise TextGunError("Failed to create text curves")
    
    log("  Created {} text curves".format(len(text_curves)))
    
    # Step 2b: Center the text on the splint centroid
    text_bbox = rg.BoundingBox.Empty
    for curve in text_curves:
        text_bbox.Union(curve.GetBoundingBox(True))
    
    if text_bbox.IsValid:
        text_center = text_bbox.Center
        center_offset = centroid - text_center
        for curve in text_curves:
            curve.Translate(center_offset)
        log("  Centered text by offset ({:.2f}, {:.2f}, {:.2f})".format(
            center_offset.X, center_offset.Y, center_offset.Z))
    
    # Step 2c: If embossing outside, mirror text horizontally so it reads correctly
    if not emboss_inside:
        # Mirror across the YZ plane passing through centroid (flip X)
        mirror_plane = rg.Plane(centroid, rg.Vector3d.XAxis)
        mirror_xform = rg.Transform.Mirror(mirror_plane)
        for curve in text_curves:
            curve.Transform(mirror_xform)
        log("  Mirrored text for outside embossing")
    
    # Step 3: Create boundary surfaces from text curves, then extrude each letter
    letter_surfaces = create_boundary_surfaces(text_curves, tolerance)
    if not letter_surfaces or len(letter_surfaces) == 0:
        raise TextGunError("Failed to create letter surfaces from curves")
    
    log("  Created {} letter surfaces".format(len(letter_surfaces)))
    
    # Calculate extrusion depth (handle None for extrusion_depth_factor)
    if extrusion_depth_factor is None:
        extrusion_depth_factor = 0.8
    extrusion_depth = wall_thickness_mm * extrusion_depth_factor
    log("  Extrusion depth: {:.2f} mm".format(extrusion_depth))
    
    # Extrusion direction depends on inside vs outside
    if emboss_inside:
        extrusion_direction = projection_direction
    else:
        extrusion_direction = -projection_direction  # Extrude inward from outside
    
    # Extrude each letter surface into a solid
    letter_breps = []
    for surf in letter_surfaces:
        extruded = extrude_surface(surf, extrusion_direction, extrusion_depth, tolerance)
        if extruded:
            letter_breps.append(extruded)
    
    if len(letter_breps) == 0:
        raise TextGunError("Failed to create any letter extrusions")
    
    log("  Created {} letter breps".format(len(letter_breps)))
    
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
        
        # Calculate move vector to bring letter to surface
        move_vector = surface_point - letter_centroid
        
        # Move the letter brep
        moved_letter = letter_brep.DuplicateBrep()
        moved_letter.Translate(move_vector)
        
        log("  Moved letter {} by ({:.2f}, {:.2f}, {:.2f})".format(
            i, move_vector.X, move_vector.Y, move_vector.Z))
        
        # Subtract this letter from the result
        diff_result = rg.Brep.CreateBooleanDifference(result_brep, moved_letter, tolerance)
        
        if diff_result and len(diff_result) > 0:
            if len(diff_result) > 1:
                result_brep = max(diff_result, key=lambda b: get_brep_volume(b) or 0)
                log("  Warning: Boolean difference produced {} pieces, using largest".format(len(diff_result)))
            else:
                result_brep = diff_result[0]
            log("  Successfully subtracted letter {}".format(i))
        else:
            log("  Warning: Boolean difference failed for letter {}".format(i))
    
    if not result_brep.IsValid:
        raise TextGunError("Result brep is not valid")
    
    log("TextGun: Successfully embossed '{}'".format(text_content))
    return result_brep


def create_text_curves(text, plane, height, bold=True):
    """
    Create curves from text using Rhino's TextEntity.
    
    Args:
        text: The text string
        plane: Plane for text placement
        height: Text height
        bold: Whether to use bold font
        
    Returns:
        list of Curve geometry objects
    """
    result_curves = []
    
    # Try using Rhino.Geometry.TextEntity.CreateCurves
    try:
        doc = Rhino.RhinoDoc.ActiveDoc
        if doc:
            dim_style = doc.DimStyles.Current
            text_entity = rg.TextEntity.Create(
                text, plane, dim_style, False, 0, 0
            )
            if text_entity:
                text_entity.TextHeight = height
                curves = text_entity.CreateCurves(dim_style, False)
                if curves and len(curves) > 0:
                    log("  Created {} curves via TextEntity.CreateCurves".format(len(curves)))
                    return list(curves)
    except Exception as e:
        log("  TextEntity.CreateCurves failed: {}".format(str(e)))
    
    # Fallback: Try rhinoscriptsyntax (may not work in GH context)
    created_ids = []
    try:
        text_id = rs.AddText(
            text,
            plane,
            height,
            "Arial",
            1 if bold else 0,  # font_style: 0=normal, 1=bold
            2 + 65536  # center horizontal + middle vertical
        )
        
        if text_id:
            created_ids.append(text_id)
            curve_ids = rs.ExplodeText(text_id, delete=False)
            
            if curve_ids:
                for cid in curve_ids:
                    created_ids.append(cid)
                    curve_geom = rs.coercecurve(cid)
                    if curve_geom:
                        result_curves.append(curve_geom.DuplicateCurve())
                
                log("  Created {} text curves via rs.ExplodeText".format(len(result_curves)))
    except Exception as e:
        log("  rs.AddText/ExplodeText failed: {}".format(str(e)))
    finally:
        for obj_id in created_ids:
            try:
                rs.DeleteObject(obj_id)
            except:
                pass
    
    return result_curves


def create_boundary_surfaces(curves, tolerance):
    """
    Create planar surfaces from closed curves.
    
    Args:
        curves: List of curves
        tolerance: Document tolerance
        
    Returns:
        list of Breps (planar surfaces)
    """
    surfaces = []
    
    # Try to create planar surfaces from each closed curve individually
    for curve in curves:
        if not curve.IsClosed:
            if curve.IsClosable:
                curve = curve.ToNurbsCurve()
                curve.MakeClosed(tolerance)
        
        if curve.IsClosed:
            breps = rg.Brep.CreatePlanarBreps([curve], tolerance)
            if breps:
                surfaces.extend(breps)
    
    # If individual curves didn't work, try all curves together
    if len(surfaces) == 0:
        breps = rg.Brep.CreatePlanarBreps(curves, tolerance)
        if breps:
            surfaces.extend(breps)
    
    return surfaces


def extrude_surface(brep_surface, direction, depth, tolerance):
    """
    Extrude a planar brep surface along a direction to create a solid.
    
    Args:
        brep_surface: A planar Brep (single face)
        direction: Vector3d direction to extrude
        depth: Extrusion depth
        tolerance: Document tolerance
        
    Returns:
        Brep solid or None
    """
    if brep_surface.Faces.Count == 0:
        return None
    
    face = brep_surface.Faces[0]
    
    # Get the outer loop curve
    outer_loop = face.OuterLoop
    if outer_loop is None:
        return None
    
    loop_curve = outer_loop.To3dCurve()
    if loop_curve is None:
        return None
    
    # Create extrusion vector
    extrusion_vector = direction * depth
    
    # Method 1: Use Extrusion.Create
    # This creates an extrusion in the curve's plane normal direction
    # We need to check if the curve is planar and get its plane
    is_planar, curve_plane = loop_curve.TryGetPlane(tolerance)
    
    if is_planar:
        # Check if curve plane normal aligns with our desired direction
        dot = abs(rg.Vector3d.Multiply(curve_plane.Normal, direction))
        if dot > 0.9:  # Reasonably aligned
            extrusion = rg.Extrusion.Create(loop_curve, depth, True)
            if extrusion:
                brep = extrusion.ToBrep()
                if brep and brep.IsValid and brep.IsSolid:
                    return brep
    
    # Method 2: Use surface offset/thickening approach
    # Create a copy of the surface, offset it, and loft between them
    try:
        # Duplicate and move the curve
        end_curve = loop_curve.DuplicateCurve()
        end_curve.Translate(extrusion_vector)
        
        # Loft between the two curves
        loft = rg.Brep.CreateFromLoft(
            [loop_curve, end_curve],
            rg.Point3d.Unset, rg.Point3d.Unset,
            rg.LoftType.Straight,
            False  # not closed
        )
        
        if loft and len(loft) > 0:
            # Cap the ends
            result = loft[0]
            capped = result.CapPlanarHoles(tolerance)
            if capped and capped.IsValid and capped.IsSolid:
                return capped
            elif result.IsValid:
                return result
    except Exception as e:
        log("  Loft extrusion failed: {}".format(str(e)))
    
    return None


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
