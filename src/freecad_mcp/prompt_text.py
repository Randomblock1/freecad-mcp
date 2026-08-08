ASSET_CREATION_STRATEGY = """
Asset Creation Strategy for FreeCAD MCP

Units: lengths are millimeters, angles are degrees.

1. Orient first: get_objects(doc_name, detail="summary") shows every object's
   type, shape validity, Volume, and BoundBox cheaply. Use detail="full" or
   get_object only when you need full properties.

2. Use the names FreeCAD actually assigned. create_document/create_object
   return the real name (spaces become underscores, duplicates get numeric
   suffixes like Box001) — always use the returned name in subsequent calls.

3. Prefer one idempotent execute_code script over long chains of
   create_object/edit_object/get_object calls. A script that deletes and
   recreates its own objects can be fixed and re-run in a single call. For
   scripts you iterate on, write them to a file and use execute_code_from_file.
   State persists between calls, so define helpers once and reuse them.

4. Check the parts library (get_parts_list / insert_part_from_library) before
   modeling standard parts from scratch.

5. Verify numerically, not just visually:
   - get_object / get_objects report BoundBox, Volume, and CenterOfMass —
     confirm "is this actually 50 mm tall?" from data, not pixels.
   - measure_distance checks clearances and interference (distance 0 +
     intersection volume = parts collide).
   - get_topology names each face (Face1, Face2, ...) with its type, area,
     centroid, and normal — use it for FEM References instead of guessing.
   - Watch for WARNING lines about objects that did not recompute cleanly,
     and check get_report_log when something misbehaves.

6. Manage screenshot feedback to save tokens. Tools that modify or inspect the
   model accept include_screenshot and view_name parameters:
   - Pass include_screenshot=False for intermediate steps and analytical
     scripts; confirm visually once at the end with get_view.
   - Pass view_name ("Front", "Top", "Right", ...) to orient the screenshot
     toward what you changed; default is "Isometric" (top-front-right).
   - Screenshots default to at most 800 px on the longest edge; pass explicit
     width/height to get_view for full resolution.

7. Finish the job: documents live only in memory until save_document; use
   export_document for STEP/STL/3MF deliverables.
"""


FEM_WORKFLOW_GUIDE = """
FEM workflow with the FreeCAD MCP tools

A complete static analysis needs, in order (all via create_object unless noted):

1. Geometry: any Part-derived solid (e.g. Part::Box, PartDesign::Body).

2. Analysis container — obj_type "Fem::AnalysisPython":
{"doc_name": "Doc", "obj_name": "FemAnalysis", "obj_type": "Fem::AnalysisPython"}

3. Material — obj_type "Fem::MaterialCommon", attached via analysis_name.
   The Material property is a string->string map: EVERY value must be a quoted
   string (a bare number like 0.3 is rejected), and the stiffness key is
   "YoungsModulus" (with the "s"), which the solver's prerequisite check
   requires:
{"doc_name": "Doc", "obj_name": "Material", "obj_type": "Fem::MaterialCommon",
 "analysis_name": "FemAnalysis",
 "obj_properties": {"Material": {"Name": "Steel", "Density": "7900 kg/m^3",
                    "YoungsModulus": "210 GPa", "PoissonRatio": "0.3"}}}

4. Constraints — e.g. "Fem::ConstraintFixed" / "Fem::ConstraintForce" /
   "Fem::ConstraintPressure". References name an object and a face; get face
   names from get_topology (face centroids/normals tell you which is which):
{"doc_name": "Doc", "obj_name": "Fixed", "obj_type": "Fem::ConstraintFixed",
 "analysis_name": "FemAnalysis",
 "obj_properties": {"References": [{"object_name": "Box", "face": "Face1"}]}}
Force constraints take a Force quantity that MUST be a unit string — "Force":
"1000 N". A bare number ("Force": 1000) is silently interpreted in FreeCAD's
internal units (millinewtons), i.e. 1000x too small. The force direction
defaults to the referenced face's normal; to set an explicit direction, bind
ConstraintForce.Direction to an edge via execute_code (it is not settable
through obj_properties):
{"doc_name": "Doc", "obj_name": "Load", "obj_type": "Fem::ConstraintForce",
 "analysis_name": "FemAnalysis",
 "obj_properties": {"References": [{"object_name": "Box", "face": "Face2"}],
                    "Force": "1000 N"}}

5. Mesh — obj_type "Fem::FemMeshGmsh" (Gmsh runs automatically on creation).
   "Shape" names the geometry object (legacy "Part" also accepted). On
   FreeCAD 1.x the size limits are CharacteristicLengthMax/Min (legacy
   ElementSizeMax/Min also accepted):
{"doc_name": "Doc", "obj_name": "Mesh", "obj_type": "Fem::FemMeshGmsh",
 "analysis_name": "FemAnalysis",
 "obj_properties": {"Shape": "Box", "CharacteristicLengthMax": 10,
                    "CharacteristicLengthMin": 0.1}}

6. Solve with run_fem_analysis(doc_name, analysis_name). A SolverCcxTools is
   auto-created if missing. Returns max and min von Mises stress (MPa), max
   displacement (mm), and node count. The solver blocks all other RPC calls
   while running; do not issue parallel requests.
"""
