# FingerGutter splint - Dev Notes

Planning document for a new Design Definition: the **FingerGutter** splint. This name is also the
"algorithm name" that links the processing stages together (web design definition -> job json ->
geo processor dispatch -> `.py` generator).

This doc follows the incremental, collaborative dev process we established while building the
RelativeMotion splint (see
[splint_geo_processor/generators/dev/RelativeMotion/DevNotes_RelativeMotion_splint.md](splint_geo_processor/generators/dev/RelativeMotion/DevNotes_RelativeMotion_splint.md)
and [splint_geo_processor/generators/README.md](splint_geo_processor/generators/README.md)). We
start with geometry in Python modules (observable/testable in Rhino via the dev harness), let that
reveal the parametric inputs, and only then build the splint_factory web form + Design Definition
db data.

Most of the work lives in **splint_geo_processor** (the polling geometry processor), with a
meaningful amount later in **splint_factory** (web server + UI: the input form, measurement guide,
and Design Definition schema/db data).

Right now this is a **planning exercise only**. No code or geometry has been written yet - this
document defines the scaffolding to create and specifies Phases 0-3.

## What a FingerGutter splint is (and how it differs from RelativeMotion)

A finger gutter splint is a trough (a "gutter" / half-pipe channel) that cradles a single finger
along its length to immobilize it. Unlike RelativeMotion's multi-finger rings-and-support-arcs
form, FingerGutter is one open channel: we build the finger's anatomical volume, thicken it into a
shell, then trim away the dorsal cap so the finger seats into the remaining **volar trough** (open
on the dorsal/back side).

| | RelativeMotion | FingerGutter |
| --- | --- | --- |
| Fingers | multi-finger (anchors + supported) | single finger (this splint) |
| Primary form | closed rings on anchors + support arcs | one open trough / channel |
| Base geometry | circles/cylinders per finger | one anatomical `FingerModel` (core + shell) |
| Main operations | perimeter walk + loft + bore | shell subtract, then trough trim |

The big reuse is [FingerModel.py](splint_geo_processor/generators/src/FingerModel.py): it builds a
full anatomical finger (jointed, tapered, optionally shelled) and exposes the joint frames we need
to define the trough. A working end-to-end example of the core/shell/subtract pattern already
exists in
[splint_geo_processor/generators/src/FingerModel_usage_example.py](splint_geo_processor/generators/src/FingerModel_usage_example.py)
(extracted from `InfinityExtend.gh`); Phase 1 follows it closely.

## General dev conventions used throughout (carry over from RelativeMotion)

These two patterns are how the whole system stays observable and debuggable; every FingerGutter
function should follow them.

### Phase reporting via `tracker.log_phase`

The generator subclasses `SplintGenerator`
([splint_geo_processor/generators/src/splint_generator.py](splint_geo_processor/generators/src/splint_generator.py))
and drives a `PhaseTracker`. After each construction phase we call
`tracker.log_phase(<phase_number>, "<label>", key1=geom1, key2=geom2, ...)` to publish that phase's
**key construction geometry** by name. See `RelativeMotionGenerator.generate()` in
[splint_geo_processor/generators/src/RelativeMotion.py](splint_geo_processor/generators/src/RelativeMotion.py)
for the pattern (e.g. `tracker.log_phase(1.0, "finger positions", mcp_points=..., p1_lines=...)`).
The tracker:
- Populates the `debug` dict (see below) with every named geometry, so the dev harness can bake and
  colour-layer each phase's outputs for hand inspection in Rhino.
- Enforces `stop_after`: passing a phase number halts the pipeline right after that phase (raising
  `StopAfterPhase`) so we can develop and preview one phase at a time.
- `tracker.add(**kwargs)` publishes extra geometry without marking a new phase boundary.

FingerGutter numbers its phases 0.0 (inputs), 1.0 (finger core/shell/blank), 2.0 (trim solid),
3.0 (subtract), and onward as later phases are designed.

### `debug` dicts as out-parameters

Throughout the geometry helpers we thread a caller-supplied `debug` dict as an out-parameter (e.g.
`cut_ring_slit(..., debug=slit_debug)`, `chamfer_bore_rim(..., debug=chamfer_debug)`,
`build_support_path_ramp(..., debug=ramp_debug)` in RelativeMotion). The contract:
- The helper **progressively** writes its intermediate breps/curves/planes into `debug` as it
  builds them - not just at the end.
- So even when the helper raises partway through, whatever it built before the failure is still in
  the caller's dict, ready to bake. This is what makes failures bisectable ("did the cutter build?
  did the intersection curve join?") instead of opaque.
- The caller forwards the useful `debug` entries into `tracker.log_phase(...)` for previewing, and
  can keep separate success/failure buckets (RelativeMotion does this for slits and ramps).

Every non-trivial FingerGutter helper should accept `debug=None` and populate it this way.

## Scaffolding to create

Mirror the RelativeMotion layout. Generic harness plumbing stays shared in
`splint_geo_processor/generators/dev/_devkit/`; we only add splint-specific files.

- `splint_geo_processor/generators/dev/FingerGutter/` - this splint's dev folder (exists, holds
  this doc).
  - `splint_geo_processor/generators/dev/FingerGutter/DevNotes_FingerGutter_splint.md` - this
    planning/spec doc (exists).
  - `splint_geo_processor/generators/dev/FingerGutter/harness.py` - dev harness. Copy
    [splint_geo_processor/generators/dev/RelativeMotion/harness.py](splint_geo_processor/generators/dev/RelativeMotion/harness.py)
    and strip it to FingerGutter specifics: the `INPUT_FILES` list, the call into
    `FingerGutterGenerator.generate()`, and which `debug` keys to bake. All generic
    bake/report/layout plumbing stays imported from
    [splint_geo_processor/generators/dev/_devkit/bake_utils.py](splint_geo_processor/generators/dev/_devkit/bake_utils.py).
  - `splint_geo_processor/generators/dev/FingerGutter/run.sh` - thin wrapper that execs
    `../_devkit/run_harness.sh` against this folder's `harness.py` (identical in shape to
    [splint_geo_processor/generators/dev/RelativeMotion/run.sh](splint_geo_processor/generators/dev/RelativeMotion/run.sh)).
  - `splint_geo_processor/generators/dev/FingerGutter/inputs/` - dev input json payloads (one full
    `raw_data` per file). Start with a single simple case (e.g. `.../inputs/basic.json`) and grow
    the set as the schema firms up.
  - `splint_geo_processor/generators/dev/FingerGutter/outputs/` - harness run artifacts (report
    file, optional exported meshes). Created automatically by the harness; nothing to author by
    hand.

- `splint_geo_processor/generators/src/FingerGutter.py` - central point of contact for the
  algorithm. Defines `FingerGutterGenerator(SplintGenerator)` with `generate(raw_data, object_id,
  debug=None, stop_after=None) -> SplintResult`, matching the `RelativeMotionGenerator` shape so the
  dev harness and production runner call it identically. All geometry logic lives here (or in helper
  modules it imports and reloads). It imports and uses `FingerModel.py`.

- `splint_geo_processor/generators/FingerGutter.py` (production runner shim) - mirrors
  [splint_geo_processor/generators/RelativeMotion.py](splint_geo_processor/generators/RelativeMotion.py):
  adds `src/` to `sys.path`, `sys.modules.pop("FingerGutter", None)` before import (defeats the
  keepRhinoAlive module cache), then dispatches via `prod_runner.run_production_job()`. **Create
  this only when we reach Mode 3 (local server/processor) validation** - not needed for Phase 0-3
  harness work.

Later, on the splint_factory side (deferred until the geometry tells us exactly which inputs it
consumes):
- `splint_factory/src/designs/` - a `FingerGutter` Design Definition (the Phase 0 schema below,
  conforming to `splint_factory/src/designs/design-definition.schema.json`) plus its db seed data.
- A web input form and a measurement guide (the form enforces the Phase 0 validation rules,
  including include-flag contiguity - RelativeMotion has an analogous "which adjacent fingers"
  contiguity UI).

## Shared modules we already have

- [splint_geo_processor/generators/src/FingerModel.py](splint_geo_processor/generators/src/FingerModel.py) -
  anatomical finger builder (Phases 1-2 lean on this).
- [splint_geo_processor/generators/src/FingerModel_usage_example.py](splint_geo_processor/generators/src/FingerModel_usage_example.py) -
  the core/shell/subtract worked example Phase 1 follows.
- [splint_geo_processor/generators/src/splint_generator.py](splint_geo_processor/generators/src/splint_generator.py) -
  `SplintGenerator`, `SplintResult`, `PhaseTracker`, `StopAfterPhase`.
- [splint_geo_processor/generators/src/splintcommon.py](splint_geo_processor/generators/src/splintcommon.py) -
  `log()` and shared utilities.
- `BrepDifference.robust_brep_difference` - the boolean-subtract helper (with fallback strategies)
  used for both the shell hollowing (Phase 1) and the trough trim (Phase 3).
- `splint_geo_processor/generators/dev/_devkit/bake_utils.py` and
  `splint_geo_processor/generators/dev/_devkit/run_harness.sh` - shared harness plumbing.

## Anatomy and coordinate conventions

Single finger, one of index ("if"), middle ("mf"), ring ("rf"), or small ("sf"). Segments in
root->tip order (matching `FingerModel` nomenclature): **metacarpal, mcp, proximal, pip, middle,
dip, distal, tip**. Parameter/variable names lead with the anatomy name they concern
(e.g. `proximal_len_mm`, `mcp_flex_deg`).

Two distinct coordinate frames are in play - keep them straight:

- **World / FingerModel build frame** (the finger as built by `_build_finger_model`): +X = distal
  (finger length), +Y = lateral (flexion axis), +Z = dorsal (the model builds with "palm faces
  -Z"). This is the same world convention RelativeMotion uses. Phase 1 keeps the finger in this
  native frame (no reorientation), so world +Y = lateral and world +Z = dorsal throughout.
- **Per-location perp frame** (returned by `FingerModelResult.get_perp_frame(name, offset)`):
  **ZAxis = finger direction, YAxis = dorsal, XAxis = lateral** (see
  [FingerModel.py](splint_geo_processor/generators/src/FingerModel.py#L706)). At a joint with
  `offset=0` the frame's Z is the bisector of the two adjoining phalanx directions. For a straight
  finger the perp-frame dorsal (+Y) coincides with world +Z; for a flexed/deflected finger it
  tilts. **This matters for Phase 2** (see the hole flagged there).

## Phase 0: input parameters (`raw_data` schema)

All parameters the splint_factory form will collect, ordered root (metacarpal) -> tip. Booleans
`*_include` select which contiguous run of segments the gutter covers (the therapist is describing
either a whole finger or a sub-segment). The UI enforces that the included segments are
**contiguous** (no gaps), analogous to RelativeMotion's contiguous-finger rule.

Design constant (NOT a user input; lives in `FingerGutter.py`): `shell_thickness_mm = 2.25`. This
is the wall thickness of the gutter, applied via `FingerModel`'s own shell mechanism (it grows the
appropriate radii internally - see Phase 1).

### Global

| Parameter | Meaning | Maps to |
| --- | --- | --- |
| `gutter_depth_mm` | Inner depth of the trough to retain - how high the trough walls rise, measured **up from the volar (palm-side) surface** of the finger. | Phase 2 cut offset |

### Metacarpal / MCP

| Parameter | Meaning | Maps to `FingerParams` |
| --- | --- | --- |
| `metacarpal_stub_include` | Gutter starts partway along the metacarpal | `start_at="metacarpal"` |
| `metacarpal_stub_len_mm` | Length of the metacarpal portion | `metacarpal_len` |
| `mcp_include` | Include the MCP joint | segment-range start |
| `mcp_circumference_mm` | Circumference at the MCP joint | `mcp_circ` |
| `mcp_flex_deg` | MCP flexion (toward palm) | `mcp_flex` |
| `mcp_lateral_deg` | MCP side-to-side deflection | `mcp_lateral` |

### Proximal phalanx (P1) / PIP

| Parameter | Meaning | Maps to `FingerParams` |
| --- | --- | --- |
| `proximal_include` | Include the proximal phalanx | segment range |
| `proximal_circumference_mm` | Mid-shaft circumference of P1 (bulge; None = taper) | `proximal_mid_circ` |
| `proximal_len_mm` | Length of the proximal phalanx | `proximal_len` |
| `pip_include` | Include the PIP joint | segment range |
| `pip_circumference_mm` | Circumference at the PIP joint | `pip_circ` |
| `pip_flex_deg` | PIP flexion | `pip_flex` |
| `pip_lateral_deg` | PIP side-to-side deflection | `pip_lateral` |

### Middle phalanx (P2) / DIP

| Parameter | Meaning | Maps to `FingerParams` |
| --- | --- | --- |
| `middle_include` | Include the middle phalanx | segment range |
| `middle_circumference_mm` | Mid-shaft circumference of P2 | `middle_mid_circ` |
| `middle_len_mm` | Length of the middle phalanx | `middle_len` |
| `dip_include` | Include the DIP joint | segment range |
| `dip_circumference_mm` | Circumference at the DIP joint | `dip_circ` |
| `distal_len_mm` | Length of the distal phalanx (DIP -> tip) | `distal_len` |
| `dip_flex_deg` | DIP flexion | `dip_flex` |
| `dip_lateral_deg` | DIP side-to-side deflection | `dip_lateral` |

### Distal phalanx (P3) / tip

| Parameter | Meaning | Maps to `FingerParams` |
| --- | --- | --- |
| `tip_include` | Include the distal phalanx + fingertip | `end_at="tip"` |
| `tip_circumference_mm` | Circumference near the fingertip | `tip_circ` |

### Holes to poke in Phase 0 (confirm before coding the schema)

- RESOLVED - `distal_len_mm` (DIP -> tip distal-phalanx length) replaces the earlier `dip_len_mm`.
- RESOLVED - `gutter_depth_mm` is measured up from the volar (palm-side) surface; this is a volar
  gutter (retain volar + sides, open dorsal). See Phase 2/3.
- RESOLVED - **Phalanx vs joint circumferences**: `proximal_circumference_mm` /
  `middle_circumference_mm` are mid-shaft (bulge) measurements and map to `proximal_mid_circ` /
  `middle_mid_circ`; the knuckle measurements (`mcp/pip/dip/tip_circumference_mm`) map to the joint
  circs (`mcp_circ`/`pip_circ`/`dip_circ`/`tip_circ`).
- **Handedness + finger identity**: your list has no `is_right_hand` or `finger_abbr`. We likely
  need at least handedness (lateral-deflection sign, and objectID emboss side later) and probably
  which finger it is (measurement guide / records). Flagging as likely-required additions rather
  than inventing them into the schema.
- **Required-circ coupling**: `FingerModel.validate_for_segment_range()` requires the joint circs at
  both ends of every rendered segment. So the `*_include` flags don't just pick geometry - they
  dictate which circumferences the form must make mandatory (e.g. rendering the proximal phalanx
  requires both `mcp_circumference_mm` and `pip_circumference_mm`). The form's required-field logic
  must follow the same rule.

## Phase 1: finger core, shell, and splint blank

Goal: build the anatomical finger and turn it into a hollow-wall blank of constant
`shell_thickness_mm`, exactly as
[FingerModel_usage_example.py](splint_geo_processor/generators/src/FingerModel_usage_example.py)
does. Nothing gutter-shaped yet - Phase 1 just produces the solid we'll later trim.

`shell_thickness_mm = 2.25` (design constant). `FingerModel` does the shell math internally: setting
`FingerParams.shell_thickness` grows the appropriate radii so the shell model is a uniform offset of
the core model.

Steps:
1. Build a `FingerParams` (`core_params`) from `raw_data`: map the Phase 0 fields per the tables
   above, and set `start_at`/`end_at` from the contiguous `*_include` run (first included segment ->
   last included segment).
2. `finger_core = create_finger_model_result(core_params)` - the `FingerModelResult` whose
   `finger_brep` is the **outer surface of the finger** (the void the finger occupies; no shell).
3. `shell_params = replace(core_params, shell_thickness=shell_thickness_mm)` -
   `finger_outer_shell = create_finger_model_result(shell_params)` - the `FingerModelResult` whose
   `finger_brep` is the **outer surface of the splint shell** (finger radii + 2.25 mm).
4. `splint_blank, success, method = robust_brep_difference(minuend=finger_outer_shell.finger_brep,
   subtrahend=finger_core.finger_brep)` - the hollow wall solid (shell minus core). This is the
   worked pattern in the usage example.

### Phase 1 key results (report via `tracker.log_phase(1.0, ...)`)

- `finger_core` - `FingerModelResult`, outer surface of the finger. We keep the whole result (not
  just the brep) because Phase 2 needs its `joint_positions`, `get_perp_frame(...)`, and `radii`.
- `finger_outer_shell` - `FingerModelResult`, outer surface of the splint shell.
- `splint_blank` - the solid hollow wall = `finger_outer_shell` minus `finger_core`.

### Notes / differences from the example

- The example uses `trim_start`/`trim_end` around the PIP to carve a short band. FingerGutter does
  **not** need that for extent: the included-segment range (`start_at`/`end_at`) already defines how
  far the blank runs. Leave trimming unused for v1 unless we later want partial-phalanx ends.
- Keep `finger_core` and `finger_outer_shell` as full `FingerModelResult`s (use
  `create_finger_model_result`, not the tuple-returning `create_finger_model`) so Phase 2 can query
  `joint_positions` / `get_perp_frame` / `radii` off them.

## Phase 2: build the gutter trim solid

Goal: construct a solid (`gutter_trim_solid`) occupying the material we want to REMOVE - the dorsal
cap (this is a volar gutter) - positioned so its lower face is the trough's cut line at
`gutter_depth_mm`. Phase 2 only builds the cutter; the actual boolean subtraction is Phase 3.

Proposed construction (first theory - holes flagged below):
1. **Cut points at each included joint.** For each included joint `j` (mcp/pip/dip/tip), take its
   `finger_core.get_perp_frame(j)` frame and its `finger_core.radii[j]` radius. Starting at the
   joint center (`joint_positions[j][0]`), move to the finger's volar surface, then back up by the
   gutter depth:
   `cut_point(j) = joint_center + (gutter_depth_mm - radius_j) * frame.YAxis`
   where `frame.YAxis` is the perp-frame **dorsal** axis (so -Y is volar). This lands the cut line
   `gutter_depth_mm` above (dorsal of) the volar-most surface at that joint.
2. **`gutter_trim_centerline`**: a polyline through the ordered `cut_point(j)`. Extend the first and
   last segments by an arbitrary large length (~100 mm) **parallel to their adjoining phalanx
   direction** so the trough runs off both ends of the finger.
3. **`gutter_trim_surface`**: extrude `gutter_trim_centerline` symmetrically +/- in world Y
   (lateral) by a half-width large enough to exceed the finger's lateral extent (use the finger
   bounding box + margin rather than a bare constant).
4. **`gutter_trim_solid`**: extrude `gutter_trim_surface` upward in world +Z (dorsal) by an arbitrary
   large amount (~100 mm) so the cutter fully caps the dorsal side of the blank.

### Notes / holes in Phase 2

- RESOLVED - **the offset is along the perp-frame Y axis, not Z.** The perp frame's Z is the
  finger's length direction; dorsal is +Y and volar is -Y. So the joint-center -> volar-surface move
  is `-radius * YAxis` and the depth move is `+gutter_depth * YAxis`, combining to
  `(gutter_depth - radius) * YAxis`. (Using -Z would push the cut point *along the finger*.)
- RESOLVED - **volar gutter.** `gutter_depth_mm` is measured up from the volar surface; the cut is
  `(gutter_depth - radius) * dorsal` per joint and Phase 3 subtracts the dorsal (+Z) cap.
- RESOLVED - **vertex placement is per-joint perp-frame (committed).** Each trim vertex is shifted in
  its own joint's perp frame, so the cut depth stays correct even when the finger is flexed/deflected
  and the local dorsal tilts away from world +Z. The subsequent sweep directions (world +/-Y to build
  the surface, world +Z to build the solid) exist only to make a cutter big enough to cap the whole
  dorsal side - they do NOT set the depth, so world axes are fine there (just size them generously;
  see the lateral-deflection note).
- **Lateral joint deflection breaks XZ-planarity** (sizing note): with non-zero `*_lateral_deg`, joints shift in Y,
  so `gutter_trim_centerline` is not planar in world XZ. The +/-Y extrusion still yields a valid
  surface, but the half-width must exceed the finger's true lateral extent *including* deflection -
  hence sizing from the bounding box, not a fixed number.
- **Use `finger_core.radii[j]`**, don't recompute radius from circumference - `FingerModel` already
  did `circ / (2*pi)` and stored it (and it's the *core*, unshelled radius, i.e. the finger's actual
  surface).

## Phase 3: subtract the trim solid

`splint_solid = robust_brep_difference(minuend=splint_blank, subtrahend=gutter_trim_solid)`.

Removes the capped side from the Phase 1 hollow blank, leaving the gutter trough: the walls that
survive are the trough. Report `splint_solid` (and keep the blank + cutter for preview) via
`tracker.log_phase(3.0, ...)`.

Open for later phases (not yet designed): edge finishing/chamfer of the trough rim, objectID emboss,
mesh + build-plate orientation, and any strap/retention features. We design these once Phases 0-3
look right in Rhino.

## Decisions locked in

- Volar gutter: retain volar + sides, open dorsal. `gutter_depth_mm` measured up from the volar
  surface.
- `distal_len_mm` naming (DIP -> tip distal phalanx).
- Trim vertices placed in each joint's own perp frame (not world axes); world-axis extrusion only
  sizes the cutter.

## Remaining open questions

1. Add `is_right_hand` and `finger_abbr` to the schema? (deferred until the geometry firms up)

_Next step after sign-off: create the `splint_geo_processor/generators/dev/FingerGutter/` harness
scaffolding and a minimal `splint_geo_processor/generators/src/FingerGutter.py` implementing Phases
0-3, previewing each phase in Rhino via the harness before moving on._
