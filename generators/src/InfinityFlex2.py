"""Python InfinityFlex geometry pipeline with named construction phases and debug previews."""

import json
import math
import os as _os
from dataclasses import replace
from importlib import reload
from typing import Callable, NamedTuple, TypeVar, cast

from Rhino import RhinoDoc
from Rhino.Geometry import (
    AreaMassProperties, BlendContinuity, Brep, BrepSolidOrientation, Curve, LineCurve, Mesh,
    MeshingParameters, Plane, Point3d, PolylineCurve, ShrinkWrapParameters, Surface, Transform,
    Vector3d, VolumeMassProperties,
)
from Rhino.Geometry.Intersect import Intersection
from splintcommon import log

import splint_generator
reload(splint_generator)
from splint_generator import SplintGenerator, SplintResult, PhaseTracker

AXIAL_BAND_THICKNESS_MM = 1.2
CENTRAL_BAND_STRAP_WIDTH_MM = 4.0
EXTENT_BAND_STRAP_WIDTH_MM = 4.0
MID_JOINT_RISE_FACTOR = 1.0

# Rhino keeps Python alive between runs; this distinguishes a reload from stale bytecode.
_MODULE_MTIME = _os.path.getmtime(__file__)
log("InfinityFlex2 loaded, source mtime={0}".format(_MODULE_MTIME))


class FingerPreformSolidResult(NamedTuple):
    inner_shell_brep: Brep
    outer_shell_brep: Brep
    shell_components: list[Brep]
    material_shell_brep: Brep
    splint_centerline: PolylineCurve
    mid_r: float


class CentralFingerLineGeometryResult(NamedTuple):
    first_segment_line: LineCurve
    first_segment_start: Point3d
    first_segment_end: Point3d
    first_segment_direction: Vector3d
    second_segment_line: LineCurve
    second_segment_end: Point3d


class AlignmentWorkResult(NamedTuple):
    average_center_line: LineCurve


class PreformOrientationAdjustmentResult(NamedTuple):
    material_shell_brep: Brep
    first_orientation_shift_transform: Transform
    outer_shell_brep: Brep
    inner_shell_brep: Brep
    first_segment_line: LineCurve
    second_segment_line: LineCurve
    shell_components: list[Brep]
    joint_centroid: Point3d


class BottomCenterBandCreationResult(NamedTuple):
    bottom_band: LineCurve


class TopBandsCreationResult(NamedTuple):
    root_top_band: LineCurve
    tip_top_band: LineCurve


class SideCutterProfileResult(NamedTuple):
    tail_slant_line: LineCurve
    tip_slant_line: LineCurve
    cutter_curve: Curve


class SideCutterExtrusionsResult(NamedTuple):
    side_cutter_surface: Brep
    side_cutter_volume: Brep
    tip_outer_curve: list[Curve]
    tail_outer_curve: list[Curve]
    tip_inner_curve: list[Curve]
    tail_inner_curve: list[Curve]


class SplintBodyIntersectionResult(NamedTuple):
    splint_body: Brep


class DirectionIndicatorResult(NamedTuple):
    finished_splint: Brep


class ComfortLipTrimmersResult(NamedTuple):
    comfort_trimmed_splint_brep: Brep


class IDEmbossingGunResult(NamedTuple):
    splint_with_embossed_id: Brep
    projected_letters_debug: list[Brep]


class SlitBotResult(NamedTuple):
    splint_brep_w_slit: Brep
    slit_sharp_edges: list[Curve]


class OrientingToBuildPlateResult(NamedTuple):
    oriented_splint: Brep


class JoiningAndSmoothingResult(NamedTuple):
    final_smooth_splint: Mesh


class LineToBrepGravitationCLResult(NamedTuple):
    output_line: LineCurve


def joining_and_smoothing(
    oriented_splint: Brep,
    *,
    debug=None,
) -> JoiningAndSmoothingResult:
    """Produce the graph's smoothed mesh without modifying the oriented source Brep.

    Keep the GH slider values and unwired defaults together here so another smoothing
    strategy can replace this stage without changing its inputs or named output.
    """
    target_edge_length_mm = 0.12
    smoothing_iterations = 8
    if (oriented_splint is None or not oriented_splint.IsValid
            or not oriented_splint.IsSolid or not oriented_splint.IsManifold):
        raise ValueError("Joining and smoothing requires a valid closed manifold splint.")
    parameters = ShrinkWrapParameters()
    parameters.TargetEdgeLength = target_edge_length_mm
    parameters.SmoothingIterations = smoothing_iterations
    parameters.Offset = 0.0
    parameters.PolygonOptimization = 10
    parameters.FillHolesInInputObjects = True
    data = debug if debug is not None else {}
    data["shrinkwrap_settings"] = {
        "target_edge_length_mm": parameters.TargetEdgeLength,
        "smoothing_iterations": parameters.SmoothingIterations,
        "offset_mm": parameters.Offset,
        "polygon_optimization_percent": parameters.PolygonOptimization,
        "fill_holes_in_input_objects": parameters.FillHolesInInputObjects,
    }
    data["shrinkwrap_input_volume_mm3"] = oriented_splint.GetVolume()
    log("InfinityFlex2: ShrinkWrap settings: {0}".format(data["shrinkwrap_settings"]))
    # Wrap one combined mesh; filling separate Brep face meshes can seal the finger bore.
    pieces = Mesh.CreateFromBrep(oriented_splint.DuplicateBrep(), MeshingParameters.Default)
    if pieces is None or len(pieces) == 0:
        raise RuntimeError("InfinityFlex2: could not mesh the oriented body for ShrinkWrap.")
    source_mesh = Mesh()
    for piece in pieces:
        source_mesh.Append(piece)
    data["shrinkwrap_input_mesh"] = source_mesh
    if not source_mesh.IsValid:
        raise RuntimeError("InfinityFlex2: invalid joined input mesh for ShrinkWrap.")
    naked_edges = source_mesh.GetNakedEdges()
    data["shrinkwrap_input_naked_edge_chains"] = len(naked_edges) if naked_edges is not None else 0
    log("InfinityFlex2: ShrinkWrap input mesh: closed={0}, naked edge chains={1}.".format(
        source_mesh.IsClosed, data["shrinkwrap_input_naked_edge_chains"]))
    # The static mesh-list overload is ambiguous in Rhino's Python binding.
    mesh = source_mesh.ShrinkWrap(parameters)
    if mesh is None:
        raise RuntimeError("InfinityFlex2: ShrinkWrap returned no mesh.")
    data["final_smooth_splint"] = mesh
    manifold, consistently_oriented, has_boundary = mesh.IsManifold(True)
    quality = {
        "valid": mesh.IsValid,
        "closed": mesh.IsClosed,
        "manifold": manifold,
        "consistently_oriented": consistently_oriented,
        "has_boundary": has_boundary,
        "disjoint_count": mesh.DisjointMeshCount,
        "vertices": mesh.Vertices.Count,
        "faces": mesh.Faces.Count,
    }
    data["shrinkwrap_mesh_quality"] = quality
    log("InfinityFlex2: ShrinkWrap mesh: {0}".format(quality))
    if (not quality["valid"] or not quality["closed"] or not manifold
            or not consistently_oriented or has_boundary or quality["disjoint_count"] != 1):
        raise RuntimeError("InfinityFlex2: ShrinkWrap did not produce one valid closed manifold mesh.")
    properties = VolumeMassProperties.Compute(mesh)
    if properties is None:
        raise RuntimeError("InfinityFlex2: unavailable ShrinkWrap mesh volume.")
    volume = properties.Volume
    properties.Dispose()
    data["shrinkwrap_mesh_volume_mm3"] = volume
    if not math.isfinite(volume) or volume <= 0:
        raise RuntimeError("InfinityFlex2: ShrinkWrap mesh must have finite positive volume.")
    log("InfinityFlex2: ShrinkWrap volume: {0:.6f} -> {1:.6f} mm^3.".format(
        data["shrinkwrap_input_volume_mm3"], volume))
    return JoiningAndSmoothingResult(mesh)


def orienting_to_build_plate(
    splint_brep_w_slit: Brep,
    tip_slant_line: LineCurve,
    *,
    debug=None,
) -> OrientingToBuildPlateResult:
    """Align the front cut with +X by rotating about the reversed tip-slant start.

    Match the graph's XZ rotation frame (normal -Y), preserving both inputs.
    This sets orientation only; placing the lowest point at Z=0 is a separate concern.
    """
    if (splint_brep_w_slit is None or not splint_brep_w_slit.IsValid
            or not splint_brep_w_slit.IsSolid or not splint_brep_w_slit.IsManifold):
        raise ValueError("Build-plate orientation requires a valid closed manifold splint.")
    if tip_slant_line is None or not tip_slant_line.IsValid:
        raise ValueError("Build-plate orientation requires a valid tip slant line.")
    flipped = tip_slant_line.DuplicateCurve()
    if not flipped.Reverse():
        raise RuntimeError("InfinityFlex2: could not reverse tip slant line.")
    direction = flipped.PointAtEnd - flipped.PointAtStart
    if not direction.Unitize():
        raise ValueError("Build-plate orientation requires a nonzero tip slant line.")
    plane = Plane(flipped.PointAtStart, Vector3d.XAxis, Vector3d.ZAxis)
    angle = Vector3d.VectorAngle(direction, Vector3d.XAxis, plane)
    if not math.isfinite(angle):
        raise RuntimeError("InfinityFlex2: unavailable build-plate rotation angle.")
    rotation = Transform.Rotation(angle, plane.Normal, plane.Origin)
    data = debug if debug is not None else {}
    data.update(build_plate_flipped_tip_slant_line=flipped,
                build_plate_rotation_plane=plane,
                build_plate_rotation_angle_degrees=math.degrees(angle),
                build_plate_rotation_transform=rotation)
    oriented_line = flipped.DuplicateCurve()
    if not oriented_line.Transform(rotation):
        raise RuntimeError("InfinityFlex2: could not rotate build-plate reference line.")
    data["build_plate_oriented_tip_slant_line"] = oriented_line
    oriented = splint_brep_w_slit.DuplicateBrep()
    if not oriented.Transform(rotation):
        raise RuntimeError("InfinityFlex2: could not rotate splint to build plate.")
    data["oriented_splint"] = oriented
    if not oriented.IsValid or not oriented.IsSolid or not oriented.IsManifold:
        raise RuntimeError("InfinityFlex2: build-plate rotation damaged splint validity.")
    log("InfinityFlex2: build-plate rotation={0:.6f} degrees about -Y at {1}.".format(
        math.degrees(angle), plane.Origin))
    return OrientingToBuildPlateResult(oriented)


def slit_bot(
    include_slit: bool,
    splint_brep: Brep,
    slit_thickness: float,
    slit_panel_z_factor: float,
    *,
    debug=None,
) -> SlitBotResult:
    """Open the bottom band with the legacy rectangular cutter, or pass through unchanged.

    Lower a finite center panel to avoid the upper bands. BrepSlit currently skips
    filleting despite its radius argument, so the returned edge curves remain sharp.
    """
    if not isinstance(include_slit, bool):
        raise TypeError("slit_bot requires a Boolean include_slit.")
    data = debug if debug is not None else {}
    if not include_slit:
        result = SlitBotResult(splint_brep, [])
        data.update(result._asdict())
        log("InfinityFlex2: slit disabled; body unchanged.")
        return result

    import BrepDifference
    import BrepSlit
    import FeatureIdentification
    reload(BrepDifference)
    reload(BrepSlit)
    reload(FeatureIdentification)

    if (splint_brep is None or not splint_brep.IsValid or not splint_brep.IsSolid
            or not splint_brep.IsManifold):
        raise ValueError("slit_bot requires a valid closed manifold splint.")
    if (not math.isfinite(slit_thickness) or slit_thickness <= 0
            or not math.isfinite(slit_panel_z_factor)):
        raise ValueError("Slit thickness must be positive and panel offset finite.")
    document = RhinoDoc.ActiveDoc
    tolerance = document.ModelAbsoluteTolerance if document is not None else 0.001
    reference_slice, sizes = FeatureIdentification.box_center_slice(
        target_brep=splint_brep, bbox_plane=Plane.WorldXY,
        choose_side_vector=Vector3d.YAxis, return_size=True,
    )
    data["slit_reference_slice"] = reference_slice
    data["slit_panel_z_size"] = sizes[2]
    slit_panel = reference_slice.DuplicateBrep()
    if not slit_panel.Translate(Vector3d.ZAxis * (sizes[2] * slit_panel_z_factor)):
        raise RuntimeError("InfinityFlex2: could not position slit panel.")
    data["slit_panel"] = slit_panel
    slit_brep, side_a, side_b, surface_a, surface_b = BrepSlit.cut_slit(
        splint_brep=splint_brep.DuplicateBrep(), slit_panel=slit_panel,
        slit_thickness=slit_thickness, fillet_radius=0.3, tolerance=tolerance,
    )
    data.update(splint_brep_w_slit=slit_brep, side_a_edges=list(side_a),
                side_b_edges=list(side_b), side_a_cutter=surface_a, side_b_cutter=surface_b)
    if (slit_brep is None or not slit_brep.IsValid or not slit_brep.IsSolid
            or not slit_brep.IsManifold):
        raise RuntimeError("InfinityFlex2: slit did not produce a valid closed manifold solid.")
    before_volume = splint_brep.GetVolume()
    after_volume = slit_brep.GetVolume()
    data.update(slit_volume_before=before_volume, slit_volume_after=after_volume)
    log("InfinityFlex2: slit: {0:.6f} -> {1:.6f} mm^3.".format(before_volume, after_volume))
    if not math.isfinite(after_volume) or not 0 < after_volume < before_volume:
        raise RuntimeError("InfinityFlex2: requested slit did not remove material.")
    edge_indices = sorted(set(list(side_a) + list(side_b)))
    edges = [slit_brep.Edges[index].DuplicateCurve() for index in edge_indices]
    data["slit_sharp_edges"] = edges
    if not edges or not all(edge is not None and edge.IsValid for edge in edges):
        raise RuntimeError("InfinityFlex2: slit has no valid identifiable sharp-edge curves.")
    return SlitBotResult(slit_brep, edges)


def id_embossing_gun(
    finished_splint_brep: Brep,
    object_id: str,
    wall_thickness_mm: float,
    projection_centroid: Point3d,
    mid_r: float,
    projection_origin_z_factor: float,
    north_star_nudge_mm: float,
    lower_angle_from_lip_degrees: float,
    text_size: float,
    extrusion_depth_factor: float,
    *,
    debug=None,
) -> IDEmbossingGunResult:
    """Recess the ID inside the wall, aiming from the raised joint centroid below the lip.

    Resolve fragmented section curves by proximity to the north star, then let TextGun
    align each projected letter with its local surface normal. Preserve the input body.
    """
    import BrepDifference
    import FeatureIdentification
    import TextGun
    reload(BrepDifference)
    reload(FeatureIdentification)
    reload(TextGun)

    if (finished_splint_brep is None or not finished_splint_brep.IsValid
            or not finished_splint_brep.IsSolid or not projection_centroid.IsValid):
        raise ValueError("id_embossing_gun requires a closed splint and valid projection centroid.")
    if not isinstance(object_id, str) or not object_id.strip():
        raise ValueError("id_embossing_gun requires nonempty object_id text.")
    if not all(math.isfinite(value) and value > 0 for value in (
        wall_thickness_mm, mid_r, text_size, extrusion_depth_factor,
    )) or not all(math.isfinite(value) for value in (
        projection_origin_z_factor, north_star_nudge_mm, lower_angle_from_lip_degrees,
    )):
        raise ValueError("ID embossing requires positive sizes/depth and finite aiming settings.")
    data = debug if debug is not None else {}
    document = RhinoDoc.ActiveDoc
    tolerance = document.ModelAbsoluteTolerance if document is not None else 0.001
    origin = projection_centroid + Vector3d.ZAxis * (mid_r * projection_origin_z_factor)
    data["id_projection_origin"] = origin
    surface = cast(Brep, FeatureIdentification.box_center_slice(
        target_brep=finished_splint_brep, bbox_plane=Plane.WorldXY,
        choose_side_vector=Vector3d.XAxis,
    ))
    data["id_reference_slice"] = surface
    properties = AreaMassProperties.Compute(surface)
    if properties is None:
        raise RuntimeError("InfinityFlex2: unavailable ID slice centroid.")
    center = properties.Centroid
    properties.Dispose()
    data["id_bbox_slice_centroid"] = center
    slice_center = Point3d(center.X, origin.Y, origin.Z)
    section_surface = surface.DuplicateBrep()
    if not section_surface.Translate(slice_center - center):
        raise RuntimeError("InfinityFlex2: could not position ID section slice.")
    data["id_section_slice"] = section_surface
    success, curves, _points = Intersection.BrepBrep(
        finished_splint_brep, section_surface, tolerance)
    sections = list(curves) if curves is not None else []
    data["id_section_curves"] = sections
    if not success or not sections or not all(curve.IsValid for curve in sections):
        raise RuntimeError("InfinityFlex2: no valid ID section curves.")
    north_star = Point3d(center.X, north_star_nudge_mm, north_star_nudge_mm)
    data["id_north_star"] = north_star
    candidates = []
    data["id_section_closest_points"] = candidates
    for curve in sections:
        found, parameter = curve.ClosestPoint(north_star)
        if not found:
            raise RuntimeError("InfinityFlex2: could not locate the ID side lip.")
        candidates.append(curve.PointAt(parameter))
    distances = [point.DistanceTo(north_star) for point in candidates]
    data["id_north_star_distances"] = distances
    closest_distance = min(distances)
    lip_point = candidates[distances.index(closest_distance)]
    if any(abs(distance - closest_distance) <= tolerance
           and point.DistanceTo(lip_point) > tolerance
           for point, distance in zip(candidates, distances)):
        raise RuntimeError("InfinityFlex2: ambiguous closest ID side lip.")
    data["id_lip_point"] = lip_point
    data["id_lip_aim_line"] = LineCurve(origin, lip_point)
    direction = lip_point - origin
    if not direction.Unitize():
        raise RuntimeError("InfinityFlex2: ID lip coincides with the projection origin.")
    lip_angle = Vector3d.VectorAngle(Vector3d.YAxis, direction)
    if not math.isfinite(lip_angle):
        raise RuntimeError("InfinityFlex2: unavailable ID lip angle.")
    angle = math.radians(lower_angle_from_lip_degrees) - lip_angle
    rotation = Transform.Rotation(angle, Vector3d.XAxis, Point3d.Origin)
    projection = Vector3d(Vector3d.YAxis)
    up = Vector3d(Vector3d.ZAxis)
    projection.Transform(rotation)
    up.Transform(rotation)
    if not projection.IsValid or not up.IsValid:
        raise RuntimeError("InfinityFlex2: could not rotate ID projection and up vectors.")
    data.update(id_lip_angle_degrees=math.degrees(lip_angle),
                id_projection_angle_degrees=math.degrees(angle),
                id_text_projection_vector=projection, id_text_up_vector=up)
    log("InfinityFlex2: ID lip angle={0:.6f}, projection angle={1:.6f} degrees.".format(
        math.degrees(lip_angle), math.degrees(angle)))
    embossed, original_letters, projected_letters, text_plane = TextGun.emboss_text(
        target_brep=finished_splint_brep.DuplicateBrep(),
        text_content=object_id,
        wall_thickness_mm=wall_thickness_mm,
        text_size=text_size,
        text_up_vector=up,
        text_projection_vector=projection,
        extrusion_depth_factor=extrusion_depth_factor,
        projection_origin=origin,
        emboss_inside=True,
        align_to_surface_normal=True,
    )
    original_letters = list(original_letters)
    projected_letters = list(projected_letters)
    data.update(original_text_breps=original_letters,
                projected_letters_debug=projected_letters, final_text_plane=text_plane)
    if embossed is not None:
        data["splint_with_embossed_id"] = embossed
    # TextGun can skip a failed subtraction; projection counts do not prove every cut succeeded.
    if not original_letters or len(projected_letters) != len(original_letters):
        raise RuntimeError("InfinityFlex2: ID projection incomplete: {0} of {1} letter solids.".format(
            len(projected_letters), len(original_letters)))
    if (embossed is None or not embossed.IsValid or not embossed.IsSolid
            or not embossed.IsManifold):
        raise RuntimeError("InfinityFlex2: ID embossing did not produce a valid closed manifold solid.")
    before_volume = finished_splint_brep.GetVolume()
    after_volume = embossed.GetVolume()
    log("InfinityFlex2: ID embossing: {0} projected letter solids, {1:.6f} -> {2:.6f} mm^3.".format(
        len(projected_letters), before_volume, after_volume))
    if not math.isfinite(after_volume) or not 0 < after_volume < before_volume:
        raise RuntimeError("InfinityFlex2: ID embossing did not remove material.")
    return IDEmbossingGunResult(embossed, projected_letters)


def comfort_lip_trimmers(
    tip_outer_curve: list[Curve],
    tail_outer_curve: list[Curve],
    splint_brep: Brep,
    joint_centroid: Point3d,
    mid_r: float,
    tip_inner_curve: list[Curve],
    tail_inner_curve: list[Curve],
    bottom_slice_x_scale: float,
    radius_multiplier: float,
    chamfer_side_limit_angle_degrees: float,
    *,
    debug=None,
) -> ComfortLipTrimmersResult:
    """Relieve each end lip using a cutter through one bottom and two lateral landmarks.

    Finite slices select the intended points on the inner/outer section curves. The
    three center-to-landmark rays define each capped loft, which is subtracted in turn.
    """
    import BrepDifference
    import FeatureIdentification
    reload(BrepDifference)
    reload(FeatureIdentification)

    if not splint_brep.IsValid or not splint_brep.IsSolid or not joint_centroid.IsValid:
        raise ValueError("comfort_lip_trimmers requires a closed body and valid centroid.")
    if not all(math.isfinite(value) and value > 0 for value in (
        mid_r, bottom_slice_x_scale, radius_multiplier,
    )) or not math.isfinite(chamfer_side_limit_angle_degrees):
        raise ValueError("comfort-lip dimensions must be finite and positive; angle must be finite.")
    data = debug if debug is not None else {}
    document = RhinoDoc.ActiveDoc
    tolerance = document.ModelAbsoluteTolerance if document is not None else 0.001
    offset = -mid_r * radius_multiplier

    def landmark(surface, curves, name):
        """Require one distinct slice hit, allowing coincident hits at section joins."""
        points = []
        data[name + "_intersections"] = points
        for curve in curves:
            if curve is None or not curve.IsValid:
                raise ValueError("{0}: invalid section curve.".format(name))
            success, overlaps, hits = Intersection.CurveBrep(curve, surface, tolerance)
            if not success or (overlaps is not None and len(overlaps) > 0):
                raise RuntimeError("InfinityFlex2: {0}: failed or overlapping slice.".format(name))
            for point in hits if hits is not None else []:
                if all(point.DistanceTo(existing) > tolerance for existing in points):
                    points.append(point)
        log("InfinityFlex2: {0}: {1} distinct landmark(s).".format(name, len(points)))
        if len(points) != 1:
            raise RuntimeError("InfinityFlex2: {0}: expected one slice point, received {1}.".format(
                name, len(points)))
        data[name] = points[0]
        return points[0]

    center_slice = cast(Brep, FeatureIdentification.box_center_slice(
        target_brep=splint_brep, bbox_plane=Plane.WorldXY,
        choose_side_vector=Vector3d.YAxis,
    ))
    data["comfort_center_slice"] = center_slice
    properties = AreaMassProperties.Compute(center_slice)
    if properties is None:
        raise RuntimeError("InfinityFlex2: unavailable comfort-slice centroid.")
    center = properties.Centroid
    properties.Dispose()
    bottom_slice = center_slice.DuplicateBrep()
    if (not bottom_slice.Transform(Transform.Scale(Plane(center, Vector3d.ZAxis),
                                                  bottom_slice_x_scale, 1.0, 1.0))
            or not bottom_slice.Translate(Vector3d.ZAxis * offset)):
        raise RuntimeError("InfinityFlex2: could not position bottom lip slice.")
    data["bottom_slicer_srf"] = bottom_slice
    tip_bottom = landmark(bottom_slice, tip_outer_curve, "tip_bottom_mid_lip")
    tail_bottom = landmark(bottom_slice, tail_outer_curve, "tail_bottom_mid_lip")

    side_points = []
    # Rotate XY, then offset along each rotated normal to put the slices on opposite sides.
    base_plane = Plane.WorldXY
    for angle, name in ((chamfer_side_limit_angle_degrees, "y_min"),
                        (-chamfer_side_limit_angle_degrees, "y_max")):
        rotation = Transform.Rotation(math.radians(angle), Vector3d.XAxis, Point3d.Origin)
        bbox_plane = Plane(base_plane)
        bbox_plane.Transform(rotation)
        view_vector = Vector3d(Vector3d.YAxis)
        view_vector.Transform(rotation)
        surface = cast(Brep, FeatureIdentification.box_center_slice(
            target_brep=splint_brep, bbox_plane=bbox_plane,
            choose_side_vector=view_vector,
        ))
        if not surface.Translate(bbox_plane.ZAxis * offset):
            raise RuntimeError("InfinityFlex2: could not position {0} lip slice.".format(name))
        data[name + "_slice_surface"] = surface
        side_points.append((
            landmark(surface, tip_inner_curve, "tip_" + name + "_pt"),
            landmark(surface, tail_inner_curve, "tail_" + name + "_pt"),
        ))

    tip_construction = {}
    tail_construction = {}
    data.update(tip_cone_construction=tip_construction,
                tail_cone_construction=tail_construction)
    tip_cutter = FeatureIdentification.cone_creator_cl(
        joint_centroid, side_points[0][0], tip_bottom, side_points[1][0],
        debug=tip_construction,
    ).brep
    data["tip_comfort_cutter"] = tip_cutter
    tail_cutter = FeatureIdentification.cone_creator_cl(
        joint_centroid, side_points[1][1], tail_bottom, side_points[0][1],
        debug=tail_construction,
    ).brep
    data["tail_comfort_cutter"] = tail_cutter
    current = splint_brep.DuplicateBrep()
    for name, cutter in (("tip", tip_cutter), ("tail", tail_cutter)):
        before_volume = current.GetVolume()
        trimmed, success, method = BrepDifference.robust_brep_difference(
            minuend=current.DuplicateBrep(), subtrahend=cutter.DuplicateBrep(),
            base_tolerance=tolerance,
        )
        data[name + "_comfort_difference_method"] = method
        if trimmed is not None:
            data[name + "_comfort_trimmed_brep"] = trimmed
        if (not success or trimmed is None or not trimmed.IsValid or not trimmed.IsSolid
                or not trimmed.IsManifold):
            raise RuntimeError("InfinityFlex2: {0} comfort trim did not produce a valid solid.".format(name))
        after_volume = trimmed.GetVolume()
        log("InfinityFlex2: {0} comfort trim: {1:.6f} -> {2:.6f} mm^3 via {3}.".format(
            name, before_volume, after_volume, method))
        if not math.isfinite(after_volume) or not 0 < after_volume < before_volume:
            raise RuntimeError("InfinityFlex2: {0} comfort trim did not remove material.".format(name))
        current = trimmed
    result = ComfortLipTrimmersResult(comfort_trimmed_splint_brep=current)
    data.update(result._asdict())
    return result


def direction_indicator(
    splint_body: Brep,
    tip_top_band: Curve,
    wall_thickness_mm: float,
    north_star_nudge_mm: float,
    nub_position_parameter: float,
    nub_sinking_factor: float,
    ball_r_to_wall_ratio: float,
    *,
    debug=None,
) -> DirectionIndicatorResult:
    """Add the distal orientation nub using the shared placement and union implementation.

    Use the local outward normal rather than the graph's world-Z displacement so the
    nub remains attached when the supporting band is sloped.
    """
    import BrepUnion
    import Orienteering
    reload(BrepUnion)
    reload(Orienteering)

    finished_splint = Orienteering.positive_sphere_direction_indicator(
        splint_brep=splint_body,
        tip_top_band_curve=tip_top_band,
        wall_thickness_mm=wall_thickness_mm,
        north_star_nudge_mm=north_star_nudge_mm,
        nub_position_param=nub_position_parameter,
        nub_sinking_factor=nub_sinking_factor,
        ball_r_to_wall_ratio=ball_r_to_wall_ratio,
        debug=debug,
    )
    result = DirectionIndicatorResult(finished_splint=finished_splint)
    if debug is not None:
        debug.update(result._asdict())
    return result


def splint_body_intersection(
    material_shell_brep: Brep,
    side_cutter_volume: Brep,
    *,
    debug=None,
) -> SplintBodyIntersectionResult:
    """Intersect the oriented material shell with the band-profile volume.

    Require exactly one validated body; never hide disconnected results by keeping
    only the largest piece. The Boolean wrapper owns diagnostics and fallback policy.
    """
    import BrepIntersection
    reload(BrepIntersection)

    pieces, success, method = BrepIntersection.robust_brep_intersection(
        brep_a=material_shell_brep,
        brep_b=side_cutter_volume,
    )
    if debug is not None:
        debug.update(splint_body_intersection_pieces=pieces,
                     splint_body_intersection_method=method)
    if not success or len(pieces) != 1:
        raise RuntimeError("InfinityFlex2: expected one splint body, received {0} pieces.".format(
            len(pieces)))
    result = SplintBodyIntersectionResult(splint_body=pieces[0])
    if debug is not None:
        debug.update(result._asdict())
    return result


def side_cutter_extrusions(
    cutter_curve: Curve,
    outer_shell_brep: Brep,
    tip_slant_line: LineCurve,
    tail_slant_line: LineCurve,
    inner_shell_brep: Brep,
    nudge_mm: float,
    *,
    debug=None,
) -> SideCutterExtrusionsResult:
    """Extrude the band profile across Y and capture the tip/tail wall sections.

    The closed extrusion selects the splint body; open slant sheets locate the inner
    and outer lip curves. Preserve every section piece for later landmark selection.
    """
    if not math.isfinite(nudge_mm) or nudge_mm == 0.0:
        raise ValueError("nudge_mm must be finite and nonzero.")
    if not cutter_curve.IsValid or not cutter_curve.IsClosed:
        raise ValueError("cutter_curve must be valid and closed.")
    if not all(geometry.IsValid for geometry in (
        outer_shell_brep, inner_shell_brep, tip_slant_line, tail_slant_line,
    )):
        raise ValueError("side_cutter_extrusions requires valid oriented shells and slants.")
    data = debug if debug is not None else {}
    document = RhinoDoc.ActiveDoc
    tolerance = document.ModelAbsoluteTolerance if document is not None else 0.001
    translation = Transform.Translation(Vector3d.YAxis * nudge_mm)
    extrusion_direction = Vector3d.YAxis * (-2.0 * nudge_mm)
    moved_curve = cutter_curve.DuplicateCurve()
    moved_tip = tip_slant_line.DuplicateCurve()
    moved_tail = tail_slant_line.DuplicateCurve()
    for geometry in (moved_curve, moved_tip, moved_tail):
        if not geometry.Transform(translation):
            raise RuntimeError("InfinityFlex2: side-cutter translation failed.")
    data["side_cutter_moved_curve"] = moved_curve

    surfaces = Brep.CreatePlanarBreps(moved_curve, tolerance)
    if surfaces is None or len(surfaces) != 1 or surfaces[0].Faces.Count != 1:
        raise RuntimeError("InfinityFlex2: cutter boundary must produce one planar face.")
    side_cutter_surface = surfaces[0]
    data["side_cutter_surface"] = side_cutter_surface
    path = LineCurve(Point3d.Origin, Point3d.Origin + extrusion_direction)
    side_cutter_volume = side_cutter_surface.Faces[0].CreateExtrusion(path, True)
    if (side_cutter_volume is None or not side_cutter_volume.IsValid
            or not side_cutter_volume.IsSolid):
        raise RuntimeError("InfinityFlex2: side-cutter extrusion must be a valid closed solid.")
    if side_cutter_volume.SolidOrientation == BrepSolidOrientation.Inward:
        log("InfinityFlex2: reversing inward side-cutter volume orientation.")
        side_cutter_volume.Flip()
    data["side_cutter_volume"] = side_cutter_volume

    sheets = {}
    for name, line in (("tip", moved_tip), ("tail", moved_tail)):
        surface = Surface.CreateExtrusion(line, extrusion_direction)
        if surface is None:
            raise RuntimeError("InfinityFlex2: {0} slant extrusion failed.".format(name))
        sheet = surface.ToBrep()
        if sheet is None or not sheet.IsValid:
            raise RuntimeError("InfinityFlex2: {0} slant sheet is invalid.".format(name))
        sheets[name] = sheet
        data[name + "_slant_surface"] = sheet

    intersections = {}
    for key, shell, sheet in (
        ("tip_outer_curve", outer_shell_brep, sheets["tip"]),
        ("tail_outer_curve", outer_shell_brep, sheets["tail"]),
        ("tip_inner_curve", inner_shell_brep, sheets["tip"]),
        ("tail_inner_curve", inner_shell_brep, sheets["tail"]),
    ):
        success, curves, _ = Intersection.BrepBrep(shell, sheet, tolerance)
        if not success:
            raise RuntimeError("InfinityFlex2: {0} intersection failed.".format(key))
        intersections[key] = list(curves) if curves is not None else []
        data[key] = intersections[key]

    result = SideCutterExtrusionsResult(
        side_cutter_surface=side_cutter_surface,
        side_cutter_volume=side_cutter_volume,
        tip_outer_curve=intersections["tip_outer_curve"],
        tail_outer_curve=intersections["tail_outer_curve"],
        tip_inner_curve=intersections["tip_inner_curve"],
        tail_inner_curve=intersections["tail_inner_curve"],
    )
    if debug is not None:
        debug.update(result._asdict())
    return result


def side_cutter_profile(
    bottom_band: LineCurve,
    root_top_band: LineCurve,
    tip_top_band: LineCurve,
    bottom_band_width_mm: float,
    mid_joint_rise_factor: float,
    *,
    debug=None,
) -> SideCutterProfileResult:
    """Connect the three bands with slanted ends and a G2 blend across the central gap.

    Project only the closed cutter outline onto XZ; retain the directed slant lines
    in construction coordinates for wall sections and the final build orientation.
    """
    connection_bulge = 0.6
    if not all(line.IsValid for line in (bottom_band, root_top_band, tip_top_band)):
        raise ValueError("side_cutter_profile requires three valid band lines.")
    if not math.isfinite(bottom_band_width_mm) or bottom_band_width_mm <= 0.0:
        raise ValueError("bottom_band_width_mm must be positive and finite.")
    if not math.isfinite(mid_joint_rise_factor):
        raise ValueError("mid_joint_rise_factor must be finite.")
    data = debug if debug is not None else {}
    document = RhinoDoc.ActiveDoc
    tolerance = document.ModelAbsoluteTolerance if document is not None else 0.001

    bottom_left = bottom_band.PointAtStart
    bottom_right = bottom_band.PointAtEnd
    root_outer = root_top_band.PointAtStart
    root_inner = root_top_band.PointAtEnd
    tip_outer = tip_top_band.PointAtStart
    tip_inner = tip_top_band.PointAtEnd
    central_point = bottom_band.PointAtNormalizedLength(0.5) + Vector3d(
        0.0, 0.0, bottom_band_width_mm * mid_joint_rise_factor)
    root_midpoint = LineCurve(root_inner, central_point).PointAtNormalizedLength(0.5)
    tip_midpoint = LineCurve(central_point, tip_inner).PointAtNormalizedLength(0.5)
    data.update(
        side_cutter_central_point=central_point,
        side_cutter_root_midpoint=root_midpoint,
        side_cutter_tip_midpoint=tip_midpoint,
    )

    tail_slant_line = LineCurve(bottom_left, root_outer)
    tip_slant_line = LineCurve(tip_outer, bottom_right)
    data.update(tail_slant_line=tail_slant_line, tip_slant_line=tip_slant_line)
    segments = [
        LineCurve(bottom_right, bottom_left),
        tail_slant_line,
        LineCurve(root_outer, root_inner),
        LineCurve(root_inner, root_midpoint),
        LineCurve(tip_midpoint, tip_inner),
        LineCurve(tip_inner, tip_outer),
        tip_slant_line,
    ]
    joined = Curve.JoinCurves(segments, tolerance)
    if joined is None or len(joined) != 1 or joined[0].IsClosed:
        raise RuntimeError("InfinityFlex2: side-cutter segments must form one open outline.")
    outline = joined[0]
    data["side_cutter_open_outline"] = outline
    blend = Curve.CreateBlendCurve(
        outline, outline, BlendContinuity.Curvature, connection_bulge, connection_bulge)
    if blend is None or not blend.IsValid:
        raise RuntimeError("InfinityFlex2: side-cutter central blend failed.")
    data["side_cutter_blend"] = blend
    closed = Curve.JoinCurves([outline, blend], tolerance)
    if closed is None or len(closed) != 1:
        raise RuntimeError("InfinityFlex2: side-cutter blend did not join the outline.")
    profile = closed[0]
    if not profile.IsClosed and not profile.MakeClosed(tolerance):
        raise RuntimeError("InfinityFlex2: side-cutter outline could not be closed.")
    data["side_cutter_unprojected_profile"] = profile
    projection_plane = Plane(Point3d.Origin, Vector3d.XAxis, Vector3d.ZAxis)
    cutter_curve = Curve.ProjectToPlane(profile, projection_plane)
    if cutter_curve is None or not cutter_curve.IsValid or not cutter_curve.IsClosed:
        raise RuntimeError("InfinityFlex2: projected side-cutter curve must be valid and closed.")
    result = SideCutterProfileResult(
        tail_slant_line=tail_slant_line,
        tip_slant_line=tip_slant_line,
        cutter_curve=cutter_curve,
    )
    if debug is not None:
        debug.update(result._asdict())
    return result


def top_bands_creation(
    outer_shell_brep: Brep,
    root_north_star: Point3d,
    north_star_nudge: Vector3d,
    top_band_width_mm: float,
    tip_north_star: Point3d,
    *,
    debug=None,
) -> TopBandsCreationResult:
    """Place root/tip guides at nudged shell references, then rotate clear of the surface.

    Test angles in the graph's order and keep the first clear candidate, not an
    optimized fit. Each guide pivots about its own start and uses the opposite sweep.
    """
    import FeatureIdentification
    reload(FeatureIdentification)

    if not math.isfinite(top_band_width_mm) or top_band_width_mm <= 0.0:
        raise ValueError("top_band_width_mm must be positive and finite.")
    data = debug if debug is not None else {}
    root_reference = FeatureIdentification.brep_ref_point(
        nudge_amount=north_star_nudge,
        north_star_pt=root_north_star,
        brep=outer_shell_brep,
    )
    data["root_top_band_reference_point"] = root_reference.result_point
    data["root_top_band_reference_distance"] = root_reference.distance
    tip_reference = FeatureIdentification.brep_ref_point(
        nudge_amount=north_star_nudge,
        north_star_pt=tip_north_star,
        brep=outer_shell_brep,
    )
    data["tip_top_band_reference_point"] = tip_reference.result_point
    data["tip_top_band_reference_distance"] = tip_reference.distance

    root_guide = LineCurve(
        root_reference.result_point,
        root_reference.result_point + Vector3d.XAxis * top_band_width_mm,
    )
    tip_guide = LineCurve(
        tip_reference.result_point,
        tip_reference.result_point - Vector3d.XAxis * top_band_width_mm,
    )
    data["root_top_band_guide_line"] = root_guide
    data["tip_top_band_guide_line"] = tip_guide
    rotation_plane = Plane(Point3d.Origin, Vector3d.XAxis, Vector3d.ZAxis)
    rotation_check_angles = [90.0 * step / 10 for step in range(11)]
    root_top_band = FeatureIdentification.find_non_intersecting_curve_rotation(
        input_brep=outer_shell_brep,
        input_curve=root_guide,
        rotation_origin_point=root_reference.result_point,
        rotation_plane=rotation_plane,
        test_rotation_degrees=rotation_check_angles,
    )
    if root_top_band is None:
        raise RuntimeError("InfinityFlex2: no tested rotation clears the root top band.")
    data["root_top_band"] = root_top_band
    tip_top_band = FeatureIdentification.find_non_intersecting_curve_rotation(
        input_brep=outer_shell_brep,
        input_curve=tip_guide,
        rotation_origin_point=tip_reference.result_point,
        rotation_plane=rotation_plane,
        test_rotation_degrees=[-angle for angle in rotation_check_angles],
    )
    if tip_top_band is None:
        raise RuntimeError("InfinityFlex2: no tested rotation clears the tip top band.")
    result = TopBandsCreationResult(
        root_top_band=cast(LineCurve, root_top_band),
        tip_top_band=cast(LineCurve, tip_top_band),
    )
    if debug is not None:
        debug.update(result._asdict())
    return result


def line_to_brep_gravitation_cl(
    input_line: LineCurve,
    output_nudge_motion: Vector3d,
    input_brep: Brep,
) -> LineToBrepGravitationCLResult:
    """Move an exterior line to the closest sampled section's Z, then apply its nudge.

    Delegate the graph's six-section sampling and longest-list matching to the shared
    helper; this is section proximity, not the separate ray-based nudge operation.
    """
    import FeatureIdentification
    reload(FeatureIdentification)

    output_line = FeatureIdentification.line_to_brep_gravitation(
        input_line=input_line,
        output_nudge_motion=output_nudge_motion,
        input_brep=input_brep,
    )
    return LineToBrepGravitationCLResult(output_line=output_line)


def bottom_center_band_creation(
    bottom_band_width_mm: float,
    joint_centroid: Point3d,
    outer_shell_brep: Brep,
    *,
    debug=None,
) -> BottomCenterBandCreationResult:
    """Center the bottom-band guide under the oriented joint and gravitate it to the shell.

    The large negative-Z nudges position search geometry outside the body; they do not
    move material. The final -1 mm nudge keeps the reference below the shell surface.
    """
    import FeatureIdentification
    reload(FeatureIdentification)

    reference_nudge_mm = -75.0
    line_nudge_mm = -100.0
    output_nudge_motion = Vector3d(0.0, 0.0, -1.0)
    if not math.isfinite(bottom_band_width_mm) or bottom_band_width_mm <= 0.0:
        raise ValueError("bottom_band_width_mm must be positive and finite.")
    if not joint_centroid.IsValid or not outer_shell_brep.IsValid:
        raise ValueError("A valid oriented joint_centroid and outer_shell_brep are required.")
    data = debug if debug is not None else {}
    document = RhinoDoc.ActiveDoc
    tolerance = document.ModelAbsoluteTolerance if document is not None else 0.001

    slice_surface = cast(Brep, FeatureIdentification.box_center_slice(
        target_brep=outer_shell_brep,
        bbox_plane=Plane.WorldXY,
        choose_side_vector=Vector3d.XAxis,
    ))
    area_properties = AreaMassProperties.Compute(slice_surface)
    if area_properties is None:
        raise RuntimeError("InfinityFlex2: bottom-band slice has no area centroid.")
    slice_centroid = area_properties.Centroid
    area_properties.Dispose()
    slice_origin = Point3d(joint_centroid.X, slice_centroid.Y, slice_centroid.Z)
    if not slice_surface.Translate(slice_origin - slice_centroid):
        raise RuntimeError("InfinityFlex2: bottom-band slice translation failed.")
    data["bottom_band_slice_surface"] = slice_surface

    slice_plane = Plane(slice_origin, Vector3d.XAxis)
    success, section_curves, _ = Intersection.BrepPlane(
        outer_shell_brep, slice_plane, tolerance)
    if not success or section_curves is None or len(section_curves) != 1:
        raise RuntimeError("InfinityFlex2: expected one outer-shell section at the joint.")
    section = section_curves[0]
    data["bottom_band_cross_section"] = section
    reference_point = joint_centroid + Vector3d(0.0, 0.0, reference_nudge_mm)
    data["bottom_band_reference_point"] = reference_point
    success, parameter = section.ClosestPoint(reference_point)
    if not success:
        raise RuntimeError("InfinityFlex2: could not locate the bottom-band anchor.")
    anchor_point = section.PointAt(parameter)
    data["bottom_band_anchor_point"] = anchor_point

    half_width = Vector3d.XAxis * (bottom_band_width_mm / 2.0)
    centered_line = LineCurve(anchor_point - half_width, anchor_point + half_width)
    data["bottom_band_line"] = centered_line
    nudged_line = cast(LineCurve, centered_line.DuplicateCurve())
    if not nudged_line.Translate(Vector3d(0.0, 0.0, line_nudge_mm)):
        raise RuntimeError("InfinityFlex2: bottom-band line nudge failed.")
    data["bottom_band_nudged_line"] = nudged_line

    gravitation = line_to_brep_gravitation_cl(
        input_line=nudged_line,
        output_nudge_motion=output_nudge_motion,
        input_brep=outer_shell_brep,
    )
    result = BottomCenterBandCreationResult(bottom_band=gravitation.output_line)
    if debug is not None:
        debug.update(result._asdict())
    return result


def preform_orientation_adjustment(
    outer_shell_brep: Brep,
    material_shell_brep: Brep,
    average_center_line: LineCurve,
    first_segment_direction: Vector3d,
    inner_shell_brep: Brep,
    first_segment_line: LineCurve,
    second_segment_line: LineCurve,
    shell_components: list[Brep],
    *,
    debug=None,
) -> PreformOrientationAdjustmentResult:
    """Align the centerline chord with the first-segment direction in the XZ frame.

    Rotate copies of every preform component about the world origin, not the joint.
    Recover the oriented joint centroid from component 1, the PIP sphere.
    """
    if not average_center_line.IsValid:
        raise ValueError("average_center_line must be a valid nonzero line.")
    if len(shell_components) < 2:
        raise ValueError("shell_components must include the joint at index 1.")

    rotation_plane = Plane(Point3d.Origin, Vector3d.XAxis, Vector3d.ZAxis)
    chord_direction = average_center_line.PointAtEnd - average_center_line.PointAtStart
    angle = Vector3d.VectorAngle(chord_direction, first_segment_direction, rotation_plane)
    if not 0.0 <= angle <= 2.0 * math.pi:
        raise ValueError("Alignment directions must have nonzero projections in the XZ plane.")
    first_orientation_shift_transform = Transform.Rotation(
        angle, rotation_plane.Normal, rotation_plane.Origin)

    rotated_material_shell = material_shell_brep.DuplicateBrep()
    rotated_outer_shell = outer_shell_brep.DuplicateBrep()
    rotated_inner_shell = inner_shell_brep.DuplicateBrep()
    rotated_first_segment = cast(LineCurve, first_segment_line.DuplicateCurve())
    rotated_second_segment = cast(LineCurve, second_segment_line.DuplicateCurve())
    rotated_components = [component.DuplicateBrep() for component in shell_components]
    for geometry in (
        rotated_material_shell, rotated_outer_shell, rotated_inner_shell,
        rotated_first_segment, rotated_second_segment, *rotated_components,
    ):
        if not geometry.Transform(first_orientation_shift_transform):
            raise RuntimeError("InfinityFlex2: preform orientation transform failed.")

    mass_properties = VolumeMassProperties.Compute(rotated_components[1])
    if mass_properties is None:
        raise RuntimeError("InfinityFlex2: joint component has no computable volume centroid.")
    joint_centroid = mass_properties.Centroid
    mass_properties.Dispose()
    if not joint_centroid.IsValid:
        raise RuntimeError("InfinityFlex2: rotated joint centroid is invalid.")

    result = PreformOrientationAdjustmentResult(
        material_shell_brep=rotated_material_shell,
        first_orientation_shift_transform=first_orientation_shift_transform,
        outer_shell_brep=rotated_outer_shell,
        inner_shell_brep=rotated_inner_shell,
        first_segment_line=rotated_first_segment,
        second_segment_line=rotated_second_segment,
        shell_components=rotated_components,
        joint_centroid=joint_centroid,
    )
    if debug is not None:
        debug.update(result._asdict())
    return result


def alignment_work(first_segment_start: Point3d, second_segment_end: Point3d,
                   *, debug=None) -> AlignmentWorkResult:
    """Join the centerline's outer endpoints with a straight alignment reference."""
    average_center_line = LineCurve(first_segment_start, second_segment_end)
    if not average_center_line.IsValid:
        raise ValueError("Alignment endpoints must define a valid nonzero line.")
    result = AlignmentWorkResult(average_center_line=average_center_line)
    if debug is not None:
        debug.update(result._asdict())
    return result


def central_finger_line_geometry(splint_centerline: PolylineCurve,
                                  *, debug=None) -> CentralFingerLineGeometryResult:
    """Expose the first two phalanx axes and endpoints without reordering the centerline."""
    if splint_centerline is None:
        raise ValueError("splint_centerline is required.")
    success, polyline = splint_centerline.TryGetPolyline()
    if not success or polyline is None or polyline.SegmentCount < 2:
        raise ValueError("splint_centerline must contain at least two straight segments.")

    first_segment = polyline.SegmentAt(0)
    second_segment = polyline.SegmentAt(1)
    if not first_segment.IsValid or not second_segment.IsValid:
        raise ValueError("The first two centerline segments must be valid and nonzero.")
    first_segment_direction = first_segment.Direction
    if not first_segment_direction.Unitize():
        raise ValueError("The first centerline segment has no unit direction.")

    result = CentralFingerLineGeometryResult(
        first_segment_line=LineCurve(first_segment),
        first_segment_start=first_segment.From,
        first_segment_end=first_segment.To,
        first_segment_direction=first_segment_direction,
        second_segment_line=LineCurve(second_segment),
        second_segment_end=second_segment.To,
    )
    if debug is not None:
        debug.update(result._asdict())
    return result


def finger_preform_solid(axial_band_thickness_mm: float, root_circ: float,
                         mid_circ: float, tip_circ: float, flexion_degrees: float,
                         inter_phalanx_distance: float, extent_band_width: float,
                         *, debug=None) -> FingerPreformSolidResult:
    """Subtract the untrimmed finger core from a thickened, PIP-centered trimmed shell.

    Root/tip inputs are shaft circumferences, so extrapolate the neighboring joint
    circumferences. Preserve the full distance for both phalanx lengths and halve it
    only when locating the trim ends. Lengths and circumferences are millimeters.
    """
    import FingerModel
    reload(FingerModel)
    import BrepDifference
    reload(BrepDifference)

    data = debug if debug is not None else {}
    trim_distance_mm = (inter_phalanx_distance + extent_band_width) / 2.0

    core_params = FingerModel.FingerParams(
        start_at="proximal",
        end_at="middle",
        mcp_circ=max(2.0, 2.0 * root_circ - mid_circ),
        proximal_mid_circ=root_circ,
        pip_circ=mid_circ,
        middle_mid_circ=tip_circ,
        dip_circ=max(2.0, 2.0 * tip_circ - mid_circ),
        metacarpal_len=20.0,
        proximal_len=inter_phalanx_distance,
        middle_len=inter_phalanx_distance,
        pip_flex=flexion_degrees,
    )
    shell_params = replace(
        core_params,
        shell_thickness=axial_band_thickness_mm,
        trim_start=("pip", -trim_distance_mm),
        trim_end=("pip", trim_distance_mm),
    )
    dimensions = {
        "mcp_circ_mm": core_params.mcp_circ,
        "dip_circ_mm": core_params.dip_circ,
        "trim_distance_mm": trim_distance_mm,
        "mid_r": mid_circ / (2.0 * math.pi),
    }
    data.update(dimensions)
    data.update(core_params=core_params, shell_params=shell_params)
    log("InfinityFlex2 preform dimensions: " + json.dumps(dimensions, sort_keys=True))

    centerline, inner_shell_brep, components, joints = FingerModel.create_finger_model(
        core_params, tolerance=None, return_parts=True)
    data.update(
        core_centerline=PolylineCurve(centerline),
        inner_shell_brep=inner_shell_brep,
        core_components=components,
        core_joint_positions=joints,
    )
    centerline, outer_shell_brep, shell_components, joints = FingerModel.create_finger_model(
        shell_params, tolerance=None, return_parts=True)
    data.update(
        outer_centerline=PolylineCurve(centerline),
        outer_shell_brep=outer_shell_brep,
        shell_components=shell_components,
        outer_joint_positions=joints,
    )
    if shell_components is None:
        raise RuntimeError("InfinityFlex2: outer model did not return shell components.")

    material_shell_brep, success, method = BrepDifference.robust_brep_difference(
        minuend=outer_shell_brep, subtrahend=inner_shell_brep, base_tolerance=0.001)
    data["preform_boolean_method"] = method
    if not success or material_shell_brep is None:
        raise RuntimeError("InfinityFlex2: preform subtraction failed ({0})".format(method))
    data["material_shell_brep"] = material_shell_brep
    if not material_shell_brep.IsValid or not material_shell_brep.IsSolid:
        raise RuntimeError("InfinityFlex2: preform must be a valid closed solid.")
    splint_centerline = data["outer_centerline"]
    data["splint_centerline"] = splint_centerline
    log("InfinityFlex2: hollow preform created via {0}".format(method))
    return FingerPreformSolidResult(
        inner_shell_brep=inner_shell_brep,
        outer_shell_brep=outer_shell_brep,
        shell_components=shell_components,
        material_shell_brep=material_shell_brep,
        splint_centerline=splint_centerline,
        mid_r=data["mid_r"],
    )


_PhaseResult = TypeVar("_PhaseResult")


def _run_phase(
    tracker: PhaseTracker,
    number: float,
    title: str,
    build: Callable[..., _PhaseResult],
    debug,
    **inputs,
) -> _PhaseResult:
    """Preserve partial construction on failure and register only completed phases.

    Group functions populate their own named outputs in the phase-local debug dict.
    Merge it before PhaseTracker can raise StopAfterPhase so early-stop previews work.
    """
    phase_debug = {}
    try:
        result = build(debug=phase_debug, **inputs)
    finally:
        if debug is not None:
            debug.update(phase_debug)
    tracker.log_phase(number, title, **phase_debug)
    return result


class InfinityFlex2Generator(SplintGenerator):
    """Orchestrate the Python pipeline without changing production InfinityFlex routing."""

    GEO_ALGORITHM_NAME = "InfinityFlex2"

    def generate(self, raw_data, object_id, debug=None, stop_after=None):
        """Build the named phases and return a smoothed mesh plus its source Brep.

        Keep patient-input extraction and explicit inter-phase wiring here. The harness
        or production runner owns export; this method never writes mesh files.
        """
        if not isinstance(raw_data, dict):
            raise TypeError("raw_data must be a decoded measurements object.")

        log(json.dumps({
            "algorithm": self.GEO_ALGORITHM_NAME,
            "object_id": object_id,
            "raw_data": raw_data,
        }, indent=2, sort_keys=True))

        tracker = PhaseTracker(debug=debug, stop_after=stop_after, log_fn=log)
        tracker.log_phase(0.0, "inputs", raw_data=raw_data, object_id=object_id)
        preform = _run_phase(
            tracker, 1.0, "finger preform solid", finger_preform_solid, debug,
            axial_band_thickness_mm=AXIAL_BAND_THICKNESS_MM,
            root_circ=raw_data["root_circumference_mm"],
            mid_circ=raw_data["mid_circumference_mm"],
            tip_circ=raw_data["tip_circumference_mm"],
            flexion_degrees=raw_data["flexion_degrees"],
            inter_phalanx_distance=raw_data["inter_phalanx_distance_mm"],
            extent_band_width=EXTENT_BAND_STRAP_WIDTH_MM,
        )
        centerline_geometry = _run_phase(
            tracker, 2.0, "central finger line geometry", central_finger_line_geometry, debug,
            splint_centerline=preform.splint_centerline,
        )
        alignment = _run_phase(
            tracker, 3.0, "alignment work", alignment_work, debug,
            first_segment_start=centerline_geometry.first_segment_start,
            second_segment_end=centerline_geometry.second_segment_end,
        )
        orientation = _run_phase(
            tracker, 4.0, "preform orientation adjustment", preform_orientation_adjustment, debug,
            outer_shell_brep=preform.outer_shell_brep,
            material_shell_brep=preform.material_shell_brep,
            average_center_line=alignment.average_center_line,
            first_segment_direction=centerline_geometry.first_segment_direction,
            inner_shell_brep=preform.inner_shell_brep,
            first_segment_line=centerline_geometry.first_segment_line,
            second_segment_line=centerline_geometry.second_segment_line,
            shell_components=preform.shell_components,
        )
        band = _run_phase(
            tracker, 5.0, "bottom center band creation", bottom_center_band_creation, debug,
            bottom_band_width_mm=CENTRAL_BAND_STRAP_WIDTH_MM,
            joint_centroid=orientation.joint_centroid,
            outer_shell_brep=orientation.outer_shell_brep,
        )
        top_bands = _run_phase(
            tracker, 6.0, "top bands creation", top_bands_creation, debug,
            outer_shell_brep=orientation.outer_shell_brep,
            root_north_star=Point3d(-200.0, 0.0, 100.0),
            north_star_nudge=Vector3d(0.0, 0.0, 3.0),
            top_band_width_mm=EXTENT_BAND_STRAP_WIDTH_MM,
            tip_north_star=Point3d(300.0, 0.0, 100.0),
        )
        cutter_profile = _run_phase(
            tracker, 7.0, "side cutter profile", side_cutter_profile, debug,
            bottom_band=band.bottom_band,
            root_top_band=top_bands.root_top_band,
            tip_top_band=top_bands.tip_top_band,
            bottom_band_width_mm=CENTRAL_BAND_STRAP_WIDTH_MM,
            mid_joint_rise_factor=MID_JOINT_RISE_FACTOR,
        )
        cutter_extrusions = _run_phase(
            tracker, 8.0, "side cutter extrusions", side_cutter_extrusions, debug,
            cutter_curve=cutter_profile.cutter_curve,
            outer_shell_brep=orientation.outer_shell_brep,
            tip_slant_line=cutter_profile.tip_slant_line,
            tail_slant_line=cutter_profile.tail_slant_line,
            inner_shell_brep=orientation.inner_shell_brep,
            nudge_mm=-50.0,
        )
        body = _run_phase(
            tracker, 9.0, "splint body intersection", splint_body_intersection, debug,
            material_shell_brep=orientation.material_shell_brep,
            side_cutter_volume=cutter_extrusions.side_cutter_volume,
        )
        indicator = _run_phase(
            tracker, 10.0, "direction indicator", direction_indicator, debug,
            splint_body=body.splint_body,
            tip_top_band=top_bands.tip_top_band,
            wall_thickness_mm=AXIAL_BAND_THICKNESS_MM,
            north_star_nudge_mm=5.0,
            nub_position_parameter=0.5,
            nub_sinking_factor=0.1,
            ball_r_to_wall_ratio=0.65,
        )
        comfort = _run_phase(
            tracker, 11.0, "comfort lip trimmers", comfort_lip_trimmers, debug,
            tip_outer_curve=cutter_extrusions.tip_outer_curve,
            tail_outer_curve=cutter_extrusions.tail_outer_curve,
            splint_brep=indicator.finished_splint,
            joint_centroid=orientation.joint_centroid,
            mid_r=preform.mid_r,
            tip_inner_curve=cutter_extrusions.tip_inner_curve,
            tail_inner_curve=cutter_extrusions.tail_inner_curve,
            bottom_slice_x_scale=0.5,
            radius_multiplier=1.29,
            chamfer_side_limit_angle_degrees=-74.0,
        )
        embossed = _run_phase(
            tracker, 12.0, "ID embossing gun", id_embossing_gun, debug,
            finished_splint_brep=comfort.comfort_trimmed_splint_brep,
            object_id=object_id,
            wall_thickness_mm=AXIAL_BAND_THICKNESS_MM,
            projection_centroid=orientation.joint_centroid,
            mid_r=preform.mid_r,
            projection_origin_z_factor=0.3,
            north_star_nudge_mm=100.0,
            lower_angle_from_lip_degrees=-15.0,
            text_size=2.7,
            extrusion_depth_factor=0.5,
        )
        slitted = _run_phase(
            tracker, 13.0, "slit bot", slit_bot, debug,
            include_slit=raw_data["include_slit"],
            splint_brep=embossed.splint_with_embossed_id,
            slit_thickness=0.3,
            slit_panel_z_factor=-0.7,
        )
        build_plate = _run_phase(
            tracker, 14.0, "orienting to build plate", orienting_to_build_plate, debug,
            splint_brep_w_slit=slitted.splint_brep_w_slit,
            tip_slant_line=cutter_profile.tip_slant_line,
        )
        smoothed = _run_phase(
            tracker, 15.0, "joining and smoothing", joining_and_smoothing, debug,
            oriented_splint=build_plate.oriented_splint,
        )
        return SplintResult(
            mesh=smoothed.final_smooth_splint,
            solid=build_plate.oriented_splint,
            metadata={
                "algorithm": self.GEO_ALGORITHM_NAME,
                "object_id": object_id,
                "status": "smoothed_mesh",
                "mid_radius_mm": preform.mid_r,
            },
        )