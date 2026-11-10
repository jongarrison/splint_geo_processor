# InfinityFlex2 Splint - Development Reference

## Status and Boundaries

[InfinityFlex2.py](../../src/InfinityFlex2.py) implements the supplied Grasshopper groups
as phases 0-15: preform, band construction, body intersection, direction indicator,
comfort relief, recessed ID, optional slit, build orientation, and ShrinkWrap smoothing.
It returns `SplintResult` with:

- `mesh`: phase-15 `final_smooth_splint`.
- `solid`: phase-14 `oriented_splint`, before mesh smoothing, not a reconstruction of it.
- `metadata`: `algorithm`, `object_id`, `status="smoothed_mesh"`, and `mid_radius_mm`.

The harness exports the completed mesh separately using the shared RelativeMotion exporter.
Production remains on `generators/InfinityFlex.gh`. There is no top-level
`generators/InfinityFlex2.py` runner, factory/database routing switch, or deployment.
This is a working development conversion, not clinical or production certification.

## Running and Inspecting

Open Rhino 8, then run:

```sh
/Users/jon/work_3d/splint_work/splint_geo_processor/generators/dev/InfinityFlex2/run.sh
```

[run.sh](run.sh) delegates to the shared `_devkit/run_harness.sh`.
[harness.py](harness.py) remains a generic fixture/preview/export driver. Its controls are:

| Setting | Default | Effect |
| ------- | ------- | ------ |
| `INPUT_FILES` | `["WQPG.json"]` | Bare measurement fixtures in `inputs/`; filename stem is the object ID |
| `STOP_AFTER` | `None` | Stop after a completed phase; `0.0` stops before geometry |
| `PREVIEW_FILTER` | `set()` | All geometry; numeric phases, `"1.0+"`, or key names restrict previews |
| `ENABLE_MESH_EXPORT` | `True` | Save only when generation returns a complete result |

Even `STOP_AFTER = 15.0` raises after that phase instead of returning a result, so it skips
export. Use `None` for a complete exporting run. Reruns replace only `InfinityFlex2_`
preview objects, not unrelated Rhino geometry.

Generated, git-ignored outputs include `last_run_report.txt`, `last_run_report.done`,
`DEV_InfinityFlex2_WQPG.3mf`, and its `.meta.json` sidecar. The `.done` file signals that
the harness finished, not that it succeeded; inspect the report for `HARNESS EXCEPTION`.

### Inputs and Units

[inputs/WQPG.json](inputs/WQPG.json) contains the six bare measurements below.
[inputs/PROD_EXAMPLE_WQPG.json](inputs/PROD_EXAMPLE_WQPG.json) preserves the downloaded
production envelope; do not select it as a harness fixture. Its JSON-encoded `params`
was decoded locally without changing production routing. `object_id` is supplied separately.

| JSON field | WQPG | Geometry meaning |
| ---------- | ---- | ---------------- |
| `root_circumference_mm` | 61 | Proximal shaft midpoint circumference |
| `mid_circumference_mm` | 58 | PIP circumference |
| `tip_circumference_mm` | 48 | Middle-phalanx shaft midpoint circumference, not fingertip circumference |
| `inter_phalanx_distance_mm` | 34 | Both full model phalanx lengths; also controls the PIP-centered trim |
| `flexion_degrees` | 40 | PIP flexion angle |
| `include_slit` | false | Enable the rectangular bottom slit |

Lengths are millimeters and angles are degrees unless explicitly converted for Rhino APIs.
Clinical landmarks, accepted measurement ranges, and the graph's ambiguous length annotation
still need confirmation; the implementation follows the supplied Python node's mapping.

### Shared Design Constants

| Constant | Value | Used By |
| -------- | ----- | ------- |
| `AXIAL_BAND_THICKNESS_MM` | 1.2 mm | Preform thickness, indicator sizing, lettering depth |
| `CENTRAL_BAND_STRAP_WIDTH_MM` | 4.0 mm | Bottom band and profile rise |
| `EXTENT_BAND_STRAP_WIDTH_MM` | 4.0 mm | Preform trim margin and top bands |
| `MID_JOINT_RISE_FACTOR` | 1.0 | Profile's central construction point |

Keep these module-level constants separate from patient JSON. The band-group screenshots
display 5 mm width panels, but `generate()` uses the earlier internal constants above.
Other group-specific settings are listed with their phases below. Phase-15 slider values
are constants inside its function, as agreed.

### Coordinates

Phase 1 follows FingerModel: +X distal, +Z dorsal, -Z volar, and Y lateral. WQPG starts
with MCP at `(20, 0, 0)` and PIP at `(54, 0, 0)`; positive flexion bends toward -Z.
Phase 4 rotates the model about the world origin without recentering it at the joint.
Subsequent shell, band, and joint references use that oriented construction frame.

The XZ plane is constructed explicitly with +X/+Z axes; its normal is -Y.
RhinoCommon has no `Plane.WorldXZ`. A "nudge" can move reference geometry rather than
material. North stars and the specified world-axis nudges are not transformed with the model.

## Function and Debug Conventions

- Function names follow Grasshopper group names. Left-side named nodes and panels are explicit
  arguments; only `generate()` reads `raw_data` and wires inter-phase results.
- Every right-side named value node is a named result field, including debug-named outputs.
  Optional `debug` is for observability, never the only way to obtain a required output.
- All group functions accept keyword-only `debug=None`, omitted from the contracts below.
  Geometry inputs are not mutated. Disabled `slit_bot` returns its input unchanged.
- `_run_phase()` centralizes phase-local debug capture, failure preservation, and completion
  logging. It does not choose geometry inputs, defaults, or operations.
- `PhaseTracker` records `{number, title, keys}` in `debug["_phases"]`, then enforces early
  stopping. A failed group keeps available partial geometry without marking the phase complete.
- Shared `bake_phase_geometry()` discovers registered geometry, including nested containers;
  scalars/vectors remain data. Unregistered partial geometry appears as unfinished construction.
  Reused output keys in `debug` refer to the latest stage; named results retain stage ownership.
- Live Rhino keeps imports cached. Reload changed dependencies explicitly; the dispatcher
  clears relevant bytecode caches. Import `StopAfterPhase` after reloading the algorithm because
  its reload of `splint_generator` recreates the exception class.

Group and cluster screenshots are retained in `gh_screenshot_data/`. These contracts record
the interpreted graph and selected live-component checks, not blanket Grasshopper parity.

## Phase Contracts

### 0 - Input Receipt

`InfinityFlex2Generator.generate(raw_data, object_id, debug=None, stop_after=None)` validates
the outer input/object-ID types and records both inputs before geometry. It returns the
completed `SplintResult` described above; phase 0 itself has no geometry output.

### 1 - Finger Preform Solid

Function: `finger_preform_solid`.

**Inputs:** `axial_band_thickness_mm` and `extent_band_width` from the design constants;
`root_circ`, `mid_circ`, `tip_circ`, `flexion_degrees`, and `inter_phalanx_distance` from
the corresponding JSON measurements.

**Outputs:** `FingerPreformSolidResult`: `inner_shell_brep`, `outer_shell_brep`,
`shell_components`, `material_shell_brep` (Breps), `splint_centerline` (`PolylineCurve`),
and `mid_r` (`float`). `shell_components` is a list of the outer construction Breps.

Build an untrimmed core and a radially thickened, PIP-centered trimmed outer model using
`FingerModel`. Extrapolate joint circumferences as `max(2, 2*root-mid)` and
`max(2, 2*tip-mid)`. Both phalanx lengths use the full supplied distance; preserve the
20 mm metacarpal offset. Trim at `+/- (distance + extent_band_width)/2` about PIP, then
subtract the untrimmed core through `BrepDifference` at 0.001 mm tolerance.
`mid_r = mid_circ / (2*pi)`. Individual outer components are not trimmed by this wrapper.

### 2 - Central Finger Line Geometry

Function: `central_finger_line_geometry`.

**Input:** phase-1 `splint_centerline`.

**Outputs:** `CentralFingerLineGeometryResult`: `first_segment_line`, `first_segment_start`,
`first_segment_end`, `first_segment_direction` (unit vector), `second_segment_line`,
and `second_segment_end`.

Extract polyline segments 0 and 1 exactly, preserving their direction/order. Missing or
degenerate segments raise; this is not an approximation or fit.

### 3 - Alignment Work

Function: `alignment_work`.

**Inputs:** phase-2 `first_segment_start`, `second_segment_end`.

**Output:** `AlignmentWorkResult.average_center_line` (`LineCurve`).

Join the endpoints directly. Despite its name, this is the bent centerline's straight chord,
not a vector average or curve fit.

### 4 - Preform Orientation Adjustment

Function: `preform_orientation_adjustment`.

**Inputs:** phase-1 `outer_shell_brep`, `material_shell_brep`, `inner_shell_brep`,
`shell_components`; phase-2 `first_segment_direction`, `first_segment_line`,
`second_segment_line`; phase-3 `average_center_line`.

**Outputs:** `PreformOrientationAdjustmentResult`: oriented `material_shell_brep`,
`outer_shell_brep`, `inner_shell_brep`, `first_segment_line`, `second_segment_line`,
`shell_components`; `first_orientation_shift_transform`; `joint_centroid` (`Point3d`).

Measure the oriented XZ angle from chord direction to first-segment direction. Rotate
copies about world origin and -Y, then compute the volume centroid of rotated component 1
(the PIP sphere). This aligns the chord but does not put the joint at the origin.

### 5 - Bottom Center Band Creation

Function: `bottom_center_band_creation`.

**Inputs:** `bottom_band_width_mm=4.0`; phase-4 `joint_centroid`, `outer_shell_brep`.

**Output:** `BottomCenterBandCreationResult.bottom_band` (`LineCurve`).

Place a +X-facing center slice at the joint's X and intersect the oriented outer shell.
Require one section curve. Find its closest point to `joint_centroid - 75*Z`, center a
+X width guide there, move it -100 mm in Z, then gravitate it to the shell with a -1 mm
Z output nudge.

Nested function: `line_to_brep_gravitation_cl(input_line, output_nudge_motion, input_brep)`
returns `LineToBrepGravitationCLResult.output_line`. It delegates to
`FeatureIdentification.line_to_brep_gravitation`: six perpendicular sections at parameters
0, 0.2, ..., 1; flattened curves and longest-list matching; closest paired point by distance.
Translate the line using its original start X/Y, the selected closest Z, and the full nudge.
Do not bend/rotate it or substitute the separate ray-based nudge helper. Sampling is not a
continuous collision solver and assumes the radar line starts outside the Brep.

### 6 - Top Bands Creation

Function: `top_bands_creation`.

**Inputs:** phase-4 `outer_shell_brep`; `root_north_star=(-200,0,100)`,
`north_star_nudge=(0,0,3)`, `top_band_width_mm=4.0`, `tip_north_star=(300,0,100)`.

**Outputs:** `TopBandsCreationResult.root_top_band`, `tip_top_band` (`LineCurve`).

Find each north star's closest shell point and add the world nudge. Root guides run +X;
tip guides run -X. Test root rotations 0, 9, ..., 90 degrees and negated tip rotations,
about each guide's start with XZ normal -Y. Keep the first surface-clearing candidate.

The shared cluster `FeatureIdentification.brep_ref_point(nudge_amount, north_star_pt, brep)`
returns `result_point` (closest point plus vector nudge) and `distance` (before nudge).
It does not offset along the surface normal.

### 7 - Side Cutter Profile

Function: `side_cutter_profile`.

**Inputs:** phase-5 `bottom_band`; phase-6 `root_top_band`, `tip_top_band`;
`bottom_band_width_mm=4.0`, `mid_joint_rise_factor=1.0`.

**Outputs:** `SideCutterProfileResult.tail_slant_line`, `tip_slant_line` (`LineCurve`),
`cutter_curve` (closed `Curve`).

The tail slant runs bottom-start to root-start; the tip slant runs tip-start to bottom-end.
Raise the central construction point by width*rise factor in +Z. Join seven straight
pieces and close their central gap with a G2 blend, bulge 0.6 at both ends. This follows
the inspected Connect Curves defaults: curvature continuity and Close=True.
Project only the final closed profile onto world XZ; retain original slant coordinates
and directions for wall intersections and build orientation.

### 8 - Side Cutter Extrusions

Function: `side_cutter_extrusions`.

**Inputs:** phase-7 `cutter_curve`, `tip_slant_line`, `tail_slant_line`; phase-4
`outer_shell_brep`, `inner_shell_brep`; `nudge_mm=-50.0`.

**Outputs:** `SideCutterExtrusionsResult.side_cutter_surface` (planar `Brep`),
`side_cutter_volume` (closed `Brep`), plus `tip_outer_curve`, `tail_outer_curve`,
`tip_inner_curve`, `tail_inner_curve` (each a `list[Curve]`, despite singular names).

Move copies by nudge*Y; extrude by -2*nudge*Y. Cap the filled profile to create the solid
cutter and flip it outward if needed. Slants create open sheets for shell intersections.
Preserve all section pieces, including successful empty lists; do not choose a first or
longest curve. With current settings the extrusions span -50 to +50 mm in Y, retaining
any original slant Y offsets.

### 9 - Splint Body Intersection

Function: `splint_body_intersection`.

**Inputs:** phase-4 `material_shell_brep`, phase-8 `side_cutter_volume`.

**Output:** `SplintBodyIntersectionResult.splint_body` (`Brep`).

Retain shared material, not a subtraction. `BrepIntersection.robust_brep_intersection`
owns native attempts, diagnostics, and future fallbacks. It validates closed manifold
solids, positive signed volume, total volume bounded by the smaller input (0.1% allowance),
and containment of bounds/boundary samples within five document tolerances.
These are sanity checks, not proof of complete/exact intersection.

The group requires exactly one accepted body; multiple pieces remain visible in debug
and raise rather than selecting the largest. Empty native output also raises because it
cannot distinguish disjoint geometry from kernel failure. A containment fallback is not
implemented; a fully contained-box test returned empty and was correctly rejected.

### 10 - Direction Indicator

Function: `direction_indicator`.

**Inputs:** phase-9 `splint_body`, phase-6 `tip_top_band`; `wall_thickness_mm=1.2`,
`north_star_nudge_mm=5.0`, `nub_position_parameter=0.5`, `nub_sinking_factor=0.1`,
`ball_r_to_wall_ratio=0.65`.

**Output:** `DirectionIndicatorResult.finished_splint` (`Brep`).

Reuse `Orienteering.positive_sphere_direction_indicator` and its robust union. Extend the
band, project endpoints to the body, place a sphere at the specified fraction between them,
and shift it outward by sinking_factor*radius. Radius is wall_thickness*ratio (0.78 mm).
Unlike the graph's world-Z displacement, the shared implementation resolves a local outward
normal using an inside probe. Reload `BrepUnion` before `Orienteering` in live Rhino.

### 11 - Comfort Lip Trimmers

Function: `comfort_lip_trimmers`.

**Inputs:** phase-8 `tip_outer_curve`, `tail_outer_curve`, `tip_inner_curve`,
`tail_inner_curve`; phase-10 `splint_brep=finished_splint`; phase-4 `joint_centroid`;
phase-1 `mid_r`; `bottom_slice_x_scale=0.5`, `radius_multiplier=1.29`,
`chamfer_side_limit_angle_degrees=-74.0`.

**Output:** `ComfortLipTrimmersResult.comfort_trimmed_splint_brep` (`Brep`).

This is localized lip relief, not general edge filleting. A finite world-XY/+Y center
slice is scaled only in X about its centroid, then moved by `-mid_r*radius_multiplier`
in world Z to find bottom landmarks on the outer sections. For lateral landmarks,
rotate **WorldXY**, not XZ, and the +Y view vector about +X by each signed angle.
Move each resulting finite slice along **its own rotated Z axis** by the same negative
radius-scaled amount. This places slices on opposite sides. Intersect inner sections.

Each landmark examines all curve pieces, deduplicates coincident hits within document
tolerance, and requires exactly one distinct point. Empty, multiple, overlapping, and
failed intersections raise. Construct each capped cutter from the joint and three landmarks:
tip uses `(y_min, bottom, y_max)`, tail uses `(y_max, bottom, y_min)`.
Subtract tip then tail through `BrepDifference`; require closed manifold outputs with
positive, strictly decreasing volumes.

Shared cluster helpers in `FeatureIdentification`:

- `ext_ln_pts_cl(start_point, mid_point, extension_dist)` returns `end_point` and
  `extend_line`. Here `mid_point` means the existing ray endpoint; extend only beyond it.
- `cone_creator_cl(splint_center_point, point_a, point_b, point_c)` returns `brep`.
  Extend the three rays by 10 mm, build circles at normalized positions 0.1 and 1.0,
  loft with Normal settings, cap, and orient outward. This is a truncated, possibly oblique
  loft, not a native apex cone. Nested construction geometry remains available in debug.

### 12 - ID Embossing Gun

Function: `id_embossing_gun`.

**Inputs:** phase-11 `finished_splint_brep`; generator `object_id`; `wall_thickness_mm=1.2`;
phase-4 `projection_centroid=joint_centroid`; phase-1 `mid_r`;
`projection_origin_z_factor=0.3`, `north_star_nudge_mm=100.0`,
`lower_angle_from_lip_degrees=-15.0`, `text_size=2.7`, `extrusion_depth_factor=0.5`.

**Outputs:** `IDEmbossingGunResult.splint_with_embossed_id` (`Brep`),
`projected_letters_debug` (`list[Brep]`, a required named output).

Raise the projection origin by factor*mid_r in world Z. Reposition a finite +X-facing
center slice, retaining its bbox-center X and replacing Y/Z with the raised origin's.
Intersect the comfort-trimmed body and choose the globally closest section point to
`(sliceX, 100, 100)`; these are absolute north-star Y/Z coordinates, not origin offsets.
Consider every section piece and reject distinct tied minima within document tolerance.

Negate the unsigned angle from +Y to the origin-to-lip vector, then add the lower-angle
setting. Rotate +Y projection and +Z text-up together about +X, retaining a +X baseline.
Call unchanged `TextGun.emboss_text` with `emboss_inside=True` and
`align_to_surface_normal=True`: despite the group name, this recesses ID material.
The graph's `x` Boolean was only a recompute trigger and is omitted.

Require complete letter projections, a valid closed manifold body, and net volume decrease.
TextGun can warn and skip individual Boolean cuts; projected counts and net decrease do
not prove every letter was cut. WQPG's section has four raw fragments joining to two open
traces, so full graph data-tree parity remains unverified.

### 13 - Slit Bot

Function: `slit_bot`.

**Inputs:** JSON `include_slit`; phase-12 `splint_brep=splint_with_embossed_id`;
`slit_thickness=0.3`, `slit_panel_z_factor=-0.7`.

**Outputs:** `SlitBotResult.splint_brep_w_slit` (`Brep`), `slit_sharp_edges` (`list[Curve]`).

Disabled: return the same input body and empty edges before imports/construction.
Enabled: obtain a finite world-XY/+Y center panel, move it by factor*bbox_Z_size in world Z,
and call `BrepSlit.cut_slit` on a copy. Keep the legacy `fillet_radius=0.3` argument,
but shared finishing is disabled: this is a rectangular, unfilleted cut.
Duplicate curves from the union of both edge-index lists. Require valid nonempty edges,
a closed manifold body, and positive decreasing volume; requested-slit failures abort.
RelativeMotion's rounded RingSlit strategy remains deferred.

### 14 - Orienting To Build Plate

Function: `orienting_to_build_plate`.

**Inputs:** phase-13 `splint_brep_w_slit`, phase-7 `tip_slant_line`.

**Output:** `OrientingToBuildPlateResult.oriented_splint` (`Brep`).

Reverse a copied tip slant, matching Grasshopper Flip Curve. Measure its oriented XZ angle
to +X and rotate a copied body about -Y through the reversed start (original end).
The reference becomes +X-parallel and its pivot stays fixed. The graph only rotates:
**there is no Z=0 translation**, so bed-height placement remains unresolved.

### 15 - Joining And Smoothing

Function: `joining_and_smoothing`.

**Input:** phase-14 `oriented_splint`.

**Output:** `JoiningAndSmoothingResult.final_smooth_splint` (`Mesh`).

Use internal constants target edge length 0.12 mm and 8 smoothing iterations. Explicitly
set the inspected Grasshopper defaults: offset 0, optimization 10%, FillHoles=True
(RhinoCommon defaults the latter to False).

Mesh a copied Brep with `MeshingParameters.Default`, append **all face meshes into one
mesh**, then call its `ShrinkWrap(parameters)` instance method. Wrapping separate Brep
faces with hole filling can seal the finger bore. The static mesh-list overload is also
ambiguous in Rhino's Python binding. Log native tessellation gaps and let the requested
hole filling operate on the combined mesh, without silently substituting repairs.
Require a valid, closed, consistently oriented manifold mesh, no boundary, one component,
and finite positive volume.

## Export Boundary

There is no geometry phase 16 or algorithm-specific save wrapper. The harness calls
unchanged `SplintMeshes2.export_mesh_with_metadata(result.mesh, output_dir, root_filename,
"3mf", custom_metadata=result.metadata, emit_pipeline_signal=False)` after a complete run.
This is the same exporter used by `prod_runner.run_production_job`.

The graph's save inputs map to `result.mesh`, harness-local `outputs/`,
`DEV_<algorithm>_<object_id>`, and `ENABLE_MESH_EXPORT`. The exporter returns `file_path`,
`file_size_bytes`, and `mesh_quality`, and writes timing, diagnostics, and `custom` generator
metadata into the sidecar. The harness marks generation start before the build. Early stops
skip export, failures propagate, and dev runs emit no production completion sentinel.

## Validation Baseline

Live Rhino 8, WQPG with `include_slit=false`, checked 2026-11-09:

| Checkpoint | Observed Result |
| ---------- | --------------- |
| Preform | MCP/DIP circumferences 64/38 mm; phalanx lengths 34/34 mm; trim +/-19 mm |
| Radius / oriented joint | `mid_r=9.2309866993`; joint `(50.7434015214, 0, 18.4690877467)` mm |
| Body / indicator | 609.473507 -> 609.650900 mm^3; nub radius 0.78 mm |
| Comfort relief | 609.650900 -> 596.648201 -> 584.490781 mm^3; six distinct landmarks |
| Lateral slice motions | `(0, -11.446678, -3.282282)` and `(0, +11.446678, -3.282282)` mm |
| Recessed WQPG | Four successful letter cuts; volume 577.795310 mm^3 |
| ID aiming | Lip angle 43.685285 degrees; projection angle -58.685285 degrees |
| Build orientation | 316.844765 degrees about -Y; minimum Z approximately 5.461794 mm |
| ShrinkWrap | Brep 577.794055 -> mesh 559.255018 mm^3; 33724 vertices, 38806 faces |
| Export | Valid millimeter 3MF; 33724 vertices, 67452 triangles; matching metadata |

The inspected Grasshopper ShrinkWrap component returned the same mesh counts and volume.
Rigid rotation changes Rhino's volume estimate by about 3.2 ppm; orientation checks also
compare transformed vertices and allow 10 ppm rather than demanding exact volume equality.
Focused checks cover source preservation, finite-slice landmarks, retained indicator,
phase-runner success/failure/early-stop behavior, all phases, preview discovery, and export.
The slit-enabled branch was exercised earlier, before the lateral comfort correction;
the current full-pipeline baseline above does not validate slit retention after smoothing.

## Remaining Work

- Confirm clinical measurement semantics, allowed ranges, and design defaults, including
  the 4 mm internal widths versus the screenshots' 5 mm panels.
- Review final bed-height placement, optional rounded slit geometry, and production integration
  separately; none is silently introduced by this port.
- Add Boolean-intersection fallbacks centrally when needed, retaining strict rejection checks.
- Consider strict per-letter cut reporting in shared TextGun; current validation cannot prove
  every individual subtraction succeeded for arbitrary IDs.
- Validate fit, wall thickness, lip comfort, lettering, and slit width after ShrinkWrap across
  representative measurements. Current checks are not clinical validation or full graph parity.