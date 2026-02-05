"""
Grasshopper Python Component: Robust Brep Union

INPUTS:
    Breps: List of breps to union (list of Brep)
    Tolerance: Base tolerance (float, default: doc tolerance)
    CheckVolumes: Validate volume conservation (bool, default: True)

OUTPUTS:
    Result: Union result brep (Brep)
    Success: True if union succeeded (bool)
    Method: Which method succeeded (string)
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

def get_intersection_volume(brepA, brepB, tolerance):
    """Estimate intersection volume"""
    try:
        intersection = rg.Brep.CreateBooleanIntersection([brepA], [brepB], tolerance)
        if intersection and len(intersection) > 0:
            return get_brep_volume(intersection[0])
    except:
        pass
    return 0.0

def get_total_volume(breps):
    """Calculate total volume of multiple breps"""
    total = 0.0
    for brep in breps:
        vol = get_brep_volume(brep)
        if vol:
            total += vol
    return total

def validate_union_result_multi(result_brep, input_breps, tolerance_pct=5.0, base_tolerance=0.001):
    """
    Validate union result quality for multiple input breps
    
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
    
    # Check for interior self-intersections (lighter check - just sample a few face pairs)
    self_intersect_count = 0
    face_count = result_brep.Faces.Count
    # Only check up to 50 face pairs to keep it fast
    max_checks = min(50, (face_count * (face_count - 1)) // 2)
    checks_done = 0
    
    for i, faceA in enumerate(result_brep.Faces):
        for j, faceB in enumerate(result_brep.Faces):
            if i < j and checks_done < max_checks:
                checks_done += 1
                try:
                    result_check = rg.Intersect.Intersection.SurfaceSurface(
                        faceA.UnderlyingSurface(), 
                        faceB.UnderlyingSurface(), 
                        0.01
                    )
                    if result_check[0]:
                        curves = result_check[1]
                        if curves and len(curves) > 0:
                            # Quick check - just test middle point
                            for crv in curves:
                                if crv:
                                    test_pt = crv.PointAt(crv.Domain.ParameterAt(0.5))
                                    closest_edge_dist = float('inf')
                                    for edge in result_brep.Edges:
                                        edge_param = edge.ClosestPoint(test_pt, 0.0)[1]
                                        edge_pt = edge.PointAt(edge_param)
                                        dist = test_pt.DistanceTo(edge_pt)
                                        closest_edge_dist = min(closest_edge_dist, dist)
                                    if closest_edge_dist > 0.1:
                                        self_intersect_count += 1
                                        break
                except:
                    pass
    
    if self_intersect_count > 0:
        issues.append("SelfIntersections={}".format(self_intersect_count))
    
    # Volume check - account for overlaps between input breps
    result_volume = get_brep_volume(result_brep)
    total_input_volume = get_total_volume(input_breps)
    
    if result_volume is not None and total_input_volume > 0:
        # Calculate total intersection volume (pairwise)
        total_intersection_volume = 0.0
        for i in range(len(input_breps)):
            for j in range(i + 1, len(input_breps)):
                try:
                    intersection_vol = get_intersection_volume(input_breps[i], input_breps[j], base_tolerance)
                    if intersection_vol:
                        total_intersection_volume += intersection_vol
                except:
                    pass
        
        # Expected volume = sum of inputs - overlaps
        expected_volume = total_input_volume - total_intersection_volume
        
        # Allow some tolerance for the comparison
        if expected_volume > 0:
            volume_ratio = result_volume / expected_volume
            
            if volume_ratio < (1.0 - tolerance_pct / 100.0):  # Lost more than tolerance
                actual_loss_pct = (1.0 - volume_ratio) * 100.0
                issues.append("VolumeError={:.1f}%".format(actual_loss_pct))
            elif volume_ratio > (1.0 + tolerance_pct / 100.0):  # Gained volume (shouldn't happen)
                actual_gain_pct = (volume_ratio - 1.0) * 100.0
                issues.append("VolumeError=+{:.1f}%".format(actual_gain_pct))
    
    return len(issues) == 0, issues

def attempt_multi_union(breps, tolerance):
    """Attempt multi-brep boolean union in one operation"""
    try:
        result = rg.Brep.CreateBooleanUnion(breps, tolerance)
        if result and len(result) > 0:
            return result[0]
    except:
        pass
    return None

def attempt_mesh_union(brepA, brepB):
    """Attempt mesh-based boolean union as fallback"""
    try:
        # Convert to meshes
        mesh_params = rg.MeshingParameters.Default
        mesh_params.MinimumEdgeLength = 0.1
        mesh_params.MaximumEdgeLength = 2.0
        
        meshA = rg.Mesh.CreateFromBrep(brepA, mesh_params)
        meshB = rg.Mesh.CreateFromBrep(brepB, mesh_params)
        
        if meshA and len(meshA) > 0 and meshB and len(meshB) > 0:
            # Join meshes
            mesh_union = rg.Mesh()
            for m in meshA:
                mesh_union.Append(m)
            for m in meshB:
                mesh_union.Append(m)
            
            # Try mesh boolean
            union_result = rg.Mesh.CreateBooleanUnion([meshA[0]], [meshB[0]])
            if union_result and len(union_result) > 0:
                # Convert back to brep
                brep_result = rg.Brep.CreateFromMesh(union_result[0], False)
                if brep_result:
                    return brep_result
    except:
        pass
    return None

def robust_brep_union(breps, base_tolerance=None, check_volumes=True):
    """
    Attempt brep union with multiple fallback strategies.
    Supports multiple breps - tries all-at-once first, then fallback strategies.
    
    Args:
        breps: List of Rhino.Geometry.Brep objects to union
        base_tolerance: Base tolerance (uses doc tolerance if None)
        check_volumes: Validate volume conservation
    
    Returns:
        tuple: (result_brep, success, method_used)
    """
    
    # Handle both list and individual brep inputs for backwards compatibility
    if not isinstance(breps, list):
        breps = [breps]
    
    # Filter out None/invalid breps
    valid_breps = []
    for i, brep in enumerate(breps):
        if brep is None:
            log("WARNING: Brep {} is None - skipping".format(i))
        elif not brep.IsValid:
            log("WARNING: Brep {} is invalid - skipping".format(i))
        else:
            valid_breps.append(brep)
    
    if len(valid_breps) == 0:
        log("ERROR: No valid breps provided (all None or invalid)")
        return None, False, "None"
    
    if len(valid_breps) != len(breps):
        log("WARNING: Filtered {} invalid breps, {} valid remain".format(len(breps) - len(valid_breps), len(valid_breps)))
    
    breps = valid_breps
    
    if len(breps) == 1:
        log("WARNING: Only one valid brep, returning as-is")
        return breps[0], True, "SingleBrep"
    
    # Use document tolerance if not specified
    if base_tolerance is None or base_tolerance <= 0:
        base_tolerance = sc.doc.ModelAbsoluteTolerance
    
    log("=" * 60)
    log("ROBUST MULTI-BREP UNION ({} breps)".format(len(breps)))
    log("=" * 60)
    
    # Log volumes for each input
    total_volume = 0.0
    for i, brep in enumerate(breps):
        vol = get_brep_volume(brep)
        log("Brep {} volume: {:.3f}".format(i, vol if vol else 0))
        if vol:
            total_volume += vol
    log("Total input volume: {:.3f}".format(total_volume))
    
    # STRATEGY 1: Multi-brep union at base tolerance (BEST - single operation)
    log("")
    log("-" * 60)
    log("STRATEGY 1: Multi-brep union (tol={:.6f})".format(base_tolerance))
    log("-" * 60)
    
    result = attempt_multi_union(breps, base_tolerance)
    if result:
        result_vol = get_brep_volume(result)
        log("Result volume: {:.3f}".format(result_vol if result_vol else 0))
        is_valid, issues = validate_union_result_multi(result, breps)
        if is_valid:
            log("SUCCESS - Clean multi-brep union")
            return result, True, "MultiUnion(tol={:.6f})".format(base_tolerance)
        else:
            log("Result has issues: {}".format(", ".join(issues)))
            first_attempt_issues = issues  # Remember for smart routing
    else:
        log("No result returned")
        first_attempt_issues = ["NoResult"]
    
    # SMART ROUTING: Detect failure mode and adapt
    has_self_intersections = any("SelfIntersections" in issue for issue in first_attempt_issues)
    has_no_result = "NoResult" in first_attempt_issues
    has_volume_error_only = any("VolumeError" in issue for issue in first_attempt_issues) and not has_self_intersections
    
    # STRATEGY 2: If no result, try slightly higher tolerances (2-3 attempts max)
    if has_no_result:
        log("")
        log("-" * 60)
        log("STRATEGY 2: Tolerance escalation (detected 'NoResult')")
        log("-" * 60)
        
        for tol in [0.01, 0.1]:
            if tol <= base_tolerance:
                continue
            
            log("  Trying tolerance: {:.6f}".format(tol))
            result = attempt_multi_union(breps, tol)
            if result:
                is_valid, issues = validate_union_result_multi(result, breps, tolerance_pct=10.0)
                if is_valid:
                    log("SUCCESS - Union at higher tolerance")
                    return result, True, "MultiUnion(tol={:.6f})".format(tol)
                else:
                    log("  Result has issues: {}".format(", ".join(issues)))
        
        log("FAILED - Tolerance escalation didn't resolve")
    
    # STRATEGY 3: If self-intersections detected, try jiggle early
    if has_self_intersections:
        log("")
        log("-" * 60)
        log("STRATEGY 3: Jiggle/offset (detected self-intersections)")
        log("-" * 60)
        
        # Try small offsets on the LAST brep only (less disruptive)
        jiggle_offsets = [0.01, 0.05]  # mm
        jiggle_vectors = [
            rg.Vector3d(0.577, 0.577, 0.577),  # diagonal (often best)
            rg.Vector3d(0, 0, 1)  # Z-axis
        ]
        
        for offset_dist in jiggle_offsets:
            for vec in jiggle_vectors:
                try:
                    jiggled_breps = breps[:-1] + [breps[-1].Duplicate()]
                    translation = rg.Transform.Translation(vec * offset_dist)
                    jiggled_breps[-1].Transform(translation)
                    
                    result = attempt_multi_union(jiggled_breps, 0.01)
                    if result:
                        # Transform result back
                        result.Transform(rg.Transform.Translation(vec * -offset_dist))
                        
                        is_valid, issues = validate_union_result_multi(result, breps, tolerance_pct=15.0)
                        if is_valid:
                            log("SUCCESS - Jiggle {:.3f}mm {} worked".format(offset_dist, vec))
                            return result, True, "Jiggled({:.3f}mm)".format(offset_dist)
                        else:
                            log("  Jiggle {:.3f}mm {}: {}".format(offset_dist, vec, ", ".join(issues)))
                except:
                    pass
        
        log("FAILED - Jiggle didn't resolve self-intersections")
    
    # STRATEGY 4: Repair inputs (if volume error only)
    if has_volume_error_only:
        log("")
        log("-" * 60)
        log("STRATEGY 4: Repair inputs (detected volume error)")
        log("-" * 60)
        
        try:
            fixed_breps = []
            for brep in breps:
                fixed = brep.Duplicate()
                if not fixed.IsValid:
                    fixed.Repair(base_tolerance)
                fixed.Faces.SplitKinkyFaces(sc.doc.ModelAngleToleranceRadians, True)
                fixed.Compact()
                fixed_breps.append(fixed)
            
            result = attempt_multi_union(fixed_breps, base_tolerance * 10)
            if result:
                is_valid, issues = validate_union_result_multi(result, breps, tolerance_pct=15.0)
                if is_valid:
                    log("SUCCESS - Union with repaired inputs")
                    return result, True, "Repaired"
                else:
                    log("Result has issues: {}".format(", ".join(issues)))
        except Exception as e:
            log("Error: {}".format(str(e)))
        
        log("FAILED - Repair didn't help")
    
    # STRATEGY 5: Try different union orderings
    log("")
    log("-" * 60)
    log("STRATEGY 5: Different union orderings")
    log("-" * 60)
    
    # Strategy: Try a few different deterministic orderings
    # This helps when certain brep combinations create problematic intermediates
    orderings = [
        ("Sequential 0→N", list(range(len(breps)))),
        ("Sequential N→0", list(range(len(breps)-1, -1, -1))),
    ]
    
    # For even counts, add binary tree pairing strategies
    if len(breps) % 2 == 0 and len(breps) >= 4:
        # Try different half-split combinations
        mid = len(breps) // 2
        orderings.append(("Split halves", list(range(len(breps)))))  # Will use special logic
    
    for order_name, order in orderings:
        log("  Trying order: {}".format(order_name))
        try:
            if order_name == "Split halves" and len(breps) % 2 == 0:
                # Binary tree: union first half, union second half, then combine
                mid = len(breps) // 2
                first_half = [breps[i] for i in range(mid)]
                second_half = [breps[i] for i in range(mid, len(breps))]
                
                result1 = attempt_multi_union(first_half, base_tolerance * 10)
                if not result1:
                    log("    First half union failed")
                    continue
                
                result2 = attempt_multi_union(second_half, base_tolerance * 10)
                if not result2:
                    log("    Second half union failed")
                    continue
                
                result = attempt_multi_union([result1, result2], base_tolerance * 10)
                if not result:
                    log("    Final union failed")
                    continue
            else:
                # Sequential union in specified order
                result = breps[order[0]].Duplicate()
                for i in order[1:]:
                    temp = attempt_multi_union([result, breps[i]], base_tolerance * 10)
                    if not temp:
                        log("    Failed at brep index {}".format(i))
                        result = None
                        break
                    result = temp
                
                if not result:
                    continue
            
            # Validate result
            result_vol = get_brep_volume(result)
            log("    Result volume: {:.3f}".format(result_vol if result_vol else 0))
            is_valid, issues = validate_union_result_multi(result, breps, tolerance_pct=10.0)
            
            if is_valid:
                log("SUCCESS - Order '{}' produced clean result".format(order_name))
                return result, True, "Ordered({})".format(order_name)
            else:
                log("    Issues: {}".format(", ".join(issues)))
        except Exception as e:
            log("    Error: {}".format(str(e)))
    
    log("FAILED - No union ordering succeeded")
    log("NOTE: Consistent failure across orderings suggests input breps may have issues")
    
    # STRATEGY 6: Sequential pairwise with validation (fallback for any length)
    log("")
    log("-" * 60)
    log("STRATEGY 6: Sequential pairwise union with validation")
    log("-" * 60)
    
    try:
        result = breps[0].Duplicate()
        log("Starting with brep 0")
        
        for i, brep in enumerate(breps[1:], start=1):
            log("  Unioning with brep {}".format(i))
            
            temp_result = attempt_multi_union([result, brep], base_tolerance * 10)
            if temp_result:
                # Check intermediate result
                temp_vol = get_brep_volume(temp_result)
                log("    Intermediate volume: {:.3f}".format(temp_vol if temp_vol else 0))
                
                # Quick validation
                is_valid, issues = validate_union_result_multi(temp_result, [result, brep], tolerance_pct=10.0)
                if is_valid or len(issues) <= 1:  # Allow minor issues in intermediate steps
                    result = temp_result
                    log("    Step {} OK".format(i))
                else:
                    log("    Step {} has issues: {} - continuing anyway".format(i, ", ".join(issues)))
                    result = temp_result
            else:
                log("    Step {} failed - trying higher tolerance".format(i))
                temp_result = attempt_multi_union([result, brep], 0.1)
                if temp_result:
                    result = temp_result
                else:
                    log("FAILED - Sequential union stuck at step {}".format(i))
                    break
        
        # Final validation with STRICT volume checking
        result_vol = get_brep_volume(result)
        log("Final result volume: {:.3f} (expected ~{:.3f})".format(result_vol if result_vol else 0, total_volume))
        
        is_valid, issues = validate_union_result_multi(result, breps, tolerance_pct=10.0)
        
        # Check for critical failures that should not return success
        has_major_volume_error = any("VolumeError" in issue and float(issue.split("=")[1].rstrip("%")) > 10.0 for issue in issues)
        has_critical_issues = any(x in ", ".join(issues) for x in ["NotValid", "NotSolid", "NotManifold"])
        
        if has_major_volume_error or has_critical_issues:
            log("FAILED - Sequential result has CRITICAL issues: {}".format(", ".join(issues)))
        elif is_valid:
            log("SUCCESS - Sequential union completed cleanly")
            return result, True, "Sequential"
        elif result and result.IsValid and result.IsSolid and len(issues) <= 1:
            log("SUCCESS - Sequential union completed (minor issues: {})".format(", ".join(issues)))
            return result, True, "Sequential"
        else:
            log("FAILED - Sequential result unsatisfactory: {}".format(", ".join(issues)))
    except Exception as e:
        log("Error in sequential union: {}".format(str(e)))
    
    log("FAILED - Sequential approach didn't produce valid result")
    
    # STRATEGY 7: Mesh boolean (last resort)
    log("")
    log("-" * 60)
    log("STRATEGY 7: Mesh boolean fallback")
    log("-" * 60)
    
    # For mesh, try pairwise then combine
    try:
        if len(breps) == 2:
            result = attempt_mesh_union(breps[0], breps[1])
            if result:
                is_valid, issues = validate_union_result_multi(result, breps, tolerance_pct=25.0)
                if is_valid:
                    log("SUCCESS - Mesh boolean")
                    return result, True, "Mesh"
                else:
                    log("Result has issues: {}".format(", ".join(issues)))
    except:
        pass
    
    log("FAILED - Mesh boolean failed")
    
    # All strategies exhausted
    log("")
    log("=" * 60)
    log("ALL UNION STRATEGIES FAILED")
    log("Recommendations:")
    log("  1. Inspect input breps with BrepInspect - may have pre-existing issues")
    log("  2. Check if inputs actually overlap/touch")
    log("  3. Consider manual geometry repair in Rhino")
    log("=" * 60)
    
    return None, False, "None"
