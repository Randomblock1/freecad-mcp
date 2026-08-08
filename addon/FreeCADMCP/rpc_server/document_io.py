"""Save and export documents.

The public functions here are called on the GUI thread by the RPC handlers
(they write to disk and read consistent document state). GUI-free imports so
headless tests can exercise them under ``freecadcmd``.
"""

import os

import FreeCAD

from rpc_server.serialize import SCAFFOLDING_TYPE_IDS

_CAD_FORMATS = {".step", ".stp", ".iges", ".igs"}
_MESH_FORMATS = {".stl", ".obj", ".3mf", ".ply", ".amf"}
_SUPPORTED = sorted(_CAD_FORMATS | _MESH_FORMATS | {".brep"})


def _doc_not_found(doc_name: str) -> dict:
    return {
        "success": False,
        "error": f"Document '{doc_name}' not found.",
        "open_documents": list(FreeCAD.listDocuments().keys()),
    }


def save_document(doc_name: str, file_path: str | None = None) -> dict:
    try:
        doc = FreeCAD.getDocument(doc_name)
    except Exception:
        return _doc_not_found(doc_name)
    try:
        if file_path:
            path = os.path.abspath(os.path.expanduser(file_path))
            doc.saveAs(path)
        elif doc.FileName:
            doc.save()
        else:
            return {
                "success": False,
                "error": (
                    f"Document '{doc_name}' has never been saved; "
                    "pass file_path to choose a location."
                ),
            }
        return {"success": True, "document_name": doc.Name, "file_path": doc.FileName}
    except Exception as e:
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


def _default_export_objects(doc) -> list:
    """Top-level objects with computed geometry.

    RootObjects (not all Objects) so a PartDesign Body exports once instead of
    once per feature.
    """
    picked = []
    for o in doc.RootObjects:
        if o.TypeId in SCAFFOLDING_TYPE_IDS:
            continue
        if o.TypeId.startswith("Mesh::"):
            picked.append(o)
            continue
        shape = getattr(o, "Shape", None)
        if shape is not None and not shape.isNull():
            picked.append(o)
    return picked


def _export_mesh_with_deflection(objects, path, linear_deflection):
    """Tessellate each object's shape at a chosen tolerance and export as one mesh.

    MeshPart.meshFromShape lets the caller trade file size for surface fidelity;
    the plain Mesh.export path uses FreeCAD's default tessellation.
    """
    import Mesh
    import MeshPart

    meshes = []
    for obj in objects:
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            continue
        m = MeshPart.meshFromShape(Shape=shape, LinearDeflection=float(linear_deflection))
        meshes.append(Mesh.Mesh(m.Topology))
    if not meshes:
        raise ValueError("no objects with tessellatable geometry to mesh")
    combined = meshes[0]
    for extra in meshes[1:]:
        combined.addMesh(extra)
    combined.write(path)


def export_document(
    doc_name: str,
    file_path: str,
    object_names: list[str] | None = None,
    linear_deflection: float | None = None,
) -> dict:
    try:
        doc = FreeCAD.getDocument(doc_name)
    except Exception:
        return _doc_not_found(doc_name)

    if object_names:
        objects = []
        for name in object_names:
            obj = doc.getObject(name)
            if obj is None:
                return {
                    "success": False,
                    "error": (
                        f"Object '{name}' not found in '{doc.Name}'. "
                        f"Available: {[o.Name for o in doc.Objects]}"
                    ),
                }
            objects.append(obj)
    else:
        objects = _default_export_objects(doc)
        if not objects:
            return {
                "success": False,
                "error": (
                    "No exportable objects found (top-level objects with geometry); "
                    "pass object_names explicitly."
                ),
            }

    path = os.path.abspath(os.path.expanduser(file_path))
    suffix = os.path.splitext(path)[1].lower()
    note = None
    try:
        if suffix in _CAD_FORMATS:
            import Import

            Import.export(objects, path)
            if linear_deflection is not None:
                note = "linear_deflection ignored: it only affects tessellated mesh formats, not exact CAD (STEP/IGES)."
        elif suffix == ".brep":
            import Part

            Part.export(objects, path)
            if linear_deflection is not None:
                note = "linear_deflection ignored: BREP is an exact format, not a tessellated mesh."
        elif suffix in _MESH_FORMATS:
            if linear_deflection is not None:
                _export_mesh_with_deflection(objects, path, linear_deflection)
            else:
                import Mesh

                Mesh.export(objects, path)
        else:
            return {
                "success": False,
                "error": (
                    f"Unsupported export format '{suffix}'. "
                    f"Supported (chosen by file extension): {', '.join(_SUPPORTED)}"
                ),
            }
        if not os.path.exists(path):
            return {"success": False, "error": f"Export produced no file at {path!r}."}
        result = {
            "success": True,
            "file_path": path,
            # float, not int: XML-RPC <int> is limited to +/-2^31, and a finely
            # tessellated export can exceed 2 GiB. Doubles are exact for integer
            # sizes up to 2^53 (8 PB), so no precision is lost in practice.
            "file_size_bytes": float(os.path.getsize(path)),
            "exported_objects": [o.Name for o in objects],
        }
        if note:
            result["note"] = note
        return result
    except Exception as e:
        return {"success": False, "error": f"{type(e).__name__}: {e}"}
