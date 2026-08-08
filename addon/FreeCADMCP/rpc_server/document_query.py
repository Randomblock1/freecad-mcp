"""Read-only document/object queries for the RPC ``get_objects``/``get_object``
handlers.

Lives outside ``rpc_server.py`` so it stays importable without a GUI (headless
tests via ``freecadcmd``). Unknown documents/objects return structured errors
naming what *is* available instead of empty lists / None, so a typo is
distinguishable from an empty document.
"""

import FreeCAD

from rpc_server.serialize import (
    SCAFFOLDING_TYPE_IDS,
    serialize_object,
    summarize_object,
)


def _doc_not_found(doc_name: str) -> dict:
    return {
        "success": False,
        "error": f"Document '{doc_name}' not found.",
        "open_documents": list(FreeCAD.listDocuments().keys()),
    }


def query_objects(doc_name: str, detail: str = "summary") -> dict:
    try:
        doc = FreeCAD.getDocument(doc_name)
    except Exception:
        return _doc_not_found(doc_name)

    objects, omitted = [], []
    for o in doc.Objects:
        try:
            if detail != "full" and o.TypeId in SCAFFOLDING_TYPE_IDS:
                omitted.append(o.Name)
                continue
            objects.append(serialize_object(o) if detail == "full" else summarize_object(o))
        except Exception as e:
            objects.append({
                "Name": o.Name,
                "TypeId": o.TypeId,
                "Error": f"serialization failed: {type(e).__name__}: {e}",
            })
    out = {"success": True, "doc_name": doc.Name, "detail": detail, "objects": objects}
    if omitted:
        out["omitted_origin_scaffolding"] = omitted
    return out


def query_object(doc_name: str, obj_name: str) -> dict:
    try:
        doc = FreeCAD.getDocument(doc_name)
    except Exception:
        return _doc_not_found(doc_name)

    obj = doc.getObject(obj_name)
    if obj is None:
        return {
            "success": False,
            "error": f"Object '{obj_name}' not found in document '{doc.Name}'.",
            "available_objects": [o.Name for o in doc.Objects],
        }
    try:
        return {"success": True, "object": serialize_object(obj)}
    except Exception as e:
        return {
            "success": False,
            "error": f"Serialization failed for '{obj_name}': {type(e).__name__}: {e}",
        }
