import FreeCAD
import FreeCADGui

import contextlib
import base64
import io
import os
import tempfile
import threading
from typing import Any

from PySide import QtCore

from rpc_server.code_exec import (
    async_job_status,
    complete_async_job,
    exec_namespace,
    format_exec_error,
    start_async_job,
    xml_safe,
)
from rpc_server.commands import register_commands, schedule_toggle_sync
from rpc_server.fem_executor import run_fem_analysis as _run_fem_analysis
from rpc_server.gui_dispatch import (
    cleanup_waker,
    dispatch_to_gui,
    init_waker,
    process_gui_tasks,
    request_shutdown,
)
from rpc_server.document_io import export_document as _export_document
from rpc_server.document_io import save_document as _save_document
from rpc_server.document_query import query_object, query_objects
from rpc_server.geometry_tools import get_topology as _get_topology
from rpc_server.geometry_tools import measure_distance as _measure_distance
from rpc_server.ip_filter import FilteredXMLRPCServer, validate_allowed_ips
from rpc_server.object_factory import create_object_gui, recompute_state_warning
from rpc_server.parts_library import get_parts_list, insert_part_from_library
from rpc_server.property_mapper import Object, set_object_property
from rpc_server.settings import load_settings, save_settings
from rpc_server.view_manager import save_active_screenshot

rpc_server_thread = None
rpc_server_instance = None


def _ok(res) -> bool:
    """True when a GUI-thread handler returned success."""
    return res is True


def _ok_dict(res) -> bool:
    """True when a GUI-thread handler returned a structured success dict."""
    return isinstance(res, dict) and bool(res.get("success"))


def _err(res) -> dict:
    """Convert any non-True result (error string or timeout dict) to a failure dict."""
    if isinstance(res, dict):
        return res
    return {"success": False, "error": str(res)}


class FreeCADRPC:
    """RPC server for FreeCAD"""
    TIMEOUT = 60               # generous wait for GUI thread to become free
    EXECUTE_CODE_TIMEOUT = 90  # GUI-thread execution; use execute_code_async for heavy OCCT ops

    def ping(self):
        return True

    def create_document(self, name="New_Document"):
        res = dispatch_to_gui(lambda: self._create_document_gui(name))
        if _ok_dict(res):
            return res
        return _err(res)

    def create_object(self, doc_name, obj_data: dict[str, Any]):
        obj = Object(
            name=obj_data.get("Name", "New_Object"),
            type=obj_data["Type"],
            analysis=obj_data.get("Analysis", None),
            properties=obj_data.get("Properties", {}),
        )
        res = dispatch_to_gui(lambda: self._create_object_gui(doc_name, obj))
        if _ok_dict(res):
            return res
        return _err(res)

    def edit_object(self, doc_name: str, obj_name: str, properties: dict[str, Any]) -> dict[str, Any]:
        obj = Object(
            name=obj_name,
            properties=properties.get("Properties", {}),
        )
        res = dispatch_to_gui(lambda: self._edit_object_gui(doc_name, obj))
        if _ok_dict(res):
            return res
        return _err(res)

    def delete_object(self, doc_name: str, obj_name: str):
        res = dispatch_to_gui(lambda: self._delete_object_gui(doc_name, obj_name))
        if _ok(res):
            return {"success": True, "object_name": obj_name}
        return _err(res)


    def reload_document(self, doc_name: str) -> dict[str, Any]:
        """Close and re-open a document by name to pick up external file
        changes (e.g. edits made by another process such as `freecadcmd`
        running headlessly). Returns success once the new document is
        loaded from disk.
        """
        res = dispatch_to_gui(lambda: self._reload_document_gui(doc_name))
        if _ok(res):
            return {"success": True, "document_name": doc_name}
        return _err(res)

    def run_fem_analysis(self, doc_name: str, analysis_name: str, timeout: int = 600) -> dict[str, Any]:
        """Run the CalculiX solver on an existing Fem::FemAnalysis and return summary results."""
        try:
            timeout_s = int(timeout)
        except (TypeError, ValueError):
            return {"success": False, "error": f"invalid timeout: {timeout!r}"}
        res = dispatch_to_gui(
            lambda: self._run_fem_analysis_gui(doc_name, analysis_name),
            timeout=timeout_s,
        )
        if isinstance(res, dict):
            return res
        return {"success": False, "error": str(res)}

    def execute_code_async(self, code: str) -> dict[str, Any]:
        """Start code execution in a background thread and return a job id.

        Use for long-running OCCT operations (fuse/cut/loft) that would otherwise
        exceed the MCP timeout. Poll get_async_status(job_id) for completion,
        errors, and tracebacks.
        """
        def _set_status(msg):
            dispatch_to_gui(lambda: FreeCADGui.getMainWindow().statusBar().showMessage(msg))

        def _clear_status():
            dispatch_to_gui(lambda: FreeCADGui.getMainWindow().statusBar().clearMessage())

        job_id = start_async_job(code)

        def worker() -> None:
            # NOTE: we do NOT redirect sys.stdout here. contextlib.redirect_stdout
            # swaps stdout process-wide, not per-thread, so it would race with the
            # GUI thread and other concurrent work. Background code should report
            # via FreeCAD.Console (which is thread-safe) instead.
            error = None
            try:
                exec(code, exec_namespace())
                FreeCAD.Console.PrintMessage(f"Async job {job_id} completed.\n")
            except BaseException as e:
                # BaseException, not Exception: user code that calls sys.exit()
                # raises SystemExit, which threading otherwise swallows silently
                # and which would leave the job marked "completed" (false success).
                error = format_exec_error(e)
                FreeCAD.Console.PrintError(
                    f"Async job {job_id} failed: {error['error']}\n"
                    f"{error.get('traceback', '')}\n"
                )
            finally:
                complete_async_job(job_id, error)
                _clear_status()

        _set_status("MCP: running background task…")
        threading.Thread(target=worker, daemon=True).start()
        return {
            "success": True,
            "job_id": job_id,
            "message": "Code execution started in background.",
        }

    def get_async_status(self, job_id: str | None = None) -> dict[str, Any]:
        """Status of one background job (or all jobs when job_id is None)."""
        return async_job_status(job_id)

    def execute_code(self, code: str) -> dict[str, Any]:
        """Execute Python code on the GUI thread and wait for the result.

        Runs on the GUI thread so that FreeCAD document operations
        (addObject, recompute, save) are safe and correctly ordered.
        Use execute_code_async for heavy OCCT boolean ops (fuse/cut)
        that would block the GUI thread too long.

        On failure, returns the exception, the traceback frames pointing into
        the submitted code (line numbers refer to the submitted string), and
        whatever the code printed before dying.
        """
        output_buffer = io.StringIO()

        def task():
            try:
                with contextlib.redirect_stdout(output_buffer):
                    exec(code, exec_namespace())
                return True
            except Exception as e:
                return {**format_exec_error(e), "stdout": xml_safe(output_buffer.getvalue())}

        res = dispatch_to_gui(task, timeout=self.EXECUTE_CODE_TIMEOUT)
        if _ok(res):
            FreeCAD.Console.PrintMessage("Python code executed successfully.\n")
            return {
                "success": True,
                "message": "Python code executed successfully.\nOutput: "
                + xml_safe(output_buffer.getvalue()),
            }
        # Log the offending code (truncated) to make errors traceable
        code_preview = code if len(code) <= 800 else code[:800] + "\n...(truncated)"
        err_text = res.get("error") if isinstance(res, dict) else res
        FreeCAD.Console.PrintError(
            f"Error executing Python code: {err_text}\n"
            f"--- code ---\n{code_preview}\n--- end ---\n"
        )
        return _err(res)

    def get_objects(self, doc_name, detail="summary"):
        return query_objects(doc_name, detail)

    def get_object(self, doc_name, obj_name):
        return query_object(doc_name, obj_name)

    def measure_distance(self, doc_name, object1, object2, sub1=None, sub2=None):
        """Read-only OCCT distance query; safe on the RPC thread (get_objects precedent)."""
        return _measure_distance(doc_name, object1, object2, sub1, sub2)

    def get_topology(self, doc_name, obj_name, include_edges=False):
        """Read-only topology breakdown; safe on the RPC thread."""
        return _get_topology(doc_name, obj_name, include_edges)

    def save_document(self, doc_name, file_path=None):
        res = dispatch_to_gui(lambda: _save_document(doc_name, file_path))
        if _ok_dict(res):
            return res
        return _err(res)

    def export_document(self, doc_name, file_path, object_names=None):
        # Meshing/STEP-writing large models can be slow; allow a longer wait.
        res = dispatch_to_gui(
            lambda: _export_document(doc_name, file_path, object_names),
            timeout=300,
        )
        if _ok_dict(res):
            return res
        return _err(res)

    def get_report_log(self, tail=100):
        try:
            tail = max(0, int(tail))
        except (TypeError, ValueError):
            return {"success": False, "error": f"invalid tail: {tail!r}"}
        res = dispatch_to_gui(lambda: self._get_report_log_gui(tail))
        if _ok_dict(res):
            return res
        return _err(res)

    def _get_report_log_gui(self, tail: int):
        from PySide import QtWidgets

        mw = FreeCADGui.getMainWindow()
        dock = mw.findChild(QtWidgets.QDockWidget, "Report view")
        if dock is None:
            return {
                "success": False,
                "error": "Report view dock not found in this FreeCAD build.",
            }
        text_widget = dock.findChild(QtWidgets.QPlainTextEdit) or dock.findChild(
            QtWidgets.QTextEdit
        )
        if text_widget is None:
            return {"success": False, "error": "Report view text widget not found."}
        lines = text_widget.toPlainText().splitlines()
        total = len(lines)
        if tail and total > tail:
            lines = lines[-tail:]
        return {
            "success": True,
            # Report-view text is arbitrary (user PrintMessage/PrintError, solver
            # output); strip XML-illegal control chars so the response marshals.
            "log": xml_safe("\n".join(lines)),
            "total_lines": total,
            "returned_lines": len(lines),
        }

    def insert_part_from_library(self, relative_path):
        res = dispatch_to_gui(lambda: self._insert_part_from_library(relative_path))
        if _ok(res):
            return {"success": True, "message": "Part inserted from library."}
        return _err(res)

    def list_documents(self):
        return list(FreeCAD.listDocuments().keys())

    def get_parts_list(self):
        return get_parts_list()

    def get_active_screenshot(
        self,
        view_name: str = "Isometric",
        width: int | None = None,
        height: int | None = None,
        focus_object: str | None = None,
    ) -> str:
        """Get a screenshot of the active view as a base64-encoded PNG string.

        Returns None if the active view does not support screenshots
        (e.g., TechDraw or Spreadsheet workbench).
        """
        fd, tmp_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)

        def task():
            try:
                active_view = FreeCADGui.ActiveDocument.ActiveView
            except Exception:
                return False
            if active_view is None or not hasattr(active_view, "saveImage"):
                view_type = type(active_view).__name__ if active_view is not None else "None"
                FreeCAD.Console.PrintWarning(
                    f"MCP RPC: view type '{view_type}' does not support screenshots\n"
                )
                return False
            return save_active_screenshot(tmp_path, view_name, width, height, focus_object)

        try:
            res = dispatch_to_gui(task)
            if _ok(res):
                with open(tmp_path, "rb") as f:
                    return base64.b64encode(f.read()).decode("utf-8")
            if res is False:
                return None
            FreeCAD.Console.PrintWarning(f"MCP RPC: screenshot failed: {res}\n")
            return None
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def _create_document_gui(self, name):
        doc = FreeCAD.newDocument(name)
        doc.recompute()
        FreeCAD.Console.PrintMessage(f"Document '{doc.Name}' created via RPC.\n")
        # doc.Name is what FreeCAD actually assigned (sanitized, deduplicated);
        # reporting the requested name instead would corrupt the caller's model.
        return {"success": True, "document_name": doc.Name, "requested_name": name}

    def _create_object_gui(self, doc_name, obj: Object):
        return create_object_gui(doc_name, obj)

    def _edit_object_gui(self, doc_name: str, obj: Object):
        try:
            doc = FreeCAD.getDocument(doc_name)
        except Exception:
            FreeCAD.Console.PrintError(f"Document '{doc_name}' not found.\n")
            return f"Document '{doc_name}' not found.\n"

        obj_ins = doc.getObject(obj.name)
        if not obj_ins:
            FreeCAD.Console.PrintError(f"Object '{obj.name}' not found in document '{doc_name}'.\n")
            return f"Object '{obj.name}' not found in document '{doc_name}'.\n"

        try:
            set_object_property(doc, obj_ins, obj.properties)
            doc.recompute()
            FreeCAD.Console.PrintMessage(f"Object '{obj.name}' updated via RPC.\n")
            return {
                "success": True,
                "object_name": obj_ins.Name,
                **recompute_state_warning(obj_ins),
            }
        except Exception as e:
            return str(e)

    def _run_fem_analysis_gui(self, doc_name: str, analysis_name: str):
        return _run_fem_analysis(doc_name, analysis_name)

    def _delete_object_gui(self, doc_name: str, obj_name: str):
        try:
            doc = FreeCAD.getDocument(doc_name)
        except Exception:
            FreeCAD.Console.PrintError(f"Document '{doc_name}' not found.\n")
            return f"Document '{doc_name}' not found.\n"

        try:
            doc.removeObject(obj_name)
            doc.recompute()
            FreeCAD.Console.PrintMessage(f"Object '{obj_name}' deleted via RPC.\n")
            return True
        except Exception as e:
            return str(e)


    def _reload_document_gui(self, doc_name: str):
        if doc_name not in FreeCAD.listDocuments():
            return f"Document '{doc_name}' is not loaded."
        doc = FreeCAD.getDocument(doc_name)
        file_path = doc.FileName
        if not file_path:
            return (
                f"Document '{doc_name}' has no file on disk "
                "(unsaved scratch document); nothing to reload from."
            )
        if not os.path.exists(file_path):
            return f"File for '{doc_name}' not found at {file_path!r}."
        # Close, then reopen from the same file. Reopen preserves the
        # original document name when the file was previously saved
        # under that name.
        FreeCAD.closeDocument(doc_name)
        FreeCAD.openDocument(file_path)
        FreeCAD.Console.PrintMessage(
            f"Document '{doc_name}' reloaded from '{file_path}' via RPC.\n"
        )
        return True

    def _insert_part_from_library(self, relative_path):
        try:
            insert_part_from_library(relative_path)
            return True
        except Exception as e:
            return str(e)

    def _save_active_screenshot(
        self,
        save_path: str,
        view_name: str = "Isometric",
        width: int | None = None,
        height: int | None = None,
        focus_object: str | None = None,
    ):
        return save_active_screenshot(save_path, view_name, width, height, focus_object)


def start_rpc_server(port=9875):
    global rpc_server_thread, rpc_server_instance

    if rpc_server_instance:
        return "RPC Server already running."

    settings = load_settings()
    remote_enabled = settings.get("remote_enabled", False)
    allowed_ips = settings.get("allowed_ips", "127.0.0.1")

    if remote_enabled:
        host = "0.0.0.0"
    else:
        host = "127.0.0.1"

    rpc_server_instance = FilteredXMLRPCServer(
        (host, port), allowed_ips_str=allowed_ips, allow_none=True, logRequests=False
    )
    rpc_server_instance.register_instance(FreeCADRPC())

    def server_loop():
        FreeCAD.Console.PrintMessage(f"RPC Server started at {host}:{port}\n")
        if remote_enabled:
            FreeCAD.Console.PrintMessage(f"Remote connections enabled. Allowed IPs: {allowed_ips}\n")
        rpc_server_instance.serve_forever()

    rpc_server_thread = threading.Thread(target=server_loop, daemon=True)
    rpc_server_thread.start()

    init_waker()
    QtCore.QTimer.singleShot(500, process_gui_tasks)

    msg = f"RPC Server started at {host}:{port}."
    if remote_enabled:
        msg += f" Allowed IPs: {allowed_ips}"
    return msg


def stop_rpc_server():
    global rpc_server_instance, rpc_server_thread

    if rpc_server_instance:
        request_shutdown()
        cleanup_waker()
        rpc_server_instance.shutdown()
        rpc_server_thread.join()
        rpc_server_instance = None
        rpc_server_thread = None
        FreeCAD.Console.PrintMessage("RPC Server stopped.\n")
        return "RPC Server stopped."

    return "RPC Server was not running."


register_commands()
schedule_toggle_sync()
