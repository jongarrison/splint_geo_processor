
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
- create_supportpath_bridge_anchor_to_support - a tangent (G1) blend that leaves the support
  arc's near end as a smooth continuation and meets the anchor hemisphere tangentially a short
  way up from its near end (high and round, not a sharp strike). Trims only the anchor hemisphere
  back to the attach point (the support arc is left whole).
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
together), so the two hemispheres cross and leave a sharp concave crotch at the outer crossing.
That crotch is rounded with a small fillet at anchor_bridge_radius_mm: pick points on each ring's
far (outer) side steer Curve.CreateFilletCurves to the exterior corner (not the interior tangent)
and keep each ring's outer arc. If the fillet will not fit, the rings fall back to meeting
directly at the crossing (a sharp cusp); genuinely separated rings fall back to a rounded corner
between their facing ends. The radius policy lives in the dispatcher (weld_perimeter_walk), not
the helpers.

anchor_to_support keeps its own function (create_supportpath_bridge_anchor_to_support), a smooth
tangent (G1) blend: Curve.CreateBlendCurve leaves the support band's near end as a continuation
of the arc and meets the anchor hemisphere tangentially a short way up from the end nearest the
support (anchor_attach_fraction, default 0.4, of the hemisphere's remaining arc length from that
near end - roughly 20% down from the apex, so the junction is high and round rather than a sharp
straight strike). Only the anchor is trimmed, back to the attach point. It serves the mid-support
arcs and the support-side prong of an end-support cradle. The return-side prong is different:
there the anchor hemisphere is the ring's outer wall and must stay round, so blending high onto
it (which trims a chunk out of the hemisphere) is wrong. create_returnpath_bridge_anchor_to_support
rounds that crotch like the A-A joints instead - a fillet at anchor_bridge_radius_mm with far-side
pick points, keeping each curve's far portion and trimming only to the tangency points, so the
hemisphere is left un-dented (falling back to a direct crossing join, then a gap, if the fillet
will not fit). The leap (create_return_leap_bridge) lays a straight horizontal strut held
return_spine_thickness_mm outward from the leapt-over support run's return-facing extreme (so the
profile keeps that thickness at its thinnest spot and rides up under a raised finger instead of
filling a solid wedge beneath it), then fillets each end tangentially into the anchor's return
hemisphere (Curve.CreateFilletCurves against the ring body, largest ramp that fits first) - a G1
transition that rounds a concave indent where the strut cuts the ring (higher elevation) or a
convex ramp where it sits proud of the ring (near-zero elevation). It falls back to a plain
common-tangent line to the two rings (via an iterative supporting-line fixpoint) when no
leapt-over support geometry is available.
weld_perimeter_walk dispatches these, logs the outcome of every adjacent pair (bridged + length,
direct join, turn-around, skip, or failure), and JoinCurves the result. End-support caps are
handled up front (see below) so they arrive at the walk as a single pre-capped cradle.

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
anchor_support_side + end_support_cradle) via create_supportpath_bridge_anchor_to_support (the
high tangent blend), and the return prong to its return hemisphere (return-side pair,
anchor_return_side + end_support_cradle) via create_returnpath_bridge_anchor_to_support (the
A-A-style crotch fillet, which keeps the return hemisphere round). The two prongs sit only a
band thickness apart, so nearest-endpoint guessing is unreliable: build_end_support_cradles
orients the cradle so its start endpoint is the support prong and its end endpoint is the return
prong, and the weld pins each bridge to the matching endpoint via support_param. This condenses
the three visits into one and works at either end via the near/far endpoint test (no hardcoded
+Y / -Y). single_sided_support_thickness_mm is a distinct parameter (not
radial_band_thickness_mm) because the cradle is a single-sided support band, a structurally
different form from a full anchor ring wall.

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
    anchor_bridge_radius_mm, support_bridge_radius_mm, return_spine_thickness_mm)
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

### Phase 6: build_splint_solid (two-face loft)

Goal: turn the two closed profile perimeters into one watertight closed solid slab.

Where the two perimeters come from: Phase 3-5 run TWICE, once per profile plane. build_profile_planes
(Phase 3) returns a proximal plane (-X, toward the hand) and a distal plane (+X, toward the
fingertip), offset +/- longitudinal_band_thickness_mm / 2 along World X from the centre plane.
Everything upstream of the plane is shared (the same elevated cylinders and P1 lines), so only the
plane changes between the two runs. Each run produces one closed_profile_curve; the proximal and
distal curves are the band's two faces.

Why the two faces differ (and why we must keep both): an anchor cylinder is uniform along X, so any
X cuts the same ring - the two perimeters are congruent over every anchor region. They differ only
across the elevated-support regions, where a tilted support cylinder cut at two different X values
gives arcs at slightly different Z. That difference is the band's longitudinal taper over the
supports and is structurally important, so we NEVER extrude a single face - we always loft both.
(A useful side effect: at least one anchor region is an exact point correspondence between the two
curves, which we use to align the loft seam.)

Construction (build_splint_solid(proximal_profile, distal_profile) -> closed Brep):
1. Require both perimeters closed (else raise).
2. Precondition for a clean loft:
   - Re-seam both curves to their world +Y extreme (Curve.ChangeClosedCurveSeam). On the congruent
     anchor regions this is an exact correspondence, so the ruled sections line up instead of
     shearing.
   - Match directions (Curve.DoDirectionsMatch; reverse the distal curve if opposed) so the wall
     does not twist into a self-intersection.
3. Straight (ruled) loft between the two curves: Brep.CreateFromLoft([prox, dist], Straight,
   closed=false) -> one open tube wall. Require exactly one surface back.
4. CapPlanarHoles(_CAP_TOL) - both loft ends are planar closed loops, so one call caps both into a
   closed solid.
5. Validate IsSolid (else raise with IsValid / IsManifold / face count); flip if SolidOrientation
   is Inward so the normals face out.

The perimeter is a single outer silhouette with no inner holes; the finger bores are cut in a later
phase by boolean-subtracting the (capped) finger cylinders, which is why the Phase 1 cylinders were
left uncapped until then.

No fallbacks (intentional): straight loft of preconditioned, structurally-similar curves is
reliable, and we want the failure envelope to be visible while sweeping permutations. Every step
raises ValueError on failure rather than degrading (no single-face extrude, no alternate capping).
Known ways it can fail, to watch for while testing:
- Loft returns 0 or >1 surfaces when the two perimeters are too dissimilar (a support region whose
  proximal/distal arcs diverge a lot at extreme elevation, or a seam/direction mismatch that slips
  through preconditioning) -> "did not produce exactly one wall surface".
- CapPlanarHoles returns None if a loft end is not a clean planar closed loop (a self-crossing or
  non-planar perimeter from Phase 5) -> "CapPlanarHoles failed".
- Capped brep not solid (naked edges, non-manifold) -> "not a closed solid".

Usage (per profile plane, then loft):

```python
splint_solid = build_splint_solid(proximal_profile, distal_profile)
```

After the loft, Phase 6 also bores the fingers: build_finger_bores makes one capped solid
cylinder per included finger (the P1 circle swept along the P1 line, doubled in length about its
midpoint so it overshoots both band faces for a clean through-cut), and subtract_finger_bores
boolean-subtracts them from the slab one at a time via BrepDifference.robust_brep_difference (its
seven fallback strategies are welcome here - booleans are historically unreliable - in contrast to
the no-fallback loft). The result is the bored splint_solid that Phase 7 tags and exports.

### Phase 7: finishing (objectID tag, mesh, build-plate orientation)

The finishing phase turns the bored solid into a traceable, print-ready mesh.

#### objectID embossing (emboss_object_id, via TextGun.py)

Recess the 4-character objectID into the inside bottom of the anchor ring nearest the index
finger so every printed part is traceable back to its job. TextGun.emboss_text does the work:
it builds extruded letter breps on a text plane, ray-casts each letter onto the target surface
along a projection vector, and boolean-subtracts them (with robust_brep_difference as a fallback).
emboss_object_id wires it up for this splint:
- Target ring: the lowest-index anchor in the included if->sf run (nearest "if").
- Target surface: the bored anchor's inner wall - so this runs AFTER the Phase 6 subtraction,
  which is what creates that wall.
- projection_origin: the mean of the anchor's proximal and distal full-section area centroids
  (p_full_curves / d_full_curves) - the bore center, mid-band.
- text_projection_vector: world -Z, so the ray from the bore center lands on the bottom inner wall.
- text_up_vector: the profile-plane normal (points along the finger, proximal->distal).
- emboss_inside=True and align_to_surface_normal=True: recess into the curved bore with even depth.

The embossed solid replaces splint_solid. Two design coefficients live in the orchestrator:
objectid_text_size_factor (text height = factor * longitudinal_band_width_mm) and
objectid_extrusion_depth_factor (emboss depth = factor * radial_band_thickness_mm). If the text
reads upside down along the finger, flip the up-vector sign.

#### Mesh conversion (convert_to_export_meshes, splintmeshes.py)

Convert the embossed solid to an export-ready mesh with splintmeshes.convert_to_export_meshes,
the intended final conversion step before saving. It meshes the brep, cleans/welds it, and gates
on a quality check (valid / closed / manifold, repairing once if needed), returning a list of
meshes - one for our single solid, kept as splint_mesh.

#### Build-plate orientation (splint_oriented)

Lay the part distal-face-down for FDM printing. The distal loft cap is a planar face whose
outward normal is the distal-plane normal (~+X), which makes a good flat first layer. Rotate that
normal to world -Z (Transform.Rotation(distal_plane.Normal, -Z, distal_plane.Origin)), then drop
the mesh so its lowest point rests on Z=0. The result is splint_oriented, the geometry handed to
the printer.

#### Data source + saving (integrated into the orchestrator)

generate_relative_motion_splint(raw_data_dev, object_id, is_production, should_save_mesh) owns
both the job I/O and the geometry. is_production selects the data source: dev uses the caller's
raw_data_dev (fast design sweeps); production ignores it and pulls the next inbox job via
splintcommon.load_job_data("RelativeMotion"), reading raw_data = job_data["relative_motion_data"]
and taking objectID + outbox path/name from the job (see RelativeMotion_prod_inbox_data_loader.py).
should_save_mesh gates writing: when set it calls splintmeshes.save_job_output(splint_oriented,
output_dir, root_filename, "3mf", custom_metadata={...}), which writes the mesh plus a sibling
.meta.json (extra per-job data under a "custom" key) for the polling loop; sweeps leave it False.

### Phase 8: splint_factory web form (Design Definition)

This is the most complex input schema we have built, so it does not fit splint_factory's flat
scalar Design Definition (Float/Integer/Text/Boolean, one field per parameter). RelativeMotion
needs a nested, per-finger structure, so it ships a bespoke React form instead of the generic loop.

#### How the data bridges to the geo processor

The generic pipeline is: the new-job form builds parameterValues (Record<string,any>) ->
JSON.stringify -> POST /api/design-jobs -> stored -> written to the inbox job's "params" field ->
splintcommon.extract_server_params_data does job_data = json.loads(params). So job_data is exactly
whatever object the form put in parameterValues. The RelativeMotion loader then reads
raw_data = job_data["relative_motion_data"]. Therefore the form must emit:

    parameterValues = { "relative_motion_data": { is_right_hand, finger_data:[...4],
                        all_splint_finger_circ, relative_elevation_angle, longitudinal_band_width_mm } }

finger_data has one entry per finger in if->mf->rf->sf order; excluded fingers carry null
measurements. This matches the raw_data the geometry consumes (Phases 1-3).

#### Files (all in splint_factory)

- src/designs/relative-motion/definition.json - id/name/algorithmName="RelativeMotion"/isActive;
  inputParameters is [] because the custom form owns the schema (the POST validator loops the flat
  schema, so an empty array accepts the nested payload).
- src/designs/relative-motion/CustomForm.tsx - the bespoke form: hand selector, a per-finger row
  (include -> anchor/supported -> P1 circumference, P1 length, forward offset, slit), and the three
  globals. It assembles the nested payload and reports validity up via onChange / onValidChange.
- src/designs/custom-form-registry.ts - client-safe map of designId -> form component (mirrors
  hints-registry).
- src/designs/registry.ts - added the relative-motion entry.
- src/app/design-jobs/new/page.tsx - if a design has a custom form it renders that instead of the
  generic field loop and disables submit until the form reports valid; nothing downstream changes.

#### Validation enforced in the form (minimal first pass)

At least two anchors and one supported finger; included fingers contiguous (no gaps); the first
included finger is the reference (forward offset forced to 0); only anchor fingers may be slitted;
positive P1 circumference / length / band width / all-fingers circumference; relative elevation
angle within [-120, +45]. Deeper UX/validation is a follow-up.

#### Deployment

The code registry drives /api/designs, but visibility is org-scoped. To make the design usable:
1. Seed the DB row: `cd splint_factory && npx tsx prisma/seed.ts`.
2. Add OrganizationDesign visibility rows for the orgs that should see it.
3. Add public/designs/relative-motion/measurement.png (and preview.png) - placeholders render until then.

#### Follow-ups

- Measurement guide image + richer per-field hints and validation UX.
- Consume is_slitted in the geometry (the form collects it; the pipeline does not use it yet).
- Revisit whether band width and elevation defaults/ranges match Liz's clinical guidance.

### Later phases (future work)

Remaining work (to be specified as we get there):
- Apply is_slitted to the anchor rings (the form collects it; geometry does not consume it yet).
- Use the pipe subtraction method for creating fillets on sharp edges.
- A direction indicator (embedded sphere) marking up / forward for assembly.

