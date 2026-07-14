"""
BrepDifference.py
Robust Brep Difference operations with diagnostics and fallback strategies.

Primary use case: Subtracting inner finger model from shell to create hollow splint.
"""

import Rhino.Geometry as rg
import scriptcontext as sc
import math
from splintcommon import log

class BrepDifferenceError(Exception):
    """Raised when brep difference operation fails after all strategies."""
    pass


class InvalidBrepError(Exception):
    """Raised when input brep is None or invalid."""
    pass


class NoIntersectionError(Exception):
    """Raised when subtrahend does not intersect minuend."""
    pass

def get_brep_volume(brep):
    """Get brep volume, handling different return formats"""
    try:
        vol_result = brep.GetVolume()
        if isinstance(vol_result, (float, int)):
            return vol_result
        elif isinstance(vol_result, tuple) and len(vol_result) >= 2:
            if vol_result[0]:
                return vol_result[1]
        # Fallback to mass properties
        mp = rg.VolumeMassProperties.Compute(brep)
        if mp:
            return mp.Volume
    except:
        pass
    return None


def compute_intersection_volume(brep_a, brep_b, tolerance):
    """
    Compute the volume of intersection between two breps.
    Returns 0 if no intersection or on error.
    """
    try:
        intersection = rg.Brep.CreateBooleanIntersection(brep_a, brep_b, tolerance)
        if intersection and len(intersection) > 0:
            total_vol = 0.0
            for piece in intersection:
                vol = get_brep_volume(piece)
                if vol:
                    total_vol += vol
            return total_vol
    except Exception as e:
        log("  Intersection volume calc failed: {}".format(str(e)))
    return 0.0


def validate_difference_result(result_brep, minuend_brep, subtrahend_brep, intersection_vol=None,
                               tolerance_pct=10.0, min_result_fraction=None):
    """
    Validate boolean difference result quality.
    
    Args:
        result_brep: The result of minuend - subtrahend
        minuend_brep: Original brep being subtracted from (shell)
        subtrahend_brep: Brep being subtracted (inner)
        intersection_vol: Pre-computed intersection volume (optional)
        tolerance_pct: Allowed volume deviation percentage
        min_result_fraction: Optional lower bound on result_vol / minuend_vol. Guards against
            degenerate booleans (e.g. coincident faces returning the cutter itself) that collapse
            the minuend. Unlike the intersection_vol check it fires even when volume validation was
            'skipped', so a small chamfer cut that suddenly loses most of the solid is rejected.
    
    Returns:
        tuple: (is_valid: bool, issues: list of strings)
    """
    issues = []
    
    # Basic geometry checks
    if not result_brep.IsValid:
        issues.append("NotValid")
    if not result_brep.IsSolid:
        issues.append("NotSolid")
    if not result_brep.IsManifold:
        issues.append("NotManifold")
    
    # Check for naked edges
    naked_count = sum(1 for e in result_brep.Edges if e.Valence == rg.EdgeAdjacency.Naked)
    if naked_count > 0:
        issues.append("NakedEdges={}".format(naked_count))
    
    # Volume checks
    result_vol = get_brep_volume(result_brep)
    minuend_vol = get_brep_volume(minuend_brep)
    
    if result_vol and minuend_vol:
        # Result should be smaller than minuend (something was subtracted)
        vol_diff = minuend_vol - result_vol
        if vol_diff < 0.001:  # Less than 0.001 mm^3 removed
            issues.append("NothingSubtracted")

        # Degenerate-collapse guard: too much of the minuend vanished. Independent of
        # intersection_vol so it still catches a bad cut when volume validation was skipped.
        if min_result_fraction is not None and result_vol < minuend_vol * min_result_fraction:
            issues.append("ResultTooSmall={:.1f}%<{:.0f}%".format(
                100.0 * result_vol / minuend_vol, 100.0 * min_result_fraction))
        
        # If we have intersection volume, verify against expected
        if intersection_vol is not None and intersection_vol > 0:
            expected_vol = minuend_vol - intersection_vol
            if expected_vol > 0:
                volume_ratio = result_vol / expected_vol
                if volume_ratio < (1.0 - tolerance_pct / 100.0):
                    actual_loss_pct = (1.0 - volume_ratio) * 100.0
                    issues.append("VolumeError=-{:.1f}%".format(actual_loss_pct))
                elif volume_ratio > (1.0 + tolerance_pct / 100.0):
                    actual_gain_pct = (volume_ratio - 1.0) * 100.0
                    issues.append("VolumeError=+{:.1f}%".format(actual_gain_pct))
    
    return len(issues) == 0, issues


def attempt_boolean_difference(minuend, subtrahend, tolerance):
    """Attempt boolean difference operation"""
    try:
        result = rg.Brep.CreateBooleanDifference(minuend, subtrahend, tolerance)
        if result and len(result) > 0:
            # Usually returns single brep, but could be multiple pieces
            if len(result) == 1:
                return result[0]
            else:
                # Multiple pieces - try to join them
                log("  Difference returned {} pieces, attempting join".format(len(result)))
                joined = rg.Brep.JoinBreps(result, tolerance)
                if joined and len(joined) == 1:
                    return joined[0]
                # Return largest piece if join fails
                largest = max(result, key=lambda b: get_brep_volume(b) or 0)
                return largest
    except Exception as e:
        log("  Exception in difference: {}".format(str(e)))
    return None


def attempt_difference_with_lists(minuend, subtrahend, tolerance):
    """Attempt boolean difference using list-based API"""
    try:
        result = rg.Brep.CreateBooleanDifference([minuend], [subtrahend], tolerance)
        if result and len(result) > 0:
            if len(result) == 1:
                return result[0]
            largest = max(result, key=lambda b: get_brep_volume(b) or 0)
            return largest
    except Exception as e:
        log("  Exception in list-based difference: {}".format(str(e)))
    return None


def robust_brep_difference(minuend, subtrahend, base_tolerance=None, check_volumes=True,
                           min_result_fraction=None, allow_fallbacks=True):
    """
    Attempt brep boolean difference with multiple fallback strategies.
    
    Args:
        minuend: Brep to subtract FROM (e.g., shell)
        subtrahend: Brep to subtract (e.g., inner finger)
        base_tolerance: Base tolerance (uses doc tolerance if None)
        check_volumes: Validate volume conservation
        min_result_fraction: Optional lower bound on result_vol / minuend_vol (e.g. 0.5 for a
            small chamfer cut). Rejects a degenerate boolean that collapses the minuend even when
            the intersection-volume validation was skipped, so a bad cut raises instead of
            silently returning the cutter. Leave None for large cuts (e.g. hollowing).
        allow_fallbacks: When False, run only the direct/list/tolerance strategies and then fail
            fast - skips the expensive jiggle/repair/mesh strategies. Use for clean cutters (e.g.
            chamfer wedges) where those heroics only waste time and can distort the result.
    
    Returns:
        tuple: (result_brep, success, method_used)
    """
    
    # Validate inputs
    if minuend is None:
        raise InvalidBrepError("Minuend brep is None")
    if subtrahend is None:
        raise InvalidBrepError("Subtrahend brep is None")
    if not minuend.IsValid:
        raise InvalidBrepError("Minuend brep is invalid (IsValid=False)")
    if not subtrahend.IsValid:
        raise InvalidBrepError("Subtrahend brep is invalid (IsValid=False)")
    
    # Use document tolerance if not specified
    if base_tolerance is None or base_tolerance <= 0:
        base_tolerance = sc.doc.ModelAbsoluteTolerance
    
    log("=" * 60)
    log("ROBUST BOOLEAN DIFFERENCE")
    log("=" * 60)
    
    minuend_vol = get_brep_volume(minuend)
    subtrahend_vol = get_brep_volume(subtrahend)
    log("Minuend volume: {:.3f}".format(minuend_vol if minuend_vol else 0))
    log("Subtrahend volume: {:.3f}".format(subtrahend_vol if subtrahend_vol else 0))
    
    # Compute intersection volume - this is what actually gets subtracted
    intersection_vol = compute_intersection_volume(minuend, subtrahend, base_tolerance)
    log("Intersection volume: {:.3f}".format(intersection_vol))
    
    # If intersection volume is 0, check bounding box overlap before giving up.
    # CreateBooleanIntersection can fail on self-intersecting breps even when
    # the geometry clearly overlaps (e.g. concentric finger models).
    skip_volume_validation = False
    if intersection_vol < 0.001:
        bb_a = minuend.GetBoundingBox(True)
        bb_b = subtrahend.GetBoundingBox(True)
        bb_overlap = not (
            bb_a.Min.X > bb_b.Max.X or bb_b.Min.X > bb_a.Max.X or
            bb_a.Min.Y > bb_b.Max.Y or bb_b.Min.Y > bb_a.Max.Y or
            bb_a.Min.Z > bb_b.Max.Z or bb_b.Min.Z > bb_a.Max.Z
        )
        if bb_overlap:
            log("WARNING: Intersection volume=0 but bounding boxes overlap.")
            log("  Likely caused by self-intersecting input breps.")
            log("  Proceeding with difference (volume validation disabled).")
            skip_volume_validation = True
        else:
            log("ERROR: No intersection between minuend and subtrahend!")
            log("  Bounding boxes do not overlap - nothing to subtract.")
            raise NoIntersectionError(
                "Subtrahend does not intersect minuend (intersection volume < 0.001 mm^3, "
                "bounding boxes disjoint). Check that both breps occupy overlapping regions."
            )
    
    expected_result_vol = minuend_vol - intersection_vol if not skip_volume_validation else None
    if expected_result_vol is not None:
        log("Expected result volume: ~{:.3f}  (minuend - intersection)".format(expected_result_vol))
    
    # Track best result across all attempts
    best_result = None
    best_issues = None
    
    # When volume validation is skipped, pass None so validate_difference_result
    # doesn't try to check against a bogus intersection volume
    validation_vol = None if skip_volume_validation else intersection_vol
    
    # STRATEGY 1: Direct difference at base tolerance
    log("")
    log("-" * 60)
    log("STRATEGY 1: Direct difference (tol={:.6f})".format(base_tolerance))
    log("-" * 60)
    
    result = attempt_boolean_difference(minuend, subtrahend, base_tolerance)
    if result:
        result_vol = get_brep_volume(result)
        log("Result volume: {:.3f}".format(result_vol if result_vol else 0))
        is_valid, issues = validate_difference_result(result, minuend, subtrahend, validation_vol,
                                                      min_result_fraction=min_result_fraction)
        if is_valid:
            log("SUCCESS - Clean boolean difference")
            return result, True, "Difference(tol={:.6f})".format(base_tolerance)
        else:
            log("Result has issues: {}".format(", ".join(issues)))
            best_result = result
            best_issues = issues
    else:
        log("No result returned")
    
    # STRATEGY 2: Try list-based API
    log("")
    log("-" * 60)
    log("STRATEGY 2: List-based API (tol={:.6f})".format(base_tolerance))
    log("-" * 60)
    
    result = attempt_difference_with_lists(minuend, subtrahend, base_tolerance)
    if result:
        result_vol = get_brep_volume(result)
        log("Result volume: {:.3f}".format(result_vol if result_vol else 0))
        is_valid, issues = validate_difference_result(result, minuend, subtrahend, validation_vol,
                                                      min_result_fraction=min_result_fraction)
        if is_valid:
            log("SUCCESS - List-based difference")
            return result, True, "DifferenceList(tol={:.6f})".format(base_tolerance)
        else:
            log("Result has issues: {}".format(", ".join(issues)))
            if best_result is None or len(issues) < len(best_issues):
                best_result = result
                best_issues = issues
    else:
        log("No result returned")
    
    # STRATEGY 3: Tolerance escalation
    log("")
    log("-" * 60)
    log("STRATEGY 3: Tolerance escalation")
    log("-" * 60)
    
    for tol in [base_tolerance * 10, 0.01, 0.1]:
        if tol <= base_tolerance:
            continue
        
        log("  Trying tolerance: {:.6f}".format(tol))
        result = attempt_boolean_difference(minuend, subtrahend, tol)
        if result:
            is_valid, issues = validate_difference_result(result, minuend, subtrahend, validation_vol, tolerance_pct=15.0, min_result_fraction=min_result_fraction)
            if is_valid:
                log("SUCCESS - Difference at higher tolerance")
                return result, True, "Difference(tol={:.6f})".format(tol)
            else:
                log("  Result has issues: {}".format(", ".join(issues)))
                if best_result is None or len(issues) < len(best_issues):
                    best_result = result
                    best_issues = issues
    
    # Fast-fail gate for clean cutters (e.g. chamfer wedges). If direct/list/tolerance did not cut
    # cleanly, the cutter itself is wrong; jiggling, repairing, and mesh-booleaning it just burns
    # seconds per rail and mesh boolean can distort the part. Accept a solid best-effort result
    # (unless it collapsed the minuend), otherwise fail fast instead of running strategies 4-6.
    if not allow_fallbacks:
        too_small = any(i.startswith("ResultTooSmall") for i in (best_issues or []))
        if best_result is not None and best_result.IsSolid and not too_small:
            log("Fallbacks disabled - accepting best solid result ({})".format(
                ", ".join(best_issues)))
            return best_result, True, "Imperfect({})".format(",".join(best_issues))
        log("Fallbacks disabled - no clean difference; failing fast (skipping strategies 4-6)")
        raise BrepDifferenceError(
            "Boolean difference failed with fallbacks disabled (clean-cutter path). "
            "Minuend vol={:.1f}, Subtrahend vol={:.1f}, Intersection vol={:.1f}".format(
                minuend_vol or 0, subtrahend_vol or 0, intersection_vol))

    # STRATEGY 4: Jiggle subtrahend slightly
    log("")
    log("-" * 60)
    log("STRATEGY 4: Jiggle subtrahend")
    log("-" * 60)
    
    jiggle_offsets = [0.001, 0.005, 0.01]
    jiggle_vectors = [
        rg.Vector3d(0.577, 0.577, 0.577),  # diagonal
        rg.Vector3d(0, 0, 1),  # Z-axis
        rg.Vector3d(1, 0, 0),  # X-axis
    ]
    
    for offset_dist in jiggle_offsets:
        for vec in jiggle_vectors:
            try:
                jiggled_subtrahend = subtrahend.Duplicate()
                translation = rg.Transform.Translation(vec * offset_dist)
                jiggled_subtrahend.Transform(translation)
                
                result = attempt_boolean_difference(minuend, jiggled_subtrahend, base_tolerance)
                if result:
                    is_valid, issues = validate_difference_result(result, minuend, subtrahend, validation_vol, tolerance_pct=15.0, min_result_fraction=min_result_fraction)
                    if is_valid:
                        log("SUCCESS - Jiggle {:.4f}mm worked".format(offset_dist))
                        return result, True, "Jiggled({:.4f}mm)".format(offset_dist)
                    else:
                        log("  Jiggle {:.4f}mm: {}".format(offset_dist, ", ".join(issues)))
                        if best_result is None or len(issues) < len(best_issues):
                            best_result = result
                            best_issues = issues
            except:
                pass
    
    # STRATEGY 5: Repair inputs and retry
    log("")
    log("-" * 60)
    log("STRATEGY 5: Repair inputs")
    log("-" * 60)
    
    try:
        fixed_minuend = minuend.Duplicate()
        fixed_subtrahend = subtrahend.Duplicate()
        
        # Repair operations
        fixed_minuend.Faces.SplitKinkyFaces(sc.doc.ModelAngleToleranceRadians, True)
        fixed_minuend.Compact()
        fixed_subtrahend.Faces.SplitKinkyFaces(sc.doc.ModelAngleToleranceRadians, True)
        fixed_subtrahend.Compact()
        
        result = attempt_boolean_difference(fixed_minuend, fixed_subtrahend, base_tolerance * 10)
        if result:
            is_valid, issues = validate_difference_result(result, minuend, subtrahend, validation_vol, tolerance_pct=20.0, min_result_fraction=min_result_fraction)
            if is_valid:
                log("SUCCESS - Repaired inputs worked")
                return result, True, "Repaired"
            else:
                log("Result has issues: {}".format(", ".join(issues)))
                if best_result is None or len(issues) < len(best_issues):
                    best_result = result
                    best_issues = issues
    except Exception as e:
        log("Repair failed: {}".format(str(e)))
    
    # STRATEGY 6: Mesh boolean difference
    # Convert both breps to meshes and use mesh boolean, which is much more
    # robust when brep booleans fail due to self-intersecting NURBS trim curves
    log("")
    log("-" * 60)
    log("STRATEGY 6: Mesh boolean difference")
    log("-" * 60)
    
    try:
        mesh_params = rg.MeshingParameters.DefaultAnalysisMesh
        
        minuend_meshes = rg.Mesh.CreateFromBrep(minuend, mesh_params)
        subtrahend_meshes = rg.Mesh.CreateFromBrep(subtrahend, mesh_params)
        
        if minuend_meshes and subtrahend_meshes:
            # Join mesh arrays into single meshes
            mesh_a = rg.Mesh()
            for m in minuend_meshes:
                mesh_a.Append(m)
            mesh_a.Weld(math.pi)
            
            mesh_b = rg.Mesh()
            for m in subtrahend_meshes:
                mesh_b.Append(m)
            mesh_b.Weld(math.pi)
            
            log("  Minuend mesh: {} vertices, {} faces".format(
                mesh_a.Vertices.Count, mesh_a.Faces.Count))
            log("  Subtrahend mesh: {} vertices, {} faces".format(
                mesh_b.Vertices.Count, mesh_b.Faces.Count))
            
            diff_meshes = rg.Mesh.CreateBooleanDifference(
                [mesh_a], [mesh_b]
            )
            
            if diff_meshes and len(diff_meshes) > 0:
                result_mesh = diff_meshes[0]
                if len(diff_meshes) > 1:
                    for dm in diff_meshes[1:]:
                        result_mesh.Append(dm)
                
                result_mesh.Weld(math.pi)
                result_mesh.RebuildNormals()
                
                log("  Result mesh: {} vertices, {} faces".format(
                    result_mesh.Vertices.Count, result_mesh.Faces.Count))
                
                # Convert back to brep for consistent return type
                result_brep = rg.Brep.CreateFromMesh(result_mesh, True)
                if result_brep:
                    # Degenerate-collapse guard: the mesh path is otherwise unvalidated, so a
                    # coincident-face cut could still slip a collapsed solid through here.
                    rv = get_brep_volume(result_brep)
                    if (min_result_fraction is not None and rv and minuend_vol
                            and rv < minuend_vol * min_result_fraction):
                        log("  Mesh boolean rejected: result {:.3f} < {:.0f}% of minuend".format(
                            rv, 100.0 * min_result_fraction))
                    else:
                        log("SUCCESS - Mesh boolean difference")
                        return result_brep, True, "MeshBoolean"
                else:
                    log("  Mesh boolean succeeded but conversion to brep failed")
                    log("  Returning mesh wrapped as brep")
                    # Last resort: return the mesh-as-brep even if imperfect
            else:
                log("  Mesh boolean returned no results")
        else:
            log("  Failed to create meshes from input breps")
    except Exception as e:
        log("  Mesh boolean failed: {}".format(str(e)))
    
    # STRATEGY 7: Return best imperfect result if we have one
    if best_result is not None:
        log("")
        log("-" * 60)
        log("FALLBACK: Returning best imperfect result")
        log("-" * 60)
        log("Issues: {}".format(", ".join(best_issues)))
        
        # Accept if it's at least solid - unless it collapsed the minuend (degenerate cut), which
        # we never want to hand back silently.
        too_small = any(i.startswith("ResultTooSmall") for i in best_issues)
        if best_result.IsSolid and not too_small:
            log("Result is solid - accepting with issues")
            return best_result, True, "Imperfect({})".format(",".join(best_issues))
        if too_small:
            log("Best result collapses the minuend - refusing degenerate difference")
    
    log("")
    log("=" * 60)
    log("FAILED - All strategies exhausted")
    log("=" * 60)
    
    raise BrepDifferenceError(
        "Failed to compute boolean difference after all strategies. "
        "Minuend vol={:.1f}, Subtrahend vol={:.1f}, Intersection vol={:.1f}".format(
            minuend_vol or 0, subtrahend_vol or 0, intersection_vol
        )
    )
