"""
RelativeMotion.py (runner)

Production entrypoint invoked by splint_geo_processor via `rhinocode script`. Discovered by
splint_geo_processor/src/processors/pipeline.ts, which prefers this .py over RelativeMotion.gh.
Thin shim: adds generators/src to sys.path, imports the algorithm module, loads the job from
the inbox, and delegates to prod_runner for the common generate->export->sentinel flow.

IMPORTANT: keepRhinoAlive=true keeps a single Python interpreter alive across jobs, so
sys.modules caches RelativeMotion after the first job and subsequent jobs would run pre-edit
bytecode. Pop just RelativeMotion before importing; its own top-level code calls reload() on
every submodule, so a fresh RelativeMotion cascades fresh submodules too.
"""

import sys
import time
import traceback
from pathlib import Path

# Diagnostic tracer that always writes, independent of splintcommon.log(). Helps localize
# hangs during module import when the algo's own log() is not yet reachable.
_TRACE_PATH = str(Path("~/SplintFactoryFiles/logs/runner_trace.log").expanduser())
def _trace(msg):
    try:
        with open(_TRACE_PATH, "a", encoding="utf-8") as _f:
            _f.write("[{0:.3f}] {1}\n".format(time.time(), msg))
    except Exception:
        pass

_trace("runner: START")

src_dir = str(Path(__file__).resolve().parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)
_trace("runner: sys.path[0]=" + sys.path[0])

# Drop the cached algorithm module so its top-level code (including reload() calls for every
# submodule) re-executes against the current .py on disk. Safe against a persistent Rhino.
_had_cached = "RelativeMotion" in sys.modules
sys.modules.pop("RelativeMotion", None)
_trace("runner: popped RelativeMotion cached=" + str(_had_cached))

try:
    _trace("runner: importing RelativeMotion + prod_runner")
    from RelativeMotion import RelativeMotionGenerator
    from splintcommon import log as _pipeline_log, load_job_data
    from prod_runner import run_production_job

    # Load job from inbox (RelativeMotion-specific data extraction stays here).
    _trace("runner: loading job data")
    job_data, object_id, root_filename, output_dir, _ = load_job_data(
        True, "RelativeMotion")
    raw_data = job_data["relative_motion_data"]
    _pipeline_log("RelativeMotion runner: PROD job '{0}' (objectID {1})".format(
        root_filename, object_id))

    # Generate geometry and export mesh via the shared production flow.
    _trace("runner: calling run_production_job")
    generator = RelativeMotionGenerator()
    run_production_job(generator, raw_data, object_id, root_filename, output_dir)
    _trace("runner: run_production_job returned")

    _pipeline_log("[PIPELINE_RESULT:SUCCESS] RelativeMotion runner completed")
    _trace("runner: emitted [PIPELINE_RESULT:SUCCESS]")
except Exception as _exc:
    _trace("runner: EXCEPTION " + type(_exc).__name__ + ": " + str(_exc))
    _trace("runner: TRACEBACK:\n" + traceback.format_exc())
    # Fail-fast sentinel. Try splintcommon.log() first (structured, same code path as success);
    # if it isn't importable because the exception happened during import setup, fall back to a
    # direct write to the well-known outbox log path so the pipeline still sees the substring.
    _fail_msg = "[PIPELINE_RESULT:FAILURE] RelativeMotion runner: {0}: {1}".format(
        type(_exc).__name__, _exc)
    try:
        from splintcommon import log as _fail_log
        _fail_log(_fail_msg)
    except Exception:
        try:
            _outbox_log = str(Path("~/SplintFactoryFiles/outbox/log.txt").expanduser())
            with open(_outbox_log, "a", encoding="utf-8") as _f:
                _f.write(_fail_msg + "\n")
        except Exception:
            pass
    _trace("runner: emitted [PIPELINE_RESULT:FAILURE]")
    raise
