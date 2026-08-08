"""Persistent user-code namespace, exec error formatting, and the async job
registry backing ``execute_code`` / ``execute_code_async`` / ``get_async_status``.

GUI-free so it stays importable under ``freecadcmd`` for headless tests.
"""

import contextlib
import re
import threading
import time
import traceback
import uuid
from typing import Any

import FreeCAD

# XML 1.0 forbids most control characters (everything below 0x20 except tab,
# LF, CR). The stdlib XML-RPC marshaller does NOT strip them, so an ESC from an
# ANSI colour code or other control byte in user output / the report log would
# make the whole response unparseable on the client. Replace them with the
# Unicode replacement char so text round-trips safely.
_XML_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def xml_safe(text: str) -> str:
    if not isinstance(text, str):
        return text
    return _XML_ILLEGAL.sub("�", text)

# User code executes in this dict, NOT in any server module's globals: scripts
# assigning names like ``os`` or ``io`` must not clobber RPC internals. It
# persists for the FreeCAD session, so helpers and intermediate results defined
# in one execute_code call remain available to later calls (and to
# execute_code_async workers, which share it).
_EXEC_NAMESPACE: dict[str, Any] = {}


@contextlib.contextmanager
def rollback_documents():
    """Roll back every document change made inside the block (dry-run).

    Opens an undo transaction on each currently-open document and aborts it on
    exit, so object creations/edits/deletions are reverted; documents created
    inside the block are closed. Only DOCUMENT state rolls back — file writes
    (save/export) and exec-namespace variables persist.
    """
    docs_before = set(FreeCAD.listDocuments().keys())
    for name in docs_before:
        d = FreeCAD.getDocument(name)
        d.UndoMode = 1  # freecadcmd docs may start with undo disabled
        d.openTransaction("MCP dry-run")
    try:
        yield
    finally:
        for name in docs_before:
            if name in FreeCAD.listDocuments():
                FreeCAD.getDocument(name).abortTransaction()
        for name in list(FreeCAD.listDocuments().keys()):
            if name not in docs_before:
                FreeCAD.closeDocument(name)


def exec_namespace() -> dict[str, Any]:
    if not _EXEC_NAMESPACE:
        _EXEC_NAMESPACE.update({
            "__name__": "__freecad_mcp_exec__",
            "FreeCAD": FreeCAD,
            "App": FreeCAD,
        })
        try:
            import FreeCADGui

            _EXEC_NAMESPACE["FreeCADGui"] = FreeCADGui
            _EXEC_NAMESPACE["Gui"] = FreeCADGui
        except ImportError:
            pass  # headless (freecadcmd)
    return _EXEC_NAMESPACE


def format_exec_error(e: BaseException) -> dict[str, Any]:
    """Failure dict with the traceback frames that point into the user's code.

    ``exec(code)`` compiles with filename ``<string>``, so those frames carry
    the line numbers of the submitted script; server-side frames are noise.
    """
    detail: dict[str, Any] = {"success": False, "error": xml_safe(f"{type(e).__name__}: {e}")}
    if isinstance(e, SyntaxError) and e.lineno is not None:
        detail["traceback"] = xml_safe(
            f'  File "<code>", line {e.lineno}\n    {(e.text or "").rstrip().lstrip()}'
        )
        return detail
    frames = traceback.extract_tb(e.__traceback__)
    user_frames = [f for f in frames if f.filename == "<string>"] or frames[-2:]
    detail["traceback"] = xml_safe("".join(traceback.format_list(user_frames)).rstrip())
    return detail


_async_jobs: dict[str, dict[str, Any]] = {}
_async_jobs_lock = threading.Lock()
_MAX_ASYNC_JOBS = 50


def start_async_job(code: str) -> str:
    job_id = uuid.uuid4().hex[:12]
    with _async_jobs_lock:
        if len(_async_jobs) >= _MAX_ASYNC_JOBS:
            finished = sorted(
                (j for j in _async_jobs.values() if j["status"] != "running"),
                key=lambda j: j["started_at"],
            )
            for j in finished[: len(_async_jobs) - _MAX_ASYNC_JOBS + 1]:
                _async_jobs.pop(j["job_id"], None)
        _async_jobs[job_id] = {
            "job_id": job_id,
            "status": "running",
            "started_at": time.time(),
            "code_preview": code[:160],
        }
    return job_id


def complete_async_job(job_id: str, error: dict[str, Any] | None = None) -> None:
    with _async_jobs_lock:
        job = _async_jobs.get(job_id)
        if job is None:
            return
        job["finished_at"] = time.time()
        if error is None:
            job["status"] = "completed"
        else:
            job["status"] = "failed"
            job["error"] = error.get("error")
            if error.get("traceback"):
                job["traceback"] = error["traceback"]


def async_job_status(job_id: str | None = None) -> dict[str, Any]:
    with _async_jobs_lock:
        if job_id is None:
            jobs = [dict(j) for j in _async_jobs.values()]
            jobs.sort(key=lambda j: j["started_at"], reverse=True)
            for j in jobs:
                _add_runtime(j)
            return {"success": True, "jobs": jobs}
        job = _async_jobs.get(job_id)
        if job is None:
            return {
                "success": False,
                "error": f"Unknown job id '{job_id}'.",
                "known_job_ids": list(_async_jobs),
            }
        job = dict(job)
    _add_runtime(job)
    return {"success": True, **job}


def _add_runtime(job: dict[str, Any]) -> None:
    if job["status"] == "running":
        job["runtime_s"] = round(time.time() - job["started_at"], 1)


def running_async_jobs() -> list[str]:
    """Ids of async jobs still executing user code.

    Async workers mutate documents from a daemon thread, outside the GUI-thread
    dispatch queue that serializes everything else. Operations that open an undo
    transaction and then abort it (dry-run execute_code, section screenshots)
    would revert whatever such a worker committed in the meantime, so they check
    this first and refuse rather than silently destroy the worker's changes.
    """
    with _async_jobs_lock:
        return [j["job_id"] for j in _async_jobs.values() if j["status"] == "running"]


def busy_with_async_jobs(operation: str) -> str | None:
    """Error message if ``operation`` must not run right now, else None."""
    busy = running_async_jobs()
    if not busy:
        return None
    return (
        f"Refusing to run {operation} while background job(s) {', '.join(busy)} "
        "are still running: it rolls back document changes by aborting an undo "
        "transaction, which would also discard whatever those jobs have written. "
        "Poll get_async_status until they finish, then retry."
    )
