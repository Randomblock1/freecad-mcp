import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, Literal

try:
    # mcp 1.x
    from mcp.server.fastmcp import Context, FastMCP
except ImportError:
    # mcp 2.x moved mcp.server.fastmcp to mcp.server.mcpserver and renamed
    # FastMCP to MCPServer; the API surface used here is unchanged.
    from mcp.server.mcpserver import Context
    from mcp.server.mcpserver import MCPServer as FastMCP
from mcp.types import ImageContent, TextContent

from .freecad_client import FreeCADConnection
from .operations import (
    check_printability_operation,
    create_document_operation,
    create_object_operation,
    delete_object_operation,
    edit_object_operation,
    execute_code_async_operation,
    execute_code_from_file_operation,
    execute_code_operation,
    export_document_operation,
    get_async_status_operation,
    get_object_operation,
    get_objects_operation,
    get_parts_list_operation,
    get_report_log_operation,
    get_section_view_operation,
    get_topology_operation,
    get_view_operation,
    get_views_operation,
    insert_part_from_library_operation,
    list_documents_operation,
    measure_distance_operation,
    reload_document_operation,
    run_fem_analysis_operation,
    save_document_operation,
)
from .prompt_text import ASSET_CREATION_STRATEGY, FEM_WORKFLOW_GUIDE
from .server_state import ServerState


logging.basicConfig(
    level=logging.WARNING, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("FreeCADMCPserver")
logger.setLevel(logging.INFO)

ViewName = Literal[
    "Isometric", "Front", "Top", "Right", "Back", "Left", "Bottom", "Dimetric", "Trimetric"
]

state = ServerState()


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    try:
        logger.info("FreeCADMCP server starting up")
        try:
            _ = get_freecad_connection()
            logger.info("Successfully connected to FreeCAD on startup")
        except Exception as e:
            logger.warning(f"Could not connect to FreeCAD on startup: {str(e)}")
            logger.warning(
                "Make sure the FreeCAD addon is running before using FreeCAD resources or tools"
            )
        yield {}
    finally:
        if state.freecad_connection:
            logger.info("Disconnecting from FreeCAD on shutdown")
            state.freecad_connection.disconnect()
            state.freecad_connection = None
        logger.info("FreeCADMCP server shut down")


mcp = FastMCP(
    "FreeCADMCP",
    instructions=(
        "Control a live FreeCAD instance: create/edit/inspect documents and objects, "
        "run Python inside FreeCAD, measure geometry, run FEM, save/export files.\n"
        "- Units: lengths are millimeters, angles are degrees.\n"
        "- FreeCAD sanitizes requested names (spaces become underscores, duplicates "
        "get numeric suffixes). Always use the actual name returned by "
        "create_document/create_object in subsequent calls.\n"
        "- PartDesign::* and Sketcher::* objects made via create_object are empty "
        "shells; build features and sketch geometry with execute_code.\n"
        "- execute_code state persists across calls (FreeCAD/App/FreeCADGui/Gui are "
        "pre-imported); prefer one idempotent script over many small tool calls. It "
        "returns no screenshot by default; pass dry_run=true to preview a script "
        "and roll back its document changes.\n"
        "- Verify dimensions numerically with get_object (BoundBox/Volume/"
        "CenterOfMass), measure_distance, and get_topology — not screenshots alone. "
        "Use get_views for several angles at once, get_section_view to see inside, "
        "and check_printability for 3D-print readiness.\n"
        "- Documents live only in memory until save_document; export_document "
        "writes STEP/STL/3MF etc. (linear_deflection sets mesh quality).\n"
        "- FreeCAD's console output (recompute errors, async job output) is "
        "readable via get_report_log.\n"
        "- FEM recipes: read the freecad://fem-workflow resource."
    ),
    lifespan=server_lifespan,
)


def get_freecad_connection() -> FreeCADConnection:
    """Get or create a persistent FreeCAD connection"""
    if state.freecad_connection is None:
        state.freecad_connection = FreeCADConnection(host=state.rpc_host, port=9875)
        if not state.freecad_connection.ping():
            logger.error("Failed to ping FreeCAD")
            state.freecad_connection = None
            raise Exception(
                "Failed to connect to FreeCAD. Make sure the FreeCAD addon is running."
            )
    return state.freecad_connection


@mcp.tool(structured_output=False)
def create_document(ctx: Context, name: str) -> list[TextContent]:
    """Create a new document in FreeCAD.

    Args:
        name: The name of the document to create.

    Returns:
        A message indicating the success or failure of the document creation.

    Examples:
        If you want to create a document named "MyDocument", you can use the following data.
        ```json
        {
            "name": "MyDocument"
        }
        ```
    """
    return create_document_operation(get_freecad_connection(), name)


@mcp.tool(structured_output=False)
def create_object(
    ctx: Context,
    doc_name: str,
    obj_type: str,
    obj_name: str,
    analysis_name: str | None = None,
    obj_properties: dict[str, Any] | None = None,
    include_screenshot: bool = True,
    view_name: ViewName = "Isometric",
) -> list[TextContent | ImageContent]:
    """Create a new object in FreeCAD. Lengths in mm, angles in degrees.

    The response reports the name FreeCAD actually assigned (requested names
    are sanitized and deduplicated) — use that name in subsequent calls.

    Note: PartDesign::* and Sketcher::* objects are created as empty shells;
    add features/sketch geometry with execute_code afterwards. For FEM objects
    (Fem::AnalysisPython, materials, constraints, meshes) read the
    freecad://fem-workflow resource for complete recipes.

    Args:
        doc_name: The name of the document to create the object in.
        obj_type: The type of the object to create (e.g. 'Part::Box',
            'Part::Cylinder', 'Draft::Circle', 'PartDesign::Body', 'Fem::*').
        obj_name: The requested name of the object.
        analysis_name: For Fem::* objects only: the Fem::AnalysisPython
            container to add the new object to (required for Fem::FemMeshGmsh).
        obj_properties: Properties to set on the new object.
        include_screenshot: Whether to return a screenshot of the model (default True).
            Set to False to save tokens when visual feedback is not needed,
            e.g. for intermediate steps in a longer sequence of changes.
        view_name: The view orientation of the returned screenshot (default "Isometric").
            Pick the view that best shows the change being made.

    Returns:
        The created object's actual name (with a warning if it did not
        recompute cleanly) and a screenshot of the model.

    Example — a rotated cylinder at (10, 10, 0):
        ```json
        {
            "doc_name": "MyDoc",
            "obj_name": "Cylinder",
            "obj_type": "Part::Cylinder",
            "obj_properties": {
                "Height": 30,
                "Radius": 10,
                "Placement": {
                    "Base": {"x": 10, "y": 10, "z": 0},
                    "Rotation": {"Axis": {"x": 0, "y": 0, "z": 1}, "Angle": 45}
                },
                "ViewObject": {"ShapeColor": [0.5, 0.5, 0.5, 1.0]}
            }
        }
        ```
    """
    return create_object_operation(
        get_freecad_connection(),
        state.only_text_feedback,
        doc_name,
        obj_type,
        obj_name,
        analysis_name,
        obj_properties,
        include_screenshot,
        view_name,
    )


@mcp.tool(structured_output=False)
def edit_object(
    ctx: Context,
    doc_name: str,
    obj_name: str,
    obj_properties: dict[str, Any],
    include_screenshot: bool = True,
    view_name: ViewName = "Isometric",
) -> list[TextContent | ImageContent]:
    """Edit an object in FreeCAD.
    This tool is used when the `create_object` tool cannot handle the object creation.

    Args:
        doc_name: The name of the document to edit the object in.
        obj_name: The name of the object to edit.
        obj_properties: The properties of the object to edit.
        include_screenshot: Whether to return a screenshot of the model (default True).
            Set to False to save tokens when visual feedback is not needed,
            e.g. for intermediate steps in a longer sequence of changes.
        view_name: The view orientation of the returned screenshot (default "Isometric").
            Pick the view that best shows the change being made.

    Returns:
        A message indicating the success or failure of the object editing and a screenshot of the object.
    """
    return edit_object_operation(
        get_freecad_connection(),
        state.only_text_feedback,
        doc_name,
        obj_name,
        obj_properties,
        include_screenshot,
        view_name,
    )


@mcp.tool(structured_output=False)
def delete_object(
    ctx: Context,
    doc_name: str,
    obj_name: str,
    include_screenshot: bool = True,
    view_name: ViewName = "Isometric",
) -> list[TextContent | ImageContent]:
    """Delete an object in FreeCAD.

    Args:
        doc_name: The name of the document to delete the object from.
        obj_name: The name of the object to delete.
        include_screenshot: Whether to return a screenshot of the model (default True).
            Set to False to save tokens when visual feedback is not needed,
            e.g. for intermediate steps in a longer sequence of changes.
        view_name: The view orientation of the returned screenshot (default "Isometric").
            Pick the view that best shows the change being made.

    Returns:
        A message indicating the success or failure of the object deletion and a screenshot of the object.
    """
    return delete_object_operation(
        get_freecad_connection(),
        state.only_text_feedback,
        doc_name,
        obj_name,
        include_screenshot,
        view_name,
    )


@mcp.tool(structured_output=False)
def execute_code_async(ctx: Context, code: str) -> list[TextContent]:
    """Execute Python code in FreeCAD without waiting for completion.

    Use this ONLY for long-running background computations that do NOT touch the
    FreeCAD GUI or mutate the FreeCAD document tree directly.

    This tool runs the submitted code in a background thread and returns
    immediately. Because it does not run on FreeCAD's main GUI thread, the code
    must NOT call FreeCADGui APIs, manipulate the active view or selection, create
    or edit document objects, change object properties, call doc.recompute(), or
    save documents.

    For code that touches FreeCAD documents, document objects, FreeCADGui, the
    active view, selection, recompute, or save operations, use execute_code instead.
    execute_code runs on the FreeCAD GUI thread and is the safe default for normal
    FreeCAD automation.

    Use execute_code_async only for background-safe work such as long-running
    pure OCCT geometry calculations (e.g. fuse/cut/loft on already-fetched shapes)
    or other CPU-bound computations that do not interact with the document or GUI.

    Typical usage pattern:
    1. Fetch shapes into variables first (via execute_code on the GUI thread);
       the execution namespace is shared, so execute_code_async sees them.
    2. Run the heavy computation via execute_code_async; note the returned job id.
    3. Poll get_async_status(job_id) until status is "completed" or "failed"
       (failures include the error and traceback).
    4. Apply results to the document via execute_code (which runs on the GUI
       thread) — the shared namespace still holds the computed values.

    Args:
        code: Background-safe Python code to execute.

    Returns:
        A message with the background job id to poll via get_async_status.
        Rejected while a dry run or section screenshot is in progress, since
        those roll back document changes and would discard the job's work;
        retry once they return.
    """
    return execute_code_async_operation(get_freecad_connection(), code)


@mcp.tool()
def get_async_status(ctx: Context, job_id: str | None = None) -> list[TextContent]:
    """Get the status of background jobs started with execute_code_async.

    Args:
        job_id: The job id returned by execute_code_async. Omit to list all
            jobs from this FreeCAD session (newest first).

    Returns:
        Job status: "running" (with runtime_s), "completed", or "failed"
        (with the error and the traceback pointing into the submitted code).
    """
    return get_async_status_operation(get_freecad_connection(), job_id)


@mcp.tool(structured_output=False)
def execute_code(
    ctx: Context,
    code: str,
    include_screenshot: bool = False,
    view_name: ViewName = "Isometric",
    dry_run: bool = False,
) -> list[TextContent | ImageContent]:
    """Execute arbitrary Python code in FreeCAD.

    Best suited for short one-off snippets. For long scripts you intend to
    iterate on, write them to a file and use execute_code_from_file instead,
    so you can edit the file selectively and re-run it without resending the
    full source each time.

    State persists across calls: variables and helpers you define remain
    available to later execute_code and execute_code_async calls for the
    FreeCAD session. FreeCAD/App and FreeCADGui/Gui are pre-imported. The
    namespace is isolated from the RPC server's internals.

    On failure you get the exception, the traceback line numbers within your
    submitted code, and any output printed before the error.

    Args:
        code: The Python code to execute.
        include_screenshot: Whether to return a screenshot of the model
            (default False — this tool is often used for probes and analytical
            scripts). Set True when the code changes the model and you want to
            see the result; or call get_view/get_views afterwards.
        view_name: The view orientation of the returned screenshot (default "Isometric").
            Pick the view that best shows the change being made.
        dry_run: When True, run the code and return its output/errors, then roll
            back all document changes (created/edited/deleted objects and new
            documents). Use it to preview or validate a script without committing.
            Only document state rolls back — file writes (save/export) and
            persisted namespace variables do not. Rejected while an
            execute_code_async job is still running, since the rollback would
            also discard that job's changes.

    Returns:
        A message indicating success or failure, the code's output, and
        (unless dry_run) a screenshot when include_screenshot is True.
    """
    return execute_code_operation(
        get_freecad_connection(),
        state.only_text_feedback,
        code,
        include_screenshot,
        view_name,
        dry_run,
    )


@mcp.tool(structured_output=False)
def execute_code_from_file(
    ctx: Context,
    file_path: str,
    include_screenshot: bool = False,
    view_name: ViewName = "Isometric",
    dry_run: bool = False,
) -> list[TextContent | ImageContent]:
    """Execute Python code from a file in FreeCAD.

    Prefer this over execute_code for long scripts that will be modified and
    re-run (e.g. a script that parametrically generates a model): write the
    script to a file once, then selectively edit the file and re-run this tool
    instead of resending the entire source as a string each time. You can also
    format, lint, or check the file before executing it.

    The file is read by the MCP server, so the path must exist on the machine
    where the MCP server runs (not necessarily the machine running FreeCAD when
    connecting to a remote host). The code itself executes inside FreeCAD on
    the GUI thread, exactly like execute_code.

    The script is exec()'d inside FreeCAD's embedded interpreter, not run as a
    standalone program: __name__ is not "__main__" (code behind an
    `if __name__ == "__main__":` guard is silently skipped), and __file__ does
    not point at this file. Put the code to run at the top level of the file
    and use absolute paths inside the script.

    Args:
        file_path: Absolute path to a UTF-8 encoded Python file to execute
            ("~" is expanded).
        include_screenshot: Whether to return a screenshot afterwards
            (default False). Set True to see the result, or use get_view/get_views.
        view_name: The view orientation of the returned screenshot (default "Isometric").
        dry_run: When True, run the script and return its output/errors, then
            roll back all document changes — a safe way to test a generator
            script before committing. File writes and namespace vars persist.
            Rejected while an execute_code_async job is still running.

    Returns:
        A message indicating success or failure, the code's output, and
        (unless dry_run) a screenshot when include_screenshot is True.
    """
    return execute_code_from_file_operation(
        get_freecad_connection(),
        state.only_text_feedback,
        file_path,
        include_screenshot,
        view_name,
        dry_run,
    )


@mcp.tool(structured_output=False)
def get_view(
    ctx: Context,
    view_name: ViewName,
    width: int | None = None,
    height: int | None = None,
    focus_object: str | None = None,
) -> list[ImageContent | TextContent]:
    """Get a screenshot of the active view.

    Args:
        view_name: The name of the view to get the screenshot of.
        The following views are available:
        - "Isometric"
        - "Front"
        - "Top"
        - "Right"
        - "Back"
        - "Left"
        - "Bottom"
        - "Dimetric"
        - "Trimetric"
        width: The width of the screenshot in pixels. When width and height are both
            omitted, the viewport size is used, downscaled so the longest edge is at
            most 800 px (token economy). Pass explicit values for full resolution.
        height: The height of the screenshot in pixels (see width).
        focus_object: The name of the object to focus on. If not specified, fits all objects in the view.

    Returns:
        A screenshot of the active view.
    """
    return get_view_operation(get_freecad_connection(), view_name, width, height, focus_object)


@mcp.tool(structured_output=False)
def get_views(
    ctx: Context,
    view_names: list[ViewName] | None = None,
    width: int | None = None,
    height: int | None = None,
    focus_object: str | None = None,
) -> list[ImageContent | TextContent]:
    """Get screenshots of several views in one call.

    Use this instead of repeated get_view calls when verifying a model from
    multiple angles (the common front/top/isometric check). Each image is
    preceded by a text label naming its view.

    Args:
        view_names: Views to capture, in order. Defaults to
            ["Isometric", "Front", "Top"]. Any of: Isometric, Front, Top, Right,
            Back, Left, Bottom, Dimetric, Trimetric.
        width: Pixel width per image. Omit for the auto-sized default (longest
            edge capped at 800 px); pass explicit values for full resolution.
        height: Pixel height per image (see width).
        focus_object: Object to frame in every view; omit to fit all objects.

    Returns:
        One labelled screenshot per requested view.
    """
    if view_names is None:
        view_names = ["Isometric", "Front", "Top"]
    return get_views_operation(
        get_freecad_connection(),
        state.only_text_feedback,
        view_names,
        width,
        height,
        focus_object,
    )


@mcp.tool(structured_output=False)
def get_section_view(
    ctx: Context,
    doc_name: str,
    plane: Literal["XY", "XZ", "YZ"] = "XZ",
    offset: float | None = None,
    object_names: list[str] | None = None,
    view_name: ViewName = "Isometric",
) -> list[ImageContent | TextContent]:
    """Render a cutaway (section) screenshot to see inside a model.

    Temporarily cuts away the half of the model on the +normal side of the
    plane, screenshots the exposed cross-section, then rolls everything back —
    the document is never actually modified. Rejected while an
    execute_code_async job is still running, since the rollback would also
    discard that job's changes.

    Args:
        doc_name: The document to section.
        plane: The cutting plane — "XY" (normal Z), "XZ" (normal Y, default), or
            "YZ" (normal X). The material on the +normal side is removed.
        offset: Position of the plane along its normal axis (mm). Defaults to the
            model's center along that axis.
        object_names: Objects to section. Omit to section all visible solids.
        view_name: Camera orientation for the screenshot (default "Isometric").

    Returns:
        A labelled cutaway screenshot.
    """
    return get_section_view_operation(
        get_freecad_connection(),
        state.only_text_feedback,
        doc_name,
        plane,
        offset,
        object_names,
        view_name,
    )


@mcp.tool(structured_output=False)
def insert_part_from_library(
    ctx: Context,
    relative_path: str,
    include_screenshot: bool = True,
    view_name: ViewName = "Isometric",
) -> list[TextContent | ImageContent]:
    """Insert a part from the parts library addon.

    Args:
        relative_path: The relative path of the part to insert.
        include_screenshot: Whether to return a screenshot of the model (default True).
            Set to False to save tokens when visual feedback is not needed,
            e.g. for intermediate steps in a longer sequence of changes.
        view_name: The view orientation of the returned screenshot (default "Isometric").
            Pick the view that best shows the change being made.

    Returns:
        A message indicating the success or failure of the part insertion and a screenshot of the object.
    """
    return insert_part_from_library_operation(
        get_freecad_connection(),
        state.only_text_feedback,
        relative_path,
        include_screenshot,
        view_name,
    )


@mcp.tool(structured_output=False)
def get_objects(
    ctx: Context,
    doc_name: str,
    include_screenshot: bool = True,
    view_name: ViewName = "Isometric",
    detail: Literal["summary", "full"] = "summary",
) -> list[TextContent | ImageContent]:
    """Get all objects in a document.
    You can use this tool to get the objects in a document to see what you can check or edit.

    Args:
        doc_name: The name of the document to get the objects from.
        include_screenshot: Whether to return a screenshot of the document (default True).
            Set to False to save tokens when only the object data is needed.
        view_name: The view orientation of the returned screenshot (default "Isometric").
        detail: "summary" (default) returns Name/TypeId/State/shape validity/Volume/
            BoundBox/non-identity Placement per object and omits PartDesign Origin
            scaffolding (omitted names are listed). "full" returns every property of
            every object. Use get_object for full detail on a single object.

    Returns:
        Objects in the document (summary or full detail) and a screenshot of the document.
    """
    return get_objects_operation(
        get_freecad_connection(),
        state.only_text_feedback,
        doc_name,
        include_screenshot,
        view_name,
        detail,
    )


@mcp.tool(structured_output=False)
def get_object(
    ctx: Context,
    doc_name: str,
    obj_name: str,
    include_screenshot: bool = True,
    view_name: ViewName = "Isometric",
) -> list[TextContent | ImageContent]:
    """Get an object from a document.
    You can use this tool to get the properties of an object to see what you can check or edit.

    Args:
        doc_name: The name of the document to get the object from.
        obj_name: The name of the object to get.
        include_screenshot: Whether to return a screenshot of the document (default True).
            Set to False to save tokens when only the object data is needed.
        view_name: The view orientation of the returned screenshot (default "Isometric").

    Returns:
        The object and a screenshot of the object.
    """
    return get_object_operation(
        get_freecad_connection(),
        state.only_text_feedback,
        doc_name,
        obj_name,
        include_screenshot,
        view_name,
    )


@mcp.tool()
def measure_distance(
    ctx: Context,
    doc_name: str,
    object1: str,
    object2: str,
    sub1: str | None = None,
    sub2: str | None = None,
) -> list[TextContent]:
    """Measure the minimum distance between two objects (or sub-elements).

    Use this to verify clearances and detect interference numerically instead
    of eyeballing screenshots. Distances are in millimeters.

    Args:
        doc_name: The document containing both objects.
        object1: Name of the first object.
        object2: Name of the second object.
        sub1: Optional sub-element of object1 (e.g. "Face3", "Edge1", "Vertex2");
            get names from get_topology.
        sub2: Optional sub-element of object2.

    Returns:
        distance_mm and the closest point on each shape. Distance 0 means the
        shapes touch or overlap; for whole objects, intersection_volume_mm3 > 0
        quantifies the actual interference.
    """
    return measure_distance_operation(
        get_freecad_connection(), doc_name, object1, object2, sub1, sub2
    )


@mcp.tool()
def get_topology(
    ctx: Context,
    doc_name: str,
    obj_name: str,
    include_edges: bool = False,
) -> list[TextContent]:
    """Get an object's topology: every face's type, area, centroid, and normal.

    Face names ("Face1", "Face2", ...) are exactly the names FEM constraint
    References and measure_distance sub-elements expect — use this instead of
    guessing which face is which. Lengths in mm, areas in mm².

    Args:
        doc_name: The document containing the object.
        obj_name: The object to interrogate.
        include_edges: Also list edges (type, length, midpoint). Default False.

    Returns:
        Face/edge/vertex counts and a per-face breakdown (Name, surface Type
        like Plane/Cylinder, Area, Centroid, Normal, Orientation). Truncated
        at 200 entries with an explicit note.
    """
    return get_topology_operation(get_freecad_connection(), doc_name, obj_name, include_edges)


@mcp.tool()
def check_printability(
    ctx: Context,
    doc_name: str,
    obj_name: str,
    min_wall_thickness: float | None = None,
    include_overhangs: bool = False,
    build_direction: Literal["X", "Y", "Z"] = "Z",
    max_overhang_deg: float = 45.0,
) -> list[TextContent]:
    """Check an object for basic 3D-print readiness.

    Reports watertightness/manifoldness and an estimated minimum wall thickness.
    Overhang analysis is opt-in (modern printers handle overhangs with generated
    supports).

    Args:
        doc_name: The document containing the object.
        obj_name: The object to check.
        min_wall_thickness: If given (mm), flags when the estimated minimum wall
            is below it.
        include_overhangs: Also report downward-facing overhang faces steeper
            than max_overhang_deg (faces resting on the build plate excluded).
            Off by default.
        build_direction: Build/up axis for overhang analysis ("Z" default).
        max_overhang_deg: Overhang angle from vertical beyond which a face is
            flagged (default 45).

    Returns:
        manifold (is_valid/is_solid/is_watertight/solid_count), wall_thickness
        (sampled estimate — min_mm, samples, below_threshold), and, when opted
        in, overhangs (count, total area, worst angle, faces). Wall thickness is
        an estimate from face-centroid rays, not a guaranteed minimum.
    """
    return check_printability_operation(
        get_freecad_connection(),
        doc_name,
        obj_name,
        min_wall_thickness,
        include_overhangs,
        build_direction,
        max_overhang_deg,
    )


@mcp.tool()
def save_document(
    ctx: Context,
    doc_name: str,
    file_path: str | None = None,
) -> list[TextContent]:
    """Save a document to disk (.FCStd). Documents live only in memory until saved.

    Args:
        doc_name: The document to save.
        file_path: Where to save it (absolute path; "~" is expanded), required
            the first time. Omit to re-save to the document's existing file.

    Returns:
        The saved file path, or an error if the document has never been saved
        and no path was given.
    """
    return save_document_operation(get_freecad_connection(), doc_name, file_path)


@mcp.tool()
def export_document(
    ctx: Context,
    doc_name: str,
    file_path: str,
    object_names: list[str] | None = None,
    linear_deflection: float | None = None,
) -> list[TextContent]:
    """Export objects to a CAD or mesh file; format chosen by file extension.

    Supported: .step/.stp, .iges/.igs, .brep (exact CAD), .stl/.obj/.3mf/.ply/.amf
    (tessellated mesh).

    Args:
        doc_name: The document to export from.
        file_path: Destination path including extension (absolute; "~" expanded).
        object_names: Objects to export. Omit to export all top-level objects
            with geometry (a PartDesign Body exports once, not per-feature).
        linear_deflection: Max chord deviation (mm) for mesh tessellation —
            smaller is finer/larger (e.g. 0.05 for detailed prints, 0.5 for
            coarse). Applies to mesh formats only (STL/OBJ/3MF/PLY/AMF); ignored
            with a note for exact CAD formats. Omit for FreeCAD's default.

    Returns:
        The exported file path, size, and object list.
    """
    return export_document_operation(
        get_freecad_connection(), doc_name, file_path, object_names, linear_deflection
    )


@mcp.tool()
def get_report_log(ctx: Context, tail: int = 100) -> list[TextContent]:
    """Read FreeCAD's Report view console log (messages, warnings, errors).

    This is where recompute errors, addon diagnostics, and async job output
    land. Check it when something behaves unexpectedly or after background
    jobs finish.

    Args:
        tail: Number of trailing lines to return (default 100). Pass 0 (or a
            negative value) to return the entire log — use with care, a long
            session's log can be thousands of lines.

    Returns:
        The last `tail` lines of the report log (or the whole log when tail<=0).
    """
    return get_report_log_operation(get_freecad_connection(), tail)


@mcp.tool(structured_output=False)
def get_parts_list(ctx: Context) -> list[TextContent]:
    """Get the list of parts in the parts library addon.
    """
    return get_parts_list_operation(get_freecad_connection())


@mcp.tool(structured_output=False)
def reload_document(ctx: Context, doc_name: str) -> list[TextContent]:
    """Close and re-open a document to pick up external file changes.

    Use this AFTER the document's .FCStd file has been modified by
    something outside of FreeCAD's GUI process — for example, a
    headless `freecadcmd` script that edited and saved the file. The
    open GUI document is otherwise unaware of on-disk changes; this
    tool closes the stale in-memory copy and reopens the file from
    disk so the GUI shows current geometry.

    Args:
        doc_name: The name of the open document to reload. Must match
            the name shown by ``list_documents``.

    Returns:
        A message confirming the document was reloaded, or describing
        the failure (document not loaded, no associated file, etc).

    Examples:
        ```json
        {
            "doc_name": "chassis"
        }
        ```
    """
    return reload_document_operation(get_freecad_connection(), doc_name)


@mcp.tool(structured_output=False)
def list_documents(ctx: Context) -> list[TextContent]:
    """Get the list of open documents in FreeCAD.

    Returns:
        A list of document names.
    """
    return list_documents_operation(get_freecad_connection())


@mcp.tool(structured_output=False)
def run_fem_analysis(
    ctx: Context,
    doc_name: str,
    analysis_name: str,
    timeout: int = 600,
    include_screenshot: bool = True,
    view_name: ViewName = "Isometric",
) -> list[TextContent | ImageContent]:
    """Run the CalculiX solver on an existing Fem::FemAnalysis container and return summary results.

    Prerequisites in the document:
    - A Part-derived solid (e.g. Part::Box, PartDesign::Body) acting as the geometry.
    - A Fem::AnalysisPython container created via `create_object`.
    - A Fem::MaterialCommon assigned to the geometry, added to the analysis.
    - A Fem::FemMeshGmsh referencing the geometry, added to the analysis (the
      mesh is generated automatically when created via `create_object`).
    - At least one Fem::ConstraintFixed and one Fem::ConstraintForce (or
      ConstraintPressure) bound to faces of the geometry, added to the analysis.

    A SolverCcxTools is auto-created if the analysis has none. See the
    freecad://fem-workflow resource for complete setup recipes.

    The solver runs synchronously on the FreeCAD GUI thread and blocks all
    other RPC calls for its duration; do not fan out parallel requests.

    Returns max and min von Mises stress (MPa), max displacement (mm), node
    count, and the working directory CalculiX wrote to. On failure, returns the
    prerequisite-check or solver error along with the working directory for
    triage.

    Args:
        doc_name: Name of the FreeCAD document.
        analysis_name: Name of the Fem::AnalysisPython object.
        timeout: Seconds to wait for the solver (default 600).
        include_screenshot: Whether to return a screenshot of the model (default True).
            Set to False to save tokens when only the numeric results are needed.
        view_name: The view orientation of the returned screenshot (default "Isometric").
    """
    return run_fem_analysis_operation(
        get_freecad_connection(),
        state.only_text_feedback,
        doc_name,
        analysis_name,
        timeout,
        include_screenshot,
        view_name,
    )


@mcp.prompt()
def asset_creation_strategy() -> str:
    return ASSET_CREATION_STRATEGY


@mcp.resource("freecad://fem-workflow")
def fem_workflow() -> str:
    """Step-by-step FEM analysis recipes (analysis, material, constraints, mesh, solve)."""
    return FEM_WORKFLOW_GUIDE


def _validate_host(value: str) -> str:
    """Validate that *value* is a valid IP address or hostname.

    Used as the ``type`` callback for the ``--host`` argparse argument.
    Raises ``argparse.ArgumentTypeError`` on invalid input.
    """
    import argparse

    import validators

    if validators.ipv4(value) or validators.ipv6(value) or validators.hostname(value):
        return value
    raise argparse.ArgumentTypeError(
        f"Invalid host: '{value}'. Must be a valid IP address or hostname."
    )


def main():
    """Run the MCP server"""
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--only-text-feedback", action="store_true", help="Only return text feedback")
    parser.add_argument("--host", type=_validate_host, default="localhost", help="Host address of the FreeCAD RPC server to connect to (default: localhost)")
    args = parser.parse_args()
    state.only_text_feedback = args.only_text_feedback
    state.rpc_host = args.host
    logger.info(f"Only text feedback: {state.only_text_feedback}")
    logger.info(f"Connecting to FreeCAD RPC server at: {state.rpc_host}")
    mcp.run()
