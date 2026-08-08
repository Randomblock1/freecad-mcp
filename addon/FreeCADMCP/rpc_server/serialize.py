import math

import FreeCAD as App

# PartDesign coordinate-system scaffolding auto-created inside every Body.
# Agents never manipulate these directly; get_objects(detail="summary") omits
# them (and names the omitted objects) to keep responses small.
SCAFFOLDING_TYPE_IDS = {"App::Origin", "App::Line", "App::Plane"}


def _get_optional_app_type(name: str) -> type | tuple[type, ...] | None:
    value = getattr(App, name, None)
    if isinstance(value, type):
        return value
    if isinstance(value, tuple) and all(isinstance(item, type) for item in value):
        return value
    return None


_COLOR_TYPE = _get_optional_app_type("Color")


def serialize_value(value):
    if isinstance(value, (int, float, str, bool)):
        return value
    elif isinstance(value, App.Vector):
        return {"x": value.x, "y": value.y, "z": value.z}
    elif isinstance(value, App.Rotation):
        return {
            "Axis": {"x": value.Axis.x, "y": value.Axis.y, "z": value.Axis.z},
            # Degrees, matching what property assignment accepts on write
            # (FreeCAD.Rotation(axis, angle) takes degrees); the raw
            # Rotation.Angle attribute is radians.
            "Angle": math.degrees(value.Angle),
        }
    elif isinstance(value, App.Placement):
        return {
            "Base": serialize_value(value.Base),
            "Rotation": serialize_value(value.Rotation),
        }
    elif isinstance(value, (list, tuple)):
        return [serialize_value(v) for v in value]
    elif _COLOR_TYPE is not None and isinstance(value, _COLOR_TYPE):
        return tuple(value)
    else:
        s = str(value)
        # "<Wire object at 0x7f...>" carries no information; keep the type only.
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


def serialize_view_object(view):
    if view is None:
        return None
    result = {}
    try:
        result["ShapeColor"] = serialize_value(view.ShapeColor)
    except AttributeError:
        pass
    try:
        result["Transparency"] = view.Transparency
    except AttributeError:
        pass
    try:
        result["Visibility"] = view.Visibility
    except AttributeError:
        pass
    return result


def serialize_object(obj):
    if isinstance(obj, list):
        return [serialize_object(item) for item in obj]
    elif isinstance(obj, App.Document):
        return {
            "Name": obj.Name,
            "Label": obj.Label,
            "FileName": obj.FileName,
            "Objects": [serialize_object(child) for child in obj.Objects],
        }
    else:
        result = {
            "Name": obj.Name,
            "Label": obj.Label,
            "TypeId": obj.TypeId,
            "State": [str(s) for s in getattr(obj, "State", [])],
            "Properties": {},
            "Placement": serialize_value(getattr(obj, "Placement", None)),
            "Shape": serialize_shape(getattr(obj, "Shape", None)),
            "ViewObject": {},
        }

        for prop in obj.PropertiesList:
            # Label/Placement/Shape are already top-level fields; repeating
            # them in Properties only duplicates bytes.
            if prop in ("Label", "Placement", "Shape"):
                continue
            try:
                result["Properties"][prop] = serialize_value(getattr(obj, prop))
            except Exception as e:
                result["Properties"][prop] = f"<error: {str(e)}>"

        if hasattr(obj, "ViewObject") and obj.ViewObject is not None:
            view = obj.ViewObject
            result["ViewObject"] = serialize_view_object(view)

        return result


def summarize_object(obj):
    """Compact per-object view for get_objects(detail="summary").

    Identity + health + envelope only: Name, TypeId, Label (when it differs),
    dirty State, shape validity/Volume/BoundBox, and non-identity Placement.
    """
    d = {"Name": obj.Name, "TypeId": obj.TypeId}
    if obj.Label != obj.Name:
        d["Label"] = obj.Label
    state = [str(s) for s in getattr(obj, "State", [])]
    if state:
        d["State"] = state
    full = serialize_shape(getattr(obj, "Shape", None))
    if full is not None:
        d["Shape"] = {
            k: v for k, v in full.items() if k in ("Valid", "Volume", "BoundBox", "Error")
        }
    pl = getattr(obj, "Placement", None)
    if isinstance(pl, App.Placement):
        base, rot = pl.Base, pl.Rotation
        if base.Length > 1e-9 or abs(rot.Angle) > 1e-9:
            d["Placement"] = {"Base": [base.x, base.y, base.z]}
            if abs(rot.Angle) > 1e-9:
                d["Placement"]["RotationAxis"] = [rot.Axis.x, rot.Axis.y, rot.Axis.z]
                d["Placement"]["RotationAngleDeg"] = math.degrees(rot.Angle)
    return d
