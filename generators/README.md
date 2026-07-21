# generators/

Splint geometry generators. Each algorithm is looked up here by name (returned by the Splint
Factory API) and dispatched by `splint_geo_processor/src/processors/pipeline.ts`.

- Naming convention: `{GeometryAlgorithmName}.{gh|py}`
- If both `.py` and `.gh` exist for the same name, the pipeline prefers `.py`.
- Shared algorithm code lives in [src/](src/); dev-only tooling lives in [dev/](dev/).
- Each Python-based splint gets its own dev folder: `dev/<SplintName>/` holds that splint's
  `run.sh`, `harness.py`, and `inputs/*.json`. Splint-agnostic dev plumbing (Rhino dispatch,
  bake/report/layout helpers) lives once in [dev/_devkit/](dev/_devkit/) and is shared by every
  splint's harness - see [dev/_devkit/bake_utils.py](dev/_devkit/bake_utils.py) for the bake API
  and [dev/_devkit/run_harness.sh](dev/_devkit/run_harness.sh) for the dispatcher. To add a new
  splint, copy `dev/RelativeMotion/` as a template (`run.sh` needs no changes; `harness.py` keeps
  only the splint-specific `INPUT_FILES` list, the call into the algorithm module, and which
  debug keys to bake).

## Three ways to run RelativeMotion

Each mode targets a different phase of development. Same algorithm code under the hood
([src/RelativeMotion.py](src/RelativeMotion.py)) except mode 1, which is the older Grasshopper
implementation kept around for reference / A-B comparison.

### 1. Grasshopper script in Rhino — `RelativeMotion.gh`

Open [RelativeMotion.gh](RelativeMotion.gh) inside Rhino and tweak params on the canvas. Uses
the legacy Grasshopper implementation, NOT the Python `src/RelativeMotion.py` pipeline.

**Use when:** exploring geometry ideas visually, prototyping a new feature with GH components,
or comparing legacy vs Python behavior. Slow to iterate; not what production runs.

### 2. Dev Python harness — `dev/RelativeMotion/run.sh`

```bash
./generators/dev/RelativeMotion/run.sh
```

`run.sh` is a thin wrapper that hands off to the shared
[dev/_devkit/run_harness.sh](dev/_devkit/run_harness.sh) dispatcher, which dispatches
[dev/RelativeMotion/harness.py](dev/RelativeMotion/harness.py) into an already-running Rhino 8
session via `rhinocode script`, waits for the report file to reappear, and prints it. Calls the
same `generate_relative_motion_splint()` entrypoint production uses, with input pulled from
`dev/RelativeMotion/inputs/*.json` (edit `INPUT_FILES` in the harness to switch cases). Bakes all
intermediate breps / edges / rails into the live Rhino doc for hand inspection (via
`dev/_devkit/bake_utils.py`), and drops per-step diagnostic output into
`dev/RelativeMotion/last_run_report.txt`.

**Use when:** actively developing algorithm code in `src/`. Cycle time ~6 s. This is the
fastest, most inspectable path.

**Requires:** Rhino 8 open with an empty document. Harness auto-detects the running instance.

### 3. Local `splint_factory` server + processor — `RelativeMotion.py`

Start the factory dev server (`splint_factory`) and the processor (`splint_geo_processor`)
locally, then submit a job through the factory UI. The processor dispatches
[RelativeMotion.py](RelativeMotion.py) via `rhinocode script`; that runner shim adds `src/` to
`sys.path`, force-reloads the algorithm module (defeats the `keepRhinoAlive` `sys.modules`
cache), and calls `generate_relative_motion_splint(..., is_production=True)`.

**Use when:** validating the end-to-end pipeline — real inbox JSON, cleaned outbox, log
archiving, mesh delivery to the factory. Cycle time 30-90+ s (Rhino warmup + full pipeline +
poll interval).

**Diagnostics:**
- `~/SplintFactoryFiles/logs/runner_trace.log` — durable per-stage timestamps and exception
  tracebacks written by the runner shim itself (independent of `splintcommon.log()`).
- `~/SplintFactoryFiles/outbox/log.txt` — the algorithm's own log for the current job.
- `~/SplintFactoryFiles/archive/YYMMDD-HH-MM-*/log.txt` — archived per-job logs after cleanup.

## Quick reference: which mode for which phase?

| Phase | Mode |
| --- | --- |
| New feature exploration in GH components | 1. Grasshopper |
| Iterating on `src/` Python code, need to see geometry | 2. Dev harness |
| Verifying pipeline plumbing / prod parity | 3. Local server |
