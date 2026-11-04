# InfinityFlex Splint - Dev Notes (WIP Python port)

Running specification for the internal geometry construction phases of the InfinityFlex
splint as we port it, one Grasshopper step at a time, from the binary
`generators/InfinityFlex.gh` into pure Python.

Companion module: `generators/src/InfinityFlexDev.py` (the emerging central point of contact,
modeled on `generators/src/RelativeMotion.py`).

Status legend: `PORTED` = implemented in Python and error-checked; `TODO` = still only in the
`.gh`, not yet interpreted.

---

## Purpose and porting strategy

InfinityFlex is a 3D-printed finger splint. The bulk of its geometry currently lives in the
binary `generators/InfinityFlex.gh` (with `generators/config_data/InfinityFlex.json`), which is
what production runs today.

We are re-implementing it incrementally in Python so that as little as possible stays hidden in
the binary `.gh`, matching the RelativeMotion approach. Ground rules:

- Production is untouched. The pipeline prefers `generators/<algorithm>.py` and falls back to
  `generators/<algorithm>.gh`. There is no `generators/InfinityFlex.py`, so production keeps
  running `InfinityFlex.gh`. WIP code lives in `generators/src/InfinityFlexDev.py` with
  `GEO_ALGORITHM_NAME = "InfinityFlexDev"`, a name distinct from production `"InfinityFlex"`.
- Reusable geometry goes in dedicated helper modules (e.g. `Orienteering.py`), so each concern
  has a single point of contact. `InfinityFlexDev.py` only orchestrates them into phases.
- Functions expose their construction geometry through a `debug` out-dict / `PhaseTracker` so
  each phase can be previewed in Rhino on its own, before the whole pipeline exists.

Because the ported steps depend on inputs produced by not-yet-ported earlier steps, they are
exercised individually (methods that take their geometry inputs directly) rather than through a
runnable end-to-end `generate()`.

---

## Coordinate and anatomy conventions

TODO - to be confirmed by reading the `.gh`. Provisionally we expect the shared convention used
by RelativeMotion:

- +X: distal (along the length of the finger, toward the tip).
- +Z: dorsal (back of hand); -Z: volar (palm side).
- Y: lateral axis across the fingers.

Confirm against the InfinityFlex `.gh` before relying on these for a new phase.

---

## Phase plan (provisional)

Numbering is provisional and will firm up as more of the `.gh` is interpreted. Most body-
construction phases are still to be reverse-engineered from the graph.

| Phase   | Name                                | Status |
| ------- | ----------------------------------- | ------ |
| 1..N-1  | Splint body construction            | TODO   |
| N.0     | Positive sphere direction indicator | PORTED |

The direction indicator is a late, additive step: it grafts a landmark onto a finished splint
body, so it necessarily runs after the (still-TODO) body phases.

---

## Phase N.0 - Positive sphere direction indicator (PORTED)

Grafts a small protruding partial sphere ("nub") onto the finished splint surface so the wearer
can feel/see the splint's volar/distal orientation.

- Implementation: `generators/src/Orienteering.py` ->
  `positive_sphere_direction_indicator(...)`.
- Orchestration: `InfinityFlexDevGenerator.add_direction_indicator(...)` in
  `generators/src/InfinityFlexDev.py`.
- GH source: the "Direction Indicator" group in the InfinityFlex `.gh` family.

### Inputs

- `splint_brep` (`rg.Brep`): finished splint solid. Must be a CLOSED SOLID - the outward-normal
  resolution uses an inside/outside test (`Brep.IsPointInside`). Not mutated; a new unioned Brep
  is returned.
- `tip_top_band_curve` (`rg.Curve`): reference curve near the splint tip. Its span sets where
  the nub lands (extended endpoints project onto the surface). Produced by an earlier, still-
  TODO body phase.
- `wall_thickness_mm` (`float`, > 0): splint wall thickness; scales the nub radius.

### Tunable parameters (defaults)

- `ball_r_to_wall_ratio = 0.65`: `nub_radius = wall_thickness_mm * ratio`. Keeps the embedded
  half of the ball inside the wall.
- `nub_position_param = 0.5`: 0..1 position along the line between the two projected surface
  points (0.5 = midpoint).
- `nub_sinking_factor = 0.1`: fraction of the nub radius to shift the sphere along the OUTWARD
  surface normal. Positive = more protrusion; negative sinks it deeper. The reference `.gh` used
  a positive value for a slightly fuller-than-hemisphere bump.
- `north_star_nudge_mm = 5.0`: distance to extend the band curve past each end before taking its
  endpoints.

### Algorithm

1. Extend `tip_top_band_curve` past both ends by `north_star_nudge_mm`
   (`CurveEnd.Both`, `CurveExtensionStyle.Line`), then take `PointAtStart` / `PointAtEnd`. The
   endpoints get reprojected, so a simple linear extension is sufficient.
2. Project each endpoint onto the splint surface (closest point on the Brep) -> two on-surface
   points.
3. Build the line between the two projected points; the nub center sits at
   `PointAtNormalizedLength(nub_position_param)`.
4. `nub_radius = wall_thickness_mm * ball_r_to_wall_ratio`.
5. Shift the sphere center along the local OUTWARD surface normal by
   `nub_sinking_factor * nub_radius`, build `Sphere(center, nub_radius).ToBrep()`.
6. Boolean-union the nub into the splint via `BrepUnion.robust_brep_union([splint_brep, nub])`.

### Surface-normal note (improvement over the `.gh`)

The reference graph nudged the sphere along world `Unit Z`. This port instead uses the true
local surface normal at the nub point. Because face normals are not reliably outward on a joined
solid, `Orienteering._outward_normal_at` resolves the outward direction by probing a small step
(`_NORMAL_PROBE_MM = 0.05`) along the normal and flipping it if that point lands inside the
solid.

### GH node -> Python mapping (summary)

- "extend curve" (both ends) -> `Curve.Extend(CurveEnd.Both, nudge, CurveExtensionStyle.Line)`
- curve endpoints -> `PointAtStart` / `PointAtEnd`
- "pull point to surface" -> `Brep.ClosestPoint(...)`
- point along a line -> `LineCurve.PointAtNormalizedLength(...)`
- world `Unit Z` sink -> local outward surface normal (see note above)
- "Merge" into a list of `[splint body, moved sphere]` + solid union ->
  `robust_brep_union([splint_brep, nub_sphere])`

### Debug keys

`extended_band_curve`, `proj_point_start`, `proj_point_end`, `placement_line`, `nub_radius`,
`nub_center`, `nub_normal_line`, `nub_sphere`, `result`.

### Open questions

- Which body phase produces `tip_top_band_curve`, and in what units/orientation?
- Confirm the coordinate convention from the `.gh`.
- Confirm whether the production nub used a fixed radius or the wall-thickness ratio used here.

---

## Backlog (to interpret from `InfinityFlex.gh`)

- Splint body construction phases (the bulk of the graph).
- Source of `tip_top_band_curve` and other reference curves consumed by the direction
  indicator.
- Input schema / parametric measurements the design collects.
