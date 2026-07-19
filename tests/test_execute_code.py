"""execute_code / async job operations against a fake connection."""

from freecad_mcp.operations import core

from .test_core_operations import FakeFreeCAD, text_of


def test_execute_code_failure_includes_traceback_and_partial_output():
    fake = FakeFreeCAD(
        execute_code={
            "success": False,
            "error": "ZeroDivisionError: division by zero",
            "traceback": '  File "<string>", line 3, in <module>',
            "stdout": "step one done\nstep two done\n",
        }
    )
    text = text_of(core.execute_code_operation(fake, False, "print('x')\n1/0"))
    assert "ZeroDivisionError" in text
    assert "line 3" in text
    assert "step one done" in text
    assert not fake.called("get_active_screenshot")


def test_execute_code_failure_without_extras_still_works():
    fake = FakeFreeCAD(execute_code={"success": False, "error": "GUI dispatch timed out after 90s"})
    text = text_of(core.execute_code_operation(fake, False, "while True: pass"))
    assert "timed out" in text


def test_execute_code_async_returns_job_id():
    fake = FakeFreeCAD(
        execute_code_async={"success": True, "job_id": "ab12cd34ef56"}
    )
    text = text_of(core.execute_code_async_operation(fake, "heavy()"))
    assert "ab12cd34ef56" in text
    assert "get_async_status" in text


def test_get_async_status_formats_job():
    fake = FakeFreeCAD(
        get_async_status={
            "success": True,
            "job_id": "ab12cd34ef56",
            "status": "failed",
            "error": "ValueError: boom",
            "traceback": '  File "<string>", line 2, in <module>',
        }
    )
    text = text_of(core.get_async_status_operation(fake, "ab12cd34ef56"))
    assert "failed" in text
    assert "ValueError" in text


def test_get_async_status_unknown_job():
    fake = FakeFreeCAD(
        get_async_status={
            "success": False,
            "error": "Unknown job id 'nope'.",
            "known_job_ids": ["ab12cd34ef56"],
        }
    )
    text = text_of(core.get_async_status_operation(fake, "nope"))
    assert "Unknown job id" in text
    assert "ab12cd34ef56" in text
