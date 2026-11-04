"""
InfinityFlexDev.py

Work-in-progress Python port of the InfinityFlex splint Design Definition (the binary
generators/InfinityFlex.gh). This module is the emerging central point of contact for the
InfinityFlex geometry, mirroring the structure of src/RelativeMotion.py.

Named *Dev* on purpose: production keeps running generators/InfinityFlex.gh. The pipeline
prefers generators/<algorithm>.py and only falls back to <algorithm>.gh - there is no
generators/InfinityFlex.py, so nothing here touches production. We port one Grasshopper step
at a time into pure Python without disturbing the live design.

Reusable geometry lives in dedicated helper modules (e.g. Orienteering.py). This module
orchestrates those helpers into the InfinityFlex phase sequence and surfaces each phase's
construction geometry for Rhino preview via the debug dict / PhaseTracker pattern.

Phase plan (running spec: dev/InfinityFlex/DevNotes_InfinityFlex_splint.md). Numbers are
provisional until more of the .gh is interpreted:
  1.0 .. N-1  splint body construction - NOT YET PORTED (still only in InfinityFlex.gh)
  N.0         positive sphere direction indicator - PORTED
              (Orienteering.positive_sphere_direction_indicator)
"""

import os as _os
from importlib import reload
from splintcommon import log

import Orienteering
reload(Orienteering)
from Orienteering import positive_sphere_direction_indicator, OrienteeringError

import splint_generator
reload(splint_generator)
from splint_generator import SplintGenerator, SplintResult, PhaseTracker, StopAfterPhase

# Stamp logged on every reload so we can verify the running code matches the source file.
_MODULE_MTIME = _os.path.getmtime(__file__)
log("InfinityFlexDev loaded, source mtime={0}".format(_MODULE_MTIME))


class InfinityFlexDevGenerator(SplintGenerator):
    """WIP Python generator for the InfinityFlex splint. Not wired to production (prod runs
    InfinityFlex.gh). Ported steps are exposed as methods that take their inputs directly so
    each can be exercised and previewed in Rhino before the whole pipeline exists."""
    GEO_ALGORITHM_NAME = "InfinityFlexDev"

    def generate(self, raw_data, object_id, debug=None, stop_after=None):
        """Full InfinityFlex pipeline - NOT YET PORTED.

        The splint-body phases (the bulk of InfinityFlex.gh) are still only defined in the
        binary .gh. Until they are ported, exercise the individual ported steps directly (e.g.
        add_direction_indicator), which take their geometry inputs rather than raw_data.
        """
        raise NotImplementedError(
            "InfinityFlexDev.generate is a placeholder; the splint-body phases are not ported "
            "yet. Use add_direction_indicator() to exercise the one ported step.")

    def add_direction_indicator(self, splint_brep, tip_top_band_curve, wall_thickness_mm,
                                debug=None, stop_after=None, **overrides):
        """Graft the volar/distal orientation nub onto a finished InfinityFlex body.

        Orchestrates Orienteering.positive_sphere_direction_indicator: runs the step, records it
        as a phase so the debug dict / harness preview picks up the construction geometry, and
        returns the unioned Brep. `overrides` forwards the tunable keyword args
        (ball_r_to_wall_ratio, nub_position_param, nub_sinking_factor, north_star_nudge_mm).
        """
        tracker = PhaseTracker(debug=debug, stop_after=stop_after, log_fn=log)
        # Collect the nub's construction geometry, then register it as a phase (which survives
        # into the caller's debug dict for preview) once the union succeeds.
        indicator_debug = {}
        result = positive_sphere_direction_indicator(
            splint_brep, tip_top_band_curve, wall_thickness_mm,
            debug=indicator_debug, **overrides)
        tracker.log_phase(1.0, "direction indicator", **indicator_debug)
        return result
