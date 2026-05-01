Perfect place to be. If you want to intentionally push size down and find the visual breaking point, these are the best levers in order of impact.

target_edge_length and max_edge_length (highest impact)
Increase both together.
This is the primary triangle-count control.
Biggest size drops, biggest risk to curved silhouette and embossed text quality.
quality
Switch from high to fast once you start pushing edge lengths up.
Fast usually trims more triangles for the same edge-length settings.
min_edge_length
Raise it gradually to prevent tiny local triangles.
Helps size a lot on detail-heavy regions.
Too high can break closure on tricky geometry.
Keep refine_grid off
refine_grid=True usually increases triangles and file size.
Levers that are mostly visual, not size:

weld_angle_degrees
Changes normal continuity and perceived sharpness.
Usually does not materially reduce file size.
smoothing_iterations
Can hide faceting, but can soften intended edges.
Keep at 0 for your ring shoulders and embossed edges.
Settings to keep for robustness:

jagged_seams=False
simple_planes=True
require_closed=True
require_manifold=True
Recommended sweep ladder:

high, target/max 1.3, min 0.05
high, target/max 1.5, min 0.07
fast, target/max 1.5, min 0.07
fast, target/max 1.8, min 0.10
fast, target/max 2.0, min 0.12
What to watch as stop criteria:

Embossed character legibility (stroke corners rounding)
Top and bottom ring shoulder edge definition
Oval/round profile faceting on outer silhouette
One practical note:
If transport/storage size is the goal more than slicer compatibility, exporting 3mf instead of stl often cuts file size dramatically for the same mesh geometry because it is compressed.