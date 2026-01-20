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
        volume = brep.GetVolume()
        if volume[0]:
            report.append("Volume: {:.6f}".format(volume[1]))
        else:
            report.append("Volume: Could not compute")
    except:
        report.append("Volume: Error computing")
    
    # Bounding box
    bbox = brep.GetBoundingBox(True)
    report.append("Bounding Box: Min{} Max{}".format(bbox.Min, bbox.Max))
    
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
    
    # Compact the brep
    fixed.Compact()
    
    return fixed

def test_union_with_tolerance(brepA, brepB, tolerance):
    """Test if union works with a specific tolerance"""
    try:
        result = rg.Brep.CreateBooleanUnion([brepA, brepB], tolerance)
        if result and len(result) > 0:
            return True, result[0]
        return False, None
    except:
        return False, None

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
      
      test_tolerances = [Tolerance, Tolerance * 10, Tolerance * 100, 0.001, 0.01, 0.1]
      union_success = False
      
      union_result = None

      for tol in test_tolerances:
          success, result = test_union_with_tolerance(BrepA, BrepB, tol)
          status = "SUCCESS" if success else "FAILED"
          full_report.append("Tolerance {:.6f}: {}".format(tol, status))
          if success and not union_success:
              union_success = True
              union_result = result
              full_report.append("  -> First successful tolerance: {:.6f}".format(tol))
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
      if union_success:
          full_report.append("• Union possible with higher tolerance")
      else:
          full_report.append("• Try: 1) Increase tolerance, 2) Offset one brep slightly, 3) Use mesh boolean")
      
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

