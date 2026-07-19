"""Active-view orientation, sizing, and screenshot capture."""

from typing import Any

import FreeCAD
import FreeCADGui

from rpc_server.gui_dispatch import _flush_gui_events


_VIEW_DISPATCH = {
    "Isometric": "viewIsometric",
    "Front": "viewFront",
    "Top": "viewTop",
    "Right": "viewRight",
    "Back": "viewBack",
    "Left": "viewLeft",
    "Bottom": "viewBottom",
    "Dimetric": "viewDimetric",
    "Trimetric": "viewTrimetric",
}


def _get_view_size(view: Any) -> tuple[int, int]:
    try:
        size = view.getSize()
        if isinstance(size, (list, tuple)) and len(size) >= 2:
            return max(1, int(size[0])), max(1, int(size[1]))
        return max(1, int(size.width())), max(1, int(size.height()))
    except Exception:
        return 1024, 768


# Default screenshots follow the viewport, which on large displays produces
# ~1000+ px images costing ~1500 image tokens each. Cap the default at 800 px
# on the longest edge (~59% cheaper); explicit width/height always win.
DEFAULT_MAX_EDGE = 800


def _resolve_screenshot_size(
    view: Any,
    width: int | None,
    height: int | None,
) -> tuple[int, int]:
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


_STD_COMMAND_DISPATCH = {
    "Isometric": "Std_ViewIsometric",
    "Front": "Std_ViewFront",
    "Top": "Std_ViewTop",
    "Right": "Std_ViewRight",
    "Back": "Std_ViewRear",
    "Left": "Std_ViewLeft",
    "Bottom": "Std_ViewBottom",
    "Dimetric": "Std_ViewDimetric",
    "Trimetric": "Std_ViewTrimetric",
}


def apply_view_orientation(view: Any, view_name: str) -> None:
    method_name = _VIEW_DISPATCH.get(view_name)
    if method_name is None:
        raise ValueError(f"Invalid view name: {view_name}")
    if hasattr(view, method_name):
        getattr(view, method_name)()
    else:
        # Fallback for views that lack the direct Python method
        # (e.g. some FreeCAD versions / view types)
        cmd = _STD_COMMAND_DISPATCH.get(view_name)
        if cmd:
            FreeCADGui.runCommand(cmd)
        else:
            FreeCAD.Console.PrintWarning(
                f"apply_view_orientation: no method or command for '{view_name}'\n"
            )


def save_active_screenshot(
    save_path: str,
    view_name: str = "Isometric",
    width: int | None = None,
    height: int | None = None,
    focus_object: str | None = None,
):
    """Save a PNG of the active view to ``save_path``.

    Returns ``True`` on success, or an error string on failure (preserves the
    legacy GUI-handler return contract).
    """
    try:
        view = FreeCADGui.ActiveDocument.ActiveView
        if not hasattr(view, "saveImage"):
            return "Current view does not support screenshots"

        apply_view_orientation(view, view_name)

        focused_selection = False
        # The resolved object we frame on (when focus_object is given), kept so
        # the framing can be re-applied synchronously right before saveImage().
        focus_target = None

        if focus_object:
            doc = FreeCAD.ActiveDocument
            obj = doc.getObject(focus_object) if doc else None
            if obj:
                FreeCADGui.Selection.clearSelection()
                FreeCADGui.Selection.addSelection(obj)
                FreeCADGui.SendMsgToActiveView("ViewSelection")
                focused_selection = True
                focus_target = obj
                _flush_gui_events()
                FreeCADGui.Selection.clearSelection()
            else:
                view.fitAll()
        else:
            view.fitAll()

        _flush_gui_events()
        # On macOS, when the FreeCAD window is not exposed (fully occluded or
        # minimized), saveImage() right after pumping the event loop grabs a blank
        # frame. Re-issuing the framing synchronously forces a redraw first. The
        # flush above is kept intentionally — Linux needs it for the stale-frame
        # fix (#51/#53).
        if focused_selection and focus_target is not None:
            FreeCADGui.Selection.addSelection(focus_target)
            FreeCADGui.SendMsgToActiveView("ViewSelection")
            FreeCADGui.Selection.clearSelection()
        else:
            view.fitAll()
        resolved_width, resolved_height = _resolve_screenshot_size(view, width, height)
        view.saveImage(save_path, resolved_width, resolved_height, "Current")

        if focused_selection:
            FreeCADGui.Selection.clearSelection()
            _flush_gui_events(delay_ms=0)
        return True
    except Exception as e:
        return str(e)


def _sectionable_objects(doc, object_names):
    """Resolve the objects to section: named ones, or all visible solids."""
    if object_names:
        objs = []
        for name in object_names:
            o = doc.getObject(name)
            if o is None:
                raise ValueError(
                    f"Object '{name}' not found in '{doc.Name}'. "
                    f"Available: {[x.Name for x in doc.Objects]}"
                )
            objs.append(o)
        return objs
    objs = []
    for o in doc.Objects:
        shape = getattr(o, "Shape", None)
        vis = getattr(getattr(o, "ViewObject", None), "Visibility", False)
        if vis and shape is not None and not shape.isNull() and shape.Solids:
            objs.append(o)
    return objs


def save_section_screenshot(
    save_path: str,
    doc_name: str,
    plane: str = "XZ",
    offset=None,
    object_names=None,
    view_name: str = "Isometric",
    width: int | None = None,
    height: int | None = None,
):
    """Screenshot a cutaway: temporarily cut away the +normal half at the plane.

    All changes (temporary cut objects, visibility toggles) are rolled back
    before returning, so the document is left exactly as it was.
    Returns True on success, or an error string.
    """
    from rpc_server.geometry_tools import section_shape

    try:
        doc = FreeCAD.getDocument(doc_name)
    except Exception:
        return f"Document '{doc_name}' not found."
    try:
        targets = _sectionable_objects(doc, object_names)
    except ValueError as e:
        return str(e)
    if not targets:
        return (
            "No solids to section (no visible solids, or the named objects have "
            "no solid geometry)."
        )

    saved_vis = {}
    doc.UndoMode = 1
    doc.openTransaction("MCP section view")
    try:
        for t in targets:
            shape = getattr(t, "Shape", None)
            if shape is None or shape.isNull() or not shape.Solids:
                continue
            saved_vis[t.Name] = t.ViewObject.Visibility
            cut = section_shape(shape, plane, offset)
            feat = doc.addObject("Part::Feature", f"{t.Name}_section")
            feat.Shape = cut
            t.ViewObject.Visibility = False
        if not saved_vis:
            return "None of the requested objects had solid geometry to section."
        doc.recompute()
        result = save_active_screenshot(save_path, view_name, width, height, None)
        return result
    finally:
        for name, vis in saved_vis.items():
            obj = doc.getObject(name)
            if obj is not None:
                obj.ViewObject.Visibility = vis
        doc.abortTransaction()
        doc.recompute()
