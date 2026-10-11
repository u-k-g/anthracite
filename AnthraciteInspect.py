# SPDX-License-Identifier: LGPL-2.1-or-later
"""Readable, bounded native CAD observations; never a replacement geometry API."""
import FreeCAD as App
import math
import os
import re
import tempfile
import uuid
import json

try:
    import Part
except Exception:  # pragma: no cover - present in a normal FreeCAD build
    Part = None

_reference_documents = {}


def document_token(doc):
    previous = _reference_documents.get(doc.Name)
    if previous is None or previous[0] is not doc:
        previous = (doc, str(uuid.uuid4()))
        _reference_documents[doc.Name] = previous
    return previous[1]


def forget(doc):
    _reference_documents.pop(doc.Name, None)


def _world_placement(obj):
    if hasattr(obj, "getGlobalPlacement"):
        return obj.getGlobalPlacement()
    # App::Link exposes Placement and native container membership, but no
    # getGlobalPlacement in stock FreeCAD. Its Shape already carries link-local
    # transforms (including link scale); only the parent's rigid pose is missing.
    parent = obj.getParentGeoFeatureGroup()
    return obj.Placement if parent is None else _world_placement(parent).multiply(obj.Placement)


def native_shape(doc, target, revision):
    """Resolve an object or an explicitly revision-bound subelement. No fuzzy matching."""
    subelement = None
    if isinstance(target, dict):
        if set(target) != {"document", "documentToken", "object", "subelement", "revision"}:
            raise ValueError("Use the complete topology reference returned by cad.topology().")
        if target["document"] != doc.Name or target["documentToken"] != document_token(doc) or target["revision"] != revision:
            raise ValueError("Stale topology reference; inspect topology again at the current revision.")
        subelement = target["subelement"]
        if not isinstance(subelement, str) or (subelement and not re.fullmatch(r"(Face|Edge|Vertex)[1-9][0-9]*", subelement)):
            raise ValueError("Expected an object, FaceN, EdgeN or VertexN reference.")
        target = target["object"]
    obj = resolve(doc, target)
    if not hasattr(obj, "Shape") or obj.Shape.isNull():
        raise ValueError(f"{obj.Name} has no native shape.")
    if "Touched" in obj.State:
        raise ValueError("Geometry needs recompute before measurement.")
    shape = obj.Shape.copy()
    shape.Placement = _world_placement(obj).multiply(obj.Placement.inverse()).multiply(shape.Placement)
    return shape.getElement(subelement) if subelement else shape


def topology(doc, target, revision, *, kind="faces", surface=None, offset=0, limit=25):
    if kind not in ("faces", "edges") or offset < 0 or not 1 <= limit <= 100:
        raise ValueError("Use faces/edges, non-negative offset and limit 1..100.")
    obj = resolve(doc, target)
    shape = native_shape(doc, obj, revision)
    entries = []
    for index, part in enumerate(shape.Faces if kind == "faces" else shape.Edges, 1):
        geometry = part.Surface if kind == "faces" else part.Curve
        geometry_type = type(geometry).__name__
        if surface is not None and geometry_type != surface:
            continue
        entries.append((index, part, geometry, geometry_type))
    items = []
    for index, part, geometry, geometry_type in entries[offset:offset + limit]:
        ref = {"document": doc.Name, "documentToken": document_token(doc), "object": obj.Name,
               "subelement": f"{'Face' if kind == 'faces' else 'Edge'}{index}", "revision": revision}
        item = {"reference": ref, **_geometry_facts(geometry),
                "centerOfMass_mm": _center_of_mass(part),
                "area_mm2" if kind == "faces" else "length_mm": part.Area if kind == "faces" else part.Length,
                "tolerance_mm": part.getTolerance(1)}
        items.append(item)
    return {"document": doc.Name, "revision": revision, "object": obj.Name,
            "items": items, "total": len(entries),
            "nextOffset": offset + limit if offset + limit < len(entries) else None,
            "provenance": "FreeCAD/OCC native BRep; analytic surface/curve parameters where available; kernel-tolerance numerical integrals otherwise",
            "units": {"length": "mm", "area": "mm^2"},
            "coordinateFrame": "document/world; directions are unit vectors",
            "referencePolicy": "Every reference expires on a document revision change. Multiple matches are candidates, not a selection."}


def _center_of_mass(shape):
    """Center of mass as [x, y, z] mm, or None when the shape cannot report one.

    Compounds do not expose CenterOfMass in this build, so fall back to the
    volume-weighted average of their solids; one unavailable field must not make the
    other measurements unverifiable.
    """
    try:
        center = shape.CenterOfMass
    except Exception:
        center = None
    if center is not None:
        return [center.x, center.y, center.z]
    total = 0.0
    accumulated = App.Vector(0.0, 0.0, 0.0)
    for solid in shape.Solids:
        try:
            volume = solid.Volume
            if volume <= 0:
                continue
            accumulated = accumulated + solid.CenterOfMass * volume
        except Exception:
            return None
        total += volume
    if total <= 0:
        return None
    average = accumulated / total
    return [average.x, average.y, average.z]


def _geometry_facts(geometry):
    """Unified geometry vocabulary for topology items and measurements."""
    if geometry is None:
        return {}
    kind = type(geometry).__name__
    facts = {"geometryType": kind}
    if kind in ("Cylinder", "Circle", "Cone"):
        facts["axis"] = [geometry.Axis.x, geometry.Axis.y, geometry.Axis.z]
        facts["axisPosition"] = [geometry.Center.x, geometry.Center.y, geometry.Center.z]
        facts["radius_mm"] = geometry.Radius
    elif kind == "Torus":
        facts["axis"] = [geometry.Axis.x, geometry.Axis.y, geometry.Axis.z]
        facts["axisPosition"] = [geometry.Center.x, geometry.Center.y, geometry.Center.z]
        facts["majorRadius_mm"] = geometry.MajorRadius
        facts["minorRadius_mm"] = geometry.MinorRadius
    elif kind == "Line":
        facts["direction"] = [geometry.Direction.x, geometry.Direction.y, geometry.Direction.z]
    elif hasattr(geometry, "Axis"):
        facts["normal"] = [geometry.Axis.x, geometry.Axis.y, geometry.Axis.z]
    return facts


def _surface_facts(shape):
    """Datum/face facts for an interface check: plane normal, or cylinder axis and radius."""
    try:
        return _geometry_facts(shape.Surface)
    except Exception:
        return _geometry_facts(shape.Curve) if shape.ShapeType == "Edge" else {}


def measure(doc, target, revision, other=None, *, axis=None, at="center", other_at="center", frame="world"):
    """Native finite geometry; optional signed axis distance between explicit extent anchors."""
    if axis not in (None, "x", "y", "z") or at not in ("min", "max", "center") or other_at not in ("min", "max", "center"):
        raise ValueError("Use axis x/y/z and extent anchors min/max/center.")
    shape = native_shape(doc, target, revision)
    placement = App.Placement() if frame == "world" else _world_placement(resolve(doc, frame))
    def framed(native):
        transformed = native.copy()
        transformed.Placement = placement.inverse().multiply(transformed.Placement)
        return transformed
    bounds = framed(shape).BoundBox
    result = {"document": doc.Name, "revision": revision,
              "coordinateFrame": "document/world" if frame == "world" else resolve(doc, frame).Name,
              "units": "mm", "extentScope": "finite native BRep axis-aligned bounds in the declared frame",
              "provenance": "FreeCAD/OCC BRep, not viewport tessellation",
              "tolerance_mm": shape.getTolerance(1)}
    for coordinate in "XYZ":
        result.update({f"min{coordinate}_mm": getattr(bounds, coordinate+"Min"),
                       f"max{coordinate}_mm": getattr(bounds, coordinate+"Max"),
                       f"span{coordinate}_mm": getattr(bounds, coordinate+"Length")})
    def anchor(box, which):
        point = box.Center
        if axis is not None and which != "center":
            setattr(point, axis, getattr(box, axis.upper()+which.title()))
        return point
    first_anchor = anchor(bounds, at)
    def xyz(point):
        return [point.x, point.y, point.z]
    if axis is not None:
        result.update(axis=axis, at=at, extent_mm=getattr(first_anchor, axis),
                      anchor_mm=xyz(first_anchor), anchorWorld_mm=xyz(placement.multVec(first_anchor)))
    if other is not None:
        second = native_shape(doc, other, revision)
        second_bounds = framed(second).BoundBox
        second_anchor = anchor(second_bounds, other_at)
        result["delta_mm"] = xyz(second_anchor-first_anchor)
        result["deltaScope"] = "Second extent anchor minus first; not closest-point displacement."
        if axis is not None:
            projected = App.Vector(first_anchor)
            setattr(projected, axis, getattr(second_anchor, axis))
            result.update(other_at=other_at, otherExtent_mm=getattr(second_anchor, axis),
                          otherAnchor_mm=xyz(second_anchor), otherAnchorWorld_mm=xyz(placement.multVec(second_anchor)),
                          axisDistance_mm=getattr(second_anchor-first_anchor, axis),
                          axisGap_mm=getattr(second_bounds, axis.upper()+"Min")-getattr(bounds, axis.upper()+"Max"),
                          axisOuterSpan_mm=max(getattr(bounds, axis.upper()+"Max"), getattr(second_bounds, axis.upper()+"Max"))
                          - min(getattr(bounds, axis.upper()+"Min"), getattr(second_bounds, axis.upper()+"Min")),
                          dimensionLineWorld_mm=[xyz(placement.multVec(first_anchor)), xyz(placement.multVec(projected))])
        distance, points, _ = shape.distToShape(second)
        result.update(distance_mm=distance, closestPoints_mm=[
            [value(placement.inverse().multVec(a)), value(placement.inverse().multVec(b))] for a, b in points[:10]], closestPointCount=len(points),
            tolerance_mm=max(result["tolerance_mm"], second.getTolerance(1)))
        if shape.Solids and second.Solids:
            result["overlapVolume_mm3"] = shape.common(second).Volume
        else:
            result["overlapVolume_mm3"] = None
        first_normal = _surface_facts(shape).get("normal")
        second_normal = _surface_facts(second).get("normal")
        if first_normal and second_normal:
            dot = sum(a * b for a, b in zip(first_normal, second_normal))
            result["angleDeg"] = round(math.degrees(math.acos(max(-1.0, min(1.0, abs(dot))))), 6)
            result["parallel"] = abs(abs(dot) - 1.0) <= 1e-6
        if (shape.ShapeType == second.ShapeType == "Edge"
                and type(shape.Curve).__name__ == type(second.Curve).__name__ == "Line"):
            direction = shape.Curve.Direction
            direction.normalize()
            other_direction = second.Curve.Direction
            other_direction.normalize()
            dot = max(-1.0, min(1.0, abs(direction.dot(other_direction))))
            result["angleDeg"] = math.degrees(math.acos(dot))
            result["parallel"] = abs(dot - 1) <= 1e-9
            result["lineSeparation_mm"] = ((second.Vertexes[0].Point - shape.Vertexes[0].Point).cross(direction).Length
                                            if result["parallel"] else None)
            result["lineSeparationScope"] = "Infinite supporting lines; distance_mm remains finite-edge distance."
        result["note"] = "Zero distance alone does not distinguish touching from overlap. Null overlap volume means this check is unavailable for non-solids."
    else:
        b = bounds
        result.update(valid=shape.isValid(), solids=len(shape.Solids), volume_mm3=shape.Volume,
                      area_mm2=shape.Area, size_mm=[b.XLength, b.YLength, b.ZLength])
        result.update(_surface_facts(framed(shape)))
        if shape.ShapeType == "Edge":
            result["length_mm"] = shape.Length
        center = _center_of_mass(framed(shape))
        if center is not None:
            result["centerOfMass_mm"] = center
    return result


def remap(doc, reference, revision, facts=None):
    """Find the face or edge at the current revision that best matches a stale reference.

    The old topology is gone, so pass the facts you observed for the old reference
    (area_mm2/length_mm, centerOfMass_mm, geometryType, normal/axis). Returns a ranked
    candidate match, not proof of identity; confirm before relying on it.
    """
    if not isinstance(reference, dict):
        raise ValueError("remap needs a reference dict with object and subelement.")
    subelement = str(reference.get("subelement") or "")
    if not subelement.startswith(("Face", "Edge")):
        raise ValueError("remap needs a FaceN or EdgeN reference.")
    if not isinstance(facts, dict) or not facts:
        raise ValueError("remap needs the old geometry facts (area_mm2/length_mm, "
                         "centerOfMass_mm, geometryType); re-inspect instead if you do not have them.")
    obj = resolve(doc, reference.get("object"))
    kind = "edges" if subelement.startswith("Edge") else "faces"
    measure_key = "length_mm" if kind == "edges" else "area_mm2"
    shape = native_shape(doc, obj, revision)
    parts = shape.Edges if kind == "edges" else shape.Faces
    want_type = facts.get("geometryType")
    want_measure = facts.get(measure_key)
    want_center = facts.get("centerOfMass_mm")
    want_axis = facts.get("normal") or facts.get("axis")
    candidates = []
    for index, part in enumerate(parts, 1):
        geometry = part.Curve if kind == "edges" else part.Surface
        item = _geometry_facts(geometry)
        if want_type and item.get("geometryType") != want_type:
            continue
        score = 0.0
        measured = part.Length if kind == "edges" else part.Area
        if want_measure and measured:
            score += abs(measured - want_measure) / max(abs(want_measure), 1e-9)
        center = _center_of_mass(part)
        if want_center and center:
            score += math.dist(center, want_center) / 10.0
        axis = item.get("normal") or item.get("axis")
        if want_axis and axis:
            dot = abs(sum(a * b for a, b in zip(axis, want_axis)))
            score += 1.0 - min(1.0, dot)
        candidates.append({
            "reference": {"document": doc.Name, "documentToken": document_token(doc),
                          "object": obj.Name,
                          "subelement": f"{'Edge' if kind == 'edges' else 'Face'}{index}",
                          "revision": revision},
            "score": round(score, 6), measure_key: measured, "centerOfMass_mm": center, **item})
    candidates.sort(key=lambda candidate: candidate["score"])
    if not candidates:
        raise ValueError("No candidate matches those facts at the current revision.")
    return {"document": doc.Name, "revision": revision, "object": obj.Name,
            "reference": candidates[0]["reference"], "score": candidates[0]["score"],
            "candidates": candidates[:5],
            "note": "Ranked by area/centroid/direction closeness; confirm before relying on it."}


def verify(doc, checks, revision):
    """Evaluate only caller-declared claims. Unknown or unavailable evidence never passes."""
    if not isinstance(checks, list) or len(checks) > 50:
        raise ValueError("checks must be a list of at most 50 explicit claims.")
    results = []
    for check in checks:
        claim = {"expected": check.get("expected") if isinstance(check, dict) else None,
                 "measured": None, "status": "unverifiable"}
        try:
            if not isinstance(check, dict) or set(check) - {"object", "metric", "expected", "tolerance", "other", "axis", "at", "other_at", "frame"}:
                raise ValueError("Claim fields: object, metric, expected, tolerance, optional other/axis/at/other_at/frame.")
            claim.update(check)
            metric = check["metric"]
            expected = check["expected"]
            if metric == "fullyConstrained":
                obj = resolve(doc, check["object"])
                if "Touched" in obj.State:
                    raise ValueError("Sketch needs recompute before reading solver status.")
                if not obj.isDerivedFrom("Sketcher::SketchObject"):
                    raise ValueError("fullyConstrained requires a native sketch.")
                measured = obj.DoF == 0 and not obj.ConflictingConstraints and not obj.RedundantConstraints
                measured = bool(measured)
            elif metric in ("bodyTip", "sketchDegreesOfFreedom", "nativeFeatureHistory"):
                obj = resolve(doc, check["object"])
                if "Touched" in obj.State:
                    raise ValueError("Recompute before checking feature history.")
                if metric == "sketchDegreesOfFreedom":
                    if not obj.isDerivedFrom("Sketcher::SketchObject"):
                        raise ValueError("sketchDegreesOfFreedom requires a native sketch.")
                    measured = obj.DoF
                else:
                    if not obj.isDerivedFrom("PartDesign::Body"):
                        raise ValueError(f"{metric} requires a native PartDesign Body.")
                    if metric == "bodyTip":
                        measured = obj.Tip.Name if obj.Tip else None
                    else:
                        measured = bool(obj.Tip and obj.Tip in obj.Group
                            and obj.Tip.isDerivedFrom("PartDesign::Feature")
                            and obj.Tip.TypeId not in ("PartDesign::Feature", "PartDesign::FeaturePython")
                            and any(x.isDerivedFrom("Sketcher::SketchObject") for x in obj.Group))
                        claim["scope"] = "Body has a native parametric Tip and a sketch; not proof that every operation or design relationship is editable."
            elif metric == "type":
                measured = resolve(doc, check["object"]).TypeId
            else:
                measurements = measure(doc, check["object"], revision, check.get("other"),
                                       **{k: check[k] for k in ("axis", "at", "other_at", "frame") if k in check})
                if metric not in measurements or metric in {"document", "revision", "provenance", "note"}:
                    raise ValueError("Unsupported measurement metric.")
                measured = measurements[metric]
                claim["provenance"] = measurements["provenance"]
                claim["coordinateFrame"] = measurements["coordinateFrame"]
            claim["measured"] = measured
            if measured is None:
                raise ValueError("Measurement unavailable for this geometry.")
            if isinstance(measured, (bool, str)):
                if type(measured) is not type(expected):
                    raise ValueError("Expected value has the wrong type.")
                passed = measured == expected
            else:
                tolerance = check["tolerance"]
                if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance) or tolerance < 0:
                    raise ValueError("Declare a finite non-negative tolerance in the metric's units.")
                actual = measured if isinstance(measured, list) else [measured]
                wanted = expected if isinstance(expected, list) else [expected]
                if len(actual) != len(wanted) or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in actual + wanted):
                    raise ValueError("Expected finite numeric values matching measurement dimensions.")
                passed = all(abs(a - b) <= tolerance for a, b in zip(actual, wanted))
            claim["status"] = "pass" if passed else "fail"
        except Exception as error:
            claim["reason"] = str(error)
        results.append(claim)
    status = "fail" if any(c["status"] == "fail" for c in results) else (
        "pass" if results and all(c["status"] == "pass" for c in results) else "unverifiable")
    return {"document": doc.Name, "revision": revision, "status": status, "claims": results,
            "scope": "Only the declared checks; not certification of overall design correctness."}


def declare_checks(doc, name, checks, revision):
    """Persist explicit requirements on a native group with rename-aware object links."""
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name):
        raise ValueError("Check name must be a native identifier of at most 64 characters.")
    outcome = verify(doc, checks, revision)
    if any(c["status"] == "unverifiable" for c in outcome["claims"]) or not checks:
        raise ValueError("Every saved check must be evaluable: "+str(outcome["claims"]))
    links, encoded = [], []
    for check in checks:
        entry = dict(check)
        for key in ("object", "other", "frame"):
            if key not in entry or (key == "frame" and entry[key] == "world"):
                continue
            if not isinstance(entry[key], str):
                raise ValueError("Saved checks require named native objects/frames; transient topology references cannot persist.")
            obj = resolve(doc, entry[key])
            if obj not in links:
                links.append(obj)
            entry[key] = {"target": links.index(obj)}
        encoded.append(entry)
    obj = doc.getObject(name)
    if obj is not None and not is_check_container(obj):
        raise ValueError("That object name is already in use.")
    if obj is None:
        obj = doc.addObject("App::DocumentObjectGroup", name)
        obj.addProperty("App::PropertyString", "AnthraciteChecks", "Verification")
    for prop in obj.PropertiesList:
        if prop.startswith("CheckTarget"):
            setattr(obj, prop, None)
    for index, target in enumerate(links):
        prop = "CheckTarget"+str(index)
        if prop not in obj.PropertiesList:
            obj.addProperty("App::PropertyLink", prop, "Verification")
        setattr(obj, prop, target)
    obj.AnthraciteChecks = json.dumps(encoded, sort_keys=True, allow_nan=False)
    return {"object": obj.Name, "verification": outcome,
            "note": "Requirements persist; they report failures, not modeling constraints. Use native expressions/constraints to drive relationships."}


def is_check_container(obj):
    return obj.TypeId == "App::DocumentObjectGroup" and not obj.Group and "AnthraciteChecks" in obj.PropertiesList


def saved_checks(doc, revision, name=None):
    results = []
    containers = [resolve(doc, name)] if name is not None else [o for o in doc.Objects if is_check_container(o)]
    for obj in containers:
        try:
            if not is_check_container(obj):
                raise ValueError("Expected a saved-check container.")
            checks = json.loads(obj.AnthraciteChecks)
            for check in checks:
                for key in ("object", "other", "frame"):
                    if isinstance(check.get(key), dict):
                        target = getattr(obj, "CheckTarget"+str(check[key]["target"]))
                        if target is None:
                            raise ValueError("Saved check target was removed.")
                        check[key] = target.Name
            result = verify(doc, checks, revision)
        except Exception as error:
            result = {"document": doc.Name, "revision": revision, "status": "unverifiable", "claims": [], "reason": str(error)}
        results.append({"savedCheck": obj.Name, **result})
    return results


def value(item, depth=0):
    if depth > 4:
        return {"truncated": True, "type": type(item).__name__}
    if item is None or isinstance(item, (bool, int, float)):
        return item
    if isinstance(item, str):
        return item if len(item) <= 1000 else {"text": item[:1000], "truncated": True}
    if hasattr(item, "Name") and hasattr(item, "Document"):
        return {"object": item.Name, "document": item.Document.Name}
    if isinstance(item, (list, tuple)):
        result = [value(x, depth + 1) for x in item[:100]]
        if len(item) > 100:
            result.append({"remaining": len(item) - 100})
        return result
    if isinstance(item, App.Units.Quantity):
        return {"value": item.Value, "unit": str(item.Unit), "display": item.UserString}
    if isinstance(item, App.Vector):
        return {"x": item.x, "y": item.y, "z": item.z}
    if isinstance(item, App.Placement):
        return {"position": value(item.Base), "quaternion": list(item.Rotation.Q)}
    if isinstance(item, App.Rotation):
        return {"quaternion": list(item.Q)}
    return {"type": type(item).__name__, "details": "Use the native property API for this value"}


def resolve(doc, obj):
    if isinstance(obj, str):
        obj = doc.getObject(obj)
    if obj is None or obj.Document is not doc:
        raise ValueError("Expected an internal object name in the active document.")
    return obj


def properties(obj, names=None, offset=0, limit=40):
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("Property limit must be 1..100; offset must be non-negative.")
    names = sorted(obj.PropertiesList) if names is None else list(names)
    result = {}
    for name in names[offset:offset + limit]:
        try:
            result[name] = {"type": obj.getTypeIdOfProperty(name),
                            "value": value(getattr(obj, name)),
                            "description": obj.getDocumentationOfProperty(name)[:1000]}
            if result[name]["type"] == "App::PropertyEnumeration":
                result[name]["choices"] = list(obj.getEnumerationsOfProperty(name))
        except Exception as error:
            result[name] = {"error": str(error)}
    return {"properties": result, "total": len(names),
            "nextOffset": offset + limit if offset + limit < len(names) else None}


_EDITABLE_NUMBERS = {
    "App::PropertyLength": "mm",
    "App::PropertyDistance": "mm",
    "App::PropertyAngle": "deg",
    "App::PropertyPercent": "%",
    "App::PropertyFloat": "",
    "App::PropertyFloatConstraint": "",
    "App::PropertyInteger": "",
    "App::PropertyIntegerConstraint": "",
}
_EDITABLE_TEXT = {"App::PropertyString"}
_EDITABLE_BOOL = {"App::PropertyBool"}
_EDITABLE_ENUM = {"App::PropertyEnumeration"}
_DIMENSION_CONSTRAINTS = {"Distance", "DistanceX", "DistanceY", "Radius", "Diameter", "Angle"}


def native_bounds(obj, name):
    """Known native numeric domains, in FreeCAD's native units."""
    kind = obj.getTypeIdOfProperty(name)
    # Constraint properties expose only their scalar value through public Python
    # APIs; their runtime min/max are not serialized either. Unknown stays unknown,
    # and the checked setter still detects native clamping and rolls back.
    return [0.0, None] if kind == "App::PropertyLength" else [None, None]
# Attachment, visibility, and derived bookkeeping are not design parameters.
_SKIP_PROPERTIES = {
    "Visibility", "Label2", "ExpressionEngine", "MapMode", "MapPathParameter", "MapReversed",
    "AttacherEngine", "AttacherType", "AttachmentSupport", "AttachmentOffset",
    "FullyConstrained", "Support", "Profile", "BaseFeature", "Tip", "Shape", "Placement",
    "AnthraciteParameters",
}
_SKIP_TARGET_TYPES = {"App::Origin", "App::Plane", "App::Line", "App::Point"}


def parameter_targets(doc):
    """Objects a parameters panel can inspect, in document order."""
    return [{"name": obj.Name, "label": obj.Label, "type": obj.TypeId}
            for obj in doc.Objects if obj.TypeId not in _SKIP_TARGET_TYPES]


def _expressions(obj):
    result = {}
    for path, expression in (getattr(obj, "ExpressionEngine", None) or []):
        # Native Sketcher emits relative paths such as .Constraints.Width;
        # other features emit Length without the leading dot.
        result[str(path).lstrip(".")] = str(expression)
    return result


def parameters(doc, target):
    """Editable design parameters for one object: native properties and sketch dimensions.

    Read-only and derived properties are omitted; anything returned can be assigned back
    through the checked executor.
    """
    obj = resolve(doc, target)
    expressions = _expressions(obj)
    entries = []
    for name in obj.PropertiesList:
        if name.startswith("_") or name in _SKIP_PROPERTIES:
            continue
        status = []
        try:
            status = [str(item).casefold() for item in obj.getPropertyStatus(name)]
        except Exception:
            pass
        if "readonly" in status or "immutable" in status:
            continue
        type_id = obj.getTypeIdOfProperty(name)
        entry = {"id": f"prop:{name}", "group": "Properties", "label": name,
                 "expression": expressions.get(name)}
        if type_id in _EDITABLE_NUMBERS:
            entry.update({"kind": "number", "value": value(getattr(obj, name)),
                          "unit": _EDITABLE_NUMBERS[type_id],
                          "nativeBounds": native_bounds(obj, name),
                          "integer": type_id in {"App::PropertyInteger", "App::PropertyIntegerConstraint"}})
        elif type_id in _EDITABLE_BOOL:
            entry.update({"kind": "bool", "value": bool(getattr(obj, name))})
        elif type_id in _EDITABLE_TEXT:
            entry.update({"kind": "text", "value": str(getattr(obj, name))})
        elif type_id in _EDITABLE_ENUM:
            options = []
            try:
                options = [str(item) for item in obj.getEnumerationsOfProperty(name)]
            except Exception:
                pass
            entry.update({"kind": "enum", "value": str(getattr(obj, name)), "options": options})
        else:
            continue
        entries.append(entry)
    if obj.isDerivedFrom("Sketcher::SketchObject"):
        for index, constraint in enumerate(obj.Constraints):
            try:
                if not constraint.Driving or constraint.Type not in _DIMENSION_CONSTRAINTS:
                    continue
                current = obj.getDatum(index).Value
            except Exception:
                continue
            if current is None:
                continue
            label = str(getattr(constraint, "Name", "") or f"{constraint.Type}{index + 1}")
            entries.append({"id": f"con:{index}", "group": "Constraints", "label": label,
                            "kind": "number", "value": float(current),
                            "unit": "deg" if constraint.Type == "Angle" else "mm",
                            "expression": expressions.get(f"Constraints.{constraint.Name}",
                                                          expressions.get(f"Constraints[{index}]"))})
    return {"object": obj.Name, "label": obj.Label, "type": obj.TypeId, "entries": entries}


def sketch(obj, offset=0, limit=40):
    if not obj.isDerivedFrom("Sketcher::SketchObject"):
        raise ValueError("Expected a Sketcher object.")
    if offset < 0 or not 1 <= limit <= 100:
        raise ValueError("Constraint limit must be 1..100; offset must be non-negative.")
    constraints = []
    for index, constraint in enumerate(obj.Constraints[offset:offset + limit], offset):
        entry = {"index": index, "name": constraint.Name, "type": constraint.Type}
        for field in ("First", "FirstPos", "Second", "SecondPos", "Third", "ThirdPos", "Value"):
            entry[field] = value(getattr(constraint, field, None))
        entry["driving"] = constraint.Driving
        constraints.append(entry)
    return {"degreesOfFreedom": obj.DoF,
            "conflicting": list(obj.ConflictingConstraints),
            "redundant": list(obj.RedundantConstraints),
            "constraints": constraints, "constraintCount": obj.ConstraintCount,
            "nextOffset": offset + limit if offset + limit < obj.ConstraintCount else None,
            "geometryCount": obj.GeometryCount,
            "externalGeometry": value(obj.ExternalGeometry)}


def node(obj, detailed=False):
    result = {"name": obj.Name, "label": obj.Label, "type": obj.TypeId,
              "state": list(obj.State),
              "dependsOn": [x.Name for x in obj.OutList[:100]], "dependencyCount": len(obj.OutList),
              "usedBy": [x.Name for x in obj.InList[:100]], "usedByCount": len(obj.InList)}
    if hasattr(obj, "Group"):
        result["children"] = [x.Name for x in obj.Group[:100]]
        result["childCount"] = len(obj.Group)
    if hasattr(obj, "Tip"):
        result["tip"] = value(obj.Tip)
    result["expressions"] = value(obj.ExpressionEngine)
    important = ("Profile", "BaseFeature", "Support", "MapMode", "AttachmentOffset",
                 "Length", "Length2", "Width", "Height", "Radius", "Angle", "Type",
                 "Occurrences", "Originals", "Axis", "Midplane", "Reversed")
    result["parameters"] = {p: value(getattr(obj, p)) for p in important if p in obj.PropertiesList}
    if obj.isDerivedFrom("Sketcher::SketchObject"):
        result["sketch"] = sketch(obj, limit=40 if detailed else 8)
    if detailed:
        result.update(properties(obj))
    return result


def tree(doc, root=None, offset=0, limit=40):
    if offset < 0 or not 1 <= limit <= 100:
        raise ValueError("Tree limit must be 1..100; offset must be non-negative.")
    objects = list(doc.Objects)
    if root is not None:
        parent = resolve(doc, root)
        objects = [parent] + list(getattr(parent, "Group", []))
    def inspect_node(obj):
        try:
            return node(obj)
        except Exception as error:
            return {"name": obj.Name, "type": obj.TypeId, "inspectionError": str(error)}
    return {"document": doc.Name, "nodes": [inspect_node(o) for o in objects[offset:offset + limit]],
            "total": len(objects), "nextOffset": offset + limit if offset + limit < len(objects) else None}


def frame(doc, target):
    obj = resolve(doc, target)
    if not hasattr(obj, "Placement"):
        raise ValueError("This object has no native placement frame.")
    placement = _world_placement(obj)
    return {"object": obj.Name, "lengthUnit": "mm", "angleUnit": "degrees",
            "worldFromLocal": value(placement), "localFromWorld": value(placement.inverse()),
            "origin_world_mm": value(placement.Base),
            "axes_world": {name: value(placement.Rotation.multVec(axis)) for name, axis in
                           (("x", App.Vector(1, 0, 0)), ("y", App.Vector(0, 1, 0)), ("z", App.Vector(0, 0, 1)))},
            "pointConversion": (f"doc.getObject({obj.Name!r}).getGlobalPlacement().multVec(App.Vector(x, y, z))"
                                if hasattr(obj, "getGlobalPlacement") else
                                "Use the returned worldFromLocal placement; link shape geometry already includes link scaling."),
            "directionConversion": "Use Placement.Rotation.multVec(direction); directions must not include translation.",
            "geometryFrame": "Sketch Geometry/constraints use local XY. cad.topology(), cad.measure(), picker points and section planes use document/world coordinates.",
            "placementNote": "Placement is relative to its parent; getGlobalPlacement includes native container placement. Shape geometry can already carry placement; do not apply it twice."}


def explain(doc, target, revision, offset=0, limit=20):
    """Explain exact native dependencies, never infer which sketch edge generated a face."""
    if offset < 0 or not 1 <= limit <= 50:
        raise ValueError("Explanation limit must be 1..50; offset must be non-negative.")
    reference = target if isinstance(target, dict) else None
    if reference is not None:
        native_shape(doc, reference, revision)  # Reject stale, ambiguous, or recreated targets.
        target = reference["object"]
    obj = resolve(doc, target)
    body = obj if obj.isDerivedFrom("PartDesign::Body") else next(
        (parent for parent in obj.InList if parent.isDerivedFrom("PartDesign::Body") and obj in parent.Group), None)
    feature = body.Tip if obj is body and body.Tip else obj
    profile = getattr(feature, "Profile", None)
    if isinstance(profile, tuple):
        profile = profile[0]
    downstream = sorted({x.Name for x in feature.InListRecursive if x is not body})
    chain, seen, pending = [], set(), [feature]
    while pending and len(chain) < 100:
        current = pending.pop(0)
        if current.Name in seen:
            continue
        seen.add(current.Name)
        chain.append(current)
        pending.extend(x for x in current.OutList if x is not body and x.Name not in seen)
    return {"document": doc.Name, "revision": revision, "selectedObject": obj.Name,
            "reference": reference, "body": body.Name if body else None,
            "displayedTip": body.Tip.Name if body and body.Tip else None,
            "feature": node(feature), "profile": node(profile) if hasattr(profile, "Name") else None,
            "upstream": [node(x) for x in chain[offset:offset + limit]],
            "nextOffset": offset + limit if offset + limit < len(chain) else None,
            "truncated": bool(pending), "downstream": downstream[:50], "downstreamCount": len(downstream),
            "frame": frame(doc, obj) if hasattr(obj, "Placement") else None,
            "editability": diagnostics(doc, [feature] + ([profile] if hasattr(profile, "Name") else [])),
            "editAdvice": "Inspect the listed parameters, expressions and profile constraints before editing. Preserve feature names and downstream dependencies; confirm which dimension expresses the user's intent.",
            "topologyPolicy": "This is a native dependency chain, not face-generation provenance. A Body pick identifies its displayed Tip, not a proven originating sketch edge. Never infer a face/edge correspondence from list position."}


def diagnostics(doc, objects=None):
    warnings = []
    objects = list(doc.Objects if objects is None else objects)
    dimensions = {}
    for obj in objects:
        def warn(code, message):
            warnings.append({"object": obj.Name, "code": code, "severity": "warning", "message": message})
        if obj.TypeId in ("Part::Feature", "PartDesign::Feature") and hasattr(obj, "Shape") and not obj.Shape.isNull():
            warn("opaque-shape", "No native parametric operation is identified. Imported/static geometry may be intentional.")
        if obj.isDerivedFrom("Sketcher::SketchObject"):
            if obj.DoF > 0:
                warn("underconstrained", f"Sketch has {obj.DoF} remaining degrees of freedom; confirm these are intentional.")
            if obj.ConflictingConstraints or obj.RedundantConstraints:
                warn("constraint-conflict", "Inspect conflicting/redundant constraint indices before changing geometry.")
        if obj.isDerivedFrom("PartDesign::Body"):
            if obj.Group and not obj.Tip:
                warn("missing-body-tip", "Body contains features but has no Tip; inspect native history before continuing.")
            elif obj.Tip and obj.Tip not in obj.Group:
                warn("disconnected-body-tip", "The Body Tip is not in its native Group.")
        for prop in ("Support", "Profile", "Base"):
            if prop not in obj.PropertiesList:
                continue
            linked = repr(value(getattr(obj, prop)))
            if "Face" in linked or "Edge" in linked:
                warn("subelement-dependency", f"{prop} references generated topology; re-resolve after topology changes, never guess an index.")
        if any(s in ("Invalid", "Error") for s in obj.State):
            warn("recompute-error", "Feature failed; inspect its dependencies and native status.")
        expressions = {path for path, _ in obj.ExpressionEngine}
        for prop in ("Length", "Width", "Height", "Radius", "Diameter"):
            if prop not in obj.PropertiesList or prop in expressions:
                continue
            quantity = getattr(obj, prop)
            if isinstance(quantity, App.Units.Quantity) and quantity.Value > 0:
                dimensions.setdefault((quantity.Value, str(quantity.Unit)), []).append(f"{obj.Name}.{prop}")
    for (number, unit), references in dimensions.items():
        if len(references) > 1:
            warnings.append({"code": "repeated-dimension", "severity": "info",
                             "references": references[:20], "value": number, "unit": unit,
                             "message": "Independent equal dimensions: confirm whether an expression should encode a relationship. Equality alone does not imply design intent."})
    return {"warnings": warnings[:100], "remaining": max(0, len(warnings) - 100),
            "note": "Warnings are not proof of bad design; intentional exceptions are allowed."}


# Small native examples, returned on demand rather than injected in every prompt.
GUIDES = {
    "design-intent": {
        "purpose": "Own independent fit/section dimensions once; derive geometry in native expressions before creating features.",
        "code": "dimensions = doc.addObject('App::DocumentObjectGroup', 'DesignDimensions')\n"
                "dimensions.addProperty('App::PropertyLength', 'Thickness', 'Section')\n"
                "dimensions.Thickness = 2\n"
                "dimensions.addProperty('App::PropertyDistance', 'JunctionOffset', 'Position')\n"
                "dimensions.JunctionOffset = -10\n"
                "cad.declare_parameter('thickness', dimensions, property='Thickness', unit='mm', bounds=[1,4])\n"
                "doc.BasePad.setExpression('Length', 'DesignDimensions.Thickness')",
        "checks": "Keep this parameter container independent of Bodies it drives; putting a Body inside it or referencing the containing Body from a child can create cycles. Length is nonnegative; Distance is signed. Expose meaningful inputs, not independently duplicated vertex coordinates. Plan the native feature tree and relationships first."},
    "profiles": {
        "purpose": "Use one coherent constrained outline and shared feature profiles instead of accumulating copied trees.",
        "code": "sketch = body.newObject('Sketcher::SketchObject', 'Outline')\n"
                "a = sketch.addGeometry(Part.LineSegment(App.Vector(0,0,0), App.Vector(20,0,0)), False)\n"
                "b = sketch.addGeometry(Part.LineSegment(App.Vector(20,0,0), App.Vector(20,10,0)), False)\n"
                "sketch.addConstraint(Sketcher.Constraint('Coincident', a, 2, b, 1))\n"
                "sketch.addConstraint(Sketcher.Constraint('Horizontal', a))\n"
                "sketch.addConstraint(Sketcher.Constraint('Vertical', b))",
        "checks": "This is an open-profile fragment: close and dimension the remaining geometry before padding. Use dimensional/coincidence/tangency constraints and expressions, not Block/Frozen geometry. Multiple closed profiles can share one Pad when they join its existing base into one solid. Use a pattern for genuine repeated placement, not merely because several operations are Pads. Intersect joining outlines deliberately; fixed axis spans do not preserve perpendicular strip width when angles change."},
    "arcs": {
        "purpose": "Keep a parameter-sensitive junction radius in the driving sketch instead of a fragile generated-edge fillet when practical.",
        "code": "import math\n"
                "circle = Part.Circle(App.Vector(0,0,0), App.Vector(0,0,1), 2)\n"
                "arc = sketch.addGeometry(Part.ArcOfCircle(circle, 0, math.pi/2), False)\n"
                "radius = sketch.addConstraint(Sketcher.Constraint('Radius', arc, 2))\n"
                "sketch.renameConstraint(radius, 'JunctionRadius')",
        "checks": "Use explicit positive-normal arcs. Native arc start/end positions can differ from a negative-normal Part.Arc construction. Connect the actual endpoints, constrain the radius and tangency/position, check the intended sweep and zero DoF, then perturb dependent dimensions. Generated-edge dress-ups are appropriate when intent demands them; verify their references survive representative edits."},
    "engineering": {
        "purpose": "Test design intent and native editability beyond a successful recompute or heuristic score.",
        "checks": "Preserve the original in variant work. Check fit gaps, matching interfaces, perpendicular widths, material thickness, contour continuity and intended radius/tangency against requirements. Perturb every independent dimension and a coupled batch on isolated candidates; test native undo/redo and save/reopen with another edit. Inspect result.ok and accepted from cad.evaluate; copy/hash refusal is not a passed candidate. Bounds alone do not prove every parameter combination works. Do not claim strength or fatigue from geometric checks."},
    "authoring": {
        "purpose": "Create native constrained profiles and features with persistent named inputs.",
        "code": "body = doc.addObject('PartDesign::Body', 'Body')\n"
                "sketch = cad.rectangle_sketch(body, 'BaseSketch', '20 mm', '10 mm')\n"
                "pad = cad.pad(sketch, 'BasePad', '5 mm')\n"
                "cad.declare_parameter('width', sketch, constraint='Width', bounds=[10,40])\n"
                "cad.declare_parameter('thickness', pad, property='Length', bounds=[1,10])",
        "checks": "Helpers return native objects. Profiles use local XY; position/attach explicitly. Check dimensions, DoF, Tip and explicit requirements."},
    "parameters": {
        "purpose": "Edit independent native inputs in one action; preserve derived expressions.",
        "code": "cad.set_parameters({'width':'30 mm', 'thickness':'6 mm'})\n"
                "cad.verify([{'object':'BasePad','metric':'size_mm','expected':[30,10,6],'tolerance':0.001}])",
        "checks": "Inspect cad.parameters() first. Numbers use declared units. Candidate evaluation is standalone: cad.evaluate(values, target='BasePad', checks=[...]); inspect result.ok and result.accepted before applying."},
    "partdesign": {
        "purpose": "Create an editable Body with a fully constrained circular sketch and Pad.",
        "code": "import Part, Sketcher\nbody = doc.addObject('PartDesign::Body', 'Body')\n"
                "sketch = body.newObject('Sketcher::SketchObject', 'BaseSketch')\n"
                "sketch.addGeometry(Part.Circle(App.Vector(0,0,0), App.Vector(0,0,1), 10), False)\n"
                "sketch.addConstraint(Sketcher.Constraint('Coincident', 0, 3, -1, 1))\n"
                "i = sketch.addConstraint(Sketcher.Constraint('Diameter', 0, 20))\n"
                "sketch.renameConstraint(i, 'Diameter')\ndoc.recompute()\n"
                "pad = body.newObject('PartDesign::Pad', 'BasePad')\npad.Profile = sketch\n"
                "pad.Length = 8\nsketch.Visibility = False\ndoc.recompute()",
    },
    "edit": {"purpose": "Edit parameters of existing features; preserve names and dependent features.",
             "code": "sketch = doc.getObject('BaseSketch')\n"
                     "sketch.setDatum('Diameter', App.Units.Quantity('24 mm'))\n"
                     "doc.getObject('BasePad').Length = 12\ndoc.recompute()",
             "checks": "Inspect Body Tip, downstream errors and sketch degrees of freedom afterward."},
    "expressions": {"purpose": "Express intended relationships instead of copying dimensions.",
                    "code": "doc.getObject('BasePad').setExpression('Length', 'BaseSketch.Constraints.Diameter / 2')",
                    "checks": "Inspect ExpressionEngine and dependencies. Avoid circular references."},
    "attachments": {"purpose": "Prefer origin planes or explicit datum references when they express intent.",
                    "checks": "Inspect Support, MapMode and AttachmentOffset. For cross-body references use native ShapeBinder/SubShapeBinder when appropriate. Discover properties on the installed object; don't guess generated face indices."},
    "patterns": {"purpose": "Prefer a native pattern feature over duplicated independent solids when repetition is intended.",
                 "code": "body = doc.getObject('Body')\npad = doc.getObject('BasePad')\n"
                         "pattern = body.newObject('PartDesign::LinearPattern', 'PinPattern')\n"
                         "pattern.Originals = [pad]\npattern.Direction = (doc.getObject('X_Axis'), [''])\n"
                         "pattern.Length = 30\npattern.Occurrences = 3\npad.Visibility = False\ndoc.recompute()",
                 "checks": "Discover installed PartDesign pattern types with cad.api(query='Pattern'). Inspect Originals, Axis/Direction, Occurrences and spacing on the feature. Verify the changed count and downstream recompute."},
    "repair": {"purpose": "Repair the earliest failing dependency, not the final displayed solid.",
               "checks": "Use cad.tree(), cad.sketch(name), and cad.inspect(name). Check constraint conflicts/redundancies, Support and expressions; change only the offending input. Recompute and inspect affected downstream features."},
}


_OPAQUE_SOLID_TYPES = ("Part::Feature", "Part::FeaturePython")
_BOOLEAN_TYPES = ("Part::Cut", "Part::Fuse", "Part::MultiFuse", "Part::Common", "Part::MultiCommon")
_REPEATABLE_FEATURES = ("PartDesign::Pad", "PartDesign::Pocket", "PartDesign::Hole",
                        "PartDesign::Revolution", "PartDesign::Groove")
_EDITABILITY_WEIGHTS = {"opaque-solid": 0.4, "boolean-workaround": 0.25, "pattern-as-copies": 0.2,
                        "repeated-literal-dimensions": 0.1, "no-expressions": 0.05,
                        "pocket-not-hole": 0.05, "sketch-conflict": 0.2,
                        "underconstrained-sketch": 0.15, "frozen-sketch": 0.25}
_EXPORT_FORMATS = {"step": "Part", "stp": "Part", "iges": "Part", "igs": "Part",
                   "brep": "Part", "stl": "Mesh", "obj": "Mesh"}


def _has_solid(obj):
    try:
        return hasattr(obj, "Shape") and not obj.Shape.isNull() and bool(obj.Shape.Solids)
    except Exception:
        return False


def _numeric_dimensions(obj):
    values = []
    expressions = _expressions(obj)
    for name in obj.PropertiesList:
        if name.startswith("_") or name in _SKIP_PROPERTIES or name in expressions:
            continue
        try:
            if obj.getTypeIdOfProperty(name) not in {"App::PropertyLength", "App::PropertyDistance", "App::PropertyAngle"}:
                continue
            current = getattr(obj, name)
        except Exception:
            continue
        if isinstance(current, App.Units.Quantity):
            values.append((name, float(current.Value)))
    return values


def _single_circle_sketch(profile):
    try:
        if not profile.isDerivedFrom("Sketcher::SketchObject"):
            return False
        geometry = profile.Geometry
        return len(geometry) == 1 and type(geometry[0]).__name__ == "Circle"
    except Exception:
        return False


def editability(doc, target=None):
    """Audit how parametric the native feature tree is: the E half of a G/E verdict.

    Flags the patterns that quietly turn a design into a frozen copy: opaque solids,
    booleans standing in for native operations, repeated features with no pattern,
    literal dimensions with no expressions, pockets that should be holes, and sketches
    held by blocks instead of dimensions. Heuristic: it flags likely problems, not intent.
    """
    findings = []
    root = resolve(doc, target) if target is not None else None
    objects = list(doc.Objects) if root is None else [root, *root.OutListRecursive]

    opaque = [o.Name for o in objects if o.TypeId in _OPAQUE_SOLID_TYPES and _has_solid(o)]
    if opaque:
        findings.append({"code": "opaque-solid", "severity": "warning", "objects": opaque,
                         "message": "Opaque Part::Feature solids are not parametric; build with native features so the design stays editable."})

    booleans = [o.Name for o in objects
                if o.TypeId in _BOOLEAN_TYPES or o.isDerivedFrom("PartDesign::Boolean")]
    if booleans:
        findings.append({"code": "boolean-workaround", "severity": "warning", "objects": booleans,
                         "message": "Boolean operations are present; if a native Pocket/Hole/Pattern expresses the intent, fix that feature instead of cutting around it."})

    for body in [o for o in objects if o.isDerivedFrom("PartDesign::Body")]:
        group = [o for o in body.Group if o.isDerivedFrom("PartDesign::Feature")]
        if any(o.isDerivedFrom("PartDesign::Transformed") for o in group):
            continue
        for type_id in _REPEATABLE_FEATURES:
            repeated = {}
            for feature in [o for o in group if o.TypeId == type_id]:
                profile = getattr(feature, "Profile", None)
                profile = profile[0] if isinstance(profile, (list, tuple)) and profile else profile
                if profile is None:
                    continue
                intent = (profile.Name, tuple((p, repr(value(getattr(feature, p))))
                                             for p in ("Length", "Angle", "Reversed", "Midplane", "Direction")
                                             if p in feature.PropertiesList))
                repeated.setdefault(intent, []).append(feature.Name)
            for names in [names for names in repeated.values() if len(names) >= 2]:
                findings.append({"code": "pattern-as-copies", "severity": "warning", "objects": names,
                                 "message": "Features reuse the same profile and extent. Confirm whether a native pattern expresses the intended repetition."})

    expression_users = [o for o in objects if getattr(o, "ExpressionEngine", None)]
    dimensioned = [o for o in objects if _numeric_dimensions(o)]
    if dimensioned and not expression_users:
        findings.append({"code": "no-expressions", "severity": "info",
                         "objects": [o.Name for o in dimensioned][:20],
                         "message": "No expressions are used; bind related dimensions so one change propagates."})
    seen = {}
    for obj in dimensioned:
        for prop, current in _numeric_dimensions(obj):
            if current in (-1.0, 0.0, 1.0):
                continue
            unit = _EDITABLE_NUMBERS[obj.getTypeIdOfProperty(prop)]
            seen.setdefault((current, unit), set()).add(obj.Name)
    duplicates = [{"value": number, "unit": unit, "objects": sorted(names)}
                  for (number, unit), names in seen.items() if len(names) >= 2]
    if duplicates:
        findings.append({"code": "repeated-literal-dimensions", "severity": "warning",
                         "duplicates": duplicates[:20],
                         "message": "The same literal dimension appears on multiple objects; bind them to one expression."})

    for pocket in [o for o in objects if o.TypeId == "PartDesign::Pocket"]:
        if _single_circle_sketch(getattr(pocket, "Profile", None)):
            findings.append({"code": "pocket-not-hole", "severity": "info", "objects": [pocket.Name],
                             "message": "A Pocket on a single-circle sketch is usually a PartDesign::Hole; prefer the native Hole feature."})

    for sketch in [o for o in objects if o.isDerivedFrom("Sketcher::SketchObject")]:
        try:
            constraints = list(sketch.Constraints)
            frozen = sum(1 for c in constraints if getattr(c, "Type", "") in ("Block", "Frozen"))
            dimensional = sum(1 for c in constraints if getattr(c, "Driving", False)
                              and getattr(c, "Type", "") in _DIMENSION_CONSTRAINTS)
            if sketch.ConflictingConstraints or sketch.RedundantConstraints:
                findings.append({"code": "sketch-conflict", "severity": "warning", "objects": [sketch.Name],
                                 "message": "Sketch has conflicting or redundant constraints; resolve them before continuing."})
            if sketch.DoF > 0:
                findings.append({"code": "underconstrained-sketch", "severity": "info", "objects": [sketch.Name],
                                 "message": f"Sketch has {sketch.DoF} degrees of freedom; constrain it with dimensions."})
            if frozen and not dimensional:
                findings.append({"code": "frozen-sketch", "severity": "warning", "objects": [sketch.Name],
                                 "message": "Sketch is held by Block/Frozen constraints instead of dimensions; it will not respond to design changes."})
        except Exception:
            continue

    score = 1.0
    for finding in findings:
        score -= _EDITABILITY_WEIGHTS.get(finding["code"], 0.05)
    score = max(0.0, round(score, 2))
    verdict = "parametric" if score >= 0.8 else "mixed" if score >= 0.5 else "opaque"
    return {"document": doc.Name, "score": score, "verdict": verdict, "findings": findings,
            "expressionCount": len(expression_users),
            "target": root.Name if root is not None else None,
            "scope": "Heuristic editability audit of the target dependency closure or whole document; parameter-change and persistence tests establish editability."}


def _default_export_objects(doc):
    """Body tips plus standalone solids; never the Body wrapper and tip together."""
    bodies = [obj for obj in doc.Objects if obj.isDerivedFrom("PartDesign::Body")]
    grouped = {obj.Name for body in bodies for obj in body.Group}
    targets = [body.Tip.Name for body in bodies if body.Tip is not None]
    targets += [obj.Name for obj in doc.Objects
                if hasattr(obj, "Shape") and not obj.Shape.isNull() and obj.Shape.Solids
                and obj.Name not in grouped and not obj.isDerivedFrom("PartDesign::Body")]
    return targets


def export(doc, path, objects=None, format=None, revision=0):
    """Write geometry to a file (step, iges, brep, stl, obj); returns the absolute path."""
    if Part is None:
        raise ValueError("Part is unavailable; cannot export geometry.")
    if not path:
        raise ValueError("Export needs a destination path.")
    extension = (format or os.path.splitext(path)[1].lstrip(".")).lower()
    if extension not in _EXPORT_FORMATS:
        raise ValueError(f"Unsupported export format '{extension}'. Use step, iges, brep, stl, or obj.")
    if objects is None:
        targets = _default_export_objects(doc)
    elif isinstance(objects, (str, dict)):
        targets = [objects]
    else:
        targets = list(objects)
    resolved = [resolve(doc, target) for target in targets]
    if not resolved:
        raise ValueError("Nothing to export.")
    # OCCT exporters can change shared BRep caches. Export detached world-space
    # shapes, and replace the destination only after a complete file was written.
    shapes = [native_shape(doc, target, revision) for target in targets]
    destination = os.path.abspath(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".anthracite-export-", suffix="." + extension,
                                             dir=os.path.dirname(destination))
    os.close(descriptor)
    try:
        if _EXPORT_FORMATS[extension] == "Mesh":
            import Mesh
            import MeshPart
            mesh = Mesh.Mesh()
            for shape in shapes:
                mesh.addMesh(MeshPart.meshFromShape(Shape=shape, LinearDeflection=0.1,
                                                   AngularDeflection=0.5, Relative=False))
            mesh.write(temporary)
        else:
            shape = shapes[0] if len(shapes) == 1 else Part.makeCompound(shapes)
            method = {"step": "exportStep", "stp": "exportStep", "iges": "exportIges",
                      "igs": "exportIges", "brep": "exportBrep"}[extension]
            getattr(shape, method)(temporary)
        if not os.path.isfile(temporary) or os.path.getsize(temporary) == 0:
            raise RuntimeError("Native exporter did not write a complete file")
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    result = {"path": destination, "format": extension, "objects": [o.Name for o in resolved]}
    if _EXPORT_FORMATS[extension] == "Mesh":
        result["meshOptions"] = {"linearDeflection_mm": 0.1, "angularDeflection_rad": 0.5}
    return result


def compare(doc, reference, target, revision, **options):
    """Score detached world-space solids against a reference using rigid alignment."""
    if Part is None:
        raise ValueError("Part is unavailable; cannot compare geometry.")
    if not reference or not os.path.exists(reference):
        raise ValueError(f"Reference file not found: {reference}")
    reference_shape = Part.read(reference)
    if reference_shape.isNull():
        raise ValueError("The reference file has no shape.")
    if target is None:
        # Visible terminal objects, including assembly links. Hidden source parts
        # are definitions; visible source parts are also physical instances.
        targets = [obj for obj in doc.Objects
                   if hasattr(obj, "Shape") and not obj.Shape.isNull() and obj.Shape.Solids
                   and not obj.isDerivedFrom("App::Part")
                   and getattr(getattr(obj, "ViewObject", None), "Visibility", True)]
        body_children = {child for obj in doc.Objects if obj.isDerivedFrom("PartDesign::Body")
                         for child in obj.Group}
        def visible_ancestors(obj):
            parent = obj.getParentGeoFeatureGroup()
            while parent is not None:
                if not getattr(getattr(parent, "ViewObject", None), "Visibility", True):
                    return False
                parent = parent.getParentGeoFeatureGroup()
            return True
        targets = [obj for obj in targets if obj not in body_children and visible_ancestors(obj)]
    elif isinstance(target, (list, tuple)):
        targets = list(target)
    else:
        targets = [target]
    if not targets:
        raise ValueError("No visible solids to compare; specify target explicitly.")
    shapes = [native_shape(doc, item, revision) for item in targets]
    if any(not shape.Solids for shape in shapes):
        raise ValueError("Comparison targets must contain solids, not faces or edges.")
    target_shape = Part.makeCompound(shapes)
    def solids_only(shape):
        if shape.ShapeType == "Solid":
            return True
        if shape.ShapeType in ("Compound", "CompSolid"):
            children = shape.childShapes()
            return bool(children) and all(solids_only(child) for child in children)
        return False
    if not solids_only(reference_shape):
        raise ValueError("Reference must contain only closed solids, not standalone sheets or wires.")
    import AnthraciteCompare
    parts = [solid for shape in shapes for solid in shape.Solids] if all(solids_only(shape) for shape in shapes) else []
    result = AnthraciteCompare.score(parts,
                                    list(reference_shape.Solids), **options)

    def facts(shape):
        box = shape.BoundBox
        return {"volume_mm3": round(shape.Volume, 6), "area_mm2": round(shape.Area, 6),
                "size_mm": sorted(round(value, 6) for value in (box.XLength, box.YLength, box.ZLength)),
                "solids": len(shape.Solids)}

    want, got = facts(reference_shape), facts(target_shape)
    volume_ratio = got["volume_mm3"] / want["volume_mm3"] if want["volume_mm3"] else None
    area_ratio = got["area_mm2"] / want["area_mm2"] if want["area_mm2"] else None
    linear_scale = round(volume_ratio ** (1.0 / 3.0), 6) if volume_ratio and volume_ratio > 0 else None
    scale_matches = bool(volume_ratio and area_ratio
                         and abs(volume_ratio - 1.0) <= 0.01 and abs(area_ratio - 1.0) <= 0.01)
    result.update({"reference": os.path.abspath(reference), "referenceFacts": want, "target": got,
            "volumeRatio": round(volume_ratio, 6) if volume_ratio else None,
            "areaRatio": round(area_ratio, 6) if area_ratio else None,
            "linearScale": linear_scale, "scaleMatches": scale_matches,
            "factsScope": "Unaligned bounding sizes are diagnostic only; scaleMatches uses volume and area ratios."})
    return result


def guide(topic=None):
    if topic is None:
        return {key: entry["purpose"] for key, entry in GUIDES.items()}
    if topic not in GUIDES:
        raise ValueError(f"Unknown guide {topic!r}; available: {', '.join(GUIDES)}")
    return GUIDES[topic]


def api(doc, obj=None, query="", offset=0, limit=40, kind="types"):
    if obj is not None and kind != "methods":
        return properties(resolve(doc, obj), offset=offset, limit=limit)
    if offset < 0 or not 1 <= limit <= 100:
        raise ValueError("API limit must be 1..100; offset must be non-negative.")
    if kind == "methods":
        target = resolve(doc, obj)
        methods = []
        for name in sorted(dir(target)):
            if name.startswith("_") or query.lower() not in name.lower() or name in target.PropertiesList:
                continue
            try:
                member = getattr(target, name)
                if callable(member):
                    methods.append({"name": name, "documentation": (getattr(member, "__doc__", "") or "")[:2000]})
            except Exception:
                continue
        return {"object": target.Name, "methods": methods[offset:offset + limit], "total": len(methods),
                "nextOffset": offset + limit if offset + limit < len(methods) else None,
                "note": "Native runtime method documentation; no methods were invoked. Empty documentation is not a guessed signature."}
    if kind == "types":
        available = doc.supportedTypes()
    elif kind in ("commands", "workbenches"):
        if not App.GuiUp:
            raise ValueError(f"{kind} discovery requires the FreeCAD GUI")
        import FreeCADGui
        available = FreeCADGui.listCommands() if kind == "commands" else FreeCADGui.listWorkbenches().keys()
    elif kind == "modules":
        import sys
        available = [name for name in sys.modules if name in ("FreeCAD", "FreeCADGui", "Part", "PartDesign", "Sketcher", "Draft", "Arch", "Mesh", "MeshPart", "TechDraw", "Fem", "Assembly", "Spreadsheet", "Path", "Points")]
    else:
        raise ValueError("API kind must be types, commands, workbenches, modules, or methods (with an object).")
    types = sorted(t for t in available if query.lower() in t.lower())
    return {"objectTypes" if kind == "types" else kind: types[offset:offset + limit], "total": len(types),
            "nextOffset": offset + limit if offset + limit < len(types) else None,
            "note": "Types from loaded native modules. Import the relevant FreeCAD module to discover its types."}
