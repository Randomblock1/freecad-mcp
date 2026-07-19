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


def test_get_topology_passes_flags():
    fake = FakeFreeCAD(
        get_topology={"success": True, "faces": [], "counts": {"Faces": 0, "Edges": 0, "Vertexes": 0}}
    )
    core.get_topology_operation(fake, "Doc", "Box", True)
    assert ("get_topology", ("Doc", "Box", True)) in fake.calls


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
