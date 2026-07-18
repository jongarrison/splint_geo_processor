"""Dev harness: run the full RelativeMotion pipeline against one or more dev inputs and bake
the three named outputs for each into the live Rhino doc for side-by-side visual inspection.

WHY THIS EXISTS
    Fastest edit-run-eyeball cycle for algorithm work. Calls the same
    `generate_relative_motion_splint` entrypoint production uses, so any change to the src
    modules is immediately exercised end-to-end without touching Grasshopper. All previewable
    geometry comes back in the `out` dict - the harness bakes only what's named there.

    Any new dev/preview geometry should be produced INSIDE generate_relative_motion_splint and
    passed back through `out`. The harness stays tiny: dispatch, log, bake.

LAYOUT
    Each input file gets its own row along +Y (rows separated by _ROW_SPACING_MM). Inside a
    row the three previews step out along +X (slots 0/1/2: solid+rails, solid, oriented mesh).
    Text-dot labels identify each row and each preview.

HOW TO RUN
    ./run_harness.sh              # from splint_geo_processor/
    (auto-detects Rhino 8, dispatches this script, waits for report file to reappear, prints it)

SWITCHING INPUTS
    Edit INPUT_FILES below - list one or more *.json filenames from dev/inputs/. Each JSON is
    a full raw_data payload with the exact keys generate_relative_motion_splint reads.
"""

# ------------------------------------------------------------------ config (edit me) ---------
# One or more inputs to run in the same session. Each row is offset in +Y so results sit
# side-by-side without overlapping. Add / remove entries freely.
INPUT_FILES = [
    "BTR8_3anchor_prod.json",
    "if_mf_rf_2anchor_20deg.json",
]

import os
import sys
import json
import traceback
from pathlib import Path
from importlib import reload

import scriptcontext as sc
import rhinoscriptsyntax as rs
import Rhino.Geometry as rg

_HERE = Path(__file__).resolve().parent          # .../generators/dev
_SRC = _HERE.parent / "src"                       # .../generators/src
_INPUTS = _HERE / "inputs"
_REPORT = _HERE / "last_run_report.txt"
if str(_SRC) not in sys.path:
    sys.path.append(str(_SRC))

# Reload the module chain so edits since the Rhino session started are picked up. RelativeMotion
# reloads its own geometry deps (BrepDifference, BrepChamfer, BrepEdgeLocator, etc.) on import,
# so reloading it cascades; splintcommon is reloaded first since RelativeMotion pulls symbols from it.
import splintcommon
reload(splintcommon)
import RelativeMotion
reload(RelativeMotion)
from RelativeMotion import generate_relative_motion_splint

# ------------------------------------------------------------------ report writer ------------
_report_lines = []

def report(msg=""):
    """Print to the Rhino console AND buffer for the on-disk report file."""
    print(msg)
    _report_lines.append(str(msg))

def flush_report():
    _REPORT.write_text("\n".join(_report_lines) + "\n", encoding="utf-8")

# ------------------------------------------------------------------ baking helpers -----------
DEV_PREFIX = "DEV_"

# Preview layout: each input file is a ROW along +Y (separated by _ROW_SPACING_MM). Inside a
# row the three previews step out along +X in numbered slots (0/1/2). Slot 0 sits at the row's
# X=0 - reserved for the primary preview (splint_solid + support-rails overlay).
_PREVIEW_SPACING_MM = 90.0    # +X between slots; a bit wider than the splint bbox X extent
_ROW_SPACING_MM = 150.0       # +Y between input rows; wider than the splint bbox Y extent
_preview_slot_counter = [0]   # boxed so helpers can mutate without a `global`
_current_row_y = [0.0]        # +Y offset of the current input row


def next_preview_offset(label=None):
    """Return a Vector3d offset for the next preview slot in the CURRENT input row. Slots
    step +X inside the row; the row's own +Y offset is baked in. First call after
    start_input_row() returns slot 0 (X=0, the row's primary slot). label is optional and
    only used for readability in the report."""
    slot = _preview_slot_counter[0]
    _preview_slot_counter[0] += 1
    offset = rg.Vector3d(slot * _PREVIEW_SPACING_MM, _current_row_y[0], 0.0)
    if label:
        report("  preview slot {0} at (+X {1:.0f}, +Y {2:.0f}) mm: {3}".format(
            slot, offset.X, offset.Y, label))
    return offset


def start_input_row(input_name, row_index):
    """Begin a new input row: reset the slot counter and set the row's +Y offset. Drops a
    text-dot to the left of the row identifying which input file it belongs to."""
    _preview_slot_counter[0] = 0
    _current_row_y[0] = row_index * _ROW_SPACING_MM
    y = _current_row_y[0]
    lbl = "input {0}: {1}".format(row_index, input_name)
    ensure_layer("DEV_row_labels", (255, 220, 0))
    dot = rs.AddTextDot(lbl, (-50.0, y, 0.0))
    if dot:
        rs.ObjectLayer(dot, "DEV_row_labels")
    report("")
    report("=== row {0} at +Y {1:.0f}mm: {2} ===".format(row_index, y, input_name))


def clear_doc():
    """Wipe every baked object so each run starts from a clean doc. Only touches real document
    objects (Grasshopper preview geometry is not a doc object, so live GH work is untouched)."""
    objs = rs.AllObjects()
    if objs:
        rs.DeleteObjects(objs)
    _preview_slot_counter[0] = 0
    _current_row_y[0] = 0.0

def ensure_layer(name, color):
    if not rs.IsLayer(name):
        rs.AddLayer(name, color)
    return name

def _bake_one(geom, layer, offset=None):
    """Add a single RhinoCommon geometry object to the doc on the given layer. If offset is
    provided, the geometry is DUPLICATED first and the copy is translated - the caller's original
    is never mutated. Returns guid or None."""
    if offset is not None:
        # Duplicate before translating so preview shifts never corrupt working geometry.
        if isinstance(geom, rg.Brep):
            geom = geom.DuplicateBrep()
        elif isinstance(geom, rg.Mesh):
            geom = geom.DuplicateMesh()
        elif isinstance(geom, rg.Curve):
            geom = geom.DuplicateCurve()
        elif isinstance(geom, rg.Point3d):
            geom = rg.Point3d(geom)
        if hasattr(geom, "Translate"):
            geom.Translate(offset)
        elif isinstance(geom, rg.Point3d):
            geom = rg.Point3d(geom.X + offset.X, geom.Y + offset.Y, geom.Z + offset.Z)
    guid = None
    if isinstance(geom, rg.Brep):
        guid = sc.doc.Objects.AddBrep(geom)
    elif isinstance(geom, rg.Mesh):
        guid = sc.doc.Objects.AddMesh(geom)
    elif isinstance(geom, rg.Curve):
        guid = sc.doc.Objects.AddCurve(geom)
    elif isinstance(geom, rg.Point3d):
        guid = sc.doc.Objects.AddPoint(geom)
    if guid:
        rs.ObjectLayer(guid, layer)
    return guid

def bake(geom, layer, color, offset=None):
    """Bake one geometry or a (possibly nested) list of geometries onto a layer. Returns count.
    Pass offset (Vector3d, e.g. from next_preview_offset()) to shift the preview copy along +X
    without touching the working geometry."""
    ensure_layer(layer, color)
    count = 0
    if geom is None:
        return 0
    if isinstance(geom, (list, tuple)):
        for g in geom:
            count += bake(g, layer, color, offset=offset)
        return count
    if _bake_one(geom, layer, offset=offset) is not None:
        count += 1
    return count


def annotate(point, text, layer, offset=None):
    """Drop a text-dot annotation to draw the eye to a specific point in the viewport. Preview-
    only: use this for callouts on baked previews, not on working geometry."""
    ensure_layer(layer, (255, 255, 100))
    p = point
    if offset is not None:
        p = rg.Point3d(point.X + offset.X, point.Y + offset.Y, point.Z + offset.Z)
    guid = rs.AddTextDot(text, (p.X, p.Y, p.Z))
    if guid:
        rs.ObjectLayer(guid, layer)
    return guid


def label_rails(rails, layer, prefix, color=(255, 255, 100), offset=None):
    """Drop a text-dot at each rail's midpoint labeled "<prefix> N" so it is obvious in the
    viewport which construction curve is which rail. Skips None entries but preserves
    indexing. Pass `offset` (Vector3d) when the rails have been baked to a preview slot -
    the label follows the geometry into that slot."""
    ensure_layer(layer, color)
    for ri, rail in enumerate(rails):
        if rail is None:
            continue
        mid = rail.PointAtNormalizedLength(0.5)
        if offset is not None:
            mid = rg.Point3d(mid.X + offset.X, mid.Y + offset.Y, mid.Z + offset.Z)
        guid = rs.AddTextDot("{0} {1}".format(prefix, ri), (mid.X, mid.Y, mid.Z))
        if guid:
            rs.ObjectLayer(guid, layer)


def bake_preview(label, geom, layer, color, offset=None):
    """Bake `geom` to `layer` at `offset` (or at the true world position when offset is None),
    then drop a text-dot label above its bounding box so the preview is self-identifying in the
    viewport. Handles Brep + Mesh + Curve + lists.
    Returns the count of baked objects, or 0 when geom is None."""
    if geom is None:
        report("preview '{0}': geometry missing (None); skipping bake".format(label))
        return 0
    count = bake(geom, layer, color, offset=offset)
    if count == 0:
        return 0
    # Position the label above the bounding box. Compute on the original geom (bake already
    # applied offset internally on a duplicate), then shift the label point by the same offset.
    ref = geom[0] if isinstance(geom, (list, tuple)) and len(geom) > 0 else geom
    try:
        bbox = ref.GetBoundingBox(True)
    except Exception:
        bbox = None
    if bbox is None:
        return count
    top = rg.Point3d(0.5 * (bbox.Min.X + bbox.Max.X),
                     0.5 * (bbox.Min.Y + bbox.Max.Y),
                     bbox.Max.Z + 10.0)
    if offset is not None:
        top = rg.Point3d(top.X + offset.X, top.Y + offset.Y, top.Z + offset.Z)
    lbl_layer = layer + "_labels"
    ensure_layer(lbl_layer, (255, 255, 100))
    dot = rs.AddTextDot(label, (top.X, top.Y, top.Z))
    if dot:
        rs.ObjectLayer(dot, lbl_layer)
    return count


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
        #break #just one for now

    clear_doc()

    for row_index, (name, path) in enumerate(input_paths):
        start_input_row(name, row_index)
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
        splint_oriented = r.get("splint_oriented")
        support_rails = [c for c in ((r.get("p_support_rails") or [])
                                     + (r.get("d_support_rails") or [])) if c is not None]

        # Slot 0 (row's X=0): splint_solid overlaid with the support rails that drove Phase
        # 7.5b's variable-distance perimeter chamfer. Eyeballing the two together shows where
        # the chamfer strips landed (or where they should have landed if a rail's edge failed).
        offset0 = next_preview_offset("splint_solid + support rails")
        bake_preview("splint_solid + support rails", splint_solid,
                     "DEV_splint_solid_with_rails", (150, 220, 150), offset=offset0)
        bake(support_rails, "DEV_support_rails", (0, 200, 255), offset=offset0)
        label_rails(support_rails, "DEV_support_rails_labels", "support rail",
                    (0, 200, 255), offset=offset0)

        # Filename text-dot sitting well above slot 0's splint so each row is instantly
        # identifiable by the source input at a glance from the top-down view.
        if splint_solid is not None:
            try:
                bbox = splint_solid.GetBoundingBox(True)
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

        # Slot 1: splint_solid on its own for a clean look at the finished
        # bored/chamfered/embossed brep without the rail overlay.
        offset1 = next_preview_offset("splint_solid")
        bake_preview("splint_solid", splint_solid,
                     "DEV_splint_solid", (150, 220, 150), offset=offset1)

        # Slot 2: splint_oriented (print-ready mesh, resting distal-face-down on Z=0).
        offset2 = next_preview_offset("splint_oriented")
        bake_preview("splint_oriented (print-ready mesh)", splint_oriented,
                     "DEV_splint_oriented", (200, 200, 255), offset=offset2)

        # Slot 3: dedicated Phase 7.6 slit-debug view. Shows the splint_solid as a dim
        # reference alongside the slit cutter breps (red), wall cross-section curves
        # (yellow), and detection panels (gray). Each piece gets its own layer so noisy
        # bits (panels are large flat rectangles) can be toggled off in the Layers panel
        # while keeping cutters + cross-sections visible for placement checks.
        slit_cutter_breps = r.get("slit_cutter_breps") or []
        slit_panels = r.get("slit_panels") or []
        slit_cross_sections = r.get("slit_cross_sections") or []
        offset3 = next_preview_offset("slit debug (splint reference + cutters + panels)")
        bake_preview("slit debug", splint_solid,
                     "DEV_slit_debug_splint_ref", (110, 130, 110), offset=offset3)
        if slit_cutter_breps:
            bake(slit_cutter_breps, "DEV_slit_cutters", (255, 60, 60), offset=offset3)
            report("  baked {0} slit cutter brep(s) on DEV_slit_cutters".format(
                len(slit_cutter_breps)))
        if slit_cross_sections:
            bake(slit_cross_sections, "DEV_slit_cross_sections", (255, 255, 0),
                 offset=offset3)
            report("  baked {0} slit wall cross-section curve(s) on "
                   "DEV_slit_cross_sections".format(len(slit_cross_sections)))
        if slit_panels:
            bake(slit_panels, "DEV_slit_panels", (140, 140, 140), offset=offset3)
            report("  baked {0} slit panel brep(s) on DEV_slit_panels".format(
                len(slit_panels)))

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
