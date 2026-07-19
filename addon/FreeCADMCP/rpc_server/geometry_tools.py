"""Read-only geometry interrogation: distances and topology.

These run on the RPC thread (no GUI dispatch), matching the ``get_objects``
precedent for read-only document access. GUI-free so headless tests can
import this module under ``freecadcmd``.
"""

import FreeCAD


def _doc_not_found(doc_name: str) -> dict:
    return {
        "success": False,
        "error": f"Document '{doc_name}' not found.",
        "open_documents": list(FreeCAD.listDocuments().keys()),
    }


def _resolve_shape(doc, obj_name: str, sub_element: str | None = None):
    obj = doc.getObject(obj_name)
    if obj is None:
        raise ValueError(
            f"Object '{obj_name}' not found in '{doc.Name}'. "
            f"Available: {[o.Name for o in doc.Objects]}"
        )
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        raise ValueError(f"Object '{obj_name}' has no computed shape.")
    if sub_element:
        try:
            sub = shape.getElement(sub_element)
        except Exception:
            sub = None
        if sub is None:
            raise ValueError(
                f"Sub-element '{sub_element}' not found on '{obj_name}' "
                f"({len(shape.Faces)} faces, {len(shape.Edges)} edges, "
                f"{len(shape.Vertexes)} vertexes)."
            )
        return sub
    return shape


def measure_distance(
    doc_name: str,
    object1: str,
    object2: str,
    sub1: str | None = None,
    sub2: str | None = None,
) -> dict:
    """Minimum distance between two shapes (or sub-elements like "Face3").

    Distance 0 means the shapes touch or overlap; for whole objects the
    intersection volume is computed as an interference check.
    """
    try:
        doc = FreeCAD.getDocument(doc_name)
    except Exception:
        return _doc_not_found(doc_name)
    try:
        shape1 = _resolve_shape(doc, object1, sub1)
        shape2 = _resolve_shape(doc, object2, sub2)
        dist, point_pairs, _info = shape1.distToShape(shape2)
        result = {"success": True, "distance_mm": dist}
        if point_pairs:
            p1, p2 = point_pairs[0]
            result["closest_point_on_1"] = [p1.x, p1.y, p1.z]
            result["closest_point_on_2"] = [p2.x, p2.y, p2.z]
        if dist == 0 and sub1 is None and sub2 is None:
            # common() is a full OCCT boolean; it runs on the RPC thread with no
            # timeout, so skip it on large shapes rather than stalling every
            # other tool call for minutes on tens-of-thousands-of-face solids.
            total_faces = len(shape1.Faces) + len(shape2.Faces)
            if total_faces > _INTERSECTION_FACE_LIMIT:
                result["note"] = (
                    f"Shapes touch or overlap. Interference volume skipped "
                    f"({total_faces} faces exceeds the {_INTERSECTION_FACE_LIMIT}-face "
                    "limit for the on-demand boolean); compute it with execute_code if needed."
                )
            else:
                try:
                    result["intersection_volume_mm3"] = shape1.common(shape2).Volume
                    result["note"] = (
                        "Shapes touch or overlap. intersection_volume_mm3 > 0 means "
                        "actual interference; ~0 means surfaces merely touch."
                    )
                except Exception:
                    result["note"] = "Shapes touch or overlap (intersection volume unavailable)."
        return result
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


# Faces/edges beyond this are omitted (with an explicit truncation note) to
# keep responses bounded on organic/imported geometry.
_TOPO_LIMIT = 200

# measure_distance computes an OCCT intersection boolean for interference when
# shapes overlap; skip it above this combined face count (see measure_distance).
_INTERSECTION_FACE_LIMIT = 2000


def get_topology(doc_name: str, obj_name: str, include_edges: bool = False) -> dict:
    """Per-face (and optionally per-edge) breakdown of an object's shape.

    Face/edge names ("Face1", "Edge3") are exactly the sub-element names FEM
    constraint References and measure_distance sub-elements expect.
    """
    try:
        doc = FreeCAD.getDocument(doc_name)
    except Exception:
        return _doc_not_found(doc_name)
    try:
        shape = _resolve_shape(doc, obj_name)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    result = {
        "success": True,
        "object_name": obj_name,
        "counts": {
            "Faces": len(shape.Faces),
            "Edges": len(shape.Edges),
            "Vertexes": len(shape.Vertexes),
        },
    }

    faces = []
    for i, face in enumerate(shape.Faces[:_TOPO_LIMIT]):
        entry = {"Name": f"Face{i + 1}"}
        try:
            entry["Type"] = type(face.Surface).__name__
        except Exception:
            entry["Type"] = "unknown"
        try:
            entry["Area"] = face.Area
        except Exception:
            pass
        try:
            c = face.CenterOfMass
            entry["Centroid"] = [c.x, c.y, c.z]
        except Exception:
            pass
        try:
            u0, u1, v0, v1 = face.ParameterRange
            n = face.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
            entry["Normal"] = [n.x, n.y, n.z]
            entry["Orientation"] = face.Orientation
        except Exception:
            pass
        faces.append(entry)
    result["faces"] = faces
    if len(shape.Faces) > _TOPO_LIMIT:
        result["truncated"] = True
        result["note"] = (
            f"Only the first {_TOPO_LIMIT} of {len(shape.Faces)} faces are listed."
        )

    if include_edges:
        edges = []
        for i, edge in enumerate(shape.Edges[:_TOPO_LIMIT]):
            entry = {"Name": f"Edge{i + 1}"}
            # Degenerate edges (e.g. the pole edges of a sphere) raise
            # "undefined curve type" on .Curve; keep the rest of the entry.
            try:
                entry["Type"] = type(edge.Curve).__name__
            except Exception:
                entry["Type"] = "unknown"
            try:
                entry["Length"] = edge.Length
            except Exception:
                pass
            try:
                mid = edge.valueAt((edge.FirstParameter + edge.LastParameter) / 2)
                entry["Mid"] = [mid.x, mid.y, mid.z]
            except Exception:
                pass
            edges.append(entry)
        result["edges"] = edges
        if len(shape.Edges) > _TOPO_LIMIT:
            result["truncated"] = True
            result["note"] = (
                f"Only the first {_TOPO_LIMIT} of {len(shape.Edges)} edges are listed."
            )

    return result
