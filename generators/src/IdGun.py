"""
IdGun.py
Emboss a text ID on the inside wall of a splint brep.

Algorithm:
1. Get splint bounding box centroid
2. Create text outline geometry centered at the centroid, on the XZ plane
3. Rotate the text around the X axis by side_projection_angle_deg
4. Extrude each letter to create solid breps
5. For EACH letter separately:
   - Get the letter's centroid
   - Project from that centroid through the text plane until it hits the splint surface
   - Move the letter down to that intersection point
   - Subtract the letter from the splint
"""

import Rhino.Geometry as rg
import Rhino
import rhinoscriptsyntax as rs
import scriptcontext as sc
import math
from splintcommon import log


class IdGunError(Exception):
    """Raised when ID embossing operation fails."""
    pass


class InvalidInputError(Exception):
    """Raised when input parameters are invalid."""
    pass


def emboss_id(
    splint_brep,
    object_id,
    wall_thickness_mm,
    text_size=3.3,
    side_projection_angle_deg=-28.0,
    extrusion_depth_factor=0.8
):
    """
    Emboss a text ID on the inside wall of a splint brep.
    
    Args:
        splint_brep: The finished splint Brep to emboss
        object_id: Text string to emboss (typically 4 characters)
        wall_thickness_mm: Wall thickness in mm
        text_size: Text height (default 3.3)
        side_projection_angle_deg: Angle to rotate text plane around X axis (default -28)
        extrusion_depth_factor: Fraction of wall thickness for extrusion depth (default 0.8)
    
    Returns:
        Brep: The splint with embossed ID
        
    Raises:
        InvalidInputError: If inputs are invalid
        IdGunError: If embossing operation fails
    """
    # Validate inputs
    if splint_brep is None:
        raise InvalidInputError("splint_brep is None")
    if not splint_brep.IsValid:
        raise InvalidInputError("splint_brep is not valid")
    if not object_id or len(object_id.strip()) == 0:
        raise InvalidInputError("object_id is empty")
    if wall_thickness_mm <= 0:
        raise InvalidInputError("wall_thickness_mm must be positive")
    if text_size <= 0:
        raise InvalidInputError("text_size must be positive")
    
    tolerance = 0.01  # Rhino document tolerance
    
    log("IdGun: Starting emboss for ID '{}'".format(object_id))
    
    # Step 1: Get bounding box centroid of the splint
    bbox = splint_brep.GetBoundingBox(True)
    if not bbox.IsValid:
        raise IdGunError("Failed to get bounding box of splint")
    
    centroid = bbox.Center
    log("  Splint centroid: ({:.2f}, {:.2f}, {:.2f})".format(centroid.X, centroid.Y, centroid.Z))
    
    # Step 2: Create text outline geometry centered at the centroid, on the XZ plane
    # XZ plane at centroid: X is right, Z is up, Y is the normal
    # For text to appear upright when facing +Y direction, we need:
    # - Plane X axis = world X (text horizontal)
    # - Plane Y axis = world -Z (text vertical, flipped so text reads correctly)
    text_plane = rg.Plane(centroid, rg.Vector3d.XAxis, -rg.Vector3d.ZAxis)
    
    log("  Text plane origin: ({:.2f}, {:.2f}, {:.2f})".format(
        text_plane.Origin.X, text_plane.Origin.Y, text_plane.Origin.Z))
    
    # Create text curves at this plane
    text_curves = create_text_curves(object_id, text_plane, text_size, bold=True)
    if not text_curves or len(text_curves) == 0:
        raise IdGunError("Failed to create text curves")
    
    log("  Created {} text curves".format(len(text_curves)))
    
    # Step 2b: Center the text on the splint centroid
    # Get combined bounding box of all text curves
    text_bbox = rg.BoundingBox.Empty
    for curve in text_curves:
        text_bbox.Union(curve.GetBoundingBox(True))
    
    if text_bbox.IsValid:
        text_center = text_bbox.Center
        # Move all curves so text center = splint centroid
        center_offset = centroid - text_center
        for curve in text_curves:
            curve.Translate(center_offset)
        log("  Centered text by offset ({:.2f}, {:.2f}, {:.2f})".format(
            center_offset.X, center_offset.Y, center_offset.Z))
    
    log("  Created {} text curves".format(len(text_curves)))
    
    # Step 3: Rotate the text around the X axis by side_projection_angle_deg
    # This tilts the text plane so it projects onto the angled surface
    angle_rad = math.radians(side_projection_angle_deg)
    rotation_axis = rg.Vector3d.XAxis
    rotation_xform = rg.Transform.Rotation(angle_rad, rotation_axis, centroid)
    
    for curve in text_curves:
        curve.Transform(rotation_xform)
    
    log("  Rotated text by {:.1f} degrees around X axis".format(side_projection_angle_deg))
    
    # Calculate the projection direction (the normal of the rotated text plane)
    # Original plane normal was Y axis, after rotation it's rotated around X
    projection_direction = rg.Vector3d.YAxis
    projection_direction.Transform(rotation_xform)
    projection_direction.Unitize()
    
    log("  Projection direction: ({:.3f}, {:.3f}, {:.3f})".format(
        projection_direction.X, projection_direction.Y, projection_direction.Z))
    
    # Step 4: Create boundary surfaces from text curves, then extrude each letter
    # Group curves by character (each closed curve or set of curves forming a letter)
    letter_surfaces = create_boundary_surfaces(text_curves, tolerance)
    if not letter_surfaces or len(letter_surfaces) == 0:
        raise IdGunError("Failed to create letter surfaces from curves")
    
    log("  Created {} letter surfaces".format(len(letter_surfaces)))
    
    # Calculate extrusion depth
    extrusion_depth = wall_thickness_mm * extrusion_depth_factor
    log("  Extrusion depth: {:.2f} mm".format(extrusion_depth))
    
    # Extrude each letter surface into a solid
    # Extrusion direction is along the projection direction (into the splint)
    letter_breps = []
    for surf in letter_surfaces:
        extruded = extrude_surface(surf, projection_direction, extrusion_depth, tolerance)
        if extruded:
            letter_breps.append(extruded)
    
    if len(letter_breps) == 0:
        raise IdGunError("Failed to create any letter extrusions")
    
    log("  Created {} letter breps".format(len(letter_breps)))
    
    # Create mesh from splint for ray intersection
    mesh_params = rg.MeshingParameters.FastRenderMesh
    meshes = rg.Mesh.CreateFromBrep(splint_brep, mesh_params)
    if not meshes or len(meshes) == 0:
        raise IdGunError("Failed to create mesh from splint brep")
    
    splint_mesh = rg.Mesh()
    for m in meshes:
        splint_mesh.Append(m)
    
    log("  Created mesh with {} faces for intersection".format(splint_mesh.Faces.Count))
    
    # Step 5: For each letter, project it onto the splint surface and subtract
    result_brep = splint_brep.DuplicateBrep()
    
    for i, letter_brep in enumerate(letter_breps):
        # Get the letter's centroid
        letter_centroid = get_brep_centroid(letter_brep)
        if letter_centroid is None:
            log("  Warning: Could not get centroid for letter {}, skipping".format(i))
            continue
        
        log("  Letter {} centroid: ({:.2f}, {:.2f}, {:.2f})".format(
            i, letter_centroid.X, letter_centroid.Y, letter_centroid.Z))
        
        # Project from letter centroid along projection direction to find splint surface
        ray = rg.Ray3d(letter_centroid, projection_direction)
        intersection_param = rg.Intersect.Intersection.MeshRay(splint_mesh, ray)
        
        if intersection_param < 0:
            # Try opposite direction
            ray = rg.Ray3d(letter_centroid, -projection_direction)
            intersection_param = rg.Intersect.Intersection.MeshRay(splint_mesh, ray)
        
        if intersection_param < 0:
            log("  Warning: Could not find intersection for letter {}, skipping".format(i))
            continue
        
        # Calculate the intersection point
        surface_point = ray.PointAt(intersection_param)
        log("  Letter {} surface point: ({:.2f}, {:.2f}, {:.2f})".format(
            i, surface_point.X, surface_point.Y, surface_point.Z))
        
        # Calculate move vector to bring letter to surface
        # We want the letter to be positioned so it cuts into the surface
        # Move from current centroid to surface point
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
                # Use the largest piece
                result_brep = max(diff_result, key=lambda b: get_brep_volume(b) or 0)
                log("  Warning: Boolean difference produced {} pieces, using largest".format(len(diff_result)))
            else:
                result_brep = diff_result[0]
            log("  Successfully subtracted letter {}".format(i))
        else:
            log("  Warning: Boolean difference failed for letter {}".format(i))
    
    if not result_brep.IsValid:
        raise IdGunError("Result brep is not valid")
    
    log("IdGun: Successfully embossed ID '{}'".format(object_id))
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
