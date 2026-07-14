"""
RelativeMotion.py (runner)

Production entrypoint invoked by splint_geo_processor via `rhinocode script`. Discovered by
splint_geo_processor/src/processors/pipeline.ts, which prefers this .py over RelativeMotion.gh.
Thin shim: adds generators/src to sys.path, imports the algorithm module, and runs it in
production mode. All geometry lives in generators/src/RelativeMotion.py.

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
    _trace("runner: importing RelativeMotion")
    from RelativeMotion import generate_relative_motion_splint
    # Also grab splintcommon.log directly so we can emit the pipeline-completion sentinel
    # even in the failure path (log goes to ~/SplintFactoryFiles/outbox/log.txt, which is
    # exactly where splint_geo_processor/src/processors/pipeline.ts scans for
    # [PIPELINE_RESULT:SUCCESS] / [PIPELINE_RESULT:FAILURE] substrings to unblock its poll).
    from splintcommon import log as _pipeline_log

    _trace("runner: import ok, calling generate_relative_motion_splint")
    generate_relative_motion_splint(
        raw_data_dev=None, is_production=True, should_save_mesh=True)
    _trace("runner: generate_relative_motion_splint returned")
    # Fast-completion sentinel: pipeline.ts scans log.txt for this exact substring and stops
    # polling immediately, avoiding a ~90s inactivity timeout waiting for the mesh file.
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
