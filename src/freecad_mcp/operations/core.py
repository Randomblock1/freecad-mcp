import logging
from pathlib import Path
from typing import Any

from mcp.types import ImageContent

from ..freecad_client import FreeCADConnection
from ..responses import ToolResponse, add_screenshot_if_available, json_response, text_response


logger = logging.getLogger("FreeCADMCPserver")


def _assigned_name_note(actual: str, requested: str | None) -> str:
    if requested is None or actual == requested:
        return ""
    return (
        f" (requested '{requested}'; FreeCAD assigned '{actual}' — "
        f"use '{actual}' in subsequent calls)"
    )


def _state_warning_suffix(res: dict[str, Any]) -> str:
    if res.get("warning"):
        return f"\nWARNING: {res['warning']}"
    return ""


def create_document_operation(freecad: FreeCADConnection, name: str) -> ToolResponse:
    try:
        res = freecad.create_document(name)
        if res["success"]:
            actual = res["document_name"]
            return text_response(
                f"Document '{actual}' created successfully"
                + _assigned_name_note(actual, res.get("requested_name", name))
            )
        return text_response(f"Failed to create document: {res['error']}")
    except Exception as e:
        logger.error(f"Failed to create document: {str(e)}")
        return text_response(f"Failed to create document: {str(e)}")


def create_object_operation(
    freecad: FreeCADConnection,
    only_text_feedback: bool,
    doc_name: str,
    obj_type: str,
    obj_name: str,
    analysis_name: str | None = None,
    obj_properties: dict[str, Any] | None = None,
    include_screenshot: bool = True,
    view_name: str = "Isometric",
) -> ToolResponse:
    try:
        obj_data = {
            "Name": obj_name,
            "Type": obj_type,
            "Properties": obj_properties or {},
            "Analysis": analysis_name,
        }
        res = freecad.create_object(doc_name, obj_data)
        if res["success"]:
            actual = res["object_name"]
            response = text_response(
                f"Object '{actual}' created successfully"
                + _assigned_name_note(actual, res.get("requested_name", obj_name))
                + _state_warning_suffix(res)
            )
        else:
            return text_response(f"Failed to create object: {res['error']}")
        skip_screenshot = only_text_feedback or not include_screenshot
        screenshot = None if skip_screenshot else freecad.get_active_screenshot(view_name)
        return add_screenshot_if_available(response, screenshot, skip_screenshot)
    except Exception as e:
        logger.error(f"Failed to create object: {str(e)}")
        return text_response(f"Failed to create object: {str(e)}")


def edit_object_operation(
    freecad: FreeCADConnection,
    only_text_feedback: bool,
    doc_name: str,
    obj_name: str,
    obj_properties: dict[str, Any],
    include_screenshot: bool = True,
    view_name: str = "Isometric",
) -> ToolResponse:
    try:
        res = freecad.edit_object(doc_name, obj_name, {"Properties": obj_properties})
        if res["success"]:
            response = text_response(
                f"Object '{res['object_name']}' edited successfully"
                + _state_warning_suffix(res)
            )
        else:
            return text_response(f"Failed to edit object: {res['error']}")
        skip_screenshot = only_text_feedback or not include_screenshot
        screenshot = None if skip_screenshot else freecad.get_active_screenshot(view_name)
        return add_screenshot_if_available(response, screenshot, skip_screenshot)
    except Exception as e:
        logger.error(f"Failed to edit object: {str(e)}")
        return text_response(f"Failed to edit object: {str(e)}")


def delete_object_operation(
    freecad: FreeCADConnection,
    only_text_feedback: bool,
    doc_name: str,
    obj_name: str,
    include_screenshot: bool = True,
    view_name: str = "Isometric",
) -> ToolResponse:
    try:
        res = freecad.delete_object(doc_name, obj_name)
        if res["success"]:
            response = text_response(f"Object '{res['object_name']}' deleted successfully")
        else:
            return text_response(f"Failed to delete object: {res['error']}")
        skip_screenshot = only_text_feedback or not include_screenshot
        screenshot = None if skip_screenshot else freecad.get_active_screenshot(view_name)
        return add_screenshot_if_available(response, screenshot, skip_screenshot)
    except Exception as e:
        logger.error(f"Failed to delete object: {str(e)}")
        return text_response(f"Failed to delete object: {str(e)}")


def _execute_code(
    freecad: FreeCADConnection,
    only_text_feedback: bool,
    code: str,
    source: str = "Code",
    include_screenshot: bool = True,
    view_name: str = "Isometric",
) -> ToolResponse:
    try:
        res = freecad.execute_code(code)
        if res["success"]:
            response = text_response(f"{source} executed successfully: {res['message']}")
            # Only attempt screenshot when code completed and screenshots are wanted.
            # Skipping on failure avoids a second hanging call while the worker thread
            # may still be running.
            skip_screenshot = only_text_feedback or not include_screenshot
            screenshot = None if skip_screenshot else freecad.get_active_screenshot(view_name)
            return add_screenshot_if_available(response, screenshot, skip_screenshot)
        parts = [f"Failed to execute code: {res.get('error', 'unknown error')}"]
        if res.get("traceback"):
            parts.append(res["traceback"])
        if res.get("stdout"):
            parts.append(f"--- output before error ---\n{res['stdout']}")
        return text_response("\n".join(parts))
    except Exception as e:
        logger.error(f"Failed to execute code: {str(e)}")
        return text_response(f"Failed to execute code: {str(e)}")


def execute_code_operation(
    freecad: FreeCADConnection,
    only_text_feedback: bool,
    code: str,
    include_screenshot: bool = True,
    view_name: str = "Isometric",
) -> ToolResponse:
    return _execute_code(
        freecad,
        only_text_feedback,
        code,
        include_screenshot=include_screenshot,
        view_name=view_name,
    )


def execute_code_from_file_operation(
    freecad: FreeCADConnection,
    only_text_feedback: bool,
    file_path: str,
) -> ToolResponse:
    try:
        path = Path(file_path).expanduser()
    except RuntimeError as e:
        return text_response(
            f"Failed to execute code from file: cannot expand '~' in '{file_path}': {e}"
        )
    if not path.is_absolute():
        return text_response(
            f"Failed to execute code from file: path must be absolute, got '{file_path}'"
        )
    try:
        # utf-8-sig transparently strips a UTF-8 BOM, which would otherwise
        # reach exec() as U+FEFF and raise SyntaxError.
        code = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return text_response(
            f"Failed to execute code from file: file not found: '{path}'. "
            "The file must exist on the machine running the MCP server."
        )
    except UnicodeDecodeError as e:
        return text_response(
            f"Failed to execute code from file: '{path}' is not valid UTF-8 text: {e}"
        )
    except (OSError, ValueError) as e:
        return text_response(f"Failed to execute code from file: cannot read '{path}': {e}")
    if not code.strip():
        return text_response(f"Failed to execute code from file: '{path}' is empty")
    return _execute_code(freecad, only_text_feedback, code, source=f"Code from '{path}'")


def execute_code_async_operation(
    freecad: FreeCADConnection,
    code: str,
) -> ToolResponse:
    try:
        res = freecad.execute_code_async(code)
        if res["success"]:
            job_id = res.get("job_id", "unknown")
            return text_response(
                f"Background job '{job_id}' started.\n"
                f"Poll get_async_status(job_id='{job_id}') for completion, errors, "
                "and tracebacks. Printed output goes to FreeCAD's console "
                "(readable via get_report_log)."
            )
        return text_response(f"Failed to start async execution: {res.get('error', 'unknown')}")
    except Exception as e:
        logger.error(f"Failed to start async code execution: {str(e)}")
        return text_response(f"Failed to start async code execution: {str(e)}")


def get_async_status_operation(
    freecad: FreeCADConnection,
    job_id: str | None = None,
) -> ToolResponse:
    try:
        res = freecad.get_async_status(job_id)
        if res.get("success") is False:
            parts = [res.get("error", "unknown error")]
            if res.get("known_job_ids") is not None:
                parts.append(f"Known job ids: {res['known_job_ids']}")
            return text_response(" ".join(parts))
        return json_response(res)
    except Exception as e:
        logger.error(f"Failed to get async status: {str(e)}")
        return text_response(f"Failed to get async status: {str(e)}")


def get_view_operation(
    freecad: FreeCADConnection,
    view_name: str,
    width: int | None = None,
    height: int | None = None,
    focus_object: str | None = None,
) -> ToolResponse:
    screenshot = freecad.get_active_screenshot(view_name, width, height, focus_object)
    if screenshot is not None:
        return [ImageContent(type="image", data=screenshot, mimeType="image/png")]
    return text_response("Cannot get screenshot in the current view type (such as TechDraw or Spreadsheet)")


def insert_part_from_library_operation(
    freecad: FreeCADConnection,
    only_text_feedback: bool,
    relative_path: str,
    include_screenshot: bool = True,
    view_name: str = "Isometric",
) -> ToolResponse:
    try:
        res = freecad.insert_part_from_library(relative_path)
        if res["success"]:
            response = text_response(f"Part inserted from library: {res['message']}")
        else:
            return text_response(f"Failed to insert part from library: {res['error']}")
        skip_screenshot = only_text_feedback or not include_screenshot
        screenshot = None if skip_screenshot else freecad.get_active_screenshot(view_name)
        return add_screenshot_if_available(response, screenshot, skip_screenshot)
    except Exception as e:
        logger.error(f"Failed to insert part from library: {str(e)}")
        return text_response(f"Failed to insert part from library: {str(e)}")


def _lookup_error_response(res: dict[str, Any], context: str) -> ToolResponse:
    """Failed lookup: name what *is* available, and skip the screenshot."""
    parts = [f"Failed to {context}: {res.get('error', 'unknown error')}"]
    if res.get("open_documents") is not None:
        parts.append(f"Open documents: {res['open_documents']}")
    if res.get("available_objects") is not None:
        parts.append(f"Available objects: {res['available_objects']}")
    return text_response(" ".join(parts))


def get_objects_operation(
    freecad: FreeCADConnection,
    only_text_feedback: bool,
    doc_name: str,
    include_screenshot: bool = True,
    view_name: str = "Isometric",
    detail: str = "summary",
) -> ToolResponse:
    try:
        res = freecad.get_objects(doc_name, detail)
        if isinstance(res, dict) and res.get("success") is False:
            return _lookup_error_response(res, "get objects")
        response = json_response(res)
        skip_screenshot = only_text_feedback or not include_screenshot
        screenshot = None if skip_screenshot else freecad.get_active_screenshot(view_name)
        return add_screenshot_if_available(response, screenshot, skip_screenshot)
    except Exception as e:
        logger.error(f"Failed to get objects: {str(e)}")
        return text_response(f"Failed to get objects: {str(e)}")


def get_object_operation(
    freecad: FreeCADConnection,
    only_text_feedback: bool,
    doc_name: str,
    obj_name: str,
    include_screenshot: bool = True,
    view_name: str = "Isometric",
) -> ToolResponse:
    try:
        res = freecad.get_object(doc_name, obj_name)
        if isinstance(res, dict) and res.get("success") is False:
            return _lookup_error_response(res, "get object")
        response = json_response(res)
        skip_screenshot = only_text_feedback or not include_screenshot
        screenshot = None if skip_screenshot else freecad.get_active_screenshot(view_name)
        return add_screenshot_if_available(response, screenshot, skip_screenshot)
    except Exception as e:
        logger.error(f"Failed to get object: {str(e)}")
        return text_response(f"Failed to get object: {str(e)}")


def measure_distance_operation(
    freecad: FreeCADConnection,
    doc_name: str,
    object1: str,
    object2: str,
    sub1: str | None = None,
    sub2: str | None = None,
) -> ToolResponse:
    try:
        res = freecad.measure_distance(doc_name, object1, object2, sub1, sub2)
        if res.get("success") is False:
            return _lookup_error_response(res, "measure distance")
        return json_response(res)
    except Exception as e:
        logger.error(f"Failed to measure distance: {str(e)}")
        return text_response(f"Failed to measure distance: {str(e)}")


def get_topology_operation(
    freecad: FreeCADConnection,
    doc_name: str,
    obj_name: str,
    include_edges: bool = False,
) -> ToolResponse:
    try:
        res = freecad.get_topology(doc_name, obj_name, include_edges)
        if res.get("success") is False:
            return _lookup_error_response(res, "get topology")
        return json_response(res)
    except Exception as e:
        logger.error(f"Failed to get topology: {str(e)}")
        return text_response(f"Failed to get topology: {str(e)}")


def save_document_operation(
    freecad: FreeCADConnection,
    doc_name: str,
    file_path: str | None = None,
) -> ToolResponse:
    try:
        res = freecad.save_document(doc_name, file_path)
        if res.get("success") is False:
            return _lookup_error_response(res, "save document")
        return text_response(f"Document '{res['document_name']}' saved to {res['file_path']}")
    except Exception as e:
        logger.error(f"Failed to save document: {str(e)}")
        return text_response(f"Failed to save document: {str(e)}")


def export_document_operation(
    freecad: FreeCADConnection,
    doc_name: str,
    file_path: str,
    object_names: list[str] | None = None,
) -> ToolResponse:
    try:
        res = freecad.export_document(doc_name, file_path, object_names)
        if res.get("success") is False:
            return _lookup_error_response(res, "export document")
        return text_response(
            f"Exported {res['exported_objects']} to {res['file_path']} "
            f"({res['file_size_bytes']} bytes)"
        )
    except Exception as e:
        logger.error(f"Failed to export document: {str(e)}")
        return text_response(f"Failed to export document: {str(e)}")


def get_report_log_operation(
    freecad: FreeCADConnection,
    tail: int = 100,
) -> ToolResponse:
    try:
        res = freecad.get_report_log(tail)
        if res.get("success") is False:
            return _lookup_error_response(res, "get report log")
        return text_response(
            f"FreeCAD report log (last {res['returned_lines']} of "
            f"{res['total_lines']} lines):\n{res['log']}"
        )
    except Exception as e:
        logger.error(f"Failed to get report log: {str(e)}")
        return text_response(f"Failed to get report log: {str(e)}")


def get_parts_list_operation(freecad: FreeCADConnection) -> ToolResponse:
    try:
        parts = freecad.get_parts_list()
    except Exception as e:
        logger.error(f"Failed to get parts list: {str(e)}")
        return text_response(f"Failed to get parts list: {str(e)}")
    if parts:
        return json_response(parts)
    return text_response("No parts found in the parts library. You must add parts_library addon.")


def list_documents_operation(freecad: FreeCADConnection) -> ToolResponse:
    return json_response(freecad.list_documents())


def run_fem_analysis_operation(
    freecad: FreeCADConnection,
    only_text_feedback: bool,
    doc_name: str,
    analysis_name: str,
    timeout: int = 600,
    include_screenshot: bool = True,
    view_name: str = "Isometric",
) -> ToolResponse:
    try:
        res = freecad.run_fem_analysis(doc_name, analysis_name, timeout)
        if res.get("success"):
            def fmt(v, unit):
                return f"{v:.4g} {unit}" if isinstance(v, (int, float)) else f"unavailable ({unit})"
            skip_screenshot = only_text_feedback or not include_screenshot
            screenshot = None if skip_screenshot else freecad.get_active_screenshot(view_name)
            response = json_response({
                "summary": (
                    f"FEM analysis '{analysis_name}' solved. "
                    f"max von Mises = {fmt(res.get('max_von_mises_MPa'), 'MPa')}, "
                    f"max displacement = {fmt(res.get('max_displacement_mm'), 'mm')} "
                    f"({res.get('node_count')} nodes)."
                ),
                **res,
            })
            return add_screenshot_if_available(response, screenshot, skip_screenshot)
        return json_response({
            "summary": f"FEM analysis '{analysis_name}' failed: {res.get('error')}",
            **res,
        })
    except Exception as e:
        logger.error(f"Failed to run FEM analysis: {str(e)}")
        return text_response(f"Failed to run FEM analysis: {str(e)}")


def reload_document_operation(
    freecad: FreeCADConnection,
    doc_name: str,
) -> ToolResponse:
    """Close and re-open a document so the GUI picks up external file
    changes (e.g. headless edits via `freecadcmd`).
    """
    try:
        res = freecad.reload_document(doc_name)
        if res.get("success"):
            return text_response(
                f"Document '{res['document_name']}' reloaded from disk."
            )
        return text_response(f"Failed to reload document: {res.get('error')}")
    except Exception as e:
        logger.error(f"Failed to reload document: {str(e)}")
        return text_response(f"Failed to reload document: {str(e)}")

