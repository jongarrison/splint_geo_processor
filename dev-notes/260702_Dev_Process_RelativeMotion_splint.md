
I want us to develop a better process for creating new Design Definitions. Design Definitions are a construct for collecting parametric inputs from web users of splint_factory (SplintFactory.com) that then get picked up as json by the polling process in the splint_geo_processor sub-project. Let's concisely document this collaborative and incremental development process in splint_geo_processor/generators/README.md

The basics of the Design Definition process:
- The web side of paremetric input collection is defined in this directory: splint_factory/src/designs
- The data collected is passed as json (splint_factory/src/designs/design-definition.schema.json) to the polling process defined in splint_geo_processor
- splint_geo_process determines which Rhino3d/Grasshopper script (binary .gh files) to pass the json data to in splint_geo_processor/generators 
- The .gh scripts process the json inputs into stl (or 3mf) 3d mesh files while relying heavily on the python modules in: splint_geo_processor/generators/src

The development process:
- Start by defining the .gh files. As necessary, use existing and define new python modules to support the process.
- Incrementally, develop geometry and preview it in Rhino3d. This development process reveals the paremetric input data that the geometry will require
- When the geometry scripts are getting mature, create the Design Definition files and db data that will be needed for splint_factory (mostly found in: splint_factory/src/designs)
- Create a web input form and measurement guide for users (The content of this form isn't solidified until we know what the .gh script will need)

The example that we will be starting with and use as an archetypical Design Definition process to help with future development:
- The new 3d finger splint will be called the RelativeMotion splint, that name will also be the "agorithm name" that is used to link parts of the processing stages together.
- I've started development in: splint_geo_processor/generators/RelativeMotion.gh
- My goal this time is to have as much of the geometry generation work done in python modules so that what is hidden in the binary .gh file is as minimal as possible.
- We will figure out this new dev process together, suggest improvements as you see them.
- Let's start by creating a new python module that will serve as the central point of contact for this new Design Definitions (splint_geo_processor/generators/src/RelativeMotion.py). We want functions to be as observable as possible in Rhino. We will be relying heavily on the RhinoCommon API and will want functions to use the geometry types defined in the RhinoCommon API
### Anatomy and coordinate conventions

We are building a splint with rings on two "anchor" fingers (is_anchor_finger) that
supports the finger(s) in between them. Fingers, in anatomical order, are index ("if"),
middle ("mf"), ring ("rf"), and small ("sf"). The parts that matter for this splint are
the MCP joint (knuckle center), the P1 (proximal) phalanx, and the PIP joint. Included
fingers ("is_included") are the anchors plus the supported finger(s). Common configurations:
two anchors with the supported finger(s) sitting between them, or three consecutive anchors
supporting the one remaining end finger ("if" or "sf"). So a supported finger is not always
between the anchors - it may sit just outside a run of anchors at one end.

Coordinate frame (hand imagined laying flat on a table, as in prior designs):
- +X: distal direction (MCP toward PIP, i.e. the length of the finger).
- +Z: dorsal (back of hand); -Z: volar (palm side).
- Y: lateral axis across the fingers. For a right hand "if" is at +Y and "sf" at -Y;
  a left hand mirrors the Y sign (finger order if->mf->rf->sf is unchanged).
- Origin: the "if" PIP center is the X=0 (offset reference) and the Y baseline start. Its
  Z is its own circle radius (see step 3), so it is not exactly (0,0,0). Later construction
  may translate away from this.

### First function: setup_finger_positions

Takes the json below (schema is provisional and may change as needs emerge) and returns,
for each included finger, RhinoCommon geometry so results are inspectable in Rhino:
- A list of Point3d for the MCP joint centers.
- A list of Line for the P1 phalanges (MCP center -> PIP center).
- A list of Circle representing the P1 mid cross-section: sized from p1_mid_circ
  (radius = p1_mid_circ / (2*pi)), centered at the midpoint of each P1 line, built in the
  World YZ plane (normal +X). A later step will re-orient these per relative_elevation_angle.
- A list of open (uncapped) cylinder Breps: one per finger, using the P1 circle as the
  extrusion profile and spanning the P1 line from MCP to PIP (Cylinder.ToBrep(False, False)).
  Left uncapped so later plane intersections make incomplete cuts obvious; solids for
  boolean subtraction are built in a later phase.

Construction algorithm (this function only; each phalanx is purely along +X here):
1. Run multiple_circle_positioning in generators/src/TwoDCirclePositioning.py, passing the
   per-finger p1_mid_circ values + all_splint_finger_circ. It does the circumference->radius
   conversion internally and returns the per-finger radii plus the baseline tangent offsets.
   We will sanity-check that function's output before relying on it.
2. Place each PIP center:
   - Y = the finger's baseline tangent offset (signed per handedness so "if" is toward +Y
     on a right hand); the first finger starts the baseline.
   - Z = the finger's radius (each circle rests tangent on the Z=0 volar baseline plane).
   - X = 0 initially (all PIP centers colinear before offsets).
3. Walk the included fingers in order and shift each PIP center in X by
   pip_neighbor_fwd_offset relative to its preceding neighbor (+X = more distal/longer,
   -X = more proximal/shorter). The first included finger's offset is the 0 reference.
4. For each finger, the MCP center is the PIP center projected -X by p1_length.

Not addressed by this function yet: relative_elevation_angle (tilt out of the anchor
plane), is_slitted, and how the nested per-finger json is collected by the splint_factory
web form (a later problem, since this is the most complex input schema we have attempted).

Top view (XY plane, right hand; finger length runs along +X, fingers stacked along Y):

```
 +Y (if side)
  ^
  |  if:  x MCP --------------------o PIP   (PIP at X=0 reference)
  |
  |  mf:  x MCP -----------------------o PIP   (PIP shifted +X, more distal)
  |
  |  rf:  x MCP ---------------o PIP           (PIP shifted -X, more proximal)
  |
  |  (sf excluded in this example)
  +--------------------------------------------> +X (distal)
```

End view (YZ plane, looking down +X; circles rest on the Z=0 volar baseline, center Z = radius):

```
 +Z (dorsal)
  ^
  |    ( if )   ( mf )   ( rf )
  |___________________________________  Z=0 volar baseline (table)
     +Y <----------------------> -Y
```

  * The current draft of the parametric input json:

raw_data = {
    "is_right_hand": True,
    "finger_data": [
        { 
            "finger_abbr": "if", #just a constant provided for reference, "if" is Index Finger
            "is_included": True, #autogenerated value to indicate that the finger is included in the splint
            "is_anchor_finger": True,
            "p1_mid_circ": 70.0,
            "p1_length": 45.0,
            "pip_neighbor_fwd_offset": 0, #Always 0 for first anchor finger or Index Finger ("if")
            "is_slitted": True, #Only anchor fingers can be slitted
        },
        {
            "finger_abbr": "mf", #just a constant provided for reference, "mf" is Middle Finger
            "is_included": True,
            "is_anchor_finger": False,
            "p1_mid_circ": 71.0,
            "p1_length": 50.0,
            "pip_neighbor_fwd_offset": 5.0, #Relative to if pip location +5 means mf pip is farther distal
            "is_slitted": False,
        },
        {
            "finger_abbr": "rf", #just a constant provided for reference, "rf" is Ring Finger
            "is_included": True,
            "is_anchor_finger": True,
            "p1_mid_circ": 64.0,
            "p1_length": 47.0,
            "pip_neighbor_fwd_offset": -10,
            "is_slitted": True,
        },
        {
            "finger_abbr": "sf", #just a constant provided for reference, "sf" is Small Finger
            "is_included": False, #outside the anchor span in this config; but note some splints use 3 anchors (if+mf+rf) to support sf, which would include it
            "is_anchor_finger": False,
            "p1_mid_circ": None,
            "p1_length": None,
            "pip_neighbor_fwd_offset": None,
            "is_slitted": None,            
        }
    ],
    "all_splint_finger_circ": 148.0,
    "relative_elevation_angle": 20.0, #This is relative to the XY world plane (which is shared by the p1_line of the two anchor fingers, which will be establishing the plane in the real world)
    "band_width_mm": 9.0, #thickness of the splint profile extrusion
}

Example call:

```python
mcp_points, p1_lines, p1_circles, p1_cylinders = setup_finger_positions(raw_data, min_center_gap=1.5)
```

### Second function: elevate_supported_fingers

Goal: tilt each supported finger up out of the horizontal so its P1 line makes
relative_elevation_angle with the world XY plane, while keeping the MCP centers fixed. The
two anchor fingers' P1 lines stay horizontal (parallel to XY) and establish that reference
plane; the supported finger(s) are the "relative motion" that rides above them.

Spec:
- Reference is the world XY plane (horizontal). Supported fingers start horizontal (from
  setup_finger_positions) and rotate up to the given angle.
- Only supported fingers (included, non-anchor) rotate; anchors stay put.
- Rotation is confined to the XZ plane: the axis is parallel to world Y and passes through
  each supported finger's own (fixed) MCP center. This preserves each finger's Y and swings
  its PIP / geometry in X and Z only.
- Positive relative_elevation_angle = finger goes up (PIP toward +Z / dorsal). Sign note:
  moving the +X phalanx toward +Z is a rotation about -Y (a negative rotation about +Y);
  get this sign right at implementation so positive input reads as "up".
- Same angle applied to every supported finger (4-finger case: both tilt up by the same
  amount, each pivoting on its own MCP). No graduation for now.
- Angle limits: clamp relative_elevation_angle to [-120, +45] degrees (provisional, to be
  verified with hand therapist cofounder Liz). FUTURE REQUIREMENT: the splint_factory web
  input form must enforce this same range.
- The whole finger transforms together (PIP point, P1 line, circle, cylinder) via one
  rotation Transform.
- Handedness independent: fingers point +X and "up" is +Z for both hands (only Y differs,
  already handled upstream), so the elevation rotation is the same for left and right.

Function structure:
- Name: elevate_supported_fingers.
- Pure transform stage: takes setup_finger_positions' geometry lists + raw_data (the
  authority for the angle and the anchor/support flags) and returns rotated copies, leaving
  setup_finger_positions untouched. Each phase stays independently previewable in Rhino.
- Returns five index-aligned parallel lists (one entry per included finger): mcp_points
  (unchanged), p1_lines, p1_circles, p1_cylinders, and the per-finger rotation Transform.
- Anchor fingers get the identity (no-op) Transform and unchanged geometry, so all five
  lists are equal length. Copies are returned so the first function's outputs are not mutated.

Example call (chained onto setup_finger_positions' outputs):

```python
mcp_points, p1_lines_oriented, p1_circles_oriented, p1_cylinders_oriented, transforms = elevate_supported_fingers(
    raw_data, mcp_points, p1_lines, p1_circles, p1_cylinders)
```

### Third function: build_profile_plane

Context: phase 3 begins the solid body of the splint. Mechanically the splint is carried by
the anchor fingers - each anchor finger gets a full ring (like a wedding ring). Each
supported finger gets a partial support that pushes/holds it in the direction of
relative_elevation_angle. For FDM printing the plan is to draw a 2D outline, extrude it, and
subtract the solid finger forms (the capped cylinders). The final form must include at least
one largely flat surface to sit down on the printer build plate.

This function's single job: return the plane that the extruded profile outline will live in.
It receives the full per-included-finger list of p1_circles from the previous phase (anchors
are not rotated, so their circles are identical before and after elevation).

Plane construction:
1. For each anchor finger (is_anchor_finger == True; there may be more than two), find the
   lowest point of its p1_circle (minimum Z on the circle).
2. Project each lowest point onto the world XY plane. Kept for future-proofing; anchor circles
   currently rest on the Z=0 baseline, so this projection is a no-op today.
3. Best-fit a line through those projected points (exact line for two anchors, least-squares
   fit for three or more) via Line.TryFitLineToPoints.
4. Return the vertical plane that contains that fit line and is perpendicular to world XY
   (X axis along the fit line, Y axis along world +Z).

Returns: a single RhinoCommon Plane (or None if fewer than two anchor fingers).

Example call (p1_circles from either setup_finger_positions or elevate_supported_fingers):

```python
profile_plane = build_profile_plane(raw_data, p1_circles)
```

### Phase 4: extract_finger_cross_sections

Take the profile plane and intersect it with each finger's oriented cylinder to produce a
cross-section curve per finger, then keep the full curve for anchor fingers or a support arc
for supported fingers.

Because the cylinders are now uncapped (open tubes), a plane that fully crosses a finger
yields a closed ellipse, while a plane that only partially crosses (a steeply elevated or
offset supported finger) yields an open arc. That is intentional: an incomplete intersection
is immediately visible, and for a supported finger it is acceptable as long as the surviving
arc still spans the required support_arc_deg. No curve joining is needed.

Inputs:
- raw_data - authority for is_anchor_finger and the sign of relative_elevation_angle.
- profile_plane - from build_profile_plane.
- p1_cylinders_oriented - elevated (oriented) cylinders from elevate_supported_fingers.
- p1_lines_oriented - elevated P1 lines, used to locate each cross-section center (below).
- support_arc_deg - total angular width of the preserved arc for a supported finger.

Cross-section center: do not compute an ellipse centroid - an open arc has no closed area to
compute one from. Instead intersect profile_plane with the finger's p1_lines_oriented; that
single point is the center used for every angular-sweep measurement on that finger's section.

Preserved curve per finger:
- Anchor finger: keep the full intersection curve (the closed ellipse); this becomes the full ring.
- Supported finger: keep an arc of total width support_arc_deg, centered on world -Z when
  relative_elevation_angle >= 0 (support underneath) or world +Z when the angle is negative
  (support above). "Underneath" / "above" are strictly -Z / +Z, not relative to the finger's
  tilt. The arc is measured about the cross-section center point defined above.
- Always orient each preserved arc to start from the +Y side and end at the -Y side, so the
  Phase 5 connections are consistent.

Returns two index-aligned lists (one entry per included finger, anatomical order):
- full_intersection_curves - the raw profile_plane * cylinder intersection for each finger
  (closed ellipse or open arc).
- preserved_intersection_curves - the kept portion: full ellipse for anchors, the support arc
  for supported fingers.

Example call (chained onto the oriented geometry and profile plane):

```python
full_intersection_curves, preserved_intersection_curves = extract_finger_cross_sections(
    raw_data, profile_plane, p1_cylinders_oriented, p1_lines_oriented, support_arc_deg=120.0)
```

### Draft Phase 5: Building the outer profile (two distinct paths)

Phase 4 gives us, per finger, the finger-contact curve: a full closed ellipse for anchors
and a support arc for supported fingers. Phase 5 turns those contact curves into the outer
boundary of the splint's extruded profile. There are two clearly different jobs here, so we
expect two functions.

Inside / outside model (important - keep this straight):
- Anchor fingers sit INSIDE their rings. The Phase 4 closed ellipse is the inner
  (finger-contact) boundary; the exterior ring we build is the outer boundary. The anchor
  finger ends up enclosed inside the profile shape.
- Supported fingers sit OUTSIDE the support structure. The Phase 4 support arc IS the outer
  profile edge at that finger (the finger presses on it from the outside); the splint
  material lies on the inner side of the arc, contiguous with the anchor rings. Supported
  fingers therefore end up on the outside of the profile, and support arcs need no offset.

New input parameter for this phase:
- radial_band_thickness_mm - the wall thickness of an anchor ring (the radial gap between the
  anchor's finger-contact ellipse and the ring's outer boundary).

#### Path A: exterior anchor rings

Each anchor finger becomes a full ring (like a wedding ring): its Phase 4 closed ellipse is
the inner boundary, and we build the matching outer boundary.

- Offset each anchor's preserved (closed) curve outward, within the profile plane, by
  radial_band_thickness_mm. Curve.Offset needs the profile_plane and a signed distance /
  outward direction relative to the section center. Verify that the offset curve produces a closed 
  curve that is longer than the input curve (ensuring that it is outside)

inputs: 
- raw_data (for is_anchor_finger), 
- profile_plane, 
- preserved_intersection_curves (the anchor closed curves), 
- radial_band_thickness_mm.

returns: 
exterior_anchor_rings, index-aligned to the included fingers with None for the
  supported fingers (keeping the same None-padded indexing convention as every other list).

#### Path B: connecting curves across the gaps

Between neighboring fingers we build a continuous, smooth curve that ties the supported-finger
support arcs together (and bridges toward the anchors) into one outer profile. The goals are a
shape comfortable for the finger web spaces and a mechanically sound transition that minimizes
stress concentration (no sharp inside corners).

Working strategy (to be validated against Phase 4 output):
- Each gap is bridged by a concave blend tangent to both neighbors. A connector joins one
  finger's -Y end to the next finger's +Y start (support arcs are oriented +Y start to -Y end
  in Phase 4). On the anchor side, place the connector tangent to the exterior_anchor_ring
  (Path A); we reference the ring for placement now and weld the paths together in Phase 6.
- Shape control: describe the blend with a target concave radius plus a minimum neck (isthmus)
  width safeguard, mirroring the BuddyRingsDuo hourglass helpers in TwoDFormHelper.py. If the
  requested radius would pinch the neck below the minimum, grow the radius until the minimum is
  met (the bisection approach in _solve_hourglass_r_for_min_isthmus). Exact parameter names and
  the fixed-vs-gap-derived choice are still under discussion (see notes below).

Gap coverage in this phase:
- Anchor-support and support-support gaps get a connector now.
- Anchor-anchor gaps are deferred to Phase 6 (where we join the exterior ring path); their
  slots come back None from this function.

Returns (both, for observability):
- connecting_segments - a fixed-length list of (number of included fingers - 1). Slot k is the
  connector for the gap between included finger k and finger k+1, or None for a deferred
  anchor-anchor gap. This departs from the per-finger index (gaps live between fingers) but
  keeps a stable, None-padded gap->index mapping.
- continuous_curve - the support arcs joined with their connectors into one curve (the
  support-side run of the profile). It does NOT weld to the exterior_anchor_rings yet. Where an
  anchor-anchor gap (None) interrupts the run, continuous_curve covers only the contiguous
  support-side portion; in the common two-anchor configs there are no anchor-anchor gaps, so it
  is a single continuous curve.

Special case to keep in mind: a supported finger can land at the END of the splint (not
between two anchors), leaving only one adjacent gap. That is acceptable here; closing off that
end support shape into the loop is future work.

Still under discussion:
- How to parameterize the connector radius (a fixed target radius vs. gap-derived), and the
  name/units of the min-neck safeguard. Leaning toward the BuddyRingsDuo pattern (target
  radius + min isthmus width).
- Build orientation: the eventual build plate will be roughly parallel to the profile_plane
  when the final profile is extruded, so the "one largely flat surface" requirement is handled
  at extrusion time; the full closed loop is still a phase or two away.

### Draft Phase 6: Joining the exterior path (anchor-anchor joints and welding)

Phase 6 completes the outer profile by handling everything Phase 5 deferred:
- Anchor-anchor joints: build the exterior connector between each pair of adjacent anchor
  exterior_anchor_rings. This is the two-ring bridge case the BuddyRingsDuo design already
  solves - see create_two_circle_hourglass_bridge_perimeter in TwoDFormHelper.py, which bridges
  two co-planar circles with concave waist arcs of a target radius (hourglass_r) and a
  guaranteed neck (min_isthmus_width, via _build_pure_arc_hourglass, or the straight-bar variant
  when the radius must be preserved). Our anchor sections are near-circular (anchors are cut
  nearly perpendicular to their axes), so this helper should apply directly or with a small
  ellipse adaptation.
- Weld the exterior path: join the exterior_anchor_rings, the anchor-anchor connectors, and the
  Phase 5 support-side continuous_curve into a single closed outer profile loop.
- Close the end-support shape: the end supported finger (single-gap case from Phase 5) needs its
  open side closed into the loop here.

Output (draft): the full closed profile curve, ready for extrusion in a later phase.

Open questions:
- Where exactly the support-side run meets the ring path (tangency/blend at those junctions).
- Whether the whole loop is rebuilt as one blended curve or assembled from the phase outputs and
  welded with JoinCurves.
- How to guarantee the "one largely flat surface" for the build plate as the loop closes.

