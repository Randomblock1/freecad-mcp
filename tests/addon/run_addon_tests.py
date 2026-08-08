"""Headless addon-side tests, run with: freecadcmd tests/addon/run_addon_tests.py

Exercises the rpc_server modules that don't need a GUI (serialize, object
factory, geometry tools, document IO) against real FreeCAD documents.

Pass/fail contract: freecadcmd's process exit code is NOT reliable (it exits 0
even on an uncaught exception and re-executes the script), so callers must
check the printed sentinel: success prints "ALL ADDON TESTS PASSED" as the last
line. Its absence — whether from a failed check or a crash during import/setup
(which this file catches and turns into a sys.exit(1), the one path freecadcmd
does propagate) — means failure.
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


# All rpc_server imports and shared fixtures live in one guarded block. A
# refactor that renames or moves a symbol raises here at module level, which
# freecadcmd would otherwise swallow (exit 0, re-execute) — so we catch it and
# route it through sys.exit(1), the one exit path freecadcmd propagates, and
# the success sentinel never prints. Individual check() bodies are guarded too.
try:
    from rpc_server.serialize import (  # noqa: E402
        SCAFFOLDING_TYPE_IDS,
        serialize_object,
        serialize_shape,
        serialize_value,
        summarize_object,
    )
    from rpc_server.object_factory import create_object_gui  # noqa: E402
    from rpc_server.property_mapper import Object  # noqa: E402
    from rpc_server.document_query import query_object, query_objects  # noqa: E402
    from rpc_server.code_exec import (  # noqa: E402
        RollbackInProgress,
        async_job_status,
        complete_async_job,
        exec_namespace,
        rollback_guard,
        format_exec_error,
        rollback_documents,
        start_async_job,
        xml_safe,
    )
    from rpc_server.view_manager import DEFAULT_MAX_EDGE, _resolve_screenshot_size  # noqa: E402
    from rpc_server.geometry_tools import (  # noqa: E402
        check_printability,
        get_topology,
        measure_distance,
        section_shape,
    )
    from rpc_server.document_io import export_document, save_document  # noqa: E402
    import rpc_server.code_exec as _code_exec  # noqa: E402  # for isolation check

    # Close any stale AddonTests doc left by a prior crashed run, then build the
    # shared fixture doc + box. Address the doc by doc.Name, never the requested
    # name, since FreeCAD may have deduplicated it.
    for _stale in list(FreeCAD.listDocuments()):
        if _stale.startswith("AddonTests"):
            FreeCAD.closeDocument(_stale)
    doc = FreeCAD.newDocument("AddonTests")
    DOC = doc.Name

    box = doc.addObject("Part::Box", "Box")
    box.Length = 10
    box.Width = 10
    box.Height = 10
    doc.recompute()

    _tmpdir = tempfile.mkdtemp(prefix="freecad_mcp_test_")
except BaseException:
    p("ADDON TESTS CRASHED DURING SETUP")
    p(traceback.format_exc())
    sys.exit(1)


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
def t_create_returns_actual_name_on_collision():
    first = create_object_gui(DOC, Object(name="DupBox", type="Part::Box"))
    second = create_object_gui(DOC, Object(name="DupBox", type="Part::Box"))
    expect(first["success"] is True, f"first create failed: {first}")
    expect(first["object_name"] == "DupBox", f"unexpected first name: {first}")
    expect(second["success"] is True, f"second create failed: {second}")
    expect(second["object_name"] == "DupBox001",
           f"expected DupBox001, got {second['object_name']}")
    expect(second["requested_name"] == "DupBox", f"requested_name missing: {second}")
    doc.removeObject("DupBox")
    doc.removeObject("DupBox001")


def t_create_invalid_cut_warns():
    res = create_object_gui(DOC, Object(name="WarnCut", type="Part::Cut"))
    expect(res["success"] is True, f"create failed outright: {res}")
    expect("Invalid" in res.get("state", []), f"state missing Invalid: {res}")
    expect("warning" in res, f"warning missing: {res}")
    doc.removeObject("WarnCut")


def t_create_unknown_doc_errors():
    res = create_object_gui("NoSuchDoc", Object(name="X", type="Part::Box"))
    expect(isinstance(res, str) and "not found" in res, f"expected error string: {res!r}")


def t_material_float_value_coerced():
    # property_mapper stringifies Material map values so a numeric PoissonRatio
    # (0.3) is accepted rather than hard-failing on FreeCAD's string-only map.
    an = create_object_gui(DOC, Object(name="Analysis1", type="Fem::AnalysisPython"))
    expect(an["success"], f"analysis create failed: {an}")
    mat = create_object_gui(DOC, Object(
        name="Mat1", type="Fem::MaterialCommon", analysis="Analysis1",
        properties={"Material": {"Name": "Steel", "YoungsModulus": "210 GPa",
                                 "PoissonRatio": 0.3}}))
    expect(mat["success"], f"material create failed (coercion regressed?): {mat}")
    m = doc.getObject(mat["object_name"])
    expect(m.Material.get("PoissonRatio") == "0.3",
           f"float PoissonRatio not coerced to string: {dict(m.Material)}")
    doc.removeObject(mat["object_name"])
    doc.removeObject("Analysis1")


check("object_factory: name collision returns actual name", t_create_returns_actual_name_on_collision)
check("object_factory: invalid cut warns", t_create_invalid_cut_warns)
check("object_factory: Material float value coerced", t_material_float_value_coerced)
check("object_factory: unknown doc errors", t_create_unknown_doc_errors)


# --------------------------------------------------------------------------
# document_query: structured errors + detail levels
# --------------------------------------------------------------------------
def t_query_objects_summary_omits_scaffolding():
    body = doc.addObject("PartDesign::Body", "QueryBody")
    doc.recompute()
    res = query_objects(DOC, "summary")
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
    res = query_objects(DOC, "full")
    type_ids = {o.get("TypeId") for o in res["objects"]}
    expect("App::Origin" in type_ids, "full detail should keep scaffolding")
    expect("omitted_origin_scaffolding" not in res, "full detail should omit nothing")
    doc.removeObject(body.Name)


def t_query_objects_unknown_doc():
    res = query_objects("NoSuchDoc", "summary")
    expect(res["success"] is False, "expected failure")
    expect(DOC in res["open_documents"], f"open docs missing: {res}")


def t_query_object_unknown_object():
    res = query_object(DOC, "Bxo")
    expect(res["success"] is False, "expected failure")
    expect("Box" in res["available_objects"], f"available missing Box: {res}")


def t_query_object_success():
    res = query_object(DOC, "Box")
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
def t_namespace_persists_and_preloads():
    ns = exec_namespace()
    expect(ns["App"] is FreeCAD, "App not preloaded")
    exec("probe_var = 41", ns)
    exec("probe_var += 1", exec_namespace())
    expect(exec_namespace()["probe_var"] == 42, "namespace did not persist between execs")
    # The whole point of the fork's exec-namespace fix: user code must NOT run in
    # any rpc_server module's globals (the old bug was exec(code, globals()) in
    # rpc_server.py, letting scripts clobber RPC internals). Assert against the
    # module dict that would actually be at risk, and prove a clobber is contained.
    expect(ns is not vars(_code_exec), "exec namespace must not be code_exec's globals")
    exec("format_exec_error = 'clobbered'", exec_namespace())
    expect(callable(_code_exec.format_exec_error),
           "user code clobbered a code_exec module attribute — namespace not isolated")
    del exec_namespace()["format_exec_error"]


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


def t_rollback_guard_blocks_on_running_job():
    # dry-run execute_code and section screenshots abort undo transactions on
    # documents an async worker may be mutating, so they must refuse while any
    # job is running rather than discard the worker's changes.
    with rollback_guard("x") as busy:
        expect(busy is None, "no jobs running, guard should allow")
    job_id = start_async_job("import time; time.sleep(1)")
    try:
        with rollback_guard("execute_code with dry_run") as blocked:
            expect(blocked is not None, "guard should block while a job runs")
            expect(job_id in blocked, f"message should name the job: {blocked}")
            expect("get_async_status" in blocked, f"message should say how to wait: {blocked}")
    finally:
        complete_async_job(job_id, None)
    with rollback_guard("x") as busy:
        expect(busy is None, "guard should allow once the job finishes")


def t_rollback_guard_blocks_new_jobs():
    # The other half of the guard: holding the reservation must stop a NEW job
    # from being admitted, or it could start between the check and the
    # transaction opening on the GUI thread and get rolled back underneath.
    with rollback_guard("execute_code with dry_run") as busy:
        expect(busy is None, "guard should be held")
        try:
            leaked = start_async_job("pass")
        except RollbackInProgress as e:
            expect("dry_run" in str(e), f"message should name the blocking op: {e}")
            expect("Retry" in str(e), f"message should tell the caller to retry: {e}")
        else:
            complete_async_job(leaked, None)
            raise AssertionError("job was admitted while the guard was held")
    # Reservation released on exit, so jobs are admitted again.
    job_id = start_async_job("pass")
    complete_async_job(job_id, None)
    expect(async_job_status(job_id)["status"] == "completed", "job should run after release")


def t_rollback_guard_releases_on_error():
    # A failing dry run must not strand the reservation and wedge every future
    # async job for the rest of the session.
    try:
        with rollback_guard("execute_code with dry_run") as busy:
            expect(busy is None, "guard should be held")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    job_id = start_async_job("pass")  # raises RollbackInProgress if still held
    complete_async_job(job_id, None)


def t_format_system_exit_is_failure():
    # sys.exit() raises SystemExit (a BaseException, not Exception); the async
    # worker must treat it as a failure, not a clean finish.
    try:
        exec("import sys\nsys.exit('aborted')", exec_namespace())
    except BaseException as e:
        detail = format_exec_error(e)
    expect(detail["success"] is False, "SystemExit must format as failure")
    expect("SystemExit" in detail["error"], f"bad error: {detail['error']}")


def t_xml_safe_strips_control_chars():
    out = xml_safe("red\x1b[31mtext\x00end\ttab\nnewline")
    expect("\x1b" not in out and "\x00" not in out, f"control chars remain: {out!r}")
    expect("\t" in out and "\n" in out, "tab/newline must be preserved")
    expect("redtext" not in out, "expected replacement char between segments")


def t_rollback_reverts_edits_and_new_objects():
    # Edit an existing object and add a new one inside the block; both revert.
    box.Length = 10
    doc.recompute()
    with rollback_documents():
        box.Length = 999
        doc.addObject("Part::Box", "RollbackTmp")
        doc.recompute()
    expect(abs(box.Length.Value - 10) < 1e-9, f"edit not rolled back: Length={box.Length}")
    expect(doc.getObject("RollbackTmp") is None, "added object not rolled back")


def t_rollback_closes_new_documents():
    before = set(FreeCAD.listDocuments())
    with rollback_documents():
        FreeCAD.newDocument("RollbackScratch")
        expect("RollbackScratch" in FreeCAD.listDocuments(), "scratch doc not created")
    expect(set(FreeCAD.listDocuments()) == before, "new document not closed on rollback")


check("code_exec: namespace persists and preloads", t_namespace_persists_and_preloads)
check("code_exec: runtime error formatted with line", t_format_runtime_error_has_line)
check("code_exec: syntax error formatted with line", t_format_syntax_error_has_line)
check("code_exec: SystemExit formats as failure", t_format_system_exit_is_failure)
check("code_exec: xml_safe strips control chars", t_xml_safe_strips_control_chars)
check("code_exec: rollback reverts edits + new objects", t_rollback_reverts_edits_and_new_objects)
check("code_exec: rollback closes new documents", t_rollback_closes_new_documents)
check("code_exec: async registry lifecycle", t_async_registry_lifecycle)
check("code_exec: rollback guard blocks on running job", t_rollback_guard_blocks_on_running_job)
check("code_exec: rollback guard blocks new jobs", t_rollback_guard_blocks_new_jobs)
check("code_exec: rollback guard releases on error", t_rollback_guard_releases_on_error)


# --------------------------------------------------------------------------
# view_manager: default screenshot size cap
# --------------------------------------------------------------------------
class _StubView:
    def __init__(self, w, h):
        self._size = (w, h)

    def getSize(self):
        return self._size


def t_screenshot_default_capped():
    w, h = _resolve_screenshot_size(_StubView(1014, 1159), None, None)
    expect(max(w, h) == DEFAULT_MAX_EDGE, f"longest edge should be {DEFAULT_MAX_EDGE}: {(w, h)}")
    expect((w, h) == (700, 800), f"expected (700, 800), got {(w, h)}")


def t_screenshot_small_viewport_untouched():
    expect(_resolve_screenshot_size(_StubView(640, 480), None, None) == (640, 480),
           "small viewport should pass through")


def t_screenshot_explicit_size_honored():
    expect(_resolve_screenshot_size(_StubView(1014, 1159), 1600, 1200) == (1600, 1200),
           "explicit size must be honored uncapped")
    expect(_resolve_screenshot_size(_StubView(1014, 1159), None, 400) == (1014, 400),
           "partial explicit size keeps viewport for the other dimension")


check("view_manager: default screenshot capped at 800", t_screenshot_default_capped)
check("view_manager: small viewport untouched", t_screenshot_small_viewport_untouched)
check("view_manager: explicit size honored", t_screenshot_explicit_size_honored)


# --------------------------------------------------------------------------
# geometry_tools: measure_distance + get_topology
# --------------------------------------------------------------------------
def t_measure_distance_between_boxes():
    far = doc.addObject("Part::Box", "FarBox")
    far.Length = 10
    far.Placement = FreeCAD.Placement(FreeCAD.Vector(15, 0, 0), FreeCAD.Rotation())
    doc.recompute()
    res = measure_distance(DOC, "Box", "FarBox")
    expect(res["success"] is True, f"failed: {res}")
    expect(abs(res["distance_mm"] - 5.0) < 1e-6, f"expected 5mm, got {res['distance_mm']}")
    expect(len(res["closest_point_on_1"]) == 3, f"points missing: {res}")
    doc.removeObject("FarBox")


def t_measure_distance_overlap_reports_interference():
    near = doc.addObject("Part::Box", "NearBox")
    near.Length = 10
    near.Placement = FreeCAD.Placement(FreeCAD.Vector(5, 0, 0), FreeCAD.Rotation())
    doc.recompute()
    res = measure_distance(DOC, "Box", "NearBox")
    expect(res["success"] is True, f"failed: {res}")
    expect(res["distance_mm"] == 0.0, f"expected 0, got {res['distance_mm']}")
    expect(abs(res["intersection_volume_mm3"] - 500.0) < 1e-6,
           f"expected 500mm3 interference, got {res.get('intersection_volume_mm3')}")
    doc.removeObject("NearBox")


def t_measure_distance_sub_element():
    far = doc.addObject("Part::Box", "SubBox")
    far.Placement = FreeCAD.Placement(FreeCAD.Vector(30, 0, 0), FreeCAD.Rotation())
    doc.recompute()
    res = measure_distance(DOC, "Box", "SubBox", "Face1", None)
    expect(res["success"] is True, f"failed: {res}")
    expect(res["distance_mm"] >= 20.0, f"unexpected distance: {res['distance_mm']}")
    bad = measure_distance(DOC, "Box", "SubBox", "Face99", None)
    expect(bad["success"] is False and "Face99" in bad["error"], f"bad sub error: {bad}")
    doc.removeObject("SubBox")


def t_measure_distance_unknown_object():
    res = measure_distance(DOC, "Box", "Bxo")
    expect(res["success"] is False, "expected failure")
    expect("Box" in res["error"], f"error should list available: {res['error']}")


def t_topology_of_box():
    res = get_topology(DOC, "Box")
    expect(res["success"] is True, f"failed: {res}")
    faces = res["faces"]
    expect(len(faces) == 6, f"expected 6 faces, got {len(faces)}")
    expect(all(f["Type"] == "Plane" for f in faces), f"non-planar face reported: {faces}")
    expect(all(abs(f["Area"] - 100.0) < 1e-6 for f in faces), "areas wrong")
    for f in faces:
        n = f["Normal"]
        expect(abs(sum(c * c for c in n) - 1.0) < 1e-6, f"normal not unit: {n}")
        expect(len(f["Centroid"]) == 3, "centroid missing")
    expect(res["counts"]["Faces"] == 6 and res["counts"]["Edges"] == 12
           and res["counts"]["Vertexes"] == 8, f"bad counts: {res['counts']}")
    expect("edges" not in res, "edges should be omitted by default")
    withe = get_topology(DOC, "Box", True)
    expect(len(withe["edges"]) == 12, "expected 12 edges")
    expect(all(abs(e["Length"] - 10.0) < 1e-6 for e in withe["edges"]), "edge lengths wrong")


def t_topology_unknown_object():
    res = get_topology(DOC, "Bxo")
    expect(res["success"] is False and "Box" in res["error"], f"bad error: {res}")


def t_topology_sphere_degenerate_edges():
    # A sphere has degenerate pole edges whose .Curve raises "undefined curve
    # type"; get_topology(include_edges=True) must survive it, not fault the call.
    import Part

    sph = doc.addObject("Part::Feature", "Sphere")
    sph.Shape = Part.makeSphere(5)
    doc.recompute()
    res = get_topology(DOC, "Sphere", True)
    expect(res["success"] is True, f"sphere topology faulted: {res}")
    expect(len(res["edges"]) == res["counts"]["Edges"], "edge list truncated unexpectedly")
    expect(any(e["Type"] == "unknown" for e in res["edges"]),
           "degenerate edge should report Type 'unknown', not crash")
    doc.removeObject("Sphere")


check("geometry: distance between boxes", t_measure_distance_between_boxes)
check("geometry: overlap interference volume", t_measure_distance_overlap_reports_interference)
check("geometry: sub-element distance + bad sub error", t_measure_distance_sub_element)
check("geometry: unknown object error", t_measure_distance_unknown_object)
check("geometry: topology of box", t_topology_of_box)
check("geometry: topology unknown object", t_topology_unknown_object)
def t_section_shape_removes_half():
    import Part

    box = Part.makeBox(10, 10, 10)
    # XY plane at z=5 removes the z>5 half -> volume 500, bbox z in [0,5]
    sec = section_shape(box, "XY", 5.0)
    expect(abs(sec.Volume - 500.0) < 1e-6, f"expected 500 mm3, got {sec.Volume}")
    expect(abs(sec.BoundBox.ZMax - 5.0) < 1e-6, f"ZMax should be 5, got {sec.BoundBox.ZMax}")
    # default offset = center along normal -> also half
    sec2 = section_shape(box, "YZ", None)
    expect(abs(sec2.Volume - 500.0) < 1e-6, f"default-offset YZ expected 500, got {sec2.Volume}")


def t_printability_hollow_box_wall_thickness():
    import Part

    outer = doc.addObject("Part::Feature", "Hollow")
    outer.Shape = Part.makeBox(20, 20, 20).cut(
        Part.makeBox(16, 16, 16, FreeCAD.Vector(2, 2, 2))
    )
    doc.recompute()
    res = check_printability(DOC, "Hollow", min_wall_thickness=3.0)
    expect(res["success"] is True, f"failed: {res}")
    expect(res["manifold"]["is_solid"] and res["manifold"]["is_watertight"],
           f"hollow box should be a watertight solid: {res['manifold']}")
    wt = res["wall_thickness"]
    expect(abs(wt["min_mm"] - 2.0) < 0.3, f"min wall ~2 expected, got {wt['min_mm']}")
    expect(wt["below_threshold"] is True, f"2mm wall should be below 3mm threshold: {wt}")
    expect("overhangs" not in res, "overhangs must be opt-in (absent by default)")
    doc.removeObject("Hollow")


def t_printability_open_shell_not_watertight():
    import Part

    shell = doc.addObject("Part::Feature", "OpenShell")
    shell.Shape = Part.makeBox(10, 10, 10).Faces[0]  # a single face, not closed
    doc.recompute()
    res = check_printability(DOC, "OpenShell")
    expect(res["success"] is True, f"failed: {res}")
    expect(res["manifold"]["is_watertight"] is False, "single face is not watertight")
    expect(res["manifold"]["is_solid"] is False, "single face is not a solid")
    doc.removeObject("OpenShell")


def t_printability_overhang_opt_in():
    import Part

    # Base on the plate + a floating slab whose underside is a real overhang.
    comp = None
    try:
        base = Part.makeBox(20, 20, 4)
        slab = Part.makeBox(20, 20, 4, FreeCAD.Vector(0, 0, 10))
        comp = doc.addObject("Part::Feature", "Cantilever")
        comp.Shape = Part.makeCompound([base, slab])
        doc.recompute()
        res = check_printability(DOC, comp.Name, include_overhangs=True, max_overhang_deg=45)
        expect("overhangs" in res, "overhangs should be present when include_overhangs=True")
        oh = res["overhangs"]
        expect(oh["count"] >= 1, f"expected at least one overhang face, got {oh}")
        expect(oh["worst_angle_deg"] > 45, f"worst overhang should exceed 45deg: {oh}")
    finally:
        if comp is not None:
            doc.removeObject(comp.Name)


def t_printability_non_canonical_build_direction():
    # A non-canonical direction falls back to Z for BOTH the direction vector
    # and the axis index. A substring test against "XYZ" would accept "XY" and
    # measure the build-plate exclusion along X while pointing d at Z.
    import Part

    # Setup inside the try: a failure in makeCompound/recompute would otherwise
    # strand the object in the shared fixture doc and break unrelated checks.
    # Remove by comp.Name, not the literal: FreeCAD deduplicates names, so a
    # stale leftover would make this delete the wrong object.
    comp = None
    try:
        base = Part.makeBox(20, 20, 4)
        slab = Part.makeBox(20, 20, 4, FreeCAD.Vector(0, 0, 10))
        comp = doc.addObject("Part::Feature", "AxisFallback")
        comp.Shape = Part.makeCompound([base, slab])
        doc.recompute()
        as_z = check_printability(DOC, comp.Name, include_overhangs=True,
                                  max_overhang_deg=45)["overhangs"]
        odd = check_printability(DOC, comp.Name, include_overhangs=True,
                                 max_overhang_deg=45, build_direction="XY")["overhangs"]
        expect(odd["count"] == as_z["count"],
               f"non-canonical direction should fall back to Z: {odd} vs {as_z}")
        expect(odd["worst_angle_deg"] == as_z["worst_angle_deg"],
               f"worst angle should match the Z fallback: {odd} vs {as_z}")
    finally:
        if comp is not None:
            doc.removeObject(comp.Name)


def t_printability_unknown_object():
    res = check_printability(DOC, "Nope")
    expect(res["success"] is False and "Box" in res["error"], f"bad error: {res}")


check("geometry: sphere degenerate edges survive", t_topology_sphere_degenerate_edges)
check("geometry: section_shape removes half", t_section_shape_removes_half)
check("geometry: printability hollow-box wall thickness", t_printability_hollow_box_wall_thickness)
check("geometry: printability open shell not watertight", t_printability_open_shell_not_watertight)
check("geometry: printability overhang opt-in", t_printability_overhang_opt_in)
check("geometry: printability non-canonical build direction",
      t_printability_non_canonical_build_direction)
check("geometry: printability unknown object", t_printability_unknown_object)


# --------------------------------------------------------------------------
# document_io: save + export
# --------------------------------------------------------------------------
def t_save_unsaved_doc_requires_path():
    res = save_document(DOC)
    expect(res["success"] is False, "expected failure for never-saved doc")
    expect("file_path" in res["error"], f"error should mention file_path: {res['error']}")


def t_save_document_with_path():
    path = os.path.join(_tmpdir, "addon_tests.FCStd")
    res = save_document(DOC, path)
    expect(res["success"] is True, f"failed: {res}")
    expect(os.path.exists(path) and os.path.getsize(path) > 0, "file missing/empty")
    expect(res["file_path"] == path, f"bad path: {res}")
    res2 = save_document(DOC)  # now has a FileName; plain save must work
    expect(res2["success"] is True, f"re-save failed: {res2}")


def t_export_step_and_stl():
    for ext in (".step", ".stl"):
        path = os.path.join(_tmpdir, f"box{ext}")
        res = export_document(DOC, path, ["Box"])
        expect(res["success"] is True, f"{ext} failed: {res}")
        expect(os.path.getsize(path) > 100, f"{ext} file suspiciously small")
        expect(res["exported_objects"] == ["Box"], f"bad exported list: {res}")


def t_export_default_objects():
    path = os.path.join(_tmpdir, "all.step")
    res = export_document(DOC, path)
    expect(res["success"] is True, f"failed: {res}")
    expect("Box" in res["exported_objects"], f"Box missing from default export: {res}")


def t_export_bad_extension():
    res = export_document(DOC, os.path.join(_tmpdir, "box.xyz"))
    expect(res["success"] is False, "expected failure")
    expect(".step" in res["error"], f"error should list supported formats: {res['error']}")


def t_export_unknown_object():
    res = export_document(DOC, os.path.join(_tmpdir, "nope.step"), ["Bxo"])
    expect(res["success"] is False and "Box" in res["error"], f"bad error: {res}")


def t_export_mesh_deflection_controls_size():
    # Finer linear_deflection => more triangles => larger STL, on a curved shape.
    import Part

    cyl = doc.addObject("Part::Feature", "DefCyl")
    cyl.Shape = Part.makeCylinder(5, 10)
    doc.recompute()
    coarse = os.path.join(_tmpdir, "cyl_coarse.stl")
    fine = os.path.join(_tmpdir, "cyl_fine.stl")
    rc = export_document(DOC, coarse, ["DefCyl"], linear_deflection=2.0)
    rf = export_document(DOC, fine, ["DefCyl"], linear_deflection=0.05)
    expect(rc["success"] and rf["success"], f"export failed: {rc}, {rf}")
    expect(os.path.getsize(fine) > os.path.getsize(coarse),
           f"fine STL ({os.path.getsize(fine)}) not larger than coarse ({os.path.getsize(coarse)})")
    doc.removeObject("DefCyl")


def t_export_deflection_ignored_for_cad():
    res = export_document(DOC, os.path.join(_tmpdir, "box_defl.step"), ["Box"],
                          linear_deflection=0.1)
    expect(res["success"] is True, f"CAD export with deflection failed: {res}")
    expect("deflection" in (res.get("note") or "").lower(),
           f"expected a note that deflection was ignored: {res}")


check("document_io: unsaved doc requires path", t_save_unsaved_doc_requires_path)
check("document_io: saveAs + save", t_save_document_with_path)
check("document_io: export STEP and STL", t_export_step_and_stl)
check("document_io: export default objects", t_export_default_objects)
check("document_io: export bad extension", t_export_bad_extension)
check("document_io: export unknown object", t_export_unknown_object)
check("document_io: mesh deflection controls size", t_export_mesh_deflection_controls_size)
check("document_io: deflection ignored for CAD", t_export_deflection_ignored_for_cad)


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
