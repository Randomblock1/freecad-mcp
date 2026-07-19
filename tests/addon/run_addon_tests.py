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
