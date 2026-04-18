"""
BrepFillet.py
Fillet utilities for Brep edges.
"""

import Rhino.Geometry as rg
import math
import time
from splintcommon import log


def find_sharp_edges(brep, min_angle_degrees=10.0):
    """Find brep edges where adjacent faces meet at greater than min_angle_degrees.

    Args:
        brep: Input Brep to scan.
        min_angle_degrees: Minimum dihedral angle to consider sharp.

    Returns:
        Tuple of (edge_indices, angles_degrees) - two parallel lists.
    """
    edge_indices = []
    angles = []
    threshold_rad = math.radians(min_angle_degrees)

    for edge in brep.Edges:
        face_indices = edge.AdjacentFaces()
        if len(face_indices) != 2:
            continue

        face_a = brep.Faces[face_indices[0]]
        face_b = brep.Faces[face_indices[1]]

        mid_pt = edge.PointAt(edge.Domain.Mid)

        ok_a, u_a, v_a = face_a.ClosestPoint(mid_pt)
        ok_b, u_b, v_b = face_b.ClosestPoint(mid_pt)
        if not (ok_a and ok_b):
            continue

        normal_a = face_a.NormalAt(u_a, v_a)
        normal_b = face_b.NormalAt(u_b, v_b)

        angle = rg.Vector3d.VectorAngle(normal_a, normal_b)
        if angle > threshold_rad:
            edge_indices.append(edge.EdgeIndex)
            angles.append(math.degrees(angle))

    log("Sharp edge scan: {} sharp of {} total (threshold={} deg)".format(
        len(edge_indices), brep.Edges.Count, min_angle_degrees))
    return edge_indices, angles


def fillet_edges(brep, edge_indices, radius, tolerance=0.01):
    """Chamfer specific edges by index. Single batch call, no retry loop.

    Uses BlendType.Chamfer for reliability -- CreateFilletEdges with rolling
    ball fillets can permanently freeze Grasshopper on failure.

    Args:
        brep: Input Brep.
        edge_indices: List of edge indices to fillet.
        radius: Chamfer distance in mm.
        tolerance: Model tolerance.

    Returns:
        Chamfered Brep (or original if the call fails).
    """
    if not edge_indices:
        return brep

    t_start = time.time()
    brep.Faces.ShrinkFaces()

    blend_type = rg.BlendType.Chamfer
    rail_type = rg.RailType.DistanceFromEdge
    radii = [radius] * len(edge_indices)

    log("Chamfer: batch on {} edges, radius={:.3f}".format(len(edge_indices), radius))
    try:
        result = rg.Brep.CreateFilletEdges(
            brep, edge_indices, radii, radii, blend_type, rail_type, tolerance
        )
    except Exception as e:
        log("Chamfer: exception: {}".format(e))
        return brep

    if result and len(result) > 0 and result[0].IsValid:
        log("Chamfer: succeeded in {:.2f}s".format(time.time() - t_start))
        return result[0]

    log("Chamfer: failed, returning original")
    return brep


def fillet_sharp_edges(brep, radius, min_angle_degrees=10.0, tolerance=0.01):
    """Find and fillet all sharp edges on a brep.

    Args:
        brep: Input Brep.
        radius: Fillet radius in mm.
        min_angle_degrees: Minimum dihedral angle to consider sharp.
        tolerance: Model tolerance.

    Returns:
        Filleted Brep (or original if no sharp edges found).
    """
    indices, angles = find_sharp_edges(brep, min_angle_degrees)
    if not indices:
        log("No sharp edges found, returning original brep")
        return brep

    # Filter out edges too short for the radius
    filtered = [idx for idx, angle in zip(indices, angles)
                if brep.Edges[idx].GetLength() >= radius * 2]
    log("fillet_sharp_edges: {} candidates (of {} sharp)".format(len(filtered), len(indices)))

    return fillet_edges(brep, filtered, radius, tolerance)
