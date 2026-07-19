# Fork Audit Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement all 18 audited improvements to freecad-mcp on the fork's main: eliminate silent failures, cut per-call token cost, add missing capability tools, and fix guidance surfaces.

**Architecture:** Two halves change together: the FreeCAD addon (`addon/FreeCADMCP/rpc_server/*`, runs inside FreeCAD, XML-RPC server on :9875) and the MCP server (`src/freecad_mcp/*`, runs under uvx, XML-RPC client). Addon handlers gain structured `{"success": ..., ...}` returns with actual names/state; the MCP layer surfaces them. New read-only tools run on the RPC thread (precedent: `get_objects`); anything that writes documents dispatches to the GUI thread.

**Tech Stack:** Python 3.12+ (MCP side), FreeCAD 1.1 embedded Python 3.14 (addon side), FastMCP, xmlrpc. Tests: pytest via `uv run --with pytest`, headless addon tests via `freecadcmd`.

**Base:** fork/main (`7d98b5b`), branch `claude/freecad-silent-failures-66fb0f`.

**Verification environments:**
- Pure-Python MCP-side tests: `uv run --with pytest pytest tests/ -q` (run from repo root).
- Addon-side headless tests: `freecadcmd tests/addon/run_addon_tests.py` — grep the output for the `ALL ADDON TESTS PASSED` sentinel; freecadcmd's exit code is unreliable (it exits 0 even on an uncaught exception), though the runner catches setup/import crashes and forces `sys.exit(1)` for those.
- Live e2e: FreeCAD GUI instance with addon at `/home/benjamin/.local/share/FreeCAD/v1-1/Mod/FreeCADMCP/`, RPC on `localhost:9875`. Deploy = backup dir, copy `addon/FreeCADMCP/*` over it, schedule in-process RPC-server restart via `execute_code` (QTimer.singleShot → stop server, purge `rpc_server*` from `sys.modules`, re-import, start). Clean up all test documents afterward.

---

## Audit item → task map

| # | Audit item | Task |
|---|---|---|
| 1 | Actual-name returns (create_document/create_object) | 3 |
| 2 | serialize_shape per-object guard | 1 |
| 3 | Recompute failures return success | 4 |
| 4 | execute_code traceback + partial stdout | 5 |
| 5 | Not-found → error naming what exists; no screenshot on failure | 2 |
| 6 | Screenshot 800px default cap | 7 |
| 7 | get_objects detail="summary" default + scaffolding filter | 1, 2 |
| 8 | Compact JSON separators | 8 |
| 9 | FEM recipes out of docstring → resource | 10 |
| 10 | BoundBox/CenterOfMass in shape data | 1 |
| 11 | measure_distance tool | 9 |
| 12 | get_topology tool | 9 |
| 13 | save_document/export_document tools | 9 |
| 14 | Async job IDs + get_async_status | 6 |
| 15 | get_report_log tool | 9 |
| 16 | instructions string (units mm etc.) | 10 |
| 17 | Persistent exec namespace, isolated from server globals | 5 |
| 18 | Schema nits + ASSET_CREATION_STRATEGY rewrite | 10 |

Also: degrees for serialized Rotation.Angle (read/write symmetry — property_mapper writes degrees, serialize currently returns radians) in Task 1; README + version bump in Task 11; deploy + live e2e in Task 12; adversarial review workflow in Task 13.

---

### Task 0: Test scaffolding

**Files:**
- Create: `tests/__init__.py` (empty)
- Create: `tests/conftest.py`
- Create: `tests/addon/run_addon_tests.py` (populated in later tasks)

- [ ] **Step 1: Create conftest that puts `src` on sys.path**

```python
# tests/conftest.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
```

- [ ] **Step 2: Verify pytest collects (no tests yet)**

Run: `uv run --with pytest pytest tests/ -q`
Expected: "no tests ran"

- [ ] **Step 3: Commit** — `test: add test scaffolding`

---

### Task 1: serialize.py — guards, BoundBox/CenterOfMass, State, degrees, summary mode

**Files:**
- Modify: `addon/FreeCADMCP/rpc_server/serialize.py`
- Test: `tests/addon/run_addon_tests.py` (freecadcmd)

- [ ] **Step 1: Write failing addon tests** (in `tests/addon/run_addon_tests.py`; the file is a plain script: each `check(name, fn)` runs a callable, collects failures, exits nonzero at end)

Cases:
1. `serialize_object` on an empty `PartDesign::Body` does not raise; `Shape` is `{"Valid": False, ...}`.
2. `serialize_shape` of a `Part::Box` includes `Valid: True`, `BoundBox` `{"Min": [...], "Max": [...], "Size": [...]}`, `CenterOfMass: [5, 5, 5]` for a 10mm box.
3. Rotated placement serializes `Angle` in degrees (90.0, not 1.5707…).
4. `serialize_object` includes `"State"` list; a `Part::Cut` with no Base/Tool after recompute has `"Invalid"` in it.
5. Fallback repr: value serializing to `<Foo object at 0x…>` becomes `<Foo>`.
6. `summarize_object(box)` has keys ⊆ {Name, Label, TypeId, State, Shape, Placement} and its `Shape` only {Valid, Volume, BoundBox} (+Error keys).
7. `Properties` no longer duplicates `Label`/`Placement`/`Shape`.

- [ ] **Step 2: Run to verify failure** — `freecadcmd tests/addon/run_addon_tests.py` → nonzero exit.

- [ ] **Step 3: Implement in serialize.py**

```python
import math

def serialize_value(value):
    ...
    elif isinstance(value, App.Rotation):
        return {
            "Axis": {"x": value.Axis.x, "y": value.Axis.y, "z": value.Axis.z},
            "Angle": math.degrees(value.Angle),  # degrees: symmetric with Placement writes
        }
    ...
    else:
        s = str(value)
        if " object at 0x" in s:
            return f"<{type(value).__name__}>"
        return s


def serialize_shape(shape):
    if shape is None:
        return None
    try:
        if shape.isNull():
            return {"Valid": False, "Error": "null shape (no geometry computed yet)"}
        data = {
            "Valid": shape.isValid(),
            "Volume": shape.Volume,
            "Area": shape.Area,
            "VertexCount": len(shape.Vertexes),
            "EdgeCount": len(shape.Edges),
            "FaceCount": len(shape.Faces),
        }
        try:
            bb = shape.BoundBox
            data["BoundBox"] = {
                "Min": [bb.XMin, bb.YMin, bb.ZMin],
                "Max": [bb.XMax, bb.YMax, bb.ZMax],
                "Size": [bb.XLength, bb.YLength, bb.ZLength],
            }
        except Exception:
            pass
        try:
            com = shape.CenterOfMass
            data["CenterOfMass"] = [com.x, com.y, com.z]
        except Exception:
            pass
        return data
    except Exception as e:
        return {"Valid": False, "Error": f"{type(e).__name__}: {e}"}
```

In `serialize_object`: add `"State": [str(s) for s in getattr(obj, "State", [])]`, skip `{"Label", "Placement", "Shape"}` in the Properties loop, keep the rest.

Add `SCAFFOLDING_TYPE_IDS = {"App::Origin", "App::Line", "App::Plane"}` and:

```python
def summarize_object(obj):
    """Compact view for get_objects(detail='summary'): identity + health + envelope."""
    d = {"Name": obj.Name, "TypeId": obj.TypeId}
    if obj.Label != obj.Name:
        d["Label"] = obj.Label
    state = [str(s) for s in getattr(obj, "State", [])]
    if state:
        d["State"] = state
    full = serialize_shape(getattr(obj, "Shape", None))
    if full is not None:
        d["Shape"] = {k: v for k, v in full.items() if k in ("Valid", "Volume", "BoundBox", "Error")}
    pl = getattr(obj, "Placement", None)
    if isinstance(pl, App.Placement):
        base, rot = pl.Base, pl.Rotation
        if base.Length > 1e-9 or abs(rot.Angle) > 1e-9:
            d["Placement"] = {"Base": [base.x, base.y, base.z]}
            if abs(rot.Angle) > 1e-9:
                d["Placement"]["RotationAxis"] = [rot.Axis.x, rot.Axis.y, rot.Axis.z]
                d["Placement"]["RotationAngleDeg"] = math.degrees(rot.Angle)
    return d
```

- [ ] **Step 4: Run addon tests → pass.** `freecadcmd tests/addon/run_addon_tests.py`
- [ ] **Step 5: Commit** — `fix(addon): guard shape serialization, add BoundBox/CenterOfMass/State, degrees, summary mode`

---

### Task 2: get_objects/get_object — structured errors + detail param

**Files:**
- Modify: `addon/FreeCADMCP/rpc_server/rpc_server.py:178-195`
- Modify: `src/freecad_mcp/freecad_client.py`, `src/freecad_mcp/operations/core.py`, `src/freecad_mcp/server.py` (get_objects/get_object tools)
- Test: `tests/test_core_operations.py` (fake connection), addon runner case for RPC-level shapes

- [ ] **Step 1: Failing MCP-side tests** — fake connection returning error dicts / success dicts; assert: error → single TextContent naming alternatives, **no** screenshot call; success → JSON text.
- [ ] **Step 2: Implement addon side**

```python
def get_objects(self, doc_name, detail="summary"):
    try:
        doc = FreeCAD.getDocument(doc_name)
    except Exception:
        return {
            "success": False,
            "error": f"Document '{doc_name}' not found.",
            "open_documents": list(FreeCAD.listDocuments().keys()),
        }
    objects, omitted = [], []
    for o in doc.Objects:
        try:
            if detail != "full" and o.TypeId in SCAFFOLDING_TYPE_IDS:
                omitted.append(o.Name)
                continue
            objects.append(serialize_object(o) if detail == "full" else summarize_object(o))
        except Exception as e:
            objects.append({"Name": o.Name, "TypeId": o.TypeId,
                            "Error": f"serialization failed: {type(e).__name__}: {e}"})
    out = {"success": True, "doc_name": doc.Name, "detail": detail, "objects": objects}
    if omitted:
        out["omitted_origin_scaffolding"] = omitted
    return out

def get_object(self, doc_name, obj_name):
    try:
        doc = FreeCAD.getDocument(doc_name)
    except Exception:
        return {"success": False, "error": f"Document '{doc_name}' not found.",
                "open_documents": list(FreeCAD.listDocuments().keys())}
    obj = doc.getObject(obj_name)
    if obj is None:
        return {"success": False,
                "error": f"Object '{obj_name}' not found in document '{doc.Name}'.",
                "available_objects": [o.Name for o in doc.Objects]}
    try:
        return {"success": True, "object": serialize_object(obj)}
    except Exception as e:
        return {"success": False, "error": f"Serialization failed for '{obj_name}': {e}"}
```

- [ ] **Step 3: Implement MCP side.** Client passes `detail` through. `get_objects_operation`/`get_object_operation`: if `res.get("success") is False` → `text_response` with error + alternatives, return **before** any screenshot; else `json_response(res)` + screenshot as before. `server.py` gains `detail: Literal["summary", "full"] = "summary"` on `get_objects` with docstring explaining summary omits Origin scaffolding and full properties.
- [ ] **Step 4: Tests pass** (`uv run --with pytest pytest tests/ -q`).
- [ ] **Step 5: Commit** — `fix: structured not-found errors and summary detail for object inspection`

---

### Task 3: Actual-name returns

**Files:**
- Modify: `addon/FreeCADMCP/rpc_server/rpc_server.py:55-71,250-257` and `object_factory.py`
- Modify: `src/freecad_mcp/operations/core.py` (create_document/create_object/edit_object messages)
- Test: `tests/test_core_operations.py`, addon runner (DupBox twice → second returns DupBox001; "My Test Doc" → My_Test_Doc)

- [ ] **Step 1: Failing tests** (both sides).
- [ ] **Step 2: Addon:** `_create_document_gui` returns `{"success": True, "document_name": doc.Name, "requested_name": name}`; `create_document`/`create_object`/`edit_object`/`delete_object` RPC methods use:

```python
res = dispatch_to_gui(...)
if isinstance(res, dict) and res.get("success"):
    return res
return _err(res)
```

`object_factory`: `_create_fem_mesh`/`_create_fem_object`/`_create_generic_object` return the created DocumentObject; `create_object_gui` returns `{"success": True, "object_name": created.Name, "requested_name": obj.name, "type_id": created.TypeId, ...}` (+state, Task 4).
- [ ] **Step 3: MCP:** messages report actual name and append `(requested 'X'; FreeCAD assigned 'Y' — use 'Y' in subsequent calls)` when they differ.
- [ ] **Step 4: Tests pass.**
- [ ] **Step 5: Commit** — `fix: return FreeCAD-assigned names, not requested names`

---

### Task 4: Recompute state surfacing

**Files:**
- Modify: `addon/FreeCADMCP/rpc_server/object_factory.py` (`create_object_gui`), `rpc_server.py` (`_edit_object_gui`)
- Modify: `src/freecad_mcp/operations/core.py`
- Test: addon runner (Part::Cut no Base/Tool → success dict has `state` with "Invalid" and `warning`); MCP fake test (warning appears in text)

- [ ] **Step 1: Failing tests.**
- [ ] **Step 2: Addon:** after `doc.recompute()` in create/edit paths:

```python
state = [str(s) for s in created.State]
if any(s in ("Invalid", "Error", "Touched") for s in state):
    result["state"] = state
    result["warning"] = (
        f"Object '{created.Name}' did not recompute cleanly (state: {state}). "
        "It may be missing required inputs (e.g. Base/Tool for booleans) or have invalid geometry."
    )
```

- [ ] **Step 3: MCP:** append `\nWARNING: {res['warning']}` to success text in create/edit operations.
- [ ] **Step 4: Tests pass.** **Step 5: Commit** — `fix: surface recompute state instead of reporting unconditional success`

---

### Task 5: execute_code — persistent namespace, traceback, partial stdout

**Files:**
- Modify: `addon/FreeCADMCP/rpc_server/rpc_server.py:148-176` (+ module-level namespace + `import traceback`)
- Modify: `src/freecad_mcp/operations/core.py` (`_execute_code`), `src/freecad_mcp/server.py` (execute_code docstring: persistence note)
- Test: MCP fake test (failure text contains line number + partial stdout); addon runner (namespace isolation: exec `os = None` then RPC internals still work; persistence: var defined in one exec visible in next)

- [ ] **Step 1: Failing tests.**
- [ ] **Step 2: Addon implementation**

```python
_EXEC_NAMESPACE: dict[str, Any] = {}

def _exec_namespace() -> dict[str, Any]:
    """Dedicated persistent namespace for user code.

    Isolated from this module's globals so user scripts can't clobber RPC
    internals (e.g. assigning os/io); persists across calls so helpers and
    intermediate results survive between execute_code / execute_code_async calls.
    """
    if not _EXEC_NAMESPACE:
        _EXEC_NAMESPACE.update({
            "__name__": "__freecad_mcp_exec__",
            "FreeCAD": FreeCAD, "App": FreeCAD,
            "FreeCADGui": FreeCADGui, "Gui": FreeCADGui,
        })
    return _EXEC_NAMESPACE


def _format_exec_error(e: Exception) -> dict[str, Any]:
    detail = {"success": False, "error": f"{type(e).__name__}: {e}"}
    if isinstance(e, SyntaxError) and e.lineno is not None:
        detail["traceback"] = f'  File "<code>", line {e.lineno}\n    {(e.text or "").rstrip()}'
        return detail
    frames = traceback.extract_tb(e.__traceback__)
    user_frames = [f for f in frames if f.filename == "<string>"] or frames[-2:]
    detail["traceback"] = "".join(traceback.format_list(user_frames)).rstrip()
    return detail
```

`execute_code` task catches, returns `_format_exec_error(e) | {"stdout": output_buffer.getvalue()}`; RPC method passes failure dicts through (they carry traceback/stdout), keeps console code-preview logging.
- [ ] **Step 3: MCP `_execute_code` failure path:**

```python
parts = [f"Failed to execute code: {res['error']}"]
if res.get("traceback"):
    parts.append(res["traceback"])
if res.get("stdout"):
    parts.append(f"--- output before error ---\n{res['stdout']}")
return text_response("\n".join(parts))
```

- [ ] **Step 4: Tests pass.** **Step 5: Commit** — `fix: debuggable execute_code failures + isolated persistent namespace`

---

### Task 6: Async job registry + get_async_status

**Files:**
- Modify: `addon/FreeCADMCP/rpc_server/rpc_server.py:115-146` (+ `import time, uuid`)
- Modify: `src/freecad_mcp/freecad_client.py`, `operations/core.py`, `operations/__init__.py`, `server.py` (new tool + updated async docstring)
- Test: MCP fake tests; live e2e (Task 12) covers real threading

- [ ] **Step 1: Failing MCP tests** (async op returns job id text; get_async_status formats running/completed/failed).
- [ ] **Step 2: Addon:**

```python
_async_jobs: dict[str, dict[str, Any]] = {}
_async_jobs_lock = threading.Lock()
_MAX_ASYNC_JOBS = 50
```

`execute_code_async`: create `job_id = uuid.uuid4().hex[:12]`, record `{"job_id", "status": "running", "started_at", "code_preview": code[:160]}` under lock (evict oldest finished beyond cap), worker `exec(code, _exec_namespace())`, on success set `status="completed", finished_at`; on failure `status="failed"` + `_format_exec_error` fields. Return `{"success": True, "job_id": job_id}`.

```python
def get_async_status(self, job_id=None):
    with _async_jobs_lock:
        if job_id is None:
            jobs = sorted(_async_jobs.values(), key=lambda j: j["started_at"], reverse=True)
            return {"success": True, "jobs": [dict(j) for j in jobs]}
        job = _async_jobs.get(job_id)
        if job is None:
            return {"success": False, "error": f"Unknown job id '{job_id}'.",
                    "known_jobs": list(_async_jobs)}
        job = dict(job)
    if job["status"] == "running":
        job["runtime_s"] = round(time.time() - job["started_at"], 1)
    return {"success": True, **job}
```

- [ ] **Step 3: MCP:** client methods; `get_async_status` tool; `execute_code_async_operation` message: "Background job '{id}' started. Poll get_async_status(job_id='{id}')…"; rewrite stale docstring polling advice in `server.py`.
- [ ] **Step 4: Tests pass.** **Step 5: Commit** — `feat: async job ids and get_async_status`

---

### Task 7: Screenshot default 800px cap

**Files:**
- Modify: `addon/FreeCADMCP/rpc_server/view_manager.py:34-42`
- Modify: `src/freecad_mcp/server.py` (`get_view` docstring: default cap, override via width/height)
- Test: addon runner (pure function `_resolve_screenshot_size` with a stub view: (1014,1159)→(700,800); (640,480) unchanged; explicit width honored)

- [ ] **Steps:** failing test → implement:

```python
DEFAULT_MAX_EDGE = 800

def _resolve_screenshot_size(view, width, height):
    view_width, view_height = _get_view_size(view)
    if width is None and height is None:
        longest = max(view_width, view_height)
        if longest > DEFAULT_MAX_EDGE:
            scale = DEFAULT_MAX_EDGE / longest
            return max(1, round(view_width * scale)), max(1, round(view_height * scale))
        return view_width, view_height
    resolved_width = view_width if width is None else max(1, int(width))
    resolved_height = view_height if height is None else max(1, int(height))
    return resolved_width, resolved_height
```

→ test passes → commit `feat: cap default screenshot size at 800px longest edge`

---

### Task 8: Compact JSON

**Files:**
- Modify: `src/freecad_mcp/responses.py:13`
- Test: `tests/test_responses.py`

- [ ] Failing test: `json_response({"a": [1, 2]})[0].text == '{"a":[1,2]}'` → implement `json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)` → pass → commit `perf: compact JSON tool responses`

---

### Task 9: New capability tools (measure_distance, get_topology, save/export, get_report_log)

**Files:**
- Create: `addon/FreeCADMCP/rpc_server/geometry_tools.py` (measure_distance, get_topology — read-only, RPC thread; no FreeCADGui import)
- Create: `addon/FreeCADMCP/rpc_server/document_io.py` (save_document, export_document — GUI-thread bodies; no FreeCADGui import at module level)
- Modify: `addon/FreeCADMCP/rpc_server/rpc_server.py` (RPC methods; `_get_report_log_gui` using PySide QtWidgets)
- Modify: `src/freecad_mcp/freecad_client.py`, `operations/core.py`, `operations/__init__.py`, `server.py` (6 new tools)
- Test: addon runner (distance between two boxes = 5mm; overlap → distance 0 + intersection volume; topology of box = 6 planar faces, areas, ±unit normals; save/export STEP+STL to scratchpad, files exist and non-empty); MCP fake tests for response formatting

**geometry_tools.py core:**

```python
"""Read-only geometry interrogation: distances and topology.

These run on the RPC thread (no GUI dispatch), matching the get_objects
precedent for read-only document access.
"""

import FreeCAD


def _resolve_shape(doc, obj_name, sub_element=None):
    obj = doc.getObject(obj_name)
    if obj is None:
        raise ValueError(
            f"Object '{obj_name}' not found in '{doc.Name}'. "
            f"Available: {[o.Name for o in doc.Objects]}"
        )
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        raise ValueError(f"Object '{obj_name}' has no computed shape.")
    if sub_element:
        try:
            shape = shape.getElement(sub_element)
        except Exception:
            raise ValueError(
                f"Sub-element '{sub_element}' not found on '{obj_name}' "
                f"({len(shape.Faces)} faces, {len(shape.Edges)} edges, "
                f"{len(shape.Vertexes)} vertexes)."
            )
    return shape


def measure_distance(doc_name, object1, object2, sub1=None, sub2=None):
    try:
        doc = FreeCAD.getDocument(doc_name)
    except Exception:
        return {"success": False, "error": f"Document '{doc_name}' not found.",
                "open_documents": list(FreeCAD.listDocuments().keys())}
    try:
        shape1 = _resolve_shape(doc, object1, sub1)
        shape2 = _resolve_shape(doc, object2, sub2)
        dist, point_pairs, _info = shape1.distToShape(shape2)
        result = {"success": True, "distance_mm": dist}
        if point_pairs:
            p1, p2 = point_pairs[0]
            result["closest_point_on_1"] = [p1.x, p1.y, p1.z]
            result["closest_point_on_2"] = [p2.x, p2.y, p2.z]
        if dist == 0 and sub1 is None and sub2 is None:
            try:
                common = shape1.common(shape2)
                result["intersection_volume_mm3"] = common.Volume
                result["note"] = (
                    "Shapes touch or overlap. intersection_volume_mm3 > 0 means "
                    "actual interference; 0 means surfaces merely touch."
                )
            except Exception:
                result["note"] = "Shapes touch or overlap (intersection volume unavailable)."
        return result
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


_TOPO_LIMIT = 200


def get_topology(doc_name, obj_name, include_edges=False):
    # doc/obj resolution as above → error dicts
    # faces: for i, face in enumerate(shape.Faces[:_TOPO_LIMIT]):
    #   entry = {"Name": f"Face{i+1}", "Type": type(face.Surface).__name__, "Area": face.Area}
    #   guarded: CenterOfMass → "Centroid": [x,y,z]
    #   guarded: u0,u1,v0,v1 = face.ParameterRange; n = face.normalAt((u0+u1)/2, (v0+v1)/2)
    #            entry["Normal"] = [n.x, n.y, n.z]; entry["Orientation"] = face.Orientation
    # edges (only when include_edges): {"Name": f"Edge{i+1}", "Type": type(edge.Curve).__name__,
    #   "Length": edge.Length, guarded "Mid": valueAt(mid param)}
    # counts + "truncated": True + note when over _TOPO_LIMIT (no silent caps)
    ...
```

**document_io.py core:**

```python
"""Save and export documents. Bodies run on the GUI thread (they write)."""

import os
from pathlib import Path

import FreeCAD

_MESH_FORMATS = {".stl", ".obj", ".3mf", ".ply", ".amf"}
_CAD_FORMATS = {".step", ".stp", ".iges", ".igs"}


def save_document(doc_name, file_path=None):
    # doc lookup → error dict with open_documents
    # if file_path: doc.saveAs(expanded absolute path) else:
    #   if not doc.FileName: return error "never been saved; pass file_path"
    #   doc.save()
    # return {"success": True, "file_path": doc.FileName}


def export_document(doc_name, file_path, object_names=None):
    # doc lookup → error dict
    # objects: named (each must exist → error listing available) or default:
    #   [o for o in doc.RootObjects if getattr(o, "Shape", None) is not None
    #    and not o.Shape.isNull() and o.TypeId not in SCAFFOLDING_TYPE_IDS]
    #   → error if empty: "no exportable objects; pass object_names"
    # suffix routing: _CAD_FORMATS → import Import; Import.export(objs, path)
    #   ".brep" → import Part; Part.export(objs, path)
    #   _MESH_FORMATS → import Mesh; Mesh.export(objs, path)
    #   else error listing supported extensions
    # return {"success": True, "file_path": path, "file_size_bytes": os.path.getsize(path),
    #         "exported_objects": [o.Name for o in objs]}
```

**rpc_server.py additions:** `measure_distance`/`get_topology` call geometry_tools directly (read-only); `save_document`/`export_document` via `dispatch_to_gui`; `get_report_log(tail=100)` via `dispatch_to_gui` to `_get_report_log_gui`:

```python
def _get_report_log_gui(self, tail):
    from PySide import QtWidgets
    mw = FreeCADGui.getMainWindow()
    dock = mw.findChild(QtWidgets.QDockWidget, "Report view")
    if dock is None:
        return {"success": False, "error": "Report view dock not found in this FreeCAD build."}
    text_widget = dock.findChild(QtWidgets.QPlainTextEdit) or dock.findChild(QtWidgets.QTextEdit)
    if text_widget is None:
        return {"success": False, "error": "Report view text widget not found."}
    lines = text_widget.toPlainText().splitlines()
    total = len(lines)
    if tail and total > tail:
        lines = lines[-tail:]
    return {"success": True, "log": "\n".join(lines), "total_lines": total,
            "returned_lines": len(lines)}
```

**server.py new tools** (all text-only, no screenshots): `measure_distance(doc_name, object1, object2, sub1=None, sub2=None)`, `get_topology(doc_name, obj_name, include_edges=False)`, `save_document(doc_name, file_path=None)`, `export_document(doc_name, file_path, object_names=None)`, `get_report_log(tail=100)`, plus Task 6's `get_async_status`. Docstrings state units (mm), that sub-element names come from get_topology, and that export format is chosen by file extension.

- [ ] Steps: failing tests → addon impl → MCP impl → tests pass → commit `feat: measurement, topology, save/export, and report-log tools`

---

### Task 10: Guidance surfaces

**Files:**
- Modify: `src/freecad_mcp/server.py` (instructions string, create_object docstring trim + `obj_properties: dict[str, Any] | None = None` + analysis_name arg doc + PartDesign/Sketcher shell note, FEM resource)
- Modify: `src/freecad_mcp/prompt_text.py` (ASSET_CREATION_STRATEGY rewrite + new FEM_WORKFLOW_GUIDE)

- [ ] **instructions:**

```python
mcp = FastMCP(
    "FreeCADMCP",
    instructions=(
        "Control a live FreeCAD instance: create/edit/inspect documents and objects, "
        "run Python in FreeCAD, measure geometry, run FEM, save/export files.\n"
        "- Units: lengths are millimeters, angles are degrees.\n"
        "- FreeCAD sanitizes names (spaces become underscores, duplicates get numeric "
        "suffixes). Always use the actual name returned by create_document/create_object.\n"
        "- PartDesign::* and Sketcher::* objects made via create_object are empty shells; "
        "build features/sketch geometry with execute_code.\n"
        "- execute_code state persists across calls (FreeCAD/App/FreeCADGui/Gui preloaded).\n"
        "- Verify dimensions numerically with get_object (BoundBox/Volume/CenterOfMass), "
        "measure_distance, get_topology — don't rely on screenshots alone.\n"
        "- Documents live only in memory until save_document; use export_document for "
        "STEP/STL/3MF.\n"
        "- FreeCAD's console output (including errors from async jobs) is available via "
        "get_report_log."
    ),
    lifespan=server_lifespan,
)
```

- [ ] **FEM resource:** move the four FEM JSON recipes + run_fem_analysis prerequisites into `FEM_WORKFLOW_GUIDE` in prompt_text.py; register `@mcp.resource("freecad://fem-workflow")`; create_object docstring keeps one line: "For FEM objects (analysis/material/mesh/constraints) read the freecad://fem-workflow resource."
- [ ] **ASSET_CREATION_STRATEGY rewrite** (script-first, verify-numerically, actual names, include_screenshot economy, save/export at end).
- [ ] Verify: `uv run python -c "from freecad_mcp.server import mcp"` imports cleanly; docstring token estimate via `wc -w` noticeably down.
- [ ] Commit — `docs: instructions, FEM resource, strategy rewrite, schema fixes`

---

### Task 11: README + version bump

**Files:** `README.md` (Tools section: new tools, detail param, screenshot cap, units), `pyproject.toml` (0.1.19 → 0.2.0)

- [ ] Update + commit `docs: README for new tools; bump to 0.2.0`

---

### Task 12: Deploy to live FreeCAD + e2e verification

- [ ] **Step 1: Backup installed addon** to scratchpad (`cp -r`).
- [ ] **Step 2: Copy** `addon/FreeCADMCP/` → `/home/benjamin/.local/share/FreeCAD/v1-1/Mod/FreeCADMCP/`.
- [ ] **Step 3: Restart RPC server in-process** via current `execute_code`: QTimer.singleShot(500ms) → `stop_rpc_server()`, purge `sys.modules['rpc_server*']`, import fresh, `start_rpc_server()`; on any exception restore backup files and restart with old code.
- [ ] **Step 4: Ping until up** (≤15s).
- [ ] **Step 5: e2e probe script** (xmlrpc from Bash python): each audit item probed —
  1. `create_document('E2E Test Doc')` → returns `E2E_Test_Doc`.
  2. `create_object` DupBox twice → second returns `DupBox001`.
  3. Empty `PartDesign::Body` → `get_objects` succeeds, body Shape `Valid: false`.
  4. `Part::Cut` no Base/Tool → success **with** state warning.
  5. `execute_code` with error on line 3 after prints → traceback line 3 + partial stdout.
  6. `get_object` typo → error with available_objects; `get_objects` typo doc → error with open_documents.
  7. Default screenshot ≤ 800px longest edge (decode PNG header from base64).
  8. `get_objects` summary vs full on a PartDesign doc → summary smaller, scaffolding omitted.
  9. Box BoundBox/CenterOfMass correct; rotation serialized in degrees.
  10. `measure_distance` two boxes 5mm apart → 5.0; overlapping → 0 + intersection volume.
  11. `get_topology` box → 6 Plane faces, ±unit normals.
  12. `save_document`/`export_document` STEP+STL → files exist, non-empty (scratchpad).
  13. `execute_code_async` → job id; poll `get_async_status` → completed; failing job → failed + traceback.
  14. `get_report_log` returns recent console lines.
  15. Namespace: `exec` sets var → next call reads it; `os = None` in user code → server still healthy.
- [ ] **Step 6: Clean up** all e2e documents (close without saving; only `Rock5BPlus_Case` and `StereoCamMount` remain), delete scratch export files.
- [ ] **Step 7: Commit any fixes found.**

---

### Task 13: Adversarial review + fixes

- [ ] Run multi-agent Workflow review of `git diff fork/main` (lenses: correctness, XML-RPC marshaling safety, thread safety, token-cost regressions, docs/schema consistency), adversarially verify findings, fix confirmed ones, re-run affected tests + e2e probes.

### Task 14: Final verification + push

- [ ] Full test suite + addon runner green; live instance healthy and clean; summarize per-item status. Push branch to fork; merge to fork main per user's standing instruction ("use my fork's main") — via PR or direct merge (ask only if push fails).

---

## Self-review notes

- Spec coverage: all 18 items mapped (table above) + 3 consistency extras (degrees, README, version).
- Type consistency: all addon success returns are dicts with `"success": True`; `_err` handles string/timeout-dict; MCP layer checks `res.get("success") is False` for inspection tools (which previously returned bare lists/None) and `res["success"]` truthiness for mutation tools.
- Old-addon/new-client mismatch is out of scope (fork controls both halves); mismatch yields visible xmlrpc.Fault, not silent failure.
