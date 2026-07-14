# RhinoCommon Filleting and Chamfering Research

**STATUS (2026-07-11): landed.** Winning approach in production:
`Brep.CreateFilletEdges` + `BlendType.Chamfer` + `RailType.DistanceFromEdge`, driven by
`BrepEdgeLocator` to map construction curves to edge indices. Wrapper lives in
`generators/src/BrepChamfer.py` (fails loud, no fallback ladder). Wired into
`generate_relative_motion_splint` Phase 7.5: rims 0.5 mm, then perimeter 0.25 mm. The
hand-built wedge cutter (`BrepEdgeRound.py`) is deleted; `BrepFillet.py` is dormant (still
called from legacy .gh binaries, slated for removal). Harness `dev/harness_relmotion.py` keeps
the R&D probes (variable-radius chamfer, surface-pair) live for future variable-distance work.

Everything below is the original research log kept for reference.

---

Working doc for figuring out the "proper Rhino way" to fillet/chamfer our splint edges, after
the hand-built wedge-cutter approach proved too fragile (silent intersection=0 failures,
mis-placed cutters that bit chunks out of the solid, and a growing stack of defensive guards).

Goal: learn the real RhinoCommon fillet/chamfer methods, map each to the situations we hit,
then build one wrapper module that tries the right method for each situation, reports rich
diagnostics, and fails cleanly (raises) instead of silently corrupting the solid.

Environment: Rhino 8.30 (RhinoCode CPython, py39-rh8). Both native edge-fillet methods below
require >= Rhino 8.6, so we are covered.

Source of truth for signatures below: the local stubs shipped with our install at
`~/.rhinocode/py39-rh8/site-stubs/rhino3d-8.30.26103.11002/Rhino/Geometry/__init__.pyi`,
cross-checked against developer.rhino3d.com. Prefer the local stubs first (they match the
exact build we run), then the web docs for semantics.

---

## TL;DR ranking for our problem

Our problem is almost always "round/chamfer an edge (or edge loop) of a closed solid Brep."
That is exactly what the native **edge** methods are built for, so they lead:

1. **`Brep.CreateFilletEdges`** - primary. Fillets, chamfers, OR blends one or more edges of a
   solid in a single call. One `BlendType` per call (Chamfer / Fillet / Blend). This is the API
   twin of the Rhino `FilletEdge` / `ChamferEdge` commands. Returns `Brep[]`.
2. **`Brep.CreateFilletEdgesVariableRadius`** - primary for differential radius. Same as above
   but radius varies along each edge via handle points. This is the API twin of the
   `VariableFilletSrf` handle workflow Jon found - it is what we want for "support region 1.5mm,
   anchor region 0.5mm along the SAME edge loop."
3. **Surface-pair methods** (`CreateFilletSurface`, `CreateChamferSurface`, `CreateBlendSurface`,
   `Surface.CreateRollingBallFillet`) - fallback when the edge methods choke. These build the
   fillet surface between two faces given a uv seed on each; we then trim + join ourselves.
   More control, more manual work.
4. **Pipe-trim trick** - manual fallback (see below). Robust against some cases the native
   solver dies on, but limited by curvature (pipe self-intersects when rail radius < pipe radius).
5. **Hand-built wedge cutter** (was `BrepEdgeRound.py`) - retired 2026-07-11. Deleted.

---

## A. Native edge fillet/chamfer on a solid Brep (PREFERRED)

### `Brep.CreateFilletEdges` (static)
```
Brep.CreateFilletEdges(
    brep,                       # Brep to operate on
    edgeIndices,                # IEnumerable[int]   - which edges (indices into brep.Edges)
    startRadii,                 # IEnumerable[float] - one start radius per edge index
    endRadii,                   # IEnumerable[float] - one end radius per edge index
    blendType,                  # BlendType.Chamfer | Fillet | Blend
    railType,                   # RailType.DistanceFromEdge | RollingBall | DistanceBetweenRails
    tolerance                   # float  (there is also a setback/angleTolerance overload)
) -> Brep[]                     # array of results; empty/None => failure
```
Notes:
- ONE `blendType` and ONE `railType` per call, but per-edge start/end radii. So a single edge
  can linearly taper start->end radius. For arbitrary variation use the VariableRadius method.
- `BlendType.Chamfer` gives a flat bevel; `BlendType.Fillet` gives a rounded (arc) edge;
  `BlendType.Blend` gives a tangent/curvature blend (needs `setbackFillets` in the long overload).
- `RailType.DistanceFromEdge` measures the radius as a distance from the edge on each face
  (matches how the `FilletEdge` command's "Distance" mode behaves); `RollingBall` rolls a ball
  of the radius; `DistanceBetweenRails` sets the two rails a fixed distance apart.
- Returns `Brep[]`. Success = non-empty array with a valid closed solid. Treat empty array,
  None, or a non-solid/invalid result as failure and raise.

### `Brep.CreateFilletEdgesVariableRadius` (static)  <- differential radius along one edge
```
Brep.CreateFilletEdgesVariableRadius(
    brep,
    edgeIndices,                # IEnumerable[int]
    edgeDistances,              # IDictionary[int, IList[BrepEdgeFilletDistance]]
    blendType, railType, setbackFillets,
    tolerance, angleTolerance
) -> Brep[]
```
- `edgeDistances`: key = edge index; value = list of `BrepEdgeFilletDistance(edgeParameter, filletDistance)`
  handle points along that edge. This is the programmatic `VariableFilletSrf`: place a handle at
  each edge parameter with the radius we want there, and Rhino interpolates between handles.
- THIS is the candidate for support-vs-anchor: one continuous edge loop, big radius (1.5mm) over
  the support span, small radius (0.5mm) over the anchor span, with handles at the transition
  parameters. Needs experimentation to confirm the solver tolerates the transition.

### Supporting type / enums (verified from local stubs)
```
class BrepEdgeFilletDistance:      # __init__(edgeParameter: float, filletDistance: float)
class BlendType(enum.Enum):   Chamfer=0, Fillet=1, Blend=2
class RailType(enum.Enum):    DistanceFromEdge=0, RollingBall=1, DistanceBetweenRails=2
class BlendContinuity(enum.Enum): Position=0, Tangency=1, Curvature=2
class FilletSurfaceSplitType(enum.Enum): Nothing=0, Trim=1, Split=2
```

### Open questions to test for section A
- Do our support-finger perimeter edges exist as clean single `BrepEdge` entries we can index,
  or are they fragmented across multiple faces? (Need to enumerate `brep.Edges` and identify.)
- Does `CreateFilletEdges` tolerate our open-rail / thin-band geometry, or only closed loops?
- For anchor bore rims (closed circles): does a single edge index + one radius just work?
- Does VariableRadius handle the support<->anchor transition without kinking?

---

## B. Surface-pair fillet/chamfer (fallback, more manual)

These build a fillet/chamfer/blend SURFACE between two faces; you pick a uv seed on each face
near the edge, then trim & join the pieces into a solid yourself.

- `Brep.CreateFilletSurface(face0, uv0, face1, uv1, radius, extend, tolerance) -> Brep[]`
  - trim overload also returns the trimmed input pieces (breps0, breps1).
- `Brep.CreateChamferSurface(face0, uv0, radius0, face1, uv1, radius1, extend, tolerance) -> Brep[]`
  - allows different setback on each face (radius0 != radius1) => asymmetric chamfer.
- `Brep.CreateBlendSurface(face0, edge0, domain0, rev0, continuity0, face1, edge1, domain1, rev1, continuity1) -> Brep[]`
  - edge-driven tangent/curvature blend over a parameter domain of each edge.
- `Surface.CreateRollingBallFillet(surfaceA, surfaceB, radius, tolerance) -> Surface[]`
  - rolling-ball fillet between two raw surfaces (also flip and uv-seed overloads). Good for the
    two-untrimmed-surface case; returns the fillet surface(s) only (we trim/join).

Advanced low-level (Surface class, many overloads) if we ever need fine control over rail
degree / arc degree / sliders:
- `Surface.CreateNonRationalCubicFilletSrf`, `...QuarticFilletSrf`, `...QuinticFilletSrf`,
  `CreateRationalArcsFilletSrf`, and the `FilletSurfaceToRail` / `FilletSurfaceToCurve` methods.
  These are the internals the fillet command uses; only reach for them if the high-level calls
  fail and we need to control the cross-section construction.

---

## C. Curve-level fillets (for building rails/profiles, not solids)

Useful when we fillet a 2D profile BEFORE extruding, sidestepping solid filleting entirely:
- `Curve.CreateFilletCornersCurve(curve, radius, tolerance, angleTolerance) -> Curve`
  - rounds ALL corners of a (poly)curve in one shot. Great for pre-rounding a closed profile.
- `Curve.CreateFilletCurves(curve0, pt0, curve1, pt1, radius, join, trim, arcExtension, tol, angleTol) -> Curve[]`
  - fillet between two curves near the picked points.
- `Curve.CreateFillet(curve0, curve1, radius, t0Base, t1Base) -> Arc` (just the arc).
- `Curve.CreateBlendCurve(...)`, `Curve.CreateArcBlend(...)`, `Curve.CreateArcLineArcBlend(...)`
  for tangent transitions.

Strategy note: for edges that are "extrude a profile straight," rounding the profile with
`CreateFilletCornersCurve` and then extruding is by far the most robust route. Only edges that
are inherently 3D (where the rail leaves a single plane) actually need the solid methods above.

---

## D. Pipe-trim trick (manual fallback)

From a Rhino3D YouTube video (https://www.youtube.com/watch?v=4mXv4IaWSBw). Goal: dodge the
fillet solver's unpredictable failures by constructing the rounded surface from a pipe.
Original video uses interactive Rhino commands; RhinoCommon equivalents noted.

Rhino-command steps (from the video):
- Identify the rail/path to fillet.
- If selecting an edge of a larger object, `DupEdge` first (API: `BrepEdge.DuplicateCurve()`).
- `Pipe` a tube of the desired radius around the rail.
- `Split` the solid's face with the pipe to remove a strip, leaving an open hole.
- `Trim` away the remaining sliver of surface, leaving a clean naked-edged opening.
- The pipe wall becomes the rounded face; `Join` + cap the pieces back into a solid.

RhinoCommon equivalents (verified signatures):
- `Brep.CreatePipe(rail, radius, localBlending, cap, fitRail, absoluteTolerance, angleToleranceRadians) -> Brep[]`
  - `cap`: `PipeCapMode.None_ | Flat | Round`. Variable-radius overload takes
    `railRadiiParameters` + `radii` lists (another route to differential radius).
- `Brep.Split(cutter, intersectionTolerance) -> Brep[]` (and the list/normal overloads;
  one overload also returns a `bool` success flag).
- `Brep.Trim(cutter, intersectionTolerance) -> Brep[]` (Brep or Plane cutter).
- `Brep.Join...` + `Brep.CapPlanarHoles` to close back up.

Key limitation (must document in the wrapper): the pipe self-intersects where the rail's
curvature radius is smaller than the pipe radius. That is precisely our tight support-arch
case, so pipe-trim does NOT dodge the arch problem - it just moves it.

---

## E. Mapping methods to OUR specific situations

| Situation | Geometry | First choice | Fallback |
|-----------|----------|--------------|----------|
| Anchor bore rims (0.5mm) | closed circular edge on a cylinder-through-solid | `CreateFilletEdges` single edge | pre-round profile / wedge |
| Support-finger perimeter (1.5mm) | open rails along a curved band edge | `CreateFilletEdges` on the edge(s) | surface-pair, then pipe-trim |
| Support vs anchor differential on one loop | one edge loop, radius varies by region | `CreateFilletEdgesVariableRadius` | two separate edge sets |
| Anchor OUTER edges | intentionally left SHARP | none (skip) | - |
| Profile corners known before extrude | 2D closed profile | `CreateFilletCornersCurve` pre-extrude | - |

---

## F. Proposed wrapper module design (for discussion before coding)

A single module (working name `BrepFillet.py`) that exposes a small, observable API and NEVER
returns a silently-corrupted solid. Design goals mirror how `BrepDifference.py` already works
(rich logging, validate-or-raise, no degenerate results).

Sketch:
```
class FilletError(Exception): ...            # raised on any clean failure

def fillet_edges(brep, edge_indices, radius, *, blend=Fillet, rail=DistanceFromEdge,
                 tolerance=None, debug=None) -> Brep
    # thin wrapper over CreateFilletEdges; validates result IsSolid; picks/validates the
    # single best Brep from the returned array; raises FilletError otherwise.

def fillet_edges_variable(brep, edge_handles, *, blend=Fillet, rail=DistanceFromEdge, ...)
    # edge_handles: {edge_index: [(edge_param, radius), ...]} -> BrepEdgeFilletDistance dict

def pipe_trim_edge(brep, rail_curve, radius, *, tolerance=None, debug=None) -> Brep
    # the section-D fallback, encapsulated with curvature-vs-radius pre-check that raises early.
```
Every function: writes intermediate geometry into an optional `debug` dict (like our chamfer
records) so failures are bakeable in GH; logs method name + input/return counts + validation
result; validates `IsValid`/`IsSolid`/naked-edge count before returning; raises `FilletError`
with a specific reason on any failure. Reuse `get_brep_volume` / validation helpers from
`BrepDifference.py` where sensible.

Open design choices to settle BEFORE writing the module:
1. ~~Do we retire `BrepEdgeRound.py` (wedge cutter) entirely...~~ **Resolved 2026-07-11: deleted.**
2. Wrapper "strategy ladder" (auto-fallback native -> surface-pair -> pipe-trim) vs. explicit
   per-call method choice by us? (BrepDifference-style ladder vs. caller decides.)
   **Resolved: caller decides. `BrepChamfer.chamfer_edges` is a thin fail-loud wrapper; no
   fallback ladder. If a specific geometry breaks it, we handle that case explicitly.**
3. For the support/anchor differential radius: attempt the single-loop VariableRadius first, or
   start simpler with two separate uniform-radius `CreateFilletEdges` passes and only escalate?
   **Resolved: two uniform passes (rims 0.5 mm, then perimeter 0.25 mm). Variable-radius stays
   in the harness for when we need a ramp across a single loop.**

---

## G. Test plan / experiments to run in Rhino

Small isolated GH/py experiments, simplest-first, each baked and eyeballed:
1. Enumerate `brep.Edges` on the current splint solid; identify the anchor-rim edge indices and
   the support-perimeter edge indices (log index -> length -> curve midpoint).
2. `CreateFilletEdges` on ONE anchor bore rim at 0.5mm. Confirm valid closed solid out.
3. `CreateFilletEdges` on ONE support-perimeter edge at 1.5mm. Note if/where it fails.
4. If (3) fails, try `RailType.RollingBall` vs `DistanceFromEdge`, then surface-pair, then pipe-trim.
5. `CreateFilletEdgesVariableRadius` on a single loop with a 1.5mm->0.5mm handle transition.
6. Record every outcome (success/fail + reason + timing) in the table in section E.



