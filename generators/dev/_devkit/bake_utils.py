"""bake_utils.py - shared, splint-agnostic dev-harness plumbing (generators/dev/_devkit/).

Every per-splint harness (generators/dev/<Splint>/harness.py) imports this module to bake
preview geometry into the live Rhino doc, lay previews out in a row/slot grid, and write the
on-disk report file that run_harness.sh polls for. Nothing here knows about any specific splint
algorithm - splint-specific logic (input list, calling the algorithm, which debug keys to bake)
stays in the per-splint harness.py.
"""

import time as _time
import Rhino
import scriptcontext as sc
import rhinoscriptsyntax as rs
import Rhino.Geometry as rg


class ReportBuffer:
    """Streams report lines to `path` in real time, with elapsed-time prefixes.

    run_harness.sh tails `path` for live output and watches for a `.done` marker
    (written by flush()) as the completion signal."""

    def __init__(self, path):
        self.path = path
        self._done_path = path.with_suffix(".done")
        self._t0 = _time.time()
        # Create/truncate the report file immediately (signals "started" to run_harness.sh).
        self.path.write_text("", encoding="utf-8")

    def write(self, msg=""):
        elapsed = _time.time() - self._t0
        line = "[{:.1f}s] {}".format(elapsed, msg)
        print(line)
        import sys
        sys.stdout.flush()
        with open(str(self.path), "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def flush(self):
        # Write done marker so run_harness.sh knows the run is complete.
        self._done_path.write_text("done\n", encoding="utf-8")


def ensure_layer(name, color):
    if not rs.IsLayer(name):
        rs.AddLayer(name, color)
    return name


def clear_doc():
    """Wipe every baked object so each run starts from a clean doc. Also clears Rhino's
    command history window."""
    objs = rs.AllObjects()
    if objs:
        rs.DeleteObjects(objs)
    Rhino.RhinoApp.ClearCommandHistoryWindow()


def _bake_one(geom, layer, offset=None, name=None, color=None):
    """Add a single RhinoCommon geometry object to the doc on the given layer. If offset is
    provided, the geometry is DUPLICATED first and the copy is translated - the caller's
    original is never mutated. Sets the object's Name and per-object color when given.
    Returns guid or None."""
    if offset is not None:
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
    # AddBrep/AddCurve can return Guid.Empty (all zeros, not None) on failure; treat it as None.
    if guid and str(guid) != "00000000-0000-0000-0000-000000000000":
        rs.ObjectLayer(guid, layer)
        if name:
            rs.ObjectName(guid, name)
        if color:
            rs.ObjectColor(guid, color)
    return guid


def bake(geom, layer, color, offset=None, name=None):
    """Bake one geometry or a (possibly nested) list of geometries onto a layer. Returns count.
    Pass offset (Vector3d, e.g. from PreviewLayout.next_offset()) to shift the preview copy
    along +X without touching the working geometry."""
    count = 0
    if geom is None:
        return 0
    if isinstance(geom, (list, tuple, dict)):
        items = geom.values() if isinstance(geom, dict) else geom
        for item in items:
            count += bake(item, layer, color, offset=offset, name=name)
        return count
    if not isinstance(geom, (rg.Brep, rg.Mesh, rg.Curve, rg.Point3d)):
        return 0
    ensure_layer(layer, color)
    if _bake_one(geom, layer, offset=offset, name=name, color=color) is not None:
        count += 1
    return count


def bake_preview(label, geom, layer, color, offset=None, report=None, label_z=None,
                 label_layer="labels"):
    """Bake `geom` to `layer` at `offset`, then drop a text-dot label above it.
    When label_z is given, the label is placed at that absolute Z (pre-offset) instead of
    computing from the bounding box. Returns baked object count."""
    if geom is None:
        if report:
            report("preview '{0}': geometry missing (None); skipping bake".format(label))
        return 0
    count = bake(geom, layer, color, offset=offset, name=label)
    if count == 0:
        return 0
    # Label position: use explicit label_z when provided, otherwise bbox top + margin.
    ref = geom[0] if isinstance(geom, (list, tuple)) and len(geom) > 0 else geom
    try:
        bbox = ref.GetBoundingBox(True)
    except Exception:
        bbox = None
    if bbox is None:
        return count
    cx = 0.5 * (bbox.Min.X + bbox.Max.X)
    cy = 0.5 * (bbox.Min.Y + bbox.Max.Y)
    z = label_z if label_z is not None else (bbox.Max.Z + 10.0)
    top = rg.Point3d(cx, cy, z)
    if offset is not None:
        top = rg.Point3d(top.X + offset.X, top.Y + offset.Y, top.Z + offset.Z)
    ensure_layer(label_layer, (255, 255, 100))
    dot = rs.AddTextDot(label, (top.X, top.Y, top.Z))
    if dot:
        rs.ObjectLayer(dot, label_layer)
    return count


def bake_phase_geometry(debug, row_y=0.0, preview_filter=None, report=None, layer_prefix=""):
    """Bake construction keys by phase; filters accept phase numbers, 'N+', or key names."""
    import colorsys

    report_fn = report or (lambda message: None)
    phases = list(debug.get("_phases", []))
    registered = {key for phase in phases for key in phase["keys"]}
    pending = [key for key in debug if not key.startswith("_") and key not in registered]
    if pending:
        phases.append({"number": None, "title": "unfinished construction", "keys": pending})

    filter_phases = set()
    filter_keys = set()
    filter_min = None
    for entry in preview_filter or ():
        if isinstance(entry, (int, float)):
            filter_phases.add(entry)
        elif isinstance(entry, str) and entry.endswith("+"):
            threshold = float(entry[:-1])
            filter_min = threshold if filter_min is None else min(filter_min, threshold)
        elif isinstance(entry, str):
            filter_keys.add(entry)

    render_index = 0
    color_index = 0
    total_keys = 0
    label_layer = layer_prefix + "labels"
    for phase in phases:
        number = phase["number"]
        phase_match = (not preview_filter or number in filter_phases
                       or (filter_min is not None and number is not None and number >= filter_min))
        offset = rg.Vector3d(render_index * 100.0, row_y, 0.0)
        phase_name = ("P{0}_{1}".format(number, phase["title"].replace(" ", "_"))
                      if number is not None else "unfinished_construction")
        layer_name = layer_prefix + phase_name
        keys_baked = 0
        for key in phase["keys"]:
            if not phase_match and key not in filter_keys:
                continue
            hue = (color_index * 0.618033988749895) % 1.0
            red, green, blue = colorsys.hsv_to_rgb(hue, 0.6, 0.9)
            color = (int(red * 255), int(green * 255), int(blue * 255))
            count = bake_preview(
                key, debug.get(key), layer_name, color, offset=offset,
                label_z=20.0 + keys_baked * 8.0, label_layer=label_layer)
            if count:
                keys_baked += 1
                color_index += 1
        if keys_baked:
            title = ("Phase {0}: {1}".format(number, phase["title"])
                     if number is not None else "Unfinished construction")
            annotate(rg.Point3d(0.0, 0.0, 35.0 + keys_baked * 8.0),
                     title, label_layer, offset=offset)
            report_fn("  {0}: baked {1} key(s) on layer {2}".format(title, keys_baked, layer_name))
            render_index += 1
            total_keys += keys_baked
    if not total_keys:
        report_fn("  (no construction geometry to preview)")
    return total_keys


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
    indexing. Pass `offset` (Vector3d) when the rails have been baked to a preview slot - the
    label follows the geometry into that slot."""
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


class PreviewLayout:
    """Lays previews out in a grid: each input file gets a ROW along +Y (rows separated by
    row_spacing_mm); inside a row, previews step out along +X in numbered slots (separated by
    preview_spacing_mm). Slot 0 sits at the row's X=0 - reserved for a harness's primary
    preview. `report` is an optional callable(msg) used to log row starts and slot offsets."""

    def __init__(self, preview_spacing_mm=90.0, row_spacing_mm=150.0, report=None):
        self.preview_spacing_mm = preview_spacing_mm
        self.row_spacing_mm = row_spacing_mm
        self._report = report
        self._slot = 0
        self._row_y = 0.0

    def reset(self):
        self._slot = 0
        self._row_y = 0.0

    def start_row(self, input_name, row_index):
        """Begin a new input row: reset the slot counter and set the row's +Y offset. Drops a
        text-dot to the left of the row identifying which input file it belongs to."""
        self._slot = 0
        self._row_y = row_index * self.row_spacing_mm
        y = self._row_y
        lbl = "input {0}: {1}".format(row_index, input_name)
        ensure_layer("DEV_row_labels", (255, 220, 0))
        dot = rs.AddTextDot(lbl, (-50.0, y, 0.0))
        if dot:
            rs.ObjectLayer(dot, "DEV_row_labels")
        if self._report:
            self._report("")
            self._report("=== row {0} at +Y {1:.0f}mm: {2} ===".format(row_index, y, input_name))

    def next_offset(self, label=None):
        """Return a Vector3d offset for the next preview slot in the CURRENT input row. Slots
        step +X inside the row; the row's own +Y offset is baked in. First call after
        start_row() returns slot 0 (X=0, the row's primary slot)."""
        slot = self._slot
        self._slot += 1
        offset = rg.Vector3d(slot * self.preview_spacing_mm, self._row_y, 0.0)
        if label and self._report:
            self._report("  preview slot {0} at (+X {1:.0f}, +Y {2:.0f}) mm: {3}".format(
                slot, offset.X, offset.Y, label))
        return offset
