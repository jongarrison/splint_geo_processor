"""Dev harness: run the full RelativeMotion pipeline against one or more dev inputs and bake
the named outputs for each into the live Rhino doc for side-by-side visual inspection.

WHY THIS EXISTS
    Fastest edit-run-eyeball cycle for algorithm work. Calls the same
    `RelativeMotionGenerator.generate()` entrypoint production uses, so any change to the src
    modules is immediately exercised end-to-end without touching Grasshopper. All previewable
    geometry comes back in the `debug` dict - the harness bakes only what's named there.

    Any new dev/preview geometry should be produced INSIDE generate() and passed back through
    `debug`. The harness stays tiny: dispatch, log, bake. All the generic bake/report/layout
    plumbing (splint-agnostic) lives in generators/dev/_devkit/bake_utils.py - this file only
    contains RelativeMotion-specific logic: which inputs to run and which debug keys to bake.

LAYOUT
    Each input file gets its own row along +Y. Inside a row, phases step out along +X with
    auto-generated colors (golden-ratio HSV spacing). Set STOP_AFTER to a phase number to
    halt early and preview only completed phases.

HOW TO RUN
    ./run.sh                       # from this directory
    (auto-detects Rhino 8, dispatches this script, waits for report file to reappear, prints it)

SWITCHING INPUTS
    Edit INPUT_FILES below - list one or more *.json filenames from inputs/. Each JSON is a full
    raw_data payload with the exact keys RelativeMotionGenerator.generate() reads.
"""

# ------------------------------------------------------------------ config (edit me) ---------
# One or more inputs to run in the same session. Each row is offset in +Y so results sit
# side-by-side without overlapping. Add / remove entries freely.
INPUT_FILES = [
    # "AASA_20.json",
    # "ASAA_BTR8_prod.json", #prob
    # "ASAX_20deg.json",
    # "ASSA_20.json",
    # "XASA_ZM1Q_prod.json",
    # "2QY6_prod_exact.json",
    # "MX2E.json",
    # "AASX_20.json", #prob
    # "RVN0.json",
    # "JC8E.json", #!!! fails by running for a long time, but still creating log output
    # "KGAS.json", #fails by running for a long time, but still creating log output
    "8CH7.json",
]

ENABLE_MESH_EXPORT = False  # when True, export 3mf to outputs/ (same path prod uses)

# ------------------------------------------------------------------ stop_after config --------
# Set to a phase number to stop the pipeline early for focused dev work.
# None = run full pipeline. Examples: 7.0 stops after rail extraction, 6.0 after loft+bore.
STOP_AFTER = None #7.0 #5.9

# Whitelist of phase numbers and/or item keys to bake. Empty set = bake everything.
# Exact phases:   {6.0, 7.0}
# Minimum phase:  {"7.0+"}          (all phases >= 7.0)
# Specific keys:  {"splint_solid"}
# Mixed:          {"5.0+", "splint_solid"}
PREVIEW_FILTER = {} #{6.0, 7.0}

import sys
import json
import traceback
from pathlib import Path
from importlib import reload

import scriptcontext as sc
import rhinoscriptsyntax as rs
import Rhino.Geometry as rg

_HERE = Path(__file__).resolve().parent               # .../generators/dev/RelativeMotion
_SRC = _HERE.parent.parent / "src"                     # .../generators/src
_DEVKIT = _HERE.parent / "_devkit"                     # .../generators/dev/_devkit
_INPUTS = _HERE / "inputs"
_OUTPUTS = _HERE / "outputs"
if not _OUTPUTS.exists():
    _OUTPUTS.mkdir(parents=True)
_REPORT = _OUTPUTS / "last_run_report.txt"
for _p in (_SRC, _DEVKIT):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))

# Reload the module chain so edits since the Rhino session started are picked up. RelativeMotion
# reloads its own geometry deps (BrepDifference, BrepChamfer, BrepEdgeLocator, etc.) on import,
# so reloading it cascades; splintcommon is reloaded first since RelativeMotion pulls symbols from it.
import splintcommon
reload(splintcommon)
import RelativeMotion
reload(RelativeMotion)
from RelativeMotion import RelativeMotionGenerator

import splint_generator
reload(splint_generator)
from splint_generator import StopAfterPhase

import SplintMeshes2
reload(SplintMeshes2)
from SplintMeshes2 import export_mesh

import bake_utils as bk
reload(bk)

_rpt = bk.ReportBuffer(_REPORT)
report = _rpt.write
flush_report = _rpt.flush

_layout = bk.PreviewLayout(preview_spacing_mm=90.0, row_spacing_mm=150.0, report=report)

ensure_layer = bk.ensure_layer


def bake_preview(label, geom, layer, color, offset=None, label_z=None):
    return bk.bake_preview(label, geom, layer, color, offset=offset, report=report,
                           label_z=label_z)


def _phase_color(index, total):
    """Generate a visually distinct RGB tuple for phase index using HSV rotation."""
    import colorsys
    hue = (index * 0.618033988749895) % 1.0  # golden ratio spacing
    r, g, b = colorsys.hsv_to_rgb(hue, 0.6, 0.9)
    return (int(r * 255), int(g * 255), int(b * 255))


def _bake_phase_geometry(debug, row_y, report_fn):
    """Auto-preview all phases present in debug['_phases'], offset along +X per phase."""
    phases = debug.get("_phases", [])
    if not phases:
        report_fn("  (no phase data to preview)")
        return

    # Parse PREVIEW_FILTER: numbers = exact phases, "X.X+" = minimum threshold, strings = keys.
    filter_phases = set()
    filter_keys = set()
    filter_min = None
    for x in PREVIEW_FILTER:
        if isinstance(x, (int, float)):
            filter_phases.add(x)
        elif isinstance(x, str) and x.endswith("+"):
            filter_min = float(x[:-1])
        elif isinstance(x, str):
            filter_keys.add(x)
    has_filter = bool(PREVIEW_FILTER)

    phase_spacing = 100.0
    _LABEL_STEP = 8.0
    render_index = 0
    color_index = 0
    for i, phase in enumerate(phases):
        phase_match = ((not has_filter)
                       or (phase["number"] in filter_phases)
                       or (filter_min is not None and phase["number"] >= filter_min))
        offset = rg.Vector3d(render_index * phase_spacing, row_y, 0.0)
        layer_name = "P{0}_{1}".format(phase["number"], phase["title"].replace(" ", "_"))
        keys_baked = 0
        for key in phase["keys"]:
            if has_filter and not phase_match and key not in filter_keys:
                continue
            geo = debug.get(key)
            if geo is None:
                continue
            color = _phase_color(color_index, 0)
            label_z = 20.0 + keys_baked * _LABEL_STEP
            bake_preview(key, geo, layer_name, color, offset=offset, label_z=label_z)
            keys_baked += 1
            color_index += 1
        if keys_baked:
            title_z = 20.0 + keys_baked * _LABEL_STEP + 15.0
            title_pt = (render_index * phase_spacing, row_y, title_z)
            title_text = "Phase {0}: {1}".format(phase["number"], phase["title"])
            ensure_layer("labels", (255, 255, 100))
            title_dot = rs.AddTextDot(title_text, title_pt)
            if title_dot:
                rs.ObjectLayer(title_dot, "labels")
            render_index += 1
            report_fn("  Phase {0} ({1}): baked {2} item(s) on layer {3}".format(
                phase["number"], phase["title"], keys_baked, layer_name))


def main():
    report("=== RelativeMotion dev harness ===")
    bk.clear_doc()

    if STOP_AFTER is not None:
        report("STOP_AFTER = {0} (pipeline will halt after this phase)".format(STOP_AFTER))
    available = sorted(p.name for p in _INPUTS.glob("*.json"))
    report("inputs available in {0}:".format(_INPUTS))
    for name in available:
        marker = "  <== running" if name in INPUT_FILES else ""
        report("  - {0}{1}".format(name, marker))

    # Validate every configured input before touching the doc, so a typo doesn't half-clear it.
    input_paths = []
    for name in INPUT_FILES:
        p = _INPUTS / name
        if not p.exists():
            report("ERROR: input file not found: {0}".format(p))
            flush_report()
            return
        input_paths.append((name, p))

    _layout.reset()

    for row_index, (name, path) in enumerate(input_paths):
        row_y = row_index * _layout.row_spacing_mm
        _layout.start_row(name, row_index)
        raw_data = json.loads(path.read_text(encoding="utf-8"))
        # Distinct 4-char object ID per row so the emboss (baked into the splint) identifies
        # which input it came from at a glance.
        object_id = "DV{0:02d}".format(row_index)
        report("running RelativeMotionGenerator.generate (objectID {0})...".format(object_id))

        generator = RelativeMotionGenerator()
        debug = {}
        stopped_early = False
        try:
            result = generator.generate(raw_data, object_id, debug=debug,
                                        stop_after=STOP_AFTER)
        except StopAfterPhase as e:
            report("STOPPED AFTER phase {0}: {1}".format(e.phase, e.title))
            result = None
            stopped_early = True
        except Exception as exc:
            report("PARTIAL RUN for '{0}': {1}".format(name, exc))
            report(traceback.format_exc())
            result = None

        # Auto-preview all phases that ran, laid out along +X.
        _bake_phase_geometry(debug, row_y, report)

        # Row label text-dot above the first phase offset.
        splint_solid_blank = debug.get("splint_solid_blank")
        ref_brep = splint_solid_blank or debug.get("splint_solid")
        if ref_brep is not None:
            try:
                bbox = ref_brep.GetBoundingBox(True)
                fn_dot_pt = rg.Point3d(
                    0.5 * (bbox.Min.X + bbox.Max.X),
                    0.5 * (bbox.Min.Y + bbox.Max.Y) + row_y,
                    bbox.Max.Z + 40.0)
                ensure_layer("DEV_row_filename", (255, 220, 0))
                fn_dot = rs.AddTextDot(name, (fn_dot_pt.X, fn_dot_pt.Y, fn_dot_pt.Z))
                if fn_dot:
                    rs.ObjectLayer(fn_dot, "DEV_row_filename")
            except Exception:
                pass

        if ENABLE_MESH_EXPORT and result is not None:
            report("  pipeline complete for '{0}'".format(name))
            # Export 3mf using the same production path
            try:
                root_name = name.replace(".json", "")
                export_result = export_mesh(
                    result.mesh, str(_OUTPUTS), root_name, "3mf",
                    emit_pipeline_signal=False)
                size_kb = export_result["file_size_bytes"] / 1024.0
                report("  exported: {0}.3mf ({1:.1f} KB)".format(root_name, size_kb))
            except Exception as exc:
                report("  mesh export FAILED: {0}".format(exc))
        elif stopped_early:
            report("  early stop - {0} phase(s) previewed".format(
                len(debug.get("_phases", []))))

        if not ENABLE_MESH_EXPORT:
            report("  mesh export disabled (ENABLE_MESH_EXPORT = False)")

    # Timestamp text-dot at the origin so there's a freshness indicator in the viewport.
    import datetime
    now_str = datetime.datetime.now().strftime("%H:%M:%S")
    ensure_layer("DEV_timestamp", (200, 200, 200))
    ts_dot = rs.AddTextDot("harness run: {0}".format(now_str), (0.0, -30.0, 0.0))
    if ts_dot:
        rs.ObjectLayer(ts_dot, "DEV_timestamp")

    sc.doc.Views.Redraw()
    report("")
    report("done. report written to {0}".format(_REPORT))
    flush_report()


try:
    main()
except Exception:
    report("HARNESS EXCEPTION:")
    report(traceback.format_exc())
    flush_report()
    raise
