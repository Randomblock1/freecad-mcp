"""Formatting tests for measure/topology/save/export/report-log operations."""

from freecad_mcp.operations import core

from .test_core_operations import FakeFreeCAD, text_of


def test_measure_distance_success_json():
    fake = FakeFreeCAD(
        measure_distance={
            "success": True,
            "distance_mm": 5.0,
            "closest_point_on_1": [10, 0, 0],
            "closest_point_on_2": [15, 0, 0],
        }
    )
    text = text_of(core.measure_distance_operation(fake, "Doc", "A", "B"))
    assert '"distance_mm":5.0' in text
    assert ("measure_distance", ("Doc", "A", "B", None, None)) in fake.calls


def test_measure_distance_error_lists_available():
    fake = FakeFreeCAD(
        measure_distance={
            "success": False,
            "error": "Object 'Bxo' not found in 'Doc'. Available: ['Box']",
        }
    )
    text = text_of(core.measure_distance_operation(fake, "Doc", "A", "Bxo"))
    assert "Box" in text and "Failed" in text


def test_get_topology_passes_flags_and_returns_json():
    fake = FakeFreeCAD(
        get_topology={
            "success": True,
            "faces": [{"Name": "Face1", "Type": "Plane"}],
            "counts": {"Faces": 1, "Edges": 4, "Vertexes": 4},
        }
    )
    text = text_of(core.get_topology_operation(fake, "Doc", "Box", True))
    assert ("get_topology", ("Doc", "Box", True)) in fake.calls
    assert '"counts"' in text and "Face1" in text


def test_get_topology_error_branch_lists_available():
    fake = FakeFreeCAD(
        get_topology={
            "success": False,
            "error": "Object 'Bxo' not found in 'Doc'. Available: ['Box']",
        }
    )
    text = text_of(core.get_topology_operation(fake, "Doc", "Bxo"))
    assert "Failed to get topology" in text and "Box" in text


def test_save_document_reports_path():
    fake = FakeFreeCAD(
        save_document={"success": True, "document_name": "Doc", "file_path": "/tmp/doc.FCStd"}
    )
    text = text_of(core.save_document_operation(fake, "Doc"))
    assert "/tmp/doc.FCStd" in text


def test_save_document_never_saved_error():
    fake = FakeFreeCAD(
        save_document={"success": False, "error": "Document 'Doc' has never been saved; pass file_path."}
    )
    text = text_of(core.save_document_operation(fake, "Doc"))
    assert "file_path" in text


def test_export_document_reports_size_and_objects():
    fake = FakeFreeCAD(
        export_document={
            "success": True,
            "file_path": "/tmp/out.step",
            "file_size_bytes": 4321,
            "exported_objects": ["Box"],
        }
    )
    text = text_of(core.export_document_operation(fake, "Doc", "/tmp/out.step"))
    assert "4321" in text and "Box" in text


def test_export_document_passes_deflection_and_surfaces_note():
    fake = FakeFreeCAD(
        export_document={
            "success": True,
            "file_path": "/tmp/out.stl",
            "file_size_bytes": 9000,
            "exported_objects": ["Cyl"],
            "note": "linear_deflection ignored: ...",
        }
    )
    text = text_of(
        core.export_document_operation(fake, "Doc", "/tmp/out.stl", ["Cyl"], 0.05)
    )
    assert ("export_document", ("Doc", "/tmp/out.stl", ["Cyl"], 0.05)) in fake.calls
    assert "Note:" in text


def test_check_printability_passes_args_and_returns_json():
    fake = FakeFreeCAD(
        check_printability={
            "success": True,
            "manifold": {"is_watertight": True, "is_solid": True},
            "wall_thickness": {"min_mm": 2.0, "below_threshold": True},
        }
    )
    text = text_of(
        core.check_printability_operation(fake, "Doc", "Part", 3.0, True, "Z", 40.0)
    )
    assert ("check_printability", ("Doc", "Part", 3.0, True, "Z", 40.0)) in fake.calls
    assert "wall_thickness" in text and "is_watertight" in text


def test_check_printability_error_names_available():
    fake = FakeFreeCAD(
        check_printability={
            "success": False,
            "error": "Object 'Nope' not found in 'Doc'. Available: ['Box']",
        }
    )
    text = text_of(core.check_printability_operation(fake, "Doc", "Nope"))
    assert "Box" in text and "Failed" in text


def test_get_section_view_returns_labeled_image():
    fake = FakeFreeCAD(
        get_section_screenshot={"success": True, "image": "sectionpng"}
    )
    resp = core.get_section_view_operation(fake, False, "Doc", "XZ", 5.0)
    images = [p for p in resp if p.type == "image"]
    labels = [p.text for p in resp if p.type == "text"]
    assert len(images) == 1 and images[0].data == "sectionpng"
    assert any("Section view" in ln and "XZ" in ln for ln in labels)
    assert ("get_section_screenshot", ("Doc", "XZ", 5.0, None, "Isometric")) in fake.calls


def test_get_section_view_error_names_available():
    fake = FakeFreeCAD(
        get_section_screenshot={
            "success": False,
            "error": "Object 'Bxo' not found in 'Doc'. Available: ['Box']",
        }
    )
    text = text_of(core.get_section_view_operation(fake, False, "Doc", object_names=["Bxo"]))
    assert "Box" in text and "Failed" in text


def test_get_section_view_respects_only_text_feedback():
    fake = FakeFreeCAD(get_section_screenshot={"success": True, "image": "x"})
    resp = core.get_section_view_operation(fake, True, "Doc")
    assert not fake.called("get_section_screenshot")
    assert all(p.type == "text" for p in resp)


def test_get_views_returns_labeled_image_per_view():
    fake = FakeFreeCAD(get_active_screenshot="b64png")
    resp = core.get_views_operation(fake, False, ["Front", "Top", "Isometric"])
    labels = [p.text for p in resp if p.type == "text"]
    images = [p for p in resp if p.type == "image"]
    assert len(images) == 3
    assert any("Front" in ln for ln in labels)
    assert any("Top" in ln for ln in labels)
    # one capture call per requested view
    assert sum(1 for c in fake.calls if c[0] == "get_active_screenshot") == 3


def test_get_views_notes_unavailable_view():
    fake = FakeFreeCAD(get_active_screenshot=None)  # e.g. TechDraw active view
    resp = core.get_views_operation(fake, False, ["Front"])
    text = text_of(resp)
    assert "unavailable" in text.lower()


def test_get_views_respects_only_text_feedback():
    fake = FakeFreeCAD(get_active_screenshot="b64png")
    resp = core.get_views_operation(fake, True, ["Front", "Top"])
    assert not fake.called("get_active_screenshot")
    assert all(p.type == "text" for p in resp)


def test_get_report_log_formats():
    fake = FakeFreeCAD(
        get_report_log={
            "success": True,
            "log": "line a\nline b",
            "total_lines": 500,
            "returned_lines": 2,
        }
    )
    text = text_of(core.get_report_log_operation(fake, 2))
    assert "line b" in text and "500" in text
