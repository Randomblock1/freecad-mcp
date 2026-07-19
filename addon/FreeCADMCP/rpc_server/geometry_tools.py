"""Read-only geometry interrogation: distances and topology.

These run on the RPC thread (no GUI dispatch), matching the ``get_objects``
precedent for read-only document access. GUI-free so headless tests can
import this module under ``freecadcmd``.
"""

import math

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


_BUILD_AXES = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}
_THICKNESS_SAMPLE_LIMIT = 80


def _outward_normal(shape, face):
    """Outward unit normal of ``face`` on ``shape`` (via isInside, robust)."""
    u0, u1, v0, v1 = face.ParameterRange
    n = face.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
    c = face.CenterOfMass
    # If a small step along -n lands inside the solid, n already points outward.
    probe = FreeCAD.Vector(c.x - n.x * 0.01, c.y - n.y * 0.01, c.z - n.z * 0.01)
    try:
        inside = shape.isInside(probe, 1e-6, True)
    except Exception:
        inside = True
    return n if inside else FreeCAD.Vector(-n.x, -n.y, -n.z)


def _local_thickness(shape, face, diag):
    """Estimate wall thickness at ``face``'s centroid by casting an inward ray."""
    import Part

    n = _outward_normal(shape, face)
    c = face.CenterOfMass
    start = FreeCAD.Vector(c.x + n.x * 0.01, c.y + n.y * 0.01, c.z + n.z * 0.01)
    inward = FreeCAD.Vector(-n.x, -n.y, -n.z)
    end = FreeCAD.Vector(
        start.x + inward.x * diag * 2,
        start.y + inward.y * diag * 2,
        start.z + inward.z * diag * 2,
    )
    try:
        line = Part.makeLine(start, end)
        common = shape.common(line)
    except Exception:
        return None
    dists = sorted(
        (FreeCAD.Vector(v.X - start.x, v.Y - start.y, v.Z - start.z)).dot(inward)
        for v in common.Vertexes
    )
    dists = [d for d in dists if d >= -1e-9]
    if len(dists) < 2:
        return None
    # First solid segment [entry, exit]; its length is the local wall thickness.
    return dists[1] - dists[0]


_SECTION_PLANES = {"XY": 2, "XZ": 1, "YZ": 0}  # plane -> index of the normal axis


def section_shape(shape, plane: str, offset: float | None = None):
    """Return ``shape`` with the half beyond ``offset`` along the plane normal cut away.

    plane is "XY", "XZ", or "YZ"; the cut plane is that plane translated to
    ``offset`` along its normal axis (Z, Y, X respectively). offset defaults to
    the shape's center along the normal axis. The material on the +normal side
    of the plane is removed, exposing the cross-section.
    """
    import Part

    axis = _SECTION_PLANES.get(plane)
    if axis is None:
        raise ValueError(f"Invalid plane '{plane}'; expected one of {list(_SECTION_PLANES)}.")

    bb = shape.BoundBox
    mins = [bb.XMin, bb.YMin, bb.ZMin]
    maxs = [bb.XMax, bb.YMax, bb.ZMax]
    if offset is None:
        offset = (mins[axis] + maxs[axis]) / 2.0

    # A cutter big enough to fully cover the model on every axis, with margin.
    diag = bb.DiagonalLength or 1.0
    size = diag * 3.0
    centers = [(mins[i] + maxs[i]) / 2.0 for i in range(3)]
    # Center the cutter on the model in the two in-plane axes; along the normal
    # axis start it exactly at the section plane so it removes only the +normal side.
    lows = [centers[i] - size / 2.0 for i in range(3)]
    lows[axis] = offset
    cutter = Part.makeBox(size, size, size, FreeCAD.Vector(*lows))
    return shape.cut(cutter)


def check_printability(
    doc_name: str,
    obj_name: str,
    min_wall_thickness: float | None = None,
    include_overhangs: bool = False,
    build_direction: str = "Z",
    max_overhang_deg: float = 45.0,
) -> dict:
    """Basic 3D-print readiness: watertightness, wall thickness, optional overhangs.

    Wall thickness is a SAMPLED ESTIMATE (rays cast inward from face centroids),
    not a guaranteed minimum. Overhangs are opt-in (modern printers handle them
    with generated supports); faces resting on the build plate are excluded.
    """
    try:
        doc = FreeCAD.getDocument(doc_name)
    except Exception:
        return _doc_not_found(doc_name)
    try:
        shape = _resolve_shape(doc, obj_name)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    solids = shape.Solids
    result = {
        "success": True,
        "object_name": obj_name,
        "manifold": {
            "is_valid": bool(shape.isValid()),
            "is_solid": len(solids) >= 1,
            "solid_count": len(solids),
            "is_watertight": bool(solids) and all(s.isClosed() for s in solids),
        },
    }

    diag = shape.BoundBox.DiagonalLength or 1.0
    faces = shape.Faces
    step = max(1, len(faces) // _THICKNESS_SAMPLE_LIMIT)
    sampled = faces[::step][:_THICKNESS_SAMPLE_LIMIT]
    thicknesses = []
    for face in sampled:
        t = _local_thickness(shape, face, diag)
        if t is not None and t > 1e-6:
            thicknesses.append(t)
    wall = {
        "estimated": True,
        "samples": len(thicknesses),
        "note": "min_mm is a sampled estimate from face-centroid rays, not a guaranteed minimum.",
    }
    if thicknesses:
        wall["min_mm"] = min(thicknesses)
        if min_wall_thickness is not None:
            wall["threshold_mm"] = float(min_wall_thickness)
            wall["below_threshold"] = min(thicknesses) < float(min_wall_thickness)
    else:
        wall["min_mm"] = None
        wall["note"] += " No usable samples (open or degenerate geometry)."
    result["wall_thickness"] = wall

    if include_overhangs:
        d = FreeCAD.Vector(*_BUILD_AXES.get(build_direction.upper(), (0.0, 0.0, 1.0)))
        axis_idx = "XYZ".index(build_direction.upper()) if build_direction.upper() in "XYZ" else 2
        model_min = [shape.BoundBox.XMin, shape.BoundBox.YMin, shape.BoundBox.ZMin][axis_idx]
        overhang_faces, worst = [], 0.0
        for i, face in enumerate(faces):
            try:
                n = _outward_normal(shape, face)
                c = face.CenterOfMass
            except Exception:
                continue
            # Faces resting on the build plate (at the model's min build coord)
            # are supported by the plate, not overhangs.
            if abs([c.x, c.y, c.z][axis_idx] - model_min) < 1e-6:
                continue
            cos_up = max(-1.0, min(1.0, n.dot(d)))
            overhang_angle = math.degrees(math.acos(cos_up)) - 90.0  # >0 = downward-facing
            if overhang_angle > max_overhang_deg:
                worst = max(worst, overhang_angle)
                overhang_faces.append({"Name": f"Face{i + 1}", "angle_deg": overhang_angle,
                                       "area_mm2": face.Area})
        result["overhangs"] = {
            "build_direction": build_direction.upper(),
            "max_overhang_deg": float(max_overhang_deg),
            "count": len(overhang_faces),
            "total_area_mm2": sum(f["area_mm2"] for f in overhang_faces),
            "worst_angle_deg": worst,
            "faces": overhang_faces[:_TOPO_LIMIT],
        }

    return result


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
