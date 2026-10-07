# SPDX-License-Identifier: LGPL-2.1-or-later
"""Readable, bounded native CAD observations; never a replacement geometry API."""
import FreeCAD as App
import math
import os
import re
import uuid

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
    shape.Placement = obj.getGlobalPlacement().multiply(obj.Placement.inverse()).multiply(shape.Placement)
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
        return {}


def measure(doc, target, revision, other=None):
    shape = native_shape(doc, target, revision)
    result = {"document": doc.Name, "revision": revision,
              "coordinateFrame": "document/world; lengths in mm",
              "provenance": "FreeCAD/OCC BRep, not viewport tessellation",
              "tolerance_mm": shape.getTolerance(1)}
    if other is not None:
        second = native_shape(doc, other, revision)
        distance, points, _ = shape.distToShape(second)
        result.update(distance_mm=distance, closestPoints_mm=[
            [value(a), value(b)] for a, b in points[:10]], closestPointCount=len(points),
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
        result["note"] = "Zero distance alone does not distinguish touching from overlap. Null overlap volume means this check is unavailable for non-solids."
    else:
        b = shape.BoundBox
        result.update(valid=shape.isValid(), solids=len(shape.Solids), volume_mm3=shape.Volume,
                      area_mm2=shape.Area, size_mm=[b.XLength, b.YLength, b.ZLength])
        result.update(_surface_facts(shape))
        center = _center_of_mass(shape)
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
            if not isinstance(check, dict) or set(check) - {"object", "metric", "expected", "tolerance", "other"}:
                raise ValueError("Claim fields: object, metric, expected, tolerance, optional other.")
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
                measurements = measure(doc, check["object"], revision, check.get("other"))
                if metric not in measurements or metric in {"document", "revision", "provenance", "note"}:
                    raise ValueError("Unsupported measurement metric.")
                measured = measurements[metric]
                claim["provenance"] = measurements["provenance"]
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
# Attachment, visibility, and derived bookkeeping are not design parameters.
_SKIP_PROPERTIES = {
    "Visibility", "Label2", "ExpressionEngine", "MapMode", "MapPathParameter", "MapReversed",
    "AttacherEngine", "AttacherType", "AttachmentSupport", "AttachmentOffset",
    "FullyConstrained", "Support", "Profile", "BaseFeature", "Tip", "Shape", "Placement",
}
_SKIP_TARGET_TYPES = {"App::Origin", "App::Plane", "App::Line", "App::Point"}


def parameter_targets(doc):
    """Objects a parameters panel can inspect, in document order."""
    return [{"name": obj.Name, "label": obj.Label, "type": obj.TypeId}
            for obj in doc.Objects if obj.TypeId not in _SKIP_TARGET_TYPES]


def _expressions(obj):
    result = {}
    for path, expression in (getattr(obj, "ExpressionEngine", None) or []):
        result[str(path)] = str(expression)
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
                current = constraint.Value
            except Exception:
                continue
            if current is None:
                continue
            label = str(getattr(constraint, "Name", "") or f"{constraint.Type}{index + 1}")
            entries.append({"id": f"con:{index}", "group": "Constraints", "label": label,
                            "kind": "number", "value": float(current),
                            "unit": "deg" if constraint.Type == "Angle" else "mm",
                            "expression": None})
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
    placement = obj.getGlobalPlacement()
    return {"object": obj.Name, "lengthUnit": "mm", "angleUnit": "degrees",
            "worldFromLocal": value(placement), "localFromWorld": value(placement.inverse()),
            "origin_world_mm": value(placement.Base),
            "axes_world": {name: value(placement.Rotation.multVec(axis)) for name, axis in
                           (("x", App.Vector(1, 0, 0)), ("y", App.Vector(0, 1, 0)), ("z", App.Vector(0, 0, 1)))},
            "pointConversion": f"doc.getObject({obj.Name!r}).getGlobalPlacement().multVec(App.Vector(x, y, z))",
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
    for name in obj.PropertiesList:
        if name.startswith("_") or name in _SKIP_PROPERTIES:
            continue
        try:
            if obj.getTypeIdOfProperty(name) not in _EDITABLE_NUMBERS:
                continue
            current = getattr(obj, name)
        except Exception:
            continue
        if isinstance(current, (int, float)) and not isinstance(current, bool):
            values.append((name, float(current)))
    return values


def _single_circle_sketch(profile):
    try:
        if not profile.isDerivedFrom("Sketcher::SketchObject"):
            return False
        geometry = profile.Geometry
        return len(geometry) == 1 and type(geometry[0]).__name__ == "Circle"
    except Exception:
        return False


def editability(doc):
    """Audit how parametric the native feature tree is: the E half of a G/E verdict.

    Flags the patterns that quietly turn a design into a frozen copy: opaque solids,
    booleans standing in for native operations, repeated features with no pattern,
    literal dimensions with no expressions, pockets that should be holes, and sketches
    held by blocks instead of dimensions. Heuristic: it flags likely problems, not intent.
    """
    findings = []
    objects = list(doc.Objects)

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
            names = [o.Name for o in group if o.TypeId == type_id]
            if len(names) >= 2:
                findings.append({"code": "pattern-as-copies", "severity": "warning", "objects": names,
                                 "message": f"{len(names)} {type_id.split('::')[-1]} features repeat with no pattern; use a native pattern feature."})

    expression_users = [o for o in objects if getattr(o, "ExpressionEngine", None)]
    dimensioned = [o for o in objects if _numeric_dimensions(o)]
    if dimensioned and not expression_users:
        findings.append({"code": "no-expressions", "severity": "info",
                         "objects": [o.Name for o in dimensioned][:20],
                         "message": "No expressions are used; bind related dimensions so one change propagates."})
    seen = {}
    for obj in dimensioned:
        for _, current in _numeric_dimensions(obj):
            if current in (0.0, 1.0):
                continue
            seen.setdefault(current, set()).add(obj.Name)
    duplicates = [{"value": value, "objects": sorted(names)}
                  for value, names in seen.items() if len(names) >= 2]
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
            "scope": "Heuristic editability audit; flags likely non-parametric patterns, not design intent."}


def _default_export_objects(doc):
    """Body tips plus standalone solids; never the Body wrapper and tip together."""
    bodies = [obj for obj in doc.Objects if obj.isDerivedFrom("PartDesign::Body")]
    grouped = {obj.Name for body in bodies for obj in body.Group}
    targets = [body.Tip.Name for body in bodies if body.Tip is not None]
    targets += [obj.Name for obj in doc.Objects
                if hasattr(obj, "Shape") and not obj.Shape.isNull() and obj.Shape.Solids
                and obj.Name not in grouped and not obj.isDerivedFrom("PartDesign::Body")]
    return targets


def export(doc, path, objects=None, format=None):
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
    if _EXPORT_FORMATS[extension] == "Mesh":
        import Mesh
        Mesh.export(resolved, path)
    else:
        Part.export(resolved, path)
    return {"path": os.path.abspath(path), "format": extension, "objects": [o.Name for o in resolved]}


def compare(doc, reference, target, revision):
    """Compare a target solid against a reference file, allowing translation and rotation only.

    Uses rotation-invariant facts, so a scaled or grossly different part is caught by the
    volume/area/size ratios. Full alignment and mirror detection are not implemented yet;
    handedness is reported as not evaluated rather than guessed.
    """
    if Part is None:
        raise ValueError("Part is unavailable; cannot compare geometry.")
    if not reference or not os.path.exists(reference):
        raise ValueError(f"Reference file not found: {reference}")
    reference_shape = Part.read(reference)
    if reference_shape.isNull():
        raise ValueError("The reference file has no shape.")
    target_shape = native_shape(doc, target, revision)
    if target_shape.isNull():
        raise ValueError("The target has no shape.")

    def facts(shape):
        box = shape.BoundBox
        return {"volume_mm3": round(shape.Volume, 6), "area_mm2": round(shape.Area, 6),
                "size_mm": sorted(round(value, 6) for value in (box.XLength, box.YLength, box.ZLength)),
                "solids": len(shape.Solids)}

    want, got = facts(reference_shape), facts(target_shape)
    volume_ratio = got["volume_mm3"] / want["volume_mm3"] if want["volume_mm3"] else None
    area_ratio = got["area_mm2"] / want["area_mm2"] if want["area_mm2"] else None
    size_delta = [round(g - w, 6) for g, w in zip(got["size_mm"], want["size_mm"])]
    linear_scale = round(volume_ratio ** (1.0 / 3.0), 6) if volume_ratio and volume_ratio > 0 else None
    scale_matches = bool(volume_ratio and area_ratio
                         and abs(volume_ratio - 1.0) <= 0.01 and abs(area_ratio - 1.0) <= 0.01)
    span = max(1.0, sum(want["size_mm"]))
    deviation = 0.0 if volume_ratio is None else abs(volume_ratio - 1.0) + sum(abs(d) for d in size_delta) / span
    return {"reference": os.path.abspath(reference), "referenceFacts": want, "target": got,
            "volumeRatio": round(volume_ratio, 6) if volume_ratio else None,
            "areaRatio": round(area_ratio, 6) if area_ratio else None,
            "linearScale": linear_scale, "sizeDelta_mm": size_delta, "scaleMatches": scale_matches,
            "gScore": round(max(0.0, 1.0 - deviation), 3),
            "handedness": "not evaluated: full alignment is not implemented",
            "scope": "Translation/rotation-invariant facts only (volume, area, sorted bounding size)."}


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
