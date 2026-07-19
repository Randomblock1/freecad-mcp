"""MCP-side operation tests against a fake FreeCAD connection."""

from freecad_mcp.operations import core


class FakeFreeCAD:
    """Stands in for FreeCADConnection; records calls, returns canned responses."""

    def __init__(self, **responses):
        self._responses = responses
        self.calls = []

    def __getattr__(self, name):
        def method(*args):
            self.calls.append((name, args))
            resp = self._responses[name]
            return resp(*args) if callable(resp) else resp

        return method

    def called(self, name):
        return any(c[0] == name for c in self.calls)


def text_of(response):
    return "\n".join(part.text for part in response if part.type == "text")


# --------------------------------------------------------------------------
# Actual-name returns (audit #1)
# --------------------------------------------------------------------------

def test_create_document_reports_actual_name():
    fake = FakeFreeCAD(
        create_document={
            "success": True,
            "document_name": "My_Test_Doc",
            "requested_name": "My Test Doc",
        }
    )
    text = text_of(core.create_document_operation(fake, "My Test Doc"))
    assert "My_Test_Doc" in text
    assert "requested" in text.lower()


def test_create_document_no_note_when_names_match():
    fake = FakeFreeCAD(
        create_document={
            "success": True,
            "document_name": "Chassis",
            "requested_name": "Chassis",
        }
    )
    text = text_of(core.create_document_operation(fake, "Chassis"))
    assert "Chassis" in text
    assert "requested" not in text.lower()


def test_create_object_reports_assigned_name():
    fake = FakeFreeCAD(
        create_object={
            "success": True,
            "object_name": "DupBox001",
            "requested_name": "DupBox",
        },
        get_active_screenshot=None,
    )
    text = text_of(
        core.create_object_operation(fake, False, "Doc", "Part::Box", "DupBox")
    )
    assert "DupBox001" in text
    assert "requested" in text.lower()


# --------------------------------------------------------------------------
# Recompute state surfacing (audit #3)
# --------------------------------------------------------------------------

def test_create_object_surfaces_recompute_warning():
    fake = FakeFreeCAD(
        create_object={
            "success": True,
            "object_name": "BadCut",
            "requested_name": "BadCut",
            "state": ["Touched", "Invalid"],
            "warning": "Object 'BadCut' did not recompute cleanly (state: ['Touched', 'Invalid']).",
        },
        get_active_screenshot=None,
    )
    text = text_of(
        core.create_object_operation(fake, False, "Doc", "Part::Cut", "BadCut")
    )
    assert "WARNING" in text
    assert "Invalid" in text


def test_edit_object_surfaces_recompute_warning():
    fake = FakeFreeCAD(
        edit_object={
            "success": True,
            "object_name": "Cut",
            "state": ["Invalid"],
            "warning": "Object 'Cut' did not recompute cleanly (state: ['Invalid']).",
        },
        get_active_screenshot=None,
    )
    text = text_of(core.edit_object_operation(fake, False, "Doc", "Cut", {"Base": "Box"}))
    assert "WARNING" in text


# --------------------------------------------------------------------------
# Structured not-found errors, no screenshot on failure (audit #5)
# --------------------------------------------------------------------------

def test_get_objects_unknown_doc_errors_without_screenshot():
    fake = FakeFreeCAD(
        get_objects={
            "success": False,
            "error": "Document 'Chasis' not found.",
            "open_documents": ["Chassis", "Wheels"],
        }
    )
    text = text_of(core.get_objects_operation(fake, False, "Chasis"))
    assert "not found" in text
    assert "Chassis" in text  # names what IS available
    assert not fake.called("get_active_screenshot")


def test_get_object_unknown_object_errors_without_screenshot():
    fake = FakeFreeCAD(
        get_object={
            "success": False,
            "error": "Object 'Bxo' not found in document 'Doc'.",
            "available_objects": ["Box", "Cylinder"],
        }
    )
    text = text_of(core.get_object_operation(fake, False, "Doc", "Bxo"))
    assert "not found" in text
    assert "Box" in text
    assert not fake.called("get_active_screenshot")


def test_get_objects_success_passes_detail_and_screenshots():
    fake = FakeFreeCAD(
        get_objects={"success": True, "doc_name": "Doc", "detail": "full", "objects": []},
        get_active_screenshot=None,
    )
    core.get_objects_operation(fake, False, "Doc", detail="full")
    assert ("get_objects", ("Doc", "full")) in fake.calls
    assert fake.called("get_active_screenshot")


def test_get_object_success_returns_json():
    fake = FakeFreeCAD(
        get_object={"success": True, "object": {"Name": "Box", "TypeId": "Part::Box"}},
        get_active_screenshot=None,
    )
    text = text_of(core.get_object_operation(fake, False, "Doc", "Box"))
    assert '"Name"' in text and "Box" in text
