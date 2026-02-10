"""
BrepDifference.py
Robust Brep Difference operations with diagnostics and fallback strategies.

Primary use case: Subtracting inner finger model from shell to create hollow splint.
"""

import Rhino.Geometry as rg
import scriptcontext as sc
from splintcommon import log


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


def validate_difference_result(result_brep, minuend_brep, subtrahend_brep, tolerance_pct=10.0):
    """
    Validate boolean difference result quality.
    
    Args:
        result_brep: The result of minuend - subtrahend
        minuend_brep: Original brep being subtracted from (shell)
        subtrahend_brep: Brep being subtracted (inner)
        tolerance_pct: Allowed volume deviation percentage
    
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
    
    # Volume check: result should be less than minuend
    result_vol = get_brep_volume(result_brep)
    minuend_vol = get_brep_volume(minuend_brep)
    subtrahend_vol = get_brep_volume(subtrahend_brep)
    
    if result_vol and minuend_vol:
        if result_vol >= minuend_vol:
            issues.append("ResultNotSmaller")
        
        # Expected: minuend_vol - subtrahend_vol (approximately)
        if subtrahend_vol:
            expected_vol = minuend_vol - subtrahend_vol
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


def robust_brep_difference(minuend, subtrahend, base_tolerance=None, check_volumes=True):
    """
    Attempt brep boolean difference with multiple fallback strategies.
    
    Args:
        minuend: Brep to subtract FROM (e.g., shell)
        subtrahend: Brep to subtract (e.g., inner finger)
        base_tolerance: Base tolerance (uses doc tolerance if None)
        check_volumes: Validate volume conservation
    
    Returns:
        tuple: (result_brep, success, method_used)
    """
    
    # Validate inputs
    if minuend is None:
        log("ERROR: Minuend brep is None")
        return None, False, "None"
    if subtrahend is None:
        log("ERROR: Subtrahend brep is None")
        return None, False, "None"
    if not minuend.IsValid:
        log("ERROR: Minuend brep is invalid")
        return None, False, "InvalidInput"
    if not subtrahend.IsValid:
        log("ERROR: Subtrahend brep is invalid")
        return None, False, "InvalidInput"
    
    # Use document tolerance if not specified
    if base_tolerance is None or base_tolerance <= 0:
        base_tolerance = sc.doc.ModelAbsoluteTolerance
    
    log("=" * 60)
    log("ROBUST BOOLEAN DIFFERENCE")
    log("=" * 60)
    
    minuend_vol = get_brep_volume(minuend)
    subtrahend_vol = get_brep_volume(subtrahend)
    log("Minuend (shell) volume: {:.3f}".format(minuend_vol if minuend_vol else 0))
    log("Subtrahend (inner) volume: {:.3f}".format(subtrahend_vol if subtrahend_vol else 0))
    if minuend_vol and subtrahend_vol:
        log("Expected result volume: ~{:.3f}".format(minuend_vol - subtrahend_vol))
    
    # Track best result across all attempts
    best_result = None
    best_issues = None
    
    # STRATEGY 1: Direct difference at base tolerance
    log("")
    log("-" * 60)
    log("STRATEGY 1: Direct difference (tol={:.6f})".format(base_tolerance))
    log("-" * 60)
    
    result = attempt_boolean_difference(minuend, subtrahend, base_tolerance)
    if result:
        result_vol = get_brep_volume(result)
        log("Result volume: {:.3f}".format(result_vol if result_vol else 0))
        is_valid, issues = validate_difference_result(result, minuend, subtrahend)
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
        is_valid, issues = validate_difference_result(result, minuend, subtrahend)
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
            is_valid, issues = validate_difference_result(result, minuend, subtrahend, tolerance_pct=15.0)
            if is_valid:
                log("SUCCESS - Difference at higher tolerance")
                return result, True, "Difference(tol={:.6f})".format(tol)
            else:
                log("  Result has issues: {}".format(", ".join(issues)))
                if best_result is None or len(issues) < len(best_issues):
                    best_result = result
                    best_issues = issues
    
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
                    is_valid, issues = validate_difference_result(result, minuend, subtrahend, tolerance_pct=15.0)
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
            is_valid, issues = validate_difference_result(result, minuend, subtrahend, tolerance_pct=20.0)
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
    
    # STRATEGY 6: Return best imperfect result if we have one
    if best_result is not None:
        log("")
        log("-" * 60)
        log("FALLBACK: Returning best imperfect result")
        log("-" * 60)
        log("Issues: {}".format(", ".join(best_issues)))
        
        # Accept if it's at least solid
        if best_result.IsSolid:
            log("Result is solid - accepting with issues")
            return best_result, True, "Imperfect({})".format(",".join(best_issues))
    
    log("")
    log("=" * 60)
    log("FAILED - All strategies exhausted")
    log("=" * 60)
    
    return None, False, "AllFailed"
