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


def _coerce_brep(obj):
    """Coerce common GH/Rhino inputs (Guid, Brep, Surface, etc.) to Brep.

    Returns:
        tuple: (brep_or_none, method_string)
    """
    if obj is None:
        return None, "None"

    if isinstance(obj, rg.Brep):
        return obj, "AlreadyBrep"

    if isinstance(obj, rg.Extrusion):
        try:
            brep = obj.ToBrep()
            if brep is not None:
                return brep, "Extrusion.ToBrep"
        except Exception:
            pass

    if isinstance(obj, rg.Surface):
        try:
            brep = obj.ToBrep()
            if brep is not None:
                return brep, "Surface.ToBrep"
        except Exception:
            pass

    # Handles Guid/object-id and many GH wrapper cases in active Rhino document.
    try:
        import rhinoscriptsyntax as rs
        brep = rs.coercebrep(obj)
        if brep is not None:
            return brep, "rhinoscriptsyntax.coercebrep"
    except Exception:
        pass

    return None, "Uncoercible:{}".format(type(obj).__name__)


def _find_interior_self_intersections(
        brep,
        intersection_tolerance=0.01,
        edge_distance_tolerance=0.1):
    """Find likely interior self-intersections by scanning face pairs.

    Returns:
        tuple: (interior_intersections_found, tested_face_pairs)
    """
    interior_intersections_found = []
    tested_face_pairs = 0

    for i, faceA in enumerate(brep.Faces):
        for j, faceB in enumerate(brep.Faces):
            if i >= j:
                continue

            tested_face_pairs += 1
            try:
                result = rg.Intersect.Intersection.SurfaceSurface(
                    faceA.UnderlyingSurface(),
                    faceB.UnderlyingSurface(),
                    intersection_tolerance
                )
                if not result[0]:
                    continue

                curves = result[1]
                if not curves:
                    continue

                # Check if each intersection curve stays near a boundary edge.
                for crv in curves:
                    if not crv:
                        continue

                    is_at_edge = True
                    for t in [0.0, 0.5, 1.0]:
                        test_pt = crv.PointAt(crv.Domain.ParameterAt(t))

                        closest_edge_dist = float('inf')
                        for edge in brep.Edges:
                            edge_param = edge.ClosestPoint(test_pt, 0.0)[1]
                            edge_pt = edge.PointAt(edge_param)
                            dist = test_pt.DistanceTo(edge_pt)
                            closest_edge_dist = min(closest_edge_dist, dist)

                        if closest_edge_dist > edge_distance_tolerance:
                            is_at_edge = False
                            break

                    if not is_at_edge:
                        interior_intersections_found.append((i, j))
                        break
            except Exception:
                # Conservative: ignore pair-level failures and continue scan.
                pass

    return interior_intersections_found, tested_face_pairs


def inspect_solid_brep(
        brep,
        verbose=True,
        check_self_intersections=False,
        fail_on_self_intersection=False,
        self_intersection_tolerance=0.01,
        edge_distance_tolerance=0.1):
    """
    Inspect a brep and determine if it's usable for boolean unions.
    
    Args:
        brep: Rhino.Geometry.Brep or coerceable GH/Rhino object (e.g. Guid)
        verbose: Print detailed diagnostic information
        check_self_intersections: Run expensive face-pair self-intersection scan.
        fail_on_self_intersection: If True, mark brep unusable when interior
            self-intersections are detected. If False, log warning only.
        self_intersection_tolerance: Surface-surface intersection tolerance.
        edge_distance_tolerance: Max distance to edges for classifying
            intersection curves as edge-adjacent.
    
    Returns:
        bool: True if usable for boolean operations
    """
    
    if brep is None:
        log("ERROR: No brep provided")
        return False

    input_type = type(brep).__name__
    brep, coercion_method = _coerce_brep(brep)
    if brep is None:
        log("ERROR: Could not coerce input to Brep (got {})".format(input_type))
        return False
    if coercion_method != "AlreadyBrep":
        log("inspect_solid_brep: coerced {} -> Brep via {}".format(
            input_type, coercion_method))
    
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
    
    if check_self_intersections:
        if verbose:
            log("")
            log("=" * 60)
            log("SELF-INTERSECTION CHECK")
            log("=" * 60)
            log("Mode: {}".format(
                "FAIL" if fail_on_self_intersection else "WARN_ONLY"))
            log("IntersectionTolerance: {}".format(self_intersection_tolerance))
            log("EdgeDistanceTolerance: {}".format(edge_distance_tolerance))

        interior_intersections_found, tested_face_pairs = _find_interior_self_intersections(
            brep,
            intersection_tolerance=self_intersection_tolerance,
            edge_distance_tolerance=edge_distance_tolerance,
        )

        if verbose:
            log("Face pairs tested: {}".format(tested_face_pairs))

        if len(interior_intersections_found) > 0:
            log("INTERIOR SELF-INTERSECTIONS: {}".format(len(interior_intersections_found)))
            if fail_on_self_intersection:
                is_usable = False
                log("  -> UNUSABLE: Interior self-intersections detected")
            else:
                log("  -> WARNING: Interior self-intersections detected (not failing)")
        elif verbose:
            log("No interior self-intersections detected")
    elif verbose:
        log("Self-intersection check skipped")
    
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


def inspect_solid_breps(
    breps,
    verbose=True,
    stop_on_first_fail=False,
    check_self_intersections=False,
    fail_on_self_intersection=False,
    self_intersection_tolerance=0.01,
    edge_distance_tolerance=0.1):
    """Inspect multiple breps and return True only if all inspected breps are usable.

    Args:
        breps: Iterable of Rhino.Geometry.Brep objects (or a single Brep).
        verbose: Passed through to inspect_solid_brep.
        stop_on_first_fail: If True, stop as soon as one brep fails.
        check_self_intersections: Passed through to inspect_solid_brep.
        fail_on_self_intersection: Passed through to inspect_solid_brep.
        self_intersection_tolerance: Passed through to inspect_solid_brep.
        edge_distance_tolerance: Passed through to inspect_solid_brep.

    Returns:
        bool: True when every inspected brep is usable.
    """
    if breps is None:
        log("ERROR: inspect_solid_breps received no breps")
        return False

    if isinstance(breps, rg.Brep):
        log("inspect_solid_breps: single Brep provided, wrapping as 1-item list")
        brep_list = [breps]
    else:
        try:
            brep_list = list(breps)
        except TypeError:
            log("ERROR: inspect_solid_breps expected an iterable (got {})".format(
                type(breps).__name__))
            return False

    if not brep_list:
        log("ERROR: inspect_solid_breps received an empty brep list")
        return False

    log("")
    log("=" * 60)
    log("BATCH BREP INSPECTION")
    log("Count: {}".format(len(brep_list)))
    log("StopOnFirstFail: {}".format(stop_on_first_fail))
    log("CheckSelfIntersections: {}".format(check_self_intersections))
    log("FailOnSelfIntersection: {}".format(fail_on_self_intersection))
    if check_self_intersections:
        log("SelfIntersectionTolerance: {}".format(self_intersection_tolerance))
        log("EdgeDistanceTolerance: {}".format(edge_distance_tolerance))
    log("=" * 60)

    failed_indices = []
    inspected_count = 0

    for i, brep in enumerate(brep_list):
        inspected_count += 1
        log("")
        log("--- Inspecting brep {}/{} (index {}) ---".format(
            i + 1, len(brep_list), i))
        log("Input type: {}".format(type(brep).__name__))

        if brep is None:
            log("  -> UNUSABLE: Brep is None")
            failed_indices.append(i)
        else:
            is_usable = inspect_solid_brep(
                brep,
                verbose=verbose,
                check_self_intersections=check_self_intersections,
                fail_on_self_intersection=fail_on_self_intersection,
                self_intersection_tolerance=self_intersection_tolerance,
                edge_distance_tolerance=edge_distance_tolerance,
            )
            if is_usable:
                log("  -> RESULT: index {} PASSED".format(i))
            else:
                log("  -> RESULT: index {} FAILED".format(i))
                failed_indices.append(i)

        if stop_on_first_fail and failed_indices:
            log("Stopping batch inspection early after failure at index {}".format(
                failed_indices[-1]))
            break

    pass_count = inspected_count - len(failed_indices)
    all_usable = (len(failed_indices) == 0) and (inspected_count == len(brep_list))

    log("")
    log("=" * 60)
    log("BATCH INSPECTION SUMMARY")
    log("Inspected: {}/{}".format(inspected_count, len(brep_list)))
    log("Passed: {}".format(pass_count))
    log("Failed: {}".format(len(failed_indices)))
    if failed_indices:
        log("Failed indices: {}".format(failed_indices))
    if inspected_count < len(brep_list):
        log("Note: inspection stopped early before visiting all inputs")
    log("VERDICT: {}".format("ALL USABLE" if all_usable else "NOT ALL USABLE"))
    log("=" * 60)

    return all_usable
