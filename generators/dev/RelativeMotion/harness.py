"""Dev harness: run the full RelativeMotion pipeline against one or more dev inputs and bake
the named outputs for each into the live Rhino doc for side-by-side visual inspection.

WHY THIS EXISTS
    Fastest edit-run-eyeball cycle for algorithm work. Calls the same
    `generate_relative_motion_splint` entrypoint production uses, so any change to the src
    modules is immediately exercised end-to-end without touching Grasshopper. All previewable
    geometry comes back in the `out` dict - the harness bakes only what's named there.

    Any new dev/preview geometry should be produced INSIDE generate_relative_motion_splint and
    passed back through `out`. The harness stays tiny: dispatch, log, bake. All the generic
    bake/report/layout plumbing (splint-agnostic) lives in generators/dev/_devkit/bake_utils.py -
    this file only contains RelativeMotion-specific logic: which inputs to run and which debug
    keys from `generate_relative_motion_splint`'s result to bake.

LAYOUT
    Each input file gets its own row along +Y. Inside a row, previews step out along +X in
    slots (pre-ramp body, slit debug, ramp construction, ramp union result, oriented mesh).
    Text-dot labels identify each row and each preview (see bake_utils.PreviewLayout).

HOW TO RUN
    ./run.sh                       # from this directory
    (auto-detects Rhino 8, dispatches this script, waits for report file to reappear, prints it)

SWITCHING INPUTS
    Edit INPUT_FILES below - list one or more *.json filenames from inputs/. Each JSON is a full
    raw_data payload with the exact keys generate_relative_motion_splint reads.
"""

# ------------------------------------------------------------------ config (edit me) ---------
# One or more inputs to run in the same session. Each row is offset in +Y so results sit
# side-by-side without overlapping. Add / remove entries freely.
INPUT_FILES = [
    # "AASA_20.json",
    # "ASAA_BTR8_prod.json",
    # "ASAX_20deg.json",
    # "ASSA_20.json",
    # "XASA_ZM1Q_prod.json",
    # "ASSA_2QY6.json",
    # "2QY6_prod_exact.json",
    "MX2E.json"
]

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
_REPORT = _HERE / "last_run_report.txt"
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
from RelativeMotion import generate_relative_motion_splint

import bake_utils as bk

_rpt = bk.ReportBuffer(_REPORT)
report = _rpt.write
flush_report = _rpt.flush

_layout = bk.PreviewLayout(preview_spacing_mm=90.0, row_spacing_mm=150.0, report=report)

# Thin aliases onto the shared, splint-agnostic helpers so the rest of this file (and anyone
# copy-pasting a new splint's harness from this one) reads the same as before the split.
bake = bk.bake
ensure_layer = bk.ensure_layer
annotate = bk.annotate
label_rails = bk.label_rails


def bake_preview(label, geom, layer, color, offset=None):
    return bk.bake_preview(label, geom, layer, color, offset=offset, report=report)


def main():
    report("=== RelativeMotion dev harness ===")
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

    bk.clear_doc()
    _layout.reset()

    for row_index, (name, path) in enumerate(input_paths):
        _layout.start_row(name, row_index)
        raw_data = json.loads(path.read_text(encoding="utf-8"))
        # Distinct 4-char object ID per row so the emboss (baked into the splint) identifies
        # which input it came from at a glance.
        object_id = "DV{0:02d}".format(row_index)
        report("running generate_relative_motion_splint (objectID {0})...".format(object_id))
        # Full pipeline: perimeters -> bores -> chamfer -> emboss -> mesh -> orient. Dev mode
        # (is_production=False) keeps the try/except inside the pipeline so partial failures
        # still come back with whatever geometry got built. should_save_mesh=False skips
        # writing a DEV_ 3mf to the outbox (the harness only cares about live viewport previews).
        r = generate_relative_motion_splint(raw_data, False, False, object_id)
        if r.get("error"):
            report("PARTIAL RUN for '{0}': {1}".format(name, r["error"]))

        splint_solid = r.get("splint_solid")
        splint_solid_pre_ramp = r.get("splint_solid_pre_ramp")
        splint_oriented = r.get("splint_oriented")

        # Slot 0: "Finished body" = post-chamfer + post-slit + post-emboss, BEFORE ramp.
        # This is the stable splint body that all subtractive finishing has been applied to.
        offset0 = _layout.next_offset("pre-ramp splint body (chamfer+slit+emboss)")
        bake_preview("pre-ramp splint body", splint_solid_pre_ramp,
                     "DEV_splint_pre_ramp", (150, 220, 150), offset=offset0)

        # Filename text-dot sitting well above slot 0's splint so each row is instantly
        # identifiable by the source input at a glance from the top-down view.
        ref_brep = splint_solid_pre_ramp or splint_solid
        if ref_brep is not None:
            try:
                bbox = ref_brep.GetBoundingBox(True)
                fn_dot_pt = rg.Point3d(
                    0.5 * (bbox.Min.X + bbox.Max.X) + offset0.X,
                    0.5 * (bbox.Min.Y + bbox.Max.Y) + offset0.Y,
                    bbox.Max.Z + 40.0)
                ensure_layer("DEV_row_filename", (255, 220, 0))
                fn_dot = rs.AddTextDot(name, (fn_dot_pt.X, fn_dot_pt.Y, fn_dot_pt.Z))
                if fn_dot:
                    rs.ObjectLayer(fn_dot, "DEV_row_filename")
            except Exception:
                pass

        # Slot 1: slit debug (existing - splint ref + cutters + panels + cross-sections).
        slit_cutter_breps = r.get("slit_cutter_breps") or []
        slit_panels = r.get("slit_panels") or []
        slit_cross_sections = r.get("slit_cross_sections") or []
        offset1 = _layout.next_offset("slit debug")
        bake_preview("slit debug", splint_solid_pre_ramp,
                     "DEV_slit_debug_splint_ref", (110, 130, 110), offset=offset1)
        if slit_cutter_breps:
            bake(slit_cutter_breps, "DEV_slit_cutters", (255, 60, 60), offset=offset1)
            report("  baked {0} slit cutter brep(s) on DEV_slit_cutters".format(
                len(slit_cutter_breps)))
        if slit_cross_sections:
            bake(slit_cross_sections, "DEV_slit_cross_sections", (255, 255, 0), offset=offset1)
            report("  baked {0} slit wall cross-section curve(s) on "
                   "DEV_slit_cross_sections".format(len(slit_cross_sections)))
        if slit_panels:
            bake(slit_panels, "DEV_slit_panels", (140, 140, 140), offset=offset1)
            report("  baked {0} slit panel brep(s) on DEV_slit_panels".format(
                len(slit_panels)))
        failed_panels = r.get("failed_slit_panels") or []
        failed_raw = r.get("failed_slit_raw_intersections") or []
        failed_joined = r.get("failed_slit_joined_intersections") or []
        failed_cutters = r.get("failed_slit_cutters") or []
        if failed_panels:
            bake(failed_panels, "DEV_slit_FAILED_panels", (255, 0, 255), offset=offset1)
        if failed_raw:
            bake(failed_raw, "DEV_slit_FAILED_raw_ix", (255, 100, 100), offset=offset1)
        if failed_joined:
            bake(failed_joined, "DEV_slit_FAILED_joined_ix", (255, 140, 0), offset=offset1)
        if failed_cutters:
            bake(failed_cutters, "DEV_slit_FAILED_cutters", (255, 0, 255), offset=offset1)

        # Slot 2: ramp construction (ramp solid + pre-ramp splint as SEPARATE bodies).
        ramp_debugs = r.get("support_path_ramp_debugs") or []
        offset2 = _layout.next_offset("ramp construction (separate bodies)")
        bake_preview("ramp: splint ref", splint_solid_pre_ramp,
                     "DEV_ramp_splint_ref", (110, 130, 110), offset=offset2)
        for rd in ramp_debugs:
            if rd.get("ramp_solid") is not None:
                bake(rd["ramp_solid"], "DEV_ramp_solid", (255, 60, 60), offset=offset2)
            elif rd.get("ramp_tube") is not None:
                bake(rd["ramp_tube"], "DEV_ramp_FAILED_tube", (255, 0, 255), offset=offset2)
            if rd.get("ramp_profile") is not None:
                bake(rd["ramp_profile"], "DEV_ramp_profile", (255, 255, 0), offset=offset2)
            if rd.get("ramp_rail") is not None:
                bake(rd["ramp_rail"], "DEV_ramp_rail", (255, 140, 0), offset=offset2)
            if rd.get("trimmed_rail") is not None:
                bake(rd["trimmed_rail"], "DEV_ramp_trimmed_rail", (0, 200, 255), offset=offset2)
        if ramp_debugs:
            report("  baked {0} ramp debug set(s) on DEV_ramp_* layers".format(len(ramp_debugs)))

        # Slot 3: ramp/splint union result (the final splint_solid after union, or pre-ramp
        # if union failed). Comparing this to slot 0 reveals whether the ramp actually merged.
        offset3 = _layout.next_offset("ramp union result (final splint_solid)")
        bake_preview("ramp union result", splint_solid,
                     "DEV_ramp_union_result", (150, 220, 150), offset=offset3)

        # Slot 4: final mesh oriented (print-ready, resting proximal-face-down on Z=0).
        offset4 = _layout.next_offset("splint_oriented (print-ready mesh)")
        bake_preview("splint_oriented", splint_oriented,
                     "DEV_splint_oriented", (200, 200, 255), offset=offset4)

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
