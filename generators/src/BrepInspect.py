"""
Grasshopper Python Component: Brep Inspection Validator

INPUTS:
    Brep: Brep to inspect (Brep)
    Verbose: Print detailed diagnostic info (bool, default: True)

OUTPUTS:
    IsValid: True if brep is usable for boolean operations (bool)
"""

import Rhino.Geometry as rg
import scriptcontext as sc
from splintcommon import log

def inspect_solid_brep(brep, verbose=True):
    """
    Inspect a brep and determine if it's usable for boolean unions.
    
    Args:
        brep: Rhino.Geometry.Brep to inspect
        verbose: Print detailed diagnostic information
    
    Returns:
        bool: True if usable for boolean operations
    """
    
    if not brep:
        log("ERROR: No brep provided")
        return False
    
    # Track usability
    is_usable = True
    
    log("=" * 60)
    log("BREP INSPECTION")
    log("=" * 60)
    log("Faces: {}".format(brep.Faces.Count))
    log("Valid: {}".format(brep.IsValid))
    log("Solid: {}".format(brep.IsSolid))
    log("Manifold: {}".format(brep.IsManifold))
    
    # Check basic requirements
    if not brep.IsValid:
        is_usable = False
        log("  -> UNUSABLE: Brep is not valid")
    if not brep.IsSolid:
        is_usable = False
        log("  -> UNUSABLE: Brep is not solid")
    if not brep.IsManifold:
        is_usable = False
        log("  -> UNUSABLE: Brep is not manifold")
    
    # Check for self-intersections between faces (only if verbose)
    if verbose:
        log("")
        log("=" * 60)
        log("SELF-INTERSECTION CHECK")
        log("=" * 60)
    
    interior_intersections_found = []
    
    for i, faceA in enumerate(brep.Faces):
        for j, faceB in enumerate(brep.Faces):
            if i < j:
                try:
                    result = rg.Intersect.Intersection.SurfaceSurface(
                        faceA.UnderlyingSurface(), 
                        faceB.UnderlyingSurface(), 
                        0.01
                    )
                    if result[0]:
                        curves = result[1]
                        if curves and len(curves) > 0:
                            # Check if intersection is at edge (normal) or interior (problem)
                            for crv in curves:
                                if crv:
                                    is_at_edge = True
                                    for t in [0.0, 0.5, 1.0]:
                                        test_pt = crv.PointAt(crv.Domain.ParameterAt(t))
                                        
                                        closest_edge_dist = float('inf')
                                        for edge in brep.Edges:
                                            edge_param = edge.ClosestPoint(test_pt, 0.0)[1]
                                            edge_pt = edge.PointAt(edge_param)
                                            dist = test_pt.DistanceTo(edge_pt)
                                            closest_edge_dist = min(closest_edge_dist, dist)
                                        
                                        if closest_edge_dist > 0.1:
                                            is_at_edge = False
                                            break
                                    
                                    if not is_at_edge:
                                        interior_intersections_found.append((i, j))
                                        is_usable = False
                                        break
                except:
                    pass
    
    if len(interior_intersections_found) > 0:
        log("INTERIOR SELF-INTERSECTIONS: {}".format(len(interior_intersections_found)))
        log("  -> UNUSABLE: Interior self-intersections detected")
    elif verbose:
        log("No interior self-intersections detected")
    
    # Check edges
    naked_edges = [e for e in brep.Edges if e.Valence == rg.EdgeAdjacency.Naked]
    
    if len(naked_edges) > 0:
        is_usable = False
        log("  -> UNUSABLE: {} naked edges (solid should have none)".format(len(naked_edges)))
    
    # Final verdict
    log("")
    log("=" * 60)
    if is_usable:
        log("VERDICT: USABLE for boolean unions")
    else:
        log("VERDICT: NOT USABLE - see issues above")
    log("=" * 60)
    
    return is_usable
