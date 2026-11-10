"""Run local InfinityFlex2 fixtures, preview phase geometry, and optionally export the mesh."""

INPUT_FILES = ["WQPG.json"]
STOP_AFTER = None
PREVIEW_FILTER = set()
ENABLE_MESH_EXPORT = True

import json
import sys
import traceback
from importlib import reload
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent.parent / "src"
_DEVKIT = _HERE.parent / "_devkit"
_INPUTS = _HERE / "inputs"
_OUTPUTS = _HERE / "outputs"
_OUTPUTS.mkdir(parents=True, exist_ok=True)
_REPORT = _OUTPUTS / "last_run_report.txt"
for _path in (_SRC, _DEVKIT):
    if str(_path) not in sys.path:
        sys.path.append(str(_path))

import bake_utils as bk
reload(bk)

_report_buffer = bk.ReportBuffer(_REPORT)
report = _report_buffer.write


def main():
    """Keep geometry in the generator and preview/export responsibilities in this harness."""
    report("=== InfinityFlex2 dev harness ===")

    import splintcommon
    reload(splintcommon)
    Path(splintcommon.get_log_filepath()).write_text("", encoding="utf-8")

    import InfinityFlex2
    reload(InfinityFlex2)
    import SplintMeshes2
    reload(SplintMeshes2)
    # Algorithm reload recreates this exception class; import it afterward for a matching catch.
    from splint_generator import StopAfterPhase

    inputs = []
    for name in INPUT_FILES:
        path = _INPUTS / name
        raw_data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw_data, dict) or "params" in raw_data:
            raise ValueError("{0}: expected bare measurements, not a job envelope".format(name))
        inputs.append((path, raw_data))

    import rhinoscriptsyntax as rs
    import scriptcontext as sc

    for layer in rs.LayerNames() or []:
        if layer.startswith("InfinityFlex2_"):
            objects = rs.ObjectsByLayer(layer)
            if objects:
                rs.DeleteObjects(objects)

    for row_index, (path, raw_data) in enumerate(inputs):
        object_id = path.stem
        report("running InfinityFlex2Generator.generate: {0} (objectID {1})".format(
            path.name, object_id))
        debug = {}
        generator = InfinityFlex2.InfinityFlex2Generator()
        result = None
        splintcommon.mark_generation_start()
        try:
            result = generator.generate(raw_data, object_id, debug=debug,
                                        stop_after=STOP_AFTER)
            report("status: {0}".format(result.metadata["status"]))
        except StopAfterPhase as exc:
            report("STOPPED AFTER phase {0}: {1}".format(exc.phase, exc.title))
        finally:
            bk.bake_phase_geometry(
                debug, row_y=row_index * 150.0, preview_filter=PREVIEW_FILTER,
                report=report, layer_prefix="InfinityFlex2_{0}_".format(path.stem))
            sc.doc.Views.Redraw()

        received = {
            "algorithm": generator.GEO_ALGORITHM_NAME,
            "object_id": debug["object_id"],
            "raw_data": debug["raw_data"],
        }
        report("inputs received by the generator:")
        for line in json.dumps(received, indent=2, sort_keys=True).splitlines():
            report(line)
        if result is not None and result.solid is not None:
            report("solid: IsValid={0}, IsSolid={1}".format(
                result.solid.IsValid, result.solid.IsSolid))
        if ENABLE_MESH_EXPORT and result is not None:
            root_filename = "DEV_{0}_{1}".format(generator.GEO_ALGORITHM_NAME, object_id)
            exported = SplintMeshes2.export_mesh_with_metadata(
                result.mesh, str(_OUTPUTS), root_filename, "3mf",
                custom_metadata=result.metadata, emit_pipeline_signal=False,
            )
            report("mesh saved: {0} ({1} bytes)".format(
                exported["file_path"], exported["file_size_bytes"]))
        elif result is None:
            report("Mesh export skipped: generation stopped early.")
        else:
            report("Mesh export disabled (ENABLE_MESH_EXPORT = False).")
        report("Only InfinityFlex2 preview layers replaced.")

    report("done. report written to {0}".format(_REPORT))


try:
    main()
except Exception:
    report("HARNESS EXCEPTION:")
    report(traceback.format_exc())
    raise
finally:
    _report_buffer.flush()