"""Headless addon-side tests, run with: freecadcmd tests/addon/run_addon_tests.py

Exercises the rpc_server modules that don't need a GUI (serialize, object
factory, geometry tools, document IO) against real FreeCAD documents.
Exits nonzero when any check fails.
"""

import math
import os
import sys
import tempfile
import traceback


def p(*args):
    """freecadcmd loses buffered stdout on script exit; stderr is unbuffered."""
    print(*args, file=sys.stderr, flush=True)

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_REPO, "addon", "FreeCADMCP"))

import FreeCAD  # noqa: E402

_failures = []
_passed = 0


def check(name, fn):
    global _passed
    try:
        fn()
        _passed += 1
        p(f"PASS: {name}")
    except Exception:
        _failures.append(name)
        p(f"FAIL: {name}\n{traceback.format_exc()}")


def expect(cond, msg):
    if not cond:
        raise AssertionError(msg)


# --------------------------------------------------------------------------
# serialize.py
# --------------------------------------------------------------------------
from rpc_server.serialize import (  # noqa: E402
    SCAFFOLDING_TYPE_IDS,
    serialize_object,
    serialize_shape,
    serialize_value,
    summarize_object,
)

doc = FreeCAD.newDocument("AddonTests")

box = doc.addObject("Part::Box", "Box")
box.Length = 10
box.Width = 10
box.Height = 10
doc.recompute()


def t_empty_body_serializes():
    body = doc.addObject("PartDesign::Body", "EmptyBody")
    doc.recompute()
    data = serialize_object(body)  # must not raise
    expect(data["Shape"] is not None, "body Shape summary missing")
    expect(data["Shape"]["Valid"] is False, f"expected Valid False, got {data['Shape']}")
    doc.removeObject(body.Name)


def t_shape_has_boundbox_and_com():
    data = serialize_shape(box.Shape)
    expect(data["Valid"] is True, "box shape should be valid")
    expect(data["BoundBox"]["Size"] == [10.0, 10.0, 10.0], f"bad Size: {data['BoundBox']}")
    expect(data["BoundBox"]["Min"] == [0.0, 0.0, 0.0], f"bad Min: {data['BoundBox']}")
    com = data["CenterOfMass"]
    expect(all(abs(c - 5.0) < 1e-9 for c in com), f"bad CenterOfMass: {com}")
    expect(abs(data["Volume"] - 1000.0) < 1e-6, f"bad Volume: {data['Volume']}")


def t_rotation_serializes_in_degrees():
    rot_box = doc.addObject("Part::Box", "RotBox")
    rot_box.Placement = FreeCAD.Placement(
        FreeCAD.Vector(1, 2, 3),
        FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), 90),
    )
    doc.recompute()
    data = serialize_object(rot_box)
    angle = data["Placement"]["Rotation"]["Angle"]
    expect(abs(angle - 90.0) < 1e-9, f"expected 90 degrees, got {angle}")
    doc.removeObject(rot_box.Name)


def t_invalid_cut_state_reported():
    cut = doc.addObject("Part::Cut", "BadCut")
    doc.recompute()
    data = serialize_object(cut)
    expect("State" in data, "State missing from serialization")
    expect("Invalid" in data["State"] or "Touched" in data["State"],
           f"expected dirty state, got {data['State']}")
    doc.removeObject(cut.Name)


def t_repr_fallback_compacted():
    val = serialize_value(object())
    expect(val == "<object>", f"expected '<object>', got {val!r}")


def t_properties_deduplicated():
    data = serialize_object(box)
    for dup in ("Label", "Placement", "Shape"):
        expect(dup not in data["Properties"], f"'{dup}' duplicated in Properties")


def t_summarize_object_is_compact():
    data = summarize_object(box)
    allowed = {"Name", "Label", "TypeId", "State", "Shape", "Placement"}
    expect(set(data) <= allowed, f"unexpected summary keys: {set(data) - allowed}")
    shape_allowed = {"Valid", "Volume", "BoundBox", "Error"}
    expect(set(data["Shape"]) <= shape_allowed,
           f"unexpected shape keys: {set(data['Shape']) - shape_allowed}")
    expect(data["Name"] == "Box", "summary Name wrong")
    # identity placement omitted entirely
    expect("Placement" not in data, "identity Placement should be omitted from summary")


def t_summarize_placement_when_nonidentity():
    rot_box = doc.addObject("Part::Box", "SumRotBox")
    rot_box.Placement = FreeCAD.Placement(
        FreeCAD.Vector(5, 0, 0),
        FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), 45),
    )
    doc.recompute()
    data = summarize_object(rot_box)
    expect(data["Placement"]["Base"] == [5.0, 0.0, 0.0], f"bad Base: {data['Placement']}")
    expect(abs(data["Placement"]["RotationAngleDeg"] - 45.0) < 1e-9,
           f"bad angle: {data['Placement']}")
    doc.removeObject(rot_box.Name)


def t_scaffolding_type_ids():
    expect(SCAFFOLDING_TYPE_IDS == {"App::Origin", "App::Line", "App::Plane"},
           f"unexpected scaffolding set: {SCAFFOLDING_TYPE_IDS}")


check("serialize: empty PartDesign::Body does not raise", t_empty_body_serializes)
check("serialize: BoundBox + CenterOfMass present", t_shape_has_boundbox_and_com)
check("serialize: rotation angle in degrees", t_rotation_serializes_in_degrees)
check("serialize: invalid Part::Cut state reported", t_invalid_cut_state_reported)
check("serialize: repr fallback compacted", t_repr_fallback_compacted)
check("serialize: Properties deduplicated", t_properties_deduplicated)
check("serialize: summary mode compact", t_summarize_object_is_compact)
check("serialize: summary placement when non-identity", t_summarize_placement_when_nonidentity)
check("serialize: scaffolding type ids", t_scaffolding_type_ids)


# --------------------------------------------------------------------------
# object_factory: actual names + recompute state
# --------------------------------------------------------------------------
from rpc_server.object_factory import create_object_gui  # noqa: E402
from rpc_server.property_mapper import Object  # noqa: E402


def t_create_returns_actual_name_on_collision():
    first = create_object_gui("AddonTests", Object(name="DupBox", type="Part::Box"))
    second = create_object_gui("AddonTests", Object(name="DupBox", type="Part::Box"))
    expect(first["success"] is True, f"first create failed: {first}")
    expect(first["object_name"] == "DupBox", f"unexpected first name: {first}")
    expect(second["success"] is True, f"second create failed: {second}")
    expect(second["object_name"] == "DupBox001",
           f"expected DupBox001, got {second['object_name']}")
    expect(second["requested_name"] == "DupBox", f"requested_name missing: {second}")
    doc.removeObject("DupBox")
    doc.removeObject("DupBox001")


def t_create_invalid_cut_warns():
    res = create_object_gui("AddonTests", Object(name="WarnCut", type="Part::Cut"))
    expect(res["success"] is True, f"create failed outright: {res}")
    expect("Invalid" in res.get("state", []), f"state missing Invalid: {res}")
    expect("warning" in res, f"warning missing: {res}")
    doc.removeObject("WarnCut")


def t_create_unknown_doc_errors():
    res = create_object_gui("NoSuchDoc", Object(name="X", type="Part::Box"))
    expect(isinstance(res, str) and "not found" in res, f"expected error string: {res!r}")


check("object_factory: name collision returns actual name", t_create_returns_actual_name_on_collision)
check("object_factory: invalid cut warns", t_create_invalid_cut_warns)
check("object_factory: unknown doc errors", t_create_unknown_doc_errors)


# --------------------------------------------------------------------------
# document_query: structured errors + detail levels
# --------------------------------------------------------------------------
from rpc_server.document_query import query_object, query_objects  # noqa: E402


def t_query_objects_summary_omits_scaffolding():
    body = doc.addObject("PartDesign::Body", "QueryBody")
    doc.recompute()
    res = query_objects("AddonTests", "summary")
    expect(res["success"] is True, f"query failed: {res.get('error')}")
    type_ids = {o.get("TypeId") for o in res["objects"]}
    expect("App::Origin" not in type_ids, "summary should omit App::Origin")
    expect(res.get("omitted_origin_scaffolding"), "omitted names should be listed")
    names = {o["Name"] for o in res["objects"]}
    expect("Box" in names and "QueryBody" in names, f"objects missing: {names}")
    doc.removeObject(body.Name)


def t_query_objects_full_keeps_everything():
    body = doc.addObject("PartDesign::Body", "FullBody")
    doc.recompute()
    res = query_objects("AddonTests", "full")
    type_ids = {o.get("TypeId") for o in res["objects"]}
    expect("App::Origin" in type_ids, "full detail should keep scaffolding")
    expect("omitted_origin_scaffolding" not in res, "full detail should omit nothing")
    doc.removeObject(body.Name)


def t_query_objects_unknown_doc():
    res = query_objects("NoSuchDoc", "summary")
    expect(res["success"] is False, "expected failure")
    expect("AddonTests" in res["open_documents"], f"open docs missing: {res}")


def t_query_object_unknown_object():
    res = query_object("AddonTests", "Bxo")
    expect(res["success"] is False, "expected failure")
    expect("Box" in res["available_objects"], f"available missing Box: {res}")


def t_query_object_success():
    res = query_object("AddonTests", "Box")
    expect(res["success"] is True, f"failed: {res}")
    expect(res["object"]["Name"] == "Box", f"bad object: {res['object'].get('Name')}")


check("document_query: summary omits scaffolding", t_query_objects_summary_omits_scaffolding)
check("document_query: full keeps everything", t_query_objects_full_keeps_everything)
check("document_query: unknown doc error", t_query_objects_unknown_doc)
check("document_query: unknown object error", t_query_object_unknown_object)
check("document_query: object success", t_query_object_success)


# --------------------------------------------------------------------------
# code_exec: persistent namespace, error formatting, async registry
# --------------------------------------------------------------------------
from rpc_server.code_exec import (  # noqa: E402
    async_job_status,
    complete_async_job,
    exec_namespace,
    format_exec_error,
    start_async_job,
)


def t_namespace_persists_and_preloads():
    ns = exec_namespace()
    expect(ns["App"] is FreeCAD, "App not preloaded")
    exec("probe_var = 41", ns)
    exec("probe_var += 1", exec_namespace())
    expect(exec_namespace()["probe_var"] == 42, "namespace did not persist between execs")
    expect(ns is not globals(), "namespace must not be module globals")


def t_format_runtime_error_has_line():
    try:
        exec("y = 1\nz = y / 0", exec_namespace())
    except Exception as e:
        detail = format_exec_error(e)
    expect(detail["success"] is False, "success should be False")
    expect("ZeroDivisionError" in detail["error"], f"bad error: {detail['error']}")
    expect("line 2" in detail["traceback"], f"traceback missing line 2: {detail['traceback']}")


def t_format_syntax_error_has_line():
    try:
        exec("ok = 1\ndef broken(:\n    pass", exec_namespace())
    except SyntaxError as e:
        detail = format_exec_error(e)
    expect("SyntaxError" in detail["error"], f"bad error: {detail['error']}")
    expect("line 2" in detail["traceback"], f"traceback missing line 2: {detail['traceback']}")


def t_async_registry_lifecycle():
    job_id = start_async_job("import time; time.sleep(1)")
    running = async_job_status(job_id)
    expect(running["status"] == "running", f"expected running: {running}")
    expect("runtime_s" in running, "running job should report runtime")
    complete_async_job(job_id, None)
    done = async_job_status(job_id)
    expect(done["status"] == "completed", f"expected completed: {done}")
    expect("finished_at" in done, "finished_at missing")

    fail_id = start_async_job("boom()")
    complete_async_job(fail_id, {"error": "NameError: name 'boom' is not defined",
                                 "traceback": '  File "<string>", line 1'})
    failed = async_job_status(fail_id)
    expect(failed["status"] == "failed", f"expected failed: {failed}")
    expect("NameError" in failed["error"], f"error missing: {failed}")

    all_jobs = async_job_status(None)
    expect(all_jobs["success"] is True and len(all_jobs["jobs"]) >= 2,
           f"jobs listing wrong: {all_jobs}")

    unknown = async_job_status("nope")
    expect(unknown["success"] is False, "unknown id should fail")
    expect(job_id in unknown["known_job_ids"], f"known ids missing: {unknown}")


check("code_exec: namespace persists and preloads", t_namespace_persists_and_preloads)
check("code_exec: runtime error formatted with line", t_format_runtime_error_has_line)
check("code_exec: syntax error formatted with line", t_format_syntax_error_has_line)
check("code_exec: async registry lifecycle", t_async_registry_lifecycle)


# --------------------------------------------------------------------------
# result
# --------------------------------------------------------------------------
# freecadcmd exits 0 even when the script raises, so callers must grep for
# the sentinel below rather than relying on the exit code.
p(f"\n{_passed} passed, {len(_failures)} failed")
if _failures:
    p("Failed checks:")
    for name in _failures:
        p(f"  - {name}")
else:
    p("ALL ADDON TESTS PASSED")
sys.exit(1 if _failures else 0)
