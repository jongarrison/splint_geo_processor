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

class BrepUnionError(Exception):
    """Raised when brep union operation fails after all strategies."""
    pass


class InvalidBrepError(Exception):
    """Raised when input brep is None or invalid."""
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

def get_total_volume(breps):
    """Calculate total volume of multiple breps"""
    total = 0.0
    for brep in breps:
        vol = get_brep_volume(brep)
        if vol:
            total += vol
    return total

def validate_union_result(result_brep, input_breps):
    """
    Validate union result quality.
    
    Checks:
    1. Structural: IsValid, IsSolid, IsManifold, no naked edges
    2. Volume sanity: result > 0, all inputs > 0,
       result <= sum(inputs), result >= max(inputs)
    
    Does NOT use boolean intersection to estimate expected volume -- that
    relies on the same engine that may have produced a bad union, making
    the check circular and unreliable.
    
    Returns:
        tuple: (is_valid: bool, issues: list of strings)
    """
    issues = []
    
    # Structural geometry checks
    if not result_brep.IsValid:
        issues.append("NotValid")
    if not result_brep.IsSolid:
        issues.append("NotSolid")
    if not result_brep.IsManifold:
        issues.append("NotManifold")
    
    # Naked edges
    naked_count = sum(1 for e in result_brep.Edges if e.Valence == rg.EdgeAdjacency.Naked)
    if naked_count > 0:
        issues.append("NakedEdges={}".format(naked_count))
    
    # Self-intersection check intentionally omitted. The previous approach
    # sampled face pairs via SurfaceSurface intersection and checked whether
    # intersection curves were far from edges. This produced false positives
    # on tangent revolution surfaces (joint spheres meeting phalanx cylinders)
    # because the underlying surface extends beyond the trimmed face boundary.
    # If self-intersection detection is needed in the future, consider
    # mesh-based approaches or Brep.IsPointInside spot checks.
    
    # Volume sanity checks
    result_volume = get_brep_volume(result_brep)
    if result_volume is None or result_volume <= 0:
        issues.append("ZeroResultVolume")
        return False, issues
    
    # Verify all inputs have valid volume (catches broken input geometry)
    input_volumes = []
    for i, brep in enumerate(input_breps):
        vol = get_brep_volume(brep)
        if vol is None or vol <= 0:
            issues.append("ZeroInputVolume[{}]".format(i))
        else:
            input_volumes.append(vol)
    
    if len(input_volumes) != len(input_breps):
        return False, issues
    
    total_input_volume = sum(input_volumes)
    max_input_volume = max(input_volumes)
    
    # Union can't be larger than sum of all parts (5% tolerance for numerical noise)
    if result_volume > total_input_volume * 1.05:
        issues.append("VolumeExceedsInputs={:.1f}%".format(
            (result_volume / total_input_volume - 1.0) * 100.0))
    
    # Union must be at least as large as the largest single input.
    # If it's smaller, the boolean engine likely mangled or dropped geometry.
    if result_volume < max_input_volume * 0.95:
        issues.append("VolumeSmallerThanLargestInput={:.1f}vs{:.1f}".format(
            result_volume, max_input_volume))
    
    return len(issues) == 0, issues


def _validate_pairwise_step(prev_vol, new_comp_vol, result_vol, step_index):
    """
    Validate a single sequential pairwise union step.
    Catches Rhino's 'silently skip a component' failure mode by checking
    that the union volume grew after incorporating the new component.
    
    Returns:
        tuple: (is_ok: bool, message: str)
    """
    if result_vol is None or result_vol <= 0:
        return False, "Step {} result has zero volume".format(step_index)
    
    if prev_vol is None or prev_vol <= 0 or new_comp_vol is None or new_comp_vol <= 0:
        return True, "Step {} OK (couldn't verify growth)".format(step_index)
    
    # Volume must not shrink
    if result_vol < prev_vol * 0.95:
        return False, "Step {} volume shrank: {:.1f} -> {:.1f}".format(
            step_index, prev_vol, result_vol)
    
    # For non-trivial components, check that volume actually grew.
    # Even with heavy overlap, incorporating a real component adds at least
    # a small amount of volume. Zero growth means the component was silently
    # skipped by the boolean engine.
    if new_comp_vol > prev_vol * 0.05:  # component is >5% of running total
        growth = result_vol - prev_vol
        min_expected = new_comp_vol * 0.001  # 0.1% of component volume
        if growth < min_expected:
            return False, ("Step {} component appears skipped: "
                "prev={:.1f} + comp={:.1f} -> {:.1f} (growth={:.3f})").format(
                step_index, prev_vol, new_comp_vol, result_vol, growth)
    
    return True, "Step {} OK: {:.1f} -> {:.1f} (+{:.1f})".format(
        step_index, prev_vol, result_vol, result_vol - (prev_vol or 0))


def _sequential_pairwise_union(breps, tolerance):
    """
    Union breps sequentially, one pair at a time, from index 0 forward.
    Validates each step to catch 'silently skipped component' failures.
    
    Returns result brep or None if any step fails.
    """
    if len(breps) < 2:
        return breps[0].Duplicate() if breps else None
    
    result = breps[0].Duplicate()
    prev_vol = get_brep_volume(result)
    log("  Starting with brep 0 (vol={:.1f})".format(prev_vol if prev_vol else 0))
    
    for i, brep in enumerate(breps[1:], start=1):
        new_comp_vol = get_brep_volume(brep)
        
        temp = attempt_multi_union([result, brep], tolerance)
        if not temp:
            # Try slightly higher tolerance for this step
            temp = attempt_multi_union([result, brep], tolerance * 10)
            if not temp:
                log("  Step {} failed - no result even at {:.6f}".format(i, tolerance * 10))
                return None
        
        result_vol = get_brep_volume(temp)
        
        # Per-step validation: catch silently skipped components
        step_ok, step_msg = _validate_pairwise_step(prev_vol, new_comp_vol, result_vol, i)
        log("  " + step_msg)
        
        if not step_ok:
            return None
        
        result = temp
        prev_vol = result_vol
    
    return result


def _sequential_mesh_union(breps):
    """
    Sequential pairwise mesh boolean union for any number of breps.
    Last resort -- produces lower-quality NURBS.
    """
    if len(breps) < 2:
        return breps[0].Duplicate() if breps else None
    
    result = breps[0]
    for i, brep in enumerate(breps[1:], start=1):
        temp = attempt_mesh_union(result, brep)
        if not temp:
            log("  Mesh union failed at step {}".format(i))
            return None
        result = temp
    
    return result

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
    
    if len(breps) == 0:
        raise InvalidBrepError("No breps provided to union")
    
    # Filter out None/invalid breps
    valid_breps = []
    invalid_indices = []
    for i, brep in enumerate(breps):
        if brep is None:
            log("WARNING: Brep {} is None - skipping".format(i))
            invalid_indices.append(i)
        elif not brep.IsValid:
            log("WARNING: Brep {} is invalid - skipping".format(i))
            invalid_indices.append(i)
        else:
            valid_breps.append(brep)
    
    if len(valid_breps) == 0:
        raise InvalidBrepError(
            "No valid breps provided (all None or invalid). Invalid indices: {}".format(invalid_indices)
        )
    
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
    
    # STRATEGY 1: Multi-brep union at base tolerance (fastest if it works)
    log("")
    log("-" * 60)
    log("STRATEGY 1: Multi-brep union (tol={:.6f})".format(base_tolerance))
    log("-" * 60)
    
    result = attempt_multi_union(breps, base_tolerance)
    if result:
        result_vol = get_brep_volume(result)
        log("Result volume: {:.3f}".format(result_vol if result_vol else 0))
        is_valid, issues = validate_union_result(result, breps)
        if is_valid:
            log("SUCCESS - Clean multi-brep union")
            return result, True, "MultiUnion(tol={:.6f})".format(base_tolerance)
        else:
            log("Result has issues: {}".format(", ".join(issues)))
    else:
        log("No result returned")
    
    # STRATEGY 2: Sequential pairwise at base tolerance (most reliable for
    # overlapping revolution surfaces -- each step is a simple 2-brep union
    # with per-step volume validation to catch skipped components)
    log("")
    log("-" * 60)
    log("STRATEGY 2: Sequential pairwise union (tol={:.6f})".format(base_tolerance))
    log("-" * 60)
    
    seq_result = _sequential_pairwise_union(breps, base_tolerance)
    if seq_result:
        is_valid, issues = validate_union_result(seq_result, breps)
        if is_valid:
            log("SUCCESS - Sequential pairwise union")
            return seq_result, True, "Sequential(tol={:.6f})".format(base_tolerance)
        else:
            log("Result has issues: {}".format(", ".join(issues)))
    
    # STRATEGY 3: Tolerance escalation (both multi and sequential)
    log("")
    log("-" * 60)
    log("STRATEGY 3: Tolerance escalation")
    log("-" * 60)
    
    for tol in [0.01, 0.1]:
        if tol <= base_tolerance:
            continue
        log("  Trying tolerance: {:.6f}".format(tol))
        
        # Try multi-union first (faster)
        result = attempt_multi_union(breps, tol)
        if result:
            is_valid, issues = validate_union_result(result, breps)
            if is_valid:
                log("SUCCESS - Multi-union at higher tolerance")
                return result, True, "MultiUnion(tol={:.6f})".format(tol)
            else:
                log("  Multi-union issues: {}".format(", ".join(issues)))
        
        # Then sequential
        seq_result = _sequential_pairwise_union(breps, tol)
        if seq_result:
            is_valid, issues = validate_union_result(seq_result, breps)
            if is_valid:
                log("SUCCESS - Sequential pairwise at higher tolerance")
                return seq_result, True, "Sequential(tol={:.6f})".format(tol)
            else:
                log("  Sequential issues: {}".format(", ".join(issues)))
    
    log("FAILED - Tolerance escalation didn't resolve")
    
    # STRATEGY 4: Jiggle small offsets + sequential pairwise
    # (no gating -- always tried regardless of previous failure mode)
    log("")
    log("-" * 60)
    log("STRATEGY 4: Jiggle + sequential pairwise")
    log("-" * 60)
    
    jiggle_offsets = [0.01, 0.05]
    jiggle_vectors = [
        rg.Vector3d(0.577, 0.577, 0.577),  # diagonal
        rg.Vector3d(0, 0, 1),               # Z-axis
        rg.Vector3d(1, 0, 0),               # X-axis (along finger)
        rg.Vector3d(0, 1, 0),               # Y-axis (lateral)
    ]
    
    for offset_dist in jiggle_offsets:
        for vec in jiggle_vectors:
            try:
                jiggled_breps = [b.Duplicate() for b in breps]
                translation = rg.Transform.Translation(vec * offset_dist)
                jiggled_breps[-1].Transform(translation)
                
                seq_result = _sequential_pairwise_union(jiggled_breps, base_tolerance)
                if seq_result:
                    # Transform result back to undo the jiggle
                    seq_result.Transform(rg.Transform.Translation(vec * -offset_dist))
                    is_valid, issues = validate_union_result(seq_result, breps)
                    if is_valid:
                        log("SUCCESS - Jiggle {:.3f}mm {} + sequential".format(offset_dist, vec))
                        return seq_result, True, "Jiggled({:.3f}mm)+Sequential".format(offset_dist)
                    else:
                        log("  Jiggle {:.3f}mm {}: {}".format(offset_dist, vec, ", ".join(issues)))
            except:
                pass
    
    log("FAILED - Jiggle didn't help")
    
    # STRATEGY 5: Repair inputs + sequential pairwise
    log("")
    log("-" * 60)
    log("STRATEGY 5: Repair inputs + sequential")
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
        
        seq_result = _sequential_pairwise_union(fixed_breps, base_tolerance)
        if seq_result:
            is_valid, issues = validate_union_result(seq_result, breps)
            if is_valid:
                log("SUCCESS - Repaired inputs + sequential")
                return seq_result, True, "Repaired+Sequential"
            else:
                log("Result has issues: {}".format(", ".join(issues)))
    except Exception as e:
        log("Error: {}".format(str(e)))
    
    log("FAILED - Repair didn't help")
    
    # STRATEGY 6: Mesh boolean fallback (last resort, any number of breps)
    log("")
    log("-" * 60)
    log("STRATEGY 6: Mesh boolean fallback")
    log("-" * 60)
    
    try:
        mesh_result = _sequential_mesh_union(breps)
        if mesh_result:
            is_valid, issues = validate_union_result(mesh_result, breps)
            if is_valid:
                log("SUCCESS - Mesh boolean")
                return mesh_result, True, "Mesh"
            else:
                log("Mesh result has issues: {}".format(", ".join(issues)))
    except Exception as e:
        log("Mesh error: {}".format(str(e)))
    
    log("FAILED - Mesh boolean failed")
    
    # All strategies exhausted
    log("")
    log("=" * 60)
    log("ALL UNION STRATEGIES FAILED")
    log("Recommendations:")
    log("  1. Inspect input breps with BrepInspect - may have pre-existing issues")
    log("  2. Check if inputs actually overlap/touch")
    log("  3. Try increasing augment_joint_spheres parameter")
    log("=" * 60)
    
    raise BrepUnionError(
        "Failed to union {} breps after all strategies. "
        "Input breps may have geometry issues or insufficient overlap.".format(len(breps))
    )
