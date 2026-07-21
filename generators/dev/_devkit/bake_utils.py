"""bake_utils.py - shared, splint-agnostic dev-harness plumbing (generators/dev/_devkit/).

Every per-splint harness (generators/dev/<Splint>/harness.py) imports this module to bake
preview geometry into the live Rhino doc, lay previews out in a row/slot grid, and write the
on-disk report file that run_harness.sh polls for. Nothing here knows about any specific splint
algorithm - splint-specific logic (input list, calling the algorithm, which debug keys to bake)
stays in the per-splint harness.py.
"""

import scriptcontext as sc
import rhinoscriptsyntax as rs
import Rhino.Geometry as rg


class ReportBuffer:
    """Buffers report lines, echoing each to the Rhino console, and writes them to `path` on
    flush(). run_harness.sh deletes `path` before dispatching and waits for it to reappear as
    the "Rhino finished" signal, so flush() must be the harness's last action."""

    def __init__(self, path):
        self.path = path
        self.lines = []

    def write(self, msg=""):
        print(msg)
        self.lines.append(str(msg))

    def flush(self):
        self.path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")


def ensure_layer(name, color):
    if not rs.IsLayer(name):
        rs.AddLayer(name, color)
    return name


def clear_doc():
    """Wipe every baked object so each run starts from a clean doc. Only touches real document
    objects (Grasshopper preview geometry is not a doc object, so live GH work is untouched)."""
    objs = rs.AllObjects()
    if objs:
        rs.DeleteObjects(objs)


def _bake_one(geom, layer, offset=None):
    """Add a single RhinoCommon geometry object to the doc on the given layer. If offset is
    provided, the geometry is DUPLICATED first and the copy is translated - the caller's
    original is never mutated. Returns guid or None."""
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
    if guid:
        rs.ObjectLayer(guid, layer)
    return guid


def bake(geom, layer, color, offset=None):
    """Bake one geometry or a (possibly nested) list of geometries onto a layer. Returns count.
    Pass offset (Vector3d, e.g. from PreviewLayout.next_offset()) to shift the preview copy
    along +X without touching the working geometry."""
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


def bake_preview(label, geom, layer, color, offset=None, report=None):
    """Bake `geom` to `layer` at `offset`, then drop a text-dot label above its bounding box so
    the preview is self-identifying in the viewport. Handles Brep + Mesh + Curve + lists.
    Returns the count of baked objects, or 0 when geom is None. `report` is an optional
    callable(msg) used to log a "geometry missing" line (pass a ReportBuffer.write)."""
    if geom is None:
        if report:
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
