
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

### Input validation rules (future splint_factory web form)

The geometry pipeline assumes the collected raw_data already satisfies these; the web form
must enforce them:
- At least two anchor fingers per splint (the profile plane needs >= 2 anchors, and the splint
  is mechanically carried by the anchors).
- At least one supported finger (no relative motion = not a RelativeMotion splint).
- Included fingers are contiguous: an excluded finger (is_included == False) can only sit at an
  end of the finger run, never between two included fingers.
- Only anchor fingers may be slitted (is_slitted True only where is_anchor_finger is True).
- pip_neighbor_fwd_offset is 0 for the first included finger (the reference finger).
- relative_elevation_angle within [-120, +45] degrees (provisional; matches the Phase 2 clamp).

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

### Phase 5: Walking the profile perimeter

Rethink: rather than assembling the profile from independent pieces, we build the full closed
perimeter by walking it once. The walk has two legs:
- Support side - the run that incorporates the supported fingers' support arcs.
- Return side - the more direct run back, chosen for structural rigidity.

Framing the walk as support side then return side (rather than clockwise / counter-clockwise)
keeps it robust to the sign of relative_elevation_angle and to handedness: the same visit logic
produces a valid closed perimeter for every permutation.

Inside / outside model (still holds):
- Anchor fingers sit INSIDE their rings; the Phase 4 closed ellipse is the inner boundary and
  the exterior ring (Path A) is the outer boundary.
- Supported fingers sit OUTSIDE the support structure; the Phase 4 support arc IS the outer
  profile edge at that finger, so support arcs need no offset.

+Z / -Z convention (mind what each is relative to):
- Phase 4's -Z / +Z are relative to the finger cross-section (which part of the finger ellipse
  we keep). For relative_elevation_angle >= 0 the finger is raised, so its support arc is the
  lower (-Z) part of the finger.
- Phase 5's +Z / -Z are relative to the splint perimeter. The raised finger rests on top of the
  splint, so that same support arc is the top (+Z) edge of the profile. So for angle >= 0 the
  support side is the +Z side of the perimeter and the return side is the -Z side; for a
  negative angle they swap. This is consistent with Phase 4.

New input parameter for this phase:
- radial_band_thickness_mm - the wall thickness of an anchor ring (the radial gap between the
  anchor's finger-contact ellipse and the ring's outer boundary).

#### Path A: exterior anchor rings + hemispheres

Build each anchor's exterior ring: offset the Phase 4 closed ellipse outward within the profile
plane by radial_band_thickness_mm, and verify the offset comes back closed and longer than the
input (confirming it is outside).

Addition: also split each exterior ring into a +Z hemisphere and a -Z hemisphere at the ring's
+Y-extreme and -Y-extreme points (the extremes along the in-plane horizontal axis). Splitting
there gives the hemispheres the same +Y-start / -Y-end convention as the Phase 4 support arcs,
so the bridges line up naturally.

Inputs: raw_data (for is_anchor_finger), profile_plane, preserved_intersection_curves (anchor
closed curves), radial_band_thickness_mm.
Returns (index-aligned to included fingers, None for supported fingers):
- exterior_anchor_rings
- exterior_ring_pos_hemispheres (+Z halves)
- exterior_ring_neg_hemispheres (-Z halves)

#### The perimeter walk (two passes)

Pass 1 - lay down the ordered finger visits into perimeter_construction_segments (no bridges
yet). Each slot holds {kind, finger_index, curve} where kind is anchor_support_side /
anchor_return_side / support_arc. Walk the support side over the included fingers in if->sf
order, then the return side back:
- Support side (each included finger, if->sf):
  - anchor finger -> its support-side hemisphere (+Z when angle >= 0, else -Z)
  - support finger -> its Phase 4 support arc
- Return side (walking sf->if, landing only on anchors; support runs are leapt over):
  - anchor finger -> its return-side hemisphere (the opposite hemisphere)

Pass 2 - bridge adjacent slots. For each adjacent pair of different fingers, call the matching
bridge, which returns (bridge_segment, from_segment_revised, to_segment_revised); write the two
revised curves back into their slots and insert bridge_segment between them.

Why revisions compose: a middle segment borders exactly two bridges, and each trims the opposite
end of it (the end nearest that neighbor). The two trims are disjoint, so they compose regardless
of order - the slot just holds the current curve and each bridge reads/writes it. A small helper
(ordered slots plus prev/curr/next accessors and replace(i, curve)) keeps this readable; bridges
stay pure and the walker owns the writes.

Turn-arounds (no bridge): at the first and last included anchors the walk reverses; that anchor's
two hemispheres join directly at its far +Y / -Y extreme (the split point), so no bridge is
needed there. Bridges only ever connect two different fingers.

Visit counts: anchors are visited twice (a hemisphere per side); supported fingers are visited
once (support side only), since the return side leaps over support runs.

Final step: JoinCurves the ordered segments + bridges into one closed profile curve.

#### Bridge functions

All bridges take (from_index, from_segment, to_index, to_segment, raw_data) and return
(bridge_segment, from_segment_revised, to_segment_revised). "Near end" = the endpoint of a
segment closest to the neighbor being bridged (keying on near/far ends instead of hardcoded
+Y/-Y keeps handedness and elevation sign automatic).

Support side:
- create_supportpath_bridge_anchor_to_support - extends a straight line off the support arc's
  near end, tangent to the arc there, until it strikes the anchor hemisphere. G1-continuous with
  the support arc; meets the anchor with a small (acceptable) angular discontinuity. Trims only
  the anchor hemisphere back to the strike point (the support arc is left whole).
- create_supportpath_bridge_support_to_support - a simple tangent arc joining the near ends of
  the two support arcs.
- create_supportpath_bridge_anchor_to_anchor - hourglass blend (TwoDFormHelper) on the support
  side (+Z when angle >= 0, else -Z).

Return side:
- create_returnpath_bridge_anchor_across_support_leap - a tangent line on the return side across
  the exterior rings of the two anchors bracketing a support run (the direct, rigid leap); trims
  both anchors' return-side hemispheres at the tangent points.
- create_returnpath_bridge_anchor_to_anchor - hourglass blend on the return side (the opposite
  side from the support-side anchor-to-anchor bridge).

Adjacent-finger separation is already bounded by setup_finger_positions' min_center_gap, but
bridges should still guard against short / partial Phase 4 arcs.

Implementation note (first attempt, in RelativeMotion.py, pending Rhino validation): because the
anchor sections can be skewed ellipses (a tilted profile_plane), the bridges work against the
true curves rather than best-fit circles. The generic corner is create_rounded_corner_bridge: it
fits a constant-radius fillet (Curve.CreateFilletCurves) tangent to both curves and trims them
back to the tangency points, falling back to a plain Curve.CreateBlendCurve (G1 tangent, no
trim) if the radius will not fit. It is used directly for support-to-support joints, at a larger
support_bridge_radius_mm (the finger contacts them, so they need a smoother blend).

Anchor-to-anchor joints go through create_anchor_to_anchor_bridge instead. Adjacent anchor rings
are designed to overlap (neighbouring fingers share a single wall, like two wedding rings pressed
together), so the two hemispheres usually cross. When they do, the exterior perimeter simply
meets at the outer crossing: each hemisphere is trimmed back to it (dropping the stub that pokes
into the neighbour) and no bridge is inserted - a plain fillet would otherwise pick the interior
tangent, which is valid but on the wrong side. Only when the rings are genuinely separated does
it fall back to create_rounded_corner_bridge at a tight anchor_bridge_radius_mm (structural only).
The radius policy lives in the dispatcher (weld_perimeter_walk), not the helpers.

anchor_to_support keeps its own function (create_supportpath_bridge_anchor_to_support) that
extends a straight tangent line off the support arc's near end until it strikes the anchor
hemisphere, then trims the anchor there (no fillet yet - deferred). The leap
(create_return_leap_bridge) finds a true common tangent line to the two rings via an iterative
supporting-line fixpoint and trims both return hemispheres. weld_perimeter_walk dispatches these,
logs the outcome of every adjacent pair (bridged + length, direct join, turn-around, skip, or
failure), and JoinCurves the result. End-support caps are handled up front (see below) so they
arrive at the walk as a single pre-capped cradle.

#### End-support special case

When the first or last included finger is a supported finger (e.g. the A-A-S or S-A-A configs -
or an if supported by mf..., the mirror of an sf supported by rf...), the support side has no
anchor to turn around on at that end. Because this always lands at the very start or end of the
chain, we get extra leeway: build the whole finger as one closed-end cradle instead of three
separate visits (arc, cap, return). build_end_support_cradles turns the support arc into a
U-shaped curve = the support arc + a parallel return edge (the arc offset outward by
single_sided_support_thickness_mm) + a semicircle cap (radius = thickness / 2) joining their
free ends. The free end is the arc endpoint farther from the adjacent anchor; the near end is
left open. The two open near ends are the support prong and the return prong.

plan_perimeter_walk emits this cradle as the finger's single 'end_support_cradle' visit (in
place of the plain support arc). weld_perimeter_walk then bridges its two prongs to the same
adjacent anchor: the support prong to that anchor's support hemisphere (support-side pair,
anchor_support_side + end_support_cradle) and the return prong to its return hemisphere
(return-side pair, anchor_return_side + end_support_cradle). Both reuse
create_supportpath_bridge_anchor_to_support. The two prongs sit only a band thickness apart, so
nearest-endpoint guessing is unreliable: build_end_support_cradles orients the cradle so its
start endpoint is the support prong and its end endpoint is the return prong, and the weld pins
each bridge to the matching endpoint via support_param. This condenses the three visits into one
and works at either end via the near/far endpoint test (no hardcoded +Y / -Y).
single_sided_support_thickness_mm is a distinct parameter (not radial_band_thickness_mm) because
the cradle is a single-sided support band, a structurally different form from a full anchor ring
wall.

#### Worked example

Sample raw_data (if anchor, mf support, rf anchor, sf excluded; angle +20). angle >= 0, so the
support side is +Z and the return side is -Z. sf is excluded, so the walk runs if->rf.

Support side:
- Visit 1 - if (anchor): append if's +Z hemisphere.
- Visit 2 - gap if->mf (anchor to support): create_supportpath_bridge_anchor_to_support.
- Visit 3 - mf (support): append mf's support arc.
- Visit 4 - gap mf->rf (support to anchor): create_supportpath_bridge_anchor_to_support.
- Visit 5 - rf (anchor): append rf's +Z hemisphere. Support side complete; turn around on rf's
  far extreme (no bridge) into its -Z hemisphere.

Return side (land on anchors, leap over supports):
- Visit 6 - rf (anchor): append rf's -Z hemisphere.
- Visit 7 - gap rf..if (leaping over mf): create_returnpath_bridge_anchor_across_support_leap
  using rf's and if's -Z hemispheres.
- Visit 8 - if (anchor): append if's -Z hemisphere; the loop closes back to Visit 1 at if's far
  extreme (no bridge).

JoinCurves the slots + bridges into the closed profile perimeter. (This config does not exercise
the support-to-support or anchor-to-anchor bridges; those appear when two supports are adjacent,
or when three or more anchors are adjacent, respectively.)

Returns (for observability):
- perimeter_construction_segments - the ordered slots + bridges (previewable piece by piece).
- closed_profile_curve - the joined closed perimeter.

Open items:
- Confirm the hemisphere split points (+Y / -Y extremes) once we see Phase 4 output in Rhino.

#### Usage example

Phase 5 calls (assuming profile_plane and the Phase 4 `preserved` sections are already wired):

```python
rings, pos_hemis, neg_hemis = build_exterior_anchor_rings(
    raw_data, profile_plane, preserved)  # radial_band_thickness_mm optional

cradles = build_end_support_cradles(
    raw_data, profile_plane, preserved, rings, single_sided_support_thickness_mm)

walk_segments = plan_perimeter_walk(raw_data, pos_hemis, neg_hemis, preserved, cradles)

closed_profile, bridge_curves = weld_perimeter_walk(
    raw_data, walk_segments, profile_plane, rings,
    anchor_bridge_radius_mm, support_bridge_radius_mm)
```

Recommended incremental bring-up (bake / preview each stage before wiring the next, since the
bridges are a first attempt):

1. build_exterior_anchor_rings - preview `rings`, then `pos_hemis` and `neg_hemis` separately;
   confirm each ring is closed and outside its Phase 4 ellipse, and that the split lands cleanly
   at the +Y / -Y extremes.
2. plan_perimeter_walk - preview `[s["curve"] for s in walk_segments]` in order; confirm the
   support-side then return-side visit sequence looks right for the config.
3. weld_perimeter_walk - first preview `bridge_curves` alone to check each bridge shape, then
   `closed_profile`; confirm it reports as closed (IsClosed) with no gaps or self-crossings.

### Later phases (future work)

With the closed profile perimeter in hand, remaining work (to be specified as we get there):
- Extrude the profile by band_width_mm along the profile-plane normal.
- Build finger solids and boolean-subtract them (this is where capped / solid cylinders return).
- Apply is_slitted to the anchor rings.
- Orient for printing (build plate roughly parallel to the profile plane) and mesh to STL / 3mf.

