"""
FingerGutter.py
Central point of contact for the FingerGutter finger splint Design Definition.

A finger gutter splint is a single-finger trough (a "gutter" / half-pipe channel) that cradles
one finger along its length to immobilize it. We build the finger's anatomical volume with
FingerModel, thicken it into a hollow shell, then trim away the dorsal cap so the finger seats
into the remaining volar trough (open on the dorsal/back side).

All geometry logic lives here in Python (observable/testable in Rhino). The production runner
(generators/FingerGutter.py, added at Mode 3) and dev harness
(generators/dev/FingerGutter/harness.py) both call FingerGutterGenerator.generate() with the same
signature used by RelativeMotionGenerator.

See generators/dev/FingerGutter/DevNotes_FingerGutter_splint.md for the construction rationale
(Phases 0-3).
"""

from dataclasses import replace
from Rhino.Geometry import (Point3d, Vector3d, Polyline, PolylineCurve, LineCurve, Line,
                            Extrusion)
from splintcommon import log

import os as _os
_MODULE_MTIME = _os.path.getmtime(__file__)
log("FingerGutter loaded, source mtime={0}".format(_MODULE_MTIME))

from importlib import reload

import FingerModel
reload(FingerModel)
from FingerModel import (create_finger_model_result, FingerParams, SEGMENT_ORDER,
                        JOINT_NAMES, JOINT_ADJACENCY)

import BrepDifference
reload(BrepDifference)
from BrepDifference import robust_brep_difference

import splint_generator
reload(splint_generator)
from splint_generator import SplintGenerator, SplintResult, PhaseTracker, StopAfterPhase


# Wall thickness of the gutter (mm). Design constant, NOT a user input - applied via FingerModel's
# own shell mechanism (it grows the appropriate radii internally).
SHELL_THICKNESS_MM = 2.25

# Generous cutter oversizing (mm). Phase 2's trim solid must comfortably exceed the finger's
# lateral and dorsal extent so the boolean subtraction cleanly caps the whole dorsal side.
_CUTTER_MARGIN_MM = 20.0

# How far the trough runs off each end of the finger (mm), parallel to the end phalanx.
_END_EXTENSION_MM = 100.0


# Maps each Phase 0 include flag to the FingerModel segment(s) it turns on. tip_include covers both
# the distal phalanx and the fingertip sphere (there is no separate distal_include).
_INCLUDE_TO_SEGMENTS = [
    ("metacarpal_stub_include", ["metacarpal"]),
    ("mcp_include", ["mcp"]),
    ("proximal_include", ["proximal"]),
    ("pip_include", ["pip"]),
    ("middle_include", ["middle"]),
    ("dip_include", ["dip"]),
    ("tip_include", ["distal", "tip"]),
]


def _included_segment_range(raw_data):
    """Resolve the contiguous run of included segments into (start_at, end_at) segment names.

    Raises ValueError if no segments are included or if the included set has a gap (the web form
    enforces contiguity, but raw_data is a system boundary so we re-check here).
    """
    included = []
    for flag, segments in _INCLUDE_TO_SEGMENTS:
        if raw_data.get(flag):
            included.extend(segments)
    if not included:
        raise ValueError("FingerGutter: no segments included (all *_include flags false)")

    indices = sorted(SEGMENT_ORDER.index(s) for s in included)
    if indices != list(range(indices[0], indices[-1] + 1)):
        raise ValueError(
            "FingerGutter: included segments are not contiguous: {0}".format(
                [SEGMENT_ORDER[i] for i in indices]))
    return SEGMENT_ORDER[indices[0]], SEGMENT_ORDER[indices[-1]]


def build_core_params(raw_data):
    """Phase 0: map the raw_data measurement payload onto a FingerParams (the finger core).

    Circumference mapping (see DevNotes Phase 0): knuckle measurements -> joint circs; mid-shaft
    phalanx measurements -> mid-phalanx bulge circs (None = smooth taper).
    """
    start_at, end_at = _included_segment_range(raw_data)

    params = FingerParams(
        start_at=start_at,
        end_at=end_at,

        # Joint (knuckle) circumferences
        mcp_circ=raw_data.get("mcp_circumference_mm", 0.0),
        pip_circ=raw_data.get("pip_circumference_mm", 0.0),
        dip_circ=raw_data.get("dip_circumference_mm", 0.0),
        tip_circ=raw_data.get("tip_circumference_mm", 0.0),

        # Mid-phalanx (bulge) circumferences - optional
        proximal_mid_circ=raw_data.get("proximal_circumference_mm"),
        middle_mid_circ=raw_data.get("middle_circumference_mm"),

        # Phalanx and metacarpal-stub lengths
        metacarpal_len=raw_data.get("metacarpal_stub_len_mm", 0.0),
        proximal_len=raw_data.get("proximal_len_mm", 0.0),
        middle_len=raw_data.get("middle_len_mm", 0.0),
        distal_len=raw_data.get("distal_len_mm", 0.0),

        # Joint flexion (toward palm) and lateral deflection
        mcp_flex=raw_data.get("mcp_flex_deg", 0.0),
        pip_flex=raw_data.get("pip_flex_deg", 0.0),
        dip_flex=raw_data.get("dip_flex_deg", 0.0),
        mcp_lateral=raw_data.get("mcp_lateral_deg", 0.0),
        pip_lateral=raw_data.get("pip_lateral_deg", 0.0),
        dip_lateral=raw_data.get("dip_lateral_deg", 0.0),
    )

    errors = params.validate_for_segment_range()
    if errors:
        raise ValueError("FingerGutter: invalid inputs for segment range: " + "; ".join(errors))
    return params


def build_finger_blank(core_params, debug=None):
    """Phase 1: build the finger core, its outer shell, and the hollow splint blank.

    Returns (finger_core, finger_outer_shell, splint_blank) where finger_core /
    finger_outer_shell are full FingerModelResults (Phase 2 needs their joint frames and radii).
    """
    finger_core = create_finger_model_result(core_params)
    if debug is not None:
        debug["finger_core"] = finger_core.finger_brep

    shell_params = replace(core_params, shell_thickness=SHELL_THICKNESS_MM)
    finger_outer_shell = create_finger_model_result(shell_params)
    if debug is not None:
        debug["finger_outer_shell"] = finger_outer_shell.finger_brep

    splint_blank, success, method = robust_brep_difference(
        minuend=finger_outer_shell.finger_brep,
        subtrahend=finger_core.finger_brep,
        base_tolerance=0.001)
    if not success or splint_blank is None:
        raise RuntimeError("FingerGutter: splint blank subtraction failed ({0})".format(method))
    log("FingerGutter: splint blank created via {0}".format(method))
    if debug is not None:
        debug["splint_blank"] = splint_blank

    return finger_core, finger_outer_shell, splint_blank


def _included_joints(finger_core):
    """Joints (in root->tip order) that are present in the rendered finger core."""
    return [j for j in JOINT_NAMES
            if j in finger_core.joint_positions and j in finger_core.radii]


def _phalanx_direction(finger_core, phalanx_name):
    """Unit tip-ward direction of a phalanx centerline, or None if not rendered."""
    line = finger_core.phalanx_lines.get(phalanx_name)
    if line is None:
        return None
    d = Vector3d(line.Direction)
    d.Unitize()
    return d


def build_gutter_trim_solid(finger_core, finger_outer_shell, gutter_depth_mm, debug=None):
    """Phase 2: build the solid occupying the material to REMOVE (the dorsal cap).

    Each included joint's cut point is placed in that joint's own perp frame so the cut depth stays
    correct under flexion/lateral deflection: starting at the joint center, drop to the volar
    surface (-radius along the perp-frame dorsal axis) then rise by gutter_depth, i.e.
    cut_point = joint_center + (gutter_depth - radius) * frame.YAxis. The centerline through those
    points is extended off both ends, extruded +/-Y (world lateral) into a ribbon at the cut height,
    then extruded +Z (world dorsal) into the capping solid.
    """
    joints = _included_joints(finger_core)
    if not joints:
        raise RuntimeError("FingerGutter: no included joints to place gutter cut points")

    cut_points = []
    for j in joints:
        frame = finger_core.get_perp_frame(j)  # ZAxis=finger dir, YAxis=dorsal, XAxis=lateral
        if frame is None:
            continue
        joint_center = finger_core.joint_positions[j][0]
        radius = finger_core.radii[j]
        # -Y is volar; move to volar surface then up by gutter_depth (both along dorsal YAxis).
        cut_points.append(joint_center + (gutter_depth_mm - radius) * frame.YAxis)

    if len(cut_points) < 1:
        raise RuntimeError("FingerGutter: failed to build any gutter cut points")

    # Extend the first/last cut point off the ends, parallel to the adjoining phalanx, so the
    # trough runs past both ends of the finger. Fall back to the end perp-frame ZAxis if the
    # adjoining phalanx line is unavailable.
    prox_dir = _phalanx_direction(finger_core, JOINT_ADJACENCY[joints[0]][0])
    if prox_dir is None:
        prox_dir = Vector3d(finger_core.get_perp_frame(joints[0]).ZAxis)
    dist_dir = _phalanx_direction(finger_core, JOINT_ADJACENCY[joints[-1]][1])
    if dist_dir is None:
        dist_dir = Vector3d(finger_core.get_perp_frame(joints[-1]).ZAxis)

    start_ext = cut_points[0] - _END_EXTENSION_MM * prox_dir
    end_ext = cut_points[-1] + _END_EXTENSION_MM * dist_dir
    centerline_pts = [start_ext] + cut_points + [end_ext]

    gutter_trim_centerline = PolylineCurve(Polyline(centerline_pts))
    if debug is not None:
        debug["gutter_trim_centerline"] = gutter_trim_centerline

    # Size the cutter generously from the shell's world bounding box so it exceeds the finger's
    # lateral and dorsal extent (including any lateral deflection).
    bbox = finger_outer_shell.finger_brep.GetBoundingBox(True)
    half_width = (bbox.Max.Y - bbox.Min.Y) + _CUTTER_MARGIN_MM
    dorsal_height = (bbox.Max.Z - bbox.Min.Z) + _CUTTER_MARGIN_MM

    # Ribbon at the cut height: shift the centerline -Y half a width, then extrude +Y a full width.
    ribbon_curve = gutter_trim_centerline.DuplicateCurve()
    ribbon_curve.Translate(Vector3d(0.0, -half_width, 0.0))
    ribbon_extrusion = Extrusion.CreateExtrusion(ribbon_curve, Vector3d(0.0, 2.0 * half_width, 0.0))
    if ribbon_extrusion is None:
        raise RuntimeError("FingerGutter: gutter trim ribbon extrusion failed")
    gutter_trim_surface = ribbon_extrusion.ToBrep()
    if debug is not None:
        debug["gutter_trim_surface"] = gutter_trim_surface

    # Extrude the ribbon face +Z (dorsal) into a capped solid covering the whole dorsal side.
    dorsal_path = LineCurve(Point3d.Origin, Point3d(0.0, 0.0, dorsal_height))
    gutter_trim_solid = gutter_trim_surface.Faces[0].CreateExtrusion(dorsal_path, True)
    if gutter_trim_solid is None:
        raise RuntimeError("FingerGutter: gutter trim solid extrusion failed")
    if debug is not None:
        debug["gutter_trim_solid"] = gutter_trim_solid

    return gutter_trim_solid


class FingerGutterGenerator(SplintGenerator):
    """Python-based geometry generator for the FingerGutter finger splint."""
    GEO_ALGORITHM_NAME = "FingerGutter"

    def generate(self, raw_data, object_id, debug=None, stop_after=None):
        """FingerGutter pipeline (Phases 0-3): raw_data -> trough SplintResult.

        Returns a SplintResult carrying the trimmed solid. Meshing / build-plate orientation and
        finishing features are later phases, not yet designed, so .mesh is None for now.
        """
        tracker = PhaseTracker(debug=debug, stop_after=stop_after, log_fn=log)

        gutter_depth_mm = raw_data["gutter_depth_mm"]

        # --- Phase 0: parse inputs into FingerParams ------------------------------------------
        core_params = build_core_params(raw_data)
        tracker.log_phase(0.0, "inputs")

        # --- Phase 1: finger core, outer shell, hollow splint blank ---------------------------
        phase1_debug = {}
        finger_core, finger_outer_shell, splint_blank = build_finger_blank(
            core_params, debug=phase1_debug)
        tracker.log_phase(1.0, "finger core, shell, blank",
            finger_core=phase1_debug.get("finger_core"),
            finger_outer_shell=phase1_debug.get("finger_outer_shell"),
            splint_blank=phase1_debug.get("splint_blank"))

        # --- Phase 2: build the gutter trim solid (the dorsal cap to remove) ------------------
        phase2_debug = {}
        gutter_trim_solid = build_gutter_trim_solid(
            finger_core, finger_outer_shell, gutter_depth_mm, debug=phase2_debug)
        tracker.log_phase(2.0, "gutter trim solid",
            gutter_trim_centerline=phase2_debug.get("gutter_trim_centerline"),
            gutter_trim_surface=phase2_debug.get("gutter_trim_surface"),
            gutter_trim_solid=phase2_debug.get("gutter_trim_solid"))

        # --- Phase 3: subtract the trim solid, leaving the volar trough -----------------------
        splint_solid, success, method = robust_brep_difference(
            minuend=splint_blank,
            subtrahend=gutter_trim_solid,
            base_tolerance=0.001)
        if not success or splint_solid is None:
            raise RuntimeError("FingerGutter: gutter trim subtraction failed ({0})".format(method))
        log("FingerGutter: gutter trough created via {0}".format(method))
        tracker.log_phase(3.0, "subtract trim solid", splint_solid=splint_solid)

        metadata = {
            "geo_algorithm": self.GEO_ALGORITHM_NAME,
            "object_id": object_id,
            "gutter_depth_mm": gutter_depth_mm,
            "shell_thickness_mm": SHELL_THICKNESS_MM,
        }

        log("FingerGutterGenerator.generate: pipeline complete (objectID {0})".format(object_id))
        return SplintResult(mesh=None, metadata=metadata, solid=splint_solid)
