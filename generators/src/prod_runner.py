"""Shared production runner for Python-based splint generators.

Handles the common production flow: timing, generate, export mesh + metadata sidecar.
Sentinel emission ([PIPELINE_RESULT:...]) stays in each generator's top-level runner
shim (generators/<Algorithm>.py) so it can cover import failures too.
"""

from splintcommon import log, mark_generation_start
from SplintMeshes2 import export_mesh_with_metadata


def run_production_job(generator, raw_data, object_id, root_filename, output_dir):
    """Generate geometry and export the print-ready mesh + metadata sidecar.

    Args:
        generator: a SplintGenerator subclass instance.
        raw_data: algorithm-specific patient data dict.
        object_id: short ID for embossing/tracing.
        root_filename: base filename for the output (no extension).
        output_dir: directory for the output files.

    Returns:
        SplintResult from the generator.

    Raises:
        Whatever the generator or export raises - caller handles sentinels.
    """
    mark_generation_start()
    log("run_production_job: {0} starting (objectID {1}, file {2})".format(
        generator.GEO_ALGORITHM_NAME, object_id, root_filename))

    result = generator.generate(raw_data, object_id)

    export_mesh_with_metadata(
        result.mesh, output_dir, root_filename, "3mf",
        custom_metadata=result.metadata,
        emit_pipeline_signal=False)

    log("run_production_job: {0} export complete ({1}/{2}.3mf)".format(
        generator.GEO_ALGORITHM_NAME, output_dir, root_filename))
    return result
