"""
Grasshopper Python Component: Boolean Union Diagnostic Tool

INPUTS:
    BrepA: First Brep (Brep)
    BrepB: Second Brep (Brep)
    Tolerance: Tolerance for checks (float, default: 0.01)
    RunFix: Attempt automatic fixes (bool, default: False)

OUTPUTS:
    Report: Diagnostic report (string)
    IsValidA: Is BrepA valid (bool)
    IsValidB: Is BrepB valid (bool)
    CanUnion: Likely to union successfully (bool)
    FixedA: Fixed version of BrepA (Brep)
    FixedB: Fixed version of BrepB (Brep)
"""

import Rhino
import Rhino.Geometry as rg
import scriptcontext as sc

def analyze_brep(brep, name="Brep"):
    """Analyze a brep and return diagnostic information"""
    report = []
    report.append("=" * 50)
    report.append("ANALYZING: {}".format(name))
    report.append("=" * 50)
    
    # Basic validity
    is_valid = brep.IsValid
    report.append("Valid: {}".format(is_valid))
    
    if not is_valid:
        valid_result, log_message = brep.IsValidWithLog()
        report.append("Validation Error: {}".format(log_message))
    
    # Check if solid
    is_solid = brep.IsSolid
    report.append("Is Solid: {}".format(is_solid))
    
    # Check if manifold (solid implies manifold and closed)
    is_manifold = brep.IsManifold
    report.append("Is Manifold: {}".format(is_manifold))
    
    # Surface count
    report.append("Surface Count: {}".format(brep.Faces.Count))
    
    # Edge analysis
    naked_edges = []
    interior_edges = []
    
    for edge in brep.Edges:
        if edge.Valence == rg.EdgeAdjacency.Naked:
            naked_edges.append(edge)
        elif edge.Valence == rg.EdgeAdjacency.Interior:
            interior_edges.append(edge)
    
    report.append("Naked Edges: {}".format(len(naked_edges)))
    report.append("Interior Edges: {}".format(len(interior_edges)))
    
    if len(naked_edges) > 0 and is_solid:
        report.append("WARNING: Solid has naked edges - geometry inconsistency!")
    
    # Volume check
    try:
        volume_result = brep.GetVolume()
        # GetVolume may return float directly OR (success_bool, volume_float, error_float) depending on Rhino version
        if isinstance(volume_result, (float, int)):
            report.append("Volume: {:.6f}".format(volume_result))
        elif isinstance(volume_result, tuple) and len(volume_result) >= 2:
            if volume_result[0]:
                report.append("Volume: {:.6f}".format(volume_result[1]))
            else:
                report.append("Volume: Could not compute (GetVolume returned False)")
        else:
            report.append("Volume: Unexpected return format: {}".format(type(volume_result)))
    except Exception as e:
        report.append("Volume: Error computing - {}".format(str(e)))
    
    # Bounding box
    bbox = brep.GetBoundingBox(True)
    report.append("Bounding Box: Min{} Max{}".format(bbox.Min, bbox.Max))
    
    # Check for self-intersections
    report.append("\nSelf-Intersection Check:")
    has_self_intersect = False
    for i, faceA in enumerate(brep.Faces):
        for j, faceB in enumerate(brep.Faces):
            if i < j:  # Only check each pair once
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
                            is_interior = False
                            for crv in curves:
                                if crv:
                                    # Sample points along curve and check distance to edges
                                    for t in [0.0, 0.5, 1.0]:
                                        test_pt = crv.PointAt(crv.Domain.ParameterAt(t))
                                        
                                        # Find closest edge
                                        closest_edge_dist = float('inf')
                                        for edge in brep.Edges:
                                            edge_param = edge.ClosestPoint(test_pt, 0.0)[1]
                                            edge_pt = edge.PointAt(edge_param)
                                            dist = test_pt.DistanceTo(edge_pt)
                                            closest_edge_dist = min(closest_edge_dist, dist)
                                        
                                        # If any point is far from edges, it's an interior intersection
                                        if closest_edge_dist > 0.1:
                                            is_interior = True
                                            break
                                    if is_interior:
                                        break
                            
                            if is_interior:
                                has_self_intersect = True
                                report.append("  WARNING: Face {} and Face {} have interior intersection".format(i, j))
                except:
                    pass
    
    if not has_self_intersect:
        report.append("  No interior self-intersections detected")
    
    return "\n".join(report), is_valid, is_solid, naked_edges

def check_intersection(brepA, brepB):
    """Check how two breps intersect"""
    report = []
    report.append("\n" + "=" * 50)
    report.append("INTERSECTION ANALYSIS")
    report.append("=" * 50)
    
    # Bounding box overlap
    bboxA = brepA.GetBoundingBox(True)
    bboxB = brepB.GetBoundingBox(True)
    
    overlap = rg.BoundingBox.Intersection(bboxA, bboxB)
    if overlap.IsValid:
        report.append("Bounding boxes overlap: YES")
        report.append("Overlap volume: {:.6f}".format(overlap.Volume))
    else:
        report.append("Bounding boxes overlap: NO")
        report.append("WARNING: Breps don't overlap - union may fail!")
    
    # Try to compute intersection
    try:
        tolerance = sc.doc.ModelAbsoluteTolerance
        intersection = rg.Intersect.Intersection.BrepBrep(brepA, brepB, tolerance)
        if intersection[0]:
            curves = intersection[1]
            points = intersection[2]
            report.append("Intersection curves: {}".format(len(curves)))
            report.append("Intersection points: {}".format(len(points)))
            
            if len(curves) > 20:
                report.append("WARNING: Many intersection curves - complex intersection!")
        else:
            report.append("No intersection found")
            report.append("WARNING: Breps may not touch!")
    except Exception as e:
        report.append("Intersection computation failed: {}".format(str(e)))
    
    return "\n".join(report)

def attempt_fix(brep, tolerance, name="Brep"):
    """Attempt to fix common brep issues"""
    fixed = brep.Duplicate()
    
    # Try to repair
    if not fixed.IsValid:
        fixed.Repair(tolerance)
    
    # Try to cap planar holes if not solid
    if not fixed.IsSolid:
        fixed = fixed.CapPlanarHoles(tolerance)
    
    # Try to split kinky faces (can help with complex surface intersections)
    try:
        fixed.Faces.SplitKinkyFaces(sc.doc.ModelAngleToleranceRadians, True)
    except:
        pass
    
    # Compact the brep
    fixed.Compact()
    
    return fixed

def test_union_with_tolerance(brepA, brepB, tolerance):
    """Test if union works with a specific tolerance and validate result quality"""
    try:
        result = rg.Brep.CreateBooleanUnion([brepA, brepB], tolerance)
        if result and len(result) > 0:
            union_brep = result[0]
            
            # Collect validation issues
            issues = []
            
            # Basic validity checks
            if not union_brep.IsValid:
                issues.append("Invalid")
            if not union_brep.IsSolid:
                issues.append("NotSolid")
            if not union_brep.IsManifold:
                issues.append("NotManifold")
            
            # Check for naked edges (shouldn't have any if solid)
            naked_count = sum(1 for edge in union_brep.Edges if edge.Valence == rg.EdgeAdjacency.Naked)
            if naked_count > 0:
                issues.append("NakedEdges={}".format(naked_count))
            
            # Check face count explosion
            if union_brep.Faces.Count > 1000:
                issues.append("TooManyFaces={}".format(union_brep.Faces.Count))
            elif union_brep.Faces.Count > 100:
                issues.append("HighFaceCount={}".format(union_brep.Faces.Count))
            
            # Check for self-intersections in result (critical!)
            self_intersect_count = 0
            for i, faceA in enumerate(union_brep.Faces):
                for j, faceB in enumerate(union_brep.Faces):
                    if i < j:
                        try:
                            result_check = rg.Intersect.Intersection.SurfaceSurface(
                                faceA.UnderlyingSurface(), 
                                faceB.UnderlyingSurface(), 
                                0.01
                            )
                            if result_check[0]:
                                curves = result_check[1]
                                if curves and len(curves) > 0:
                                    # Check if this is an interior intersection
                                    for crv in curves:
                                        if crv:
                                            # Sample middle point
                                            test_pt = crv.PointAt(crv.Domain.ParameterAt(0.5))
                                            
                                            # Find closest edge
                                            closest_edge_dist = float('inf')
                                            for edge in union_brep.Edges:
                                                edge_param = edge.ClosestPoint(test_pt, 0.0)[1]
                                                edge_pt = edge.PointAt(edge_param)
                                                dist = test_pt.DistanceTo(edge_pt)
                                                closest_edge_dist = min(closest_edge_dist, dist)
                                            
                                            # If far from edges, it's a self-intersection
                                            if closest_edge_dist > 0.1:
                                                self_intersect_count += 1
                                                break
                        except:
                            pass
            
            if self_intersect_count > 0:
                issues.append("SelfIntersections={}".format(self_intersect_count))
            
            # Try to compute volume
            try:
                vol_result = union_brep.GetVolume()
                # Handle both direct float return and tuple return
                if isinstance(vol_result, tuple):
                    if len(vol_result) >= 2 and not vol_result[0]:
                        issues.append("VolumeComputeFailed")
                elif not isinstance(vol_result, (float, int)):
                    issues.append("VolumeError")
            except:
                issues.append("VolumeError")
            
            # If we have any issues, this is a problematic union
            if len(issues) > 0:
                return False, None, issues
            
            return True, union_brep, []
        return False, None, ["NoResult"]
    except Exception as e:
        return False, None, ["Exception:{}".format(str(e))]

def do_diagnostic_solid_union(BrepA, BrepB, Tolerance=None, RunFix=False):
  if BrepA and BrepB:
      # Set default tolerance
      if not Tolerance or Tolerance <= 0:
          Tolerance = sc.doc.ModelAbsoluteTolerance
      
      # Analyze both breps
      reportA, validA, solidA, nakedA = analyze_brep(BrepA, "Brep A")
      reportB, validB, solidB, nakedB = analyze_brep(BrepB, "Brep B")
      
      # Check intersection
      intersection_report = check_intersection(BrepA, BrepB)
      
      # Combine reports
      full_report = [reportA, reportB, intersection_report]
      
      # Test union at different tolerances
      full_report.append("\n" + "=" * 50)
      full_report.append("UNION TESTS")
      full_report.append("=" * 50)
      
      # More comprehensive tolerance tests with no duplicates
      test_tolerances = sorted(list(set([
          Tolerance, 
          Tolerance * 10, 
          Tolerance * 100,
          Tolerance * 1000,
          0.001, 
          0.01, 
          0.1,
          1.0,
          10.0
      ])))
      
      union_success = False
      union_result = None

      for tol in test_tolerances:
          success, result, issues = test_union_with_tolerance(BrepA, BrepB, tol)
          if success:
              status = "SUCCESS"
          elif len(issues) > 0:
              status = "FAILED ({})".format(", ".join(issues))
          else:
              status = "FAILED"
          
          full_report.append("Tolerance {:.6f}: {}".format(tol, status))
          if success and not union_success:
              union_success = True
              union_result = result
              full_report.append("  -> First successful tolerance: {:.6f}".format(tol))
      
      # If still failing, try with simplified/fixed breps
      if not union_success:
          full_report.append("\nAttempting union with pre-fixed breps...")
          fixedA_temp = attempt_fix(BrepA, Tolerance, "Brep A")
          fixedB_temp = attempt_fix(BrepB, Tolerance, "Brep B")
          
          for tol in [0.01, 0.1, 1.0, 10.0]:
              success, result, issues = test_union_with_tolerance(fixedA_temp, fixedB_temp, tol)
              if success:
                  status = "SUCCESS"
              elif len(issues) > 0:
                  status = "FAILED ({})".format(", ".join(issues))
              else:
                  status = "FAILED"
              
              full_report.append("Fixed breps, Tolerance {:.6f}: {}".format(tol, status))
              if success and not union_success:
                  union_success = True
                  union_result = result
                  full_report.append("  -> SUCCESS with pre-fixed breps at tolerance: {:.6f}".format(tol))
                  break
      
      # Recommendations
      full_report.append("\n" + "=" * 50)
      full_report.append("RECOMMENDATIONS")
      full_report.append("=" * 50)
      
      if not validA or not validB:
          full_report.append("• Fix invalid geometry first (RunFix=True)")
      if not solidA or not solidB:
          full_report.append("• One or both breps are not solid - cap holes if needed")
      if len(nakedA) > 0 or len(nakedB) > 0:
          full_report.append("• Naked edges detected - check for gaps in surfaces")
      
      # Check for self-intersections in the analysis
      if "WARNING: Face" in reportA and "interior intersection" in reportA:
          full_report.append("• CRITICAL: Brep A has interior self-intersecting faces - rebuild geometry")
      if "WARNING: Face" in reportB and "interior intersection" in reportB:
          full_report.append("• CRITICAL: Brep B has interior self-intersecting faces - rebuild geometry")
      
      if union_success:
          full_report.append("• Union succeeded - verify result is valid solid")
          if union_result:
              full_report.append("  Result: {} faces, Solid={}, Valid={}".format(
                  union_result.Faces.Count,
                  union_result.IsSolid,
                  union_result.IsValid
              ))
      else:
          full_report.append("• Union failed at all tolerances")
          full_report.append("• Try: 1) Fix self-intersections, 2) Offset one brep slightly, 3) Use mesh boolean")
          full_report.append("• To export for inspection: Right-click breps in Grasshopper > Bake")
      
      # Output results
      Report = "\n".join(full_report)
      IsValidA = validA
      IsValidB = validB
      #CanUnion = union_success
      UnionResult = union_result
      
      # Attempt fixes if requested
      if RunFix:
          FixedA = attempt_fix(BrepA, Tolerance, "Brep A")
          FixedB = attempt_fix(BrepB, Tolerance, "Brep B")
          Report += "\n\n" + "=" * 50
          Report += "\nFIXES APPLIED"
          Report += "\n" + "=" * 50
          Report += "\nFixed A Valid: {}".format(FixedA.IsValid)
          Report += "\nFixed B Valid: {}".format(FixedB.IsValid)
      else:
          FixedA = BrepA
          FixedB = BrepB

      print(Report)
      return FixedA, FixedB, UnionResult
  else:
      Report = "Please provide both BrepA and BrepB inputs"
      IsValidA = False
      IsValidB = False
      CanUnion = False
      FixedA = None
      FixedB = None
      print(Report)
      return FixedA, FixedB, None

