"""
FeatureIdentification.py
Functions for identifying and extracting geometric features from breps.
"""

import Rhino.Geometry as rg
from splintcommon import log


class FeatureIdentificationError(Exception):
    """Raised when feature identification fails."""
    pass


def box_center_slice(target_brep, bbox_plane, choose_side_vector):
    """
    Create a planar surface at the center of an oriented bounding box,
    oriented to face in a specified direction.
    
    Args:
        target_brep: A solid Brep to create the bounding box around
        bbox_plane: Plane used to orient the bounding box
        choose_side_vector: Vector3d indicating which side of the bbox to select
        
    Returns:
        Brep: A planar surface (intersection_shape) positioned at the bbox center
        
    Raises:
        FeatureIdentificationError: If operation fails
    """
    if target_brep is None:
        raise FeatureIdentificationError("target_brep is None")
    if not target_brep.IsValid:
        raise FeatureIdentificationError("target_brep is not valid")
    if bbox_plane is None:
        raise FeatureIdentificationError("bbox_plane is None")
    if choose_side_vector is None:
        raise FeatureIdentificationError("choose_side_vector is None")
    
    # Ensure choose_side_vector is a unit vector
    side_direction = rg.Vector3d(choose_side_vector)
    side_direction.Unitize()
    
    log("FeatureIdentification.box_center_slice: Starting")
    log("  bbox_plane origin: ({:.2f}, {:.2f}, {:.2f})".format(
        bbox_plane.Origin.X, bbox_plane.Origin.Y, bbox_plane.Origin.Z))
    log("  choose_side_vector: ({:.3f}, {:.3f}, {:.3f})".format(
        side_direction.X, side_direction.Y, side_direction.Z))
    
    # Step 1: Create oriented bounding box around target_brep
    # GetBoundingBox with a plane returns a world-aligned box of the transformed geometry
    # We need to use a different approach for true oriented bbox
    
    # Transform brep to bbox_plane coordinates, get bbox, then transform back
    world_to_plane = rg.Transform.ChangeBasis(rg.Plane.WorldXY, bbox_plane)
    plane_to_world = rg.Transform.ChangeBasis(bbox_plane, rg.Plane.WorldXY)
    
    # Get bounding box in the plane's coordinate system
    temp_brep = target_brep.DuplicateBrep()
    temp_brep.Transform(world_to_plane)
    local_bbox = temp_brep.GetBoundingBox(True)
    
    if not local_bbox.IsValid:
        raise FeatureIdentificationError("Failed to create bounding box")
    
    # Create a Box from the local bounding box
    local_box = rg.Box(local_bbox)
    
    # Transform the box back to world coordinates
    world_box = rg.Box(local_box)
    world_box.Transform(plane_to_world)
    
    # Get the center of the bounding box in world coordinates
    bbox_center = world_box.Center
    log("  bbox center: ({:.2f}, {:.2f}, {:.2f})".format(
        bbox_center.X, bbox_center.Y, bbox_center.Z))
    
    # Step 2: Get the 6 faces of the box as Breps
    box_brep = world_box.ToBrep()
    if box_brep is None or not box_brep.IsValid:
        raise FeatureIdentificationError("Failed to convert box to brep")
    
    # The box brep has 6 faces
    if box_brep.Faces.Count != 6:
        raise FeatureIdentificationError("Box brep does not have 6 faces")
    
    log("  Box has {} faces".format(box_brep.Faces.Count))
    
    # Step 3: Find the face most aligned with choose_side_vector
    # For each face, compute its centroid and the vector from bbox_center to face_centroid
    # The face with the highest dot product with choose_side_vector wins
    
    best_face = None
    best_face_index = -1
    best_dot = -float('inf')
    
    for i in range(box_brep.Faces.Count):
        face = box_brep.Faces[i]
        
        # Get face centroid using area mass properties
        face_brep = face.DuplicateFace(False)
        amp = rg.AreaMassProperties.Compute(face_brep)
        
        if amp is None:
            continue
        
        face_centroid = amp.Centroid
        
        # Vector from bbox center to face centroid
        to_face = face_centroid - bbox_center
        to_face.Unitize()
        
        # Dot product with choose_side_vector
        dot = rg.Vector3d.Multiply(to_face, side_direction)
        
        log("  Face {}: centroid ({:.2f}, {:.2f}, {:.2f}), dot={:.3f}".format(
            i, face_centroid.X, face_centroid.Y, face_centroid.Z, dot))
        
        if dot > best_dot:
            best_dot = dot
            best_face = face_brep
            best_face_index = i
    
    if best_face is None:
        raise FeatureIdentificationError("Could not find a suitable face")
    
    log("  Selected face {} with dot={:.3f}".format(best_face_index, best_dot))
    
    # Step 4: Move intersection_shape so its centroid is at bbox_center
    amp = rg.AreaMassProperties.Compute(best_face)
    if amp is None:
        raise FeatureIdentificationError("Could not compute centroid of selected face")
    
    face_centroid = amp.Centroid
    move_vector = bbox_center - face_centroid
    
    intersection_shape = best_face.DuplicateBrep()
    intersection_shape.Translate(move_vector)
    
    log("  Moved intersection_shape by ({:.2f}, {:.2f}, {:.2f})".format(
        move_vector.X, move_vector.Y, move_vector.Z))
    
    # Verify final position
    amp_final = rg.AreaMassProperties.Compute(intersection_shape)
    if amp_final:
        final_centroid = amp_final.Centroid
        log("  Final intersection_shape centroid: ({:.2f}, {:.2f}, {:.2f})".format(
            final_centroid.X, final_centroid.Y, final_centroid.Z))
    
    log("FeatureIdentification.box_center_slice: Complete")
    return intersection_shape


def get_ordered_vertices(surface_brep, view_direction, up_vector=None):
    """
    Get the 4 corners of a planar rectangular surface in clockwise order
    when viewed from the specified direction.
    
    Args:
        surface_brep: A planar Brep surface (should have 4 corners)
        view_direction: Vector3d - the direction you're looking TOWARD
                        (e.g., +Y means viewer is on -Y side looking toward +Y)
        up_vector: Vector3d - what's "up" in that view (default: +Z)
        
    Returns:
        list of 4 Point3d in clockwise order: [top-left, top-right, bottom-right, bottom-left]
        
    Raises:
        FeatureIdentificationError: If surface doesn't have 4 vertices
    """
    if up_vector is None:
        up_vector = rg.Vector3d.ZAxis
    
    # Normalize vectors
    view_dir = rg.Vector3d(view_direction)
    view_dir.Unitize()
    up_vec = rg.Vector3d(up_vector)
    up_vec.Unitize()
    
    # Make up_vector perpendicular to view_direction
    # Project up onto plane perpendicular to view
    dot = rg.Vector3d.Multiply(up_vec, view_dir)
    up_vec = up_vec - view_dir * dot
    up_vec.Unitize()
    
    # Right vector: view x up (right-hand rule)
    right_vec = rg.Vector3d.CrossProduct(view_dir, up_vec)
    right_vec.Unitize()
    
    log("get_ordered_vertices: view=({:.2f},{:.2f},{:.2f}), up=({:.2f},{:.2f},{:.2f}), right=({:.2f},{:.2f},{:.2f})".format(
        view_dir.X, view_dir.Y, view_dir.Z,
        up_vec.X, up_vec.Y, up_vec.Z,
        right_vec.X, right_vec.Y, right_vec.Z))
    
    # Get vertices from the surface/brep
    # Handle both Brep and Surface types
    vertices = []
    
    if hasattr(surface_brep, 'Vertices'):
        # It's a Brep - get vertices directly
        for v in surface_brep.Vertices:
            vertices.append(v.Location)
    elif hasattr(surface_brep, 'Domain'):
        # It's a Surface - get 4 corners from domain
        u_domain = surface_brep.Domain(0)
        v_domain = surface_brep.Domain(1)
        
        # Get the 4 corner points
        vertices.append(surface_brep.PointAt(u_domain.Min, v_domain.Min))
        vertices.append(surface_brep.PointAt(u_domain.Max, v_domain.Min))
        vertices.append(surface_brep.PointAt(u_domain.Max, v_domain.Max))
        vertices.append(surface_brep.PointAt(u_domain.Min, v_domain.Max))
        
        log("  Got corners from surface domain: u=[{:.2f},{:.2f}], v=[{:.2f},{:.2f}]".format(
            u_domain.Min, u_domain.Max, v_domain.Min, v_domain.Max))
    else:
        raise FeatureIdentificationError(
            "surface_brep must be a Brep or Surface, got {}".format(type(surface_brep).__name__))
    
    if len(vertices) != 4:
        raise FeatureIdentificationError(
            "Expected 4 vertices, got {}".format(len(vertices)))
    
    # Project each vertex onto up/right axes
    # Use the centroid as origin for projection
    amp = rg.AreaMassProperties.Compute(surface_brep)
    if amp is None:
        raise FeatureIdentificationError("Could not compute surface centroid")
    centroid = amp.Centroid
    
    vertex_data = []
    for v in vertices:
        offset = v - centroid
        up_component = rg.Vector3d.Multiply(rg.Vector3d(offset), up_vec)
        right_component = rg.Vector3d.Multiply(rg.Vector3d(offset), right_vec)
        vertex_data.append((v, up_component, right_component))
        log("  vertex ({:.2f},{:.2f},{:.2f}): up={:.2f}, right={:.2f}".format(
            v.X, v.Y, v.Z, up_component, right_component))
    
    # Sort to find corners:
    # Top-left: max up, min right
    # Top-right: max up, max right
    # Bottom-right: min up, max right
    # Bottom-left: min up, min right
    
    # Find top two (highest up values)
    sorted_by_up = sorted(vertex_data, key=lambda x: x[1], reverse=True)
    top_two = sorted_by_up[:2]
    bottom_two = sorted_by_up[2:]
    
    # Top-left has smaller right value, top-right has larger
    top_two_sorted = sorted(top_two, key=lambda x: x[2])
    top_left = top_two_sorted[0][0]
    top_right = top_two_sorted[1][0]
    
    # Bottom-right has larger right value, bottom-left has smaller
    bottom_two_sorted = sorted(bottom_two, key=lambda x: x[2])
    bottom_left = bottom_two_sorted[0][0]
    bottom_right = bottom_two_sorted[1][0]
    
    ordered = [top_left, top_right, bottom_right, bottom_left]
    log("  Ordered vertices (clockwise from TL):")
    for i, pt in enumerate(ordered):
        labels = ["TL", "TR", "BR", "BL"]
        log("    {}: ({:.2f}, {:.2f}, {:.2f})".format(labels[i], pt.X, pt.Y, pt.Z))
    
    return ordered


def intersect_with_reference(target_brep, slice_surface, view_direction, up_vector=None):
    """
    Intersect a brep with a surface and return curves plus reference points
    corresponding to the surface vertices.
    
    Args:
        target_brep: The solid Brep to intersect
        slice_surface: A planar surface Brep to intersect with
        view_direction: Vector3d - direction looking TOWARD (for vertex ordering)
                        (e.g., +Y means viewer is on -Y side looking toward +Y)
        up_vector: Vector3d - what's "up" in that view (default: +Z)
        
    Returns:
        tuple: (result_curves, result_points)
            - result_curves: list of Curve from intersection
            - result_points: list of 4 Point3d (closest point on curves for each 
                            vertex, in clockwise order: TL, TR, BR, BL)
                            None if no curve exists or no close point found
                            
    Raises:
        FeatureIdentificationError: If intersection fails
    """
    if target_brep is None:
        raise FeatureIdentificationError("target_brep is None")
    if slice_surface is None:
        raise FeatureIdentificationError("slice_surface is None")
    if view_direction is None:
        raise FeatureIdentificationError("view_direction is None")
    
    log("FeatureIdentification.intersect_with_reference: Starting")
    
    # Step 1: Get ordered vertices of slice_surface
    ordered_vertices = get_ordered_vertices(slice_surface, view_direction, up_vector)
    
    # Step 2: Perform intersection
    # Handle both Brep and Surface types for slice_surface
    tolerance = 0.001  # Standard Rhino tolerance
    
    if hasattr(slice_surface, 'Faces'):
        # slice_surface is a Brep
        success, intersection_curves, intersection_points = rg.Intersect.Intersection.BrepBrep(
            target_brep, slice_surface, tolerance)
    else:
        # slice_surface is a Surface - use BrepSurface intersection
        success, intersection_curves, intersection_points = rg.Intersect.Intersection.BrepSurface(
            target_brep, slice_surface, tolerance)
    
    if not success:
        raise FeatureIdentificationError("Brep-Brep intersection failed")
    
    # Convert to list
    result_curves = list(intersection_curves) if intersection_curves else []
    
    log("  Found {} intersection curves".format(len(result_curves)))
    
    # If no curves, return empty results
    if len(result_curves) == 0:
        log("  No intersection curves found")
        return ([], [None, None, None, None])
    
    # Step 3: For each vertex, find closest point on any curve
    result_points = []
    
    for i, vertex in enumerate(ordered_vertices):
        labels = ["TL", "TR", "BR", "BL"]
        
        best_point = None
        best_distance = float('inf')
        
        for curve in result_curves:
            success, t = curve.ClosestPoint(vertex)
            if success:
                pt = curve.PointAt(t)
                dist = vertex.DistanceTo(pt)
                if dist < best_distance:
                    best_distance = dist
                    best_point = pt
        
        result_points.append(best_point)
        
        if best_point is not None:
            log("  {}: vertex ({:.2f},{:.2f},{:.2f}) -> curve point ({:.2f},{:.2f},{:.2f}), dist={:.2f}".format(
                labels[i], vertex.X, vertex.Y, vertex.Z,
                best_point.X, best_point.Y, best_point.Z, best_distance))
        else:
            log("  {}: vertex ({:.2f},{:.2f},{:.2f}) -> no curve point found".format(
                labels[i], vertex.X, vertex.Y, vertex.Z))
    
    log("FeatureIdentification.intersect_with_reference: Complete")
    return (result_curves, result_points)
