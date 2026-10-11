# SPDX-License-Identifier: LGPL-2.1-or-later
"""Small conveniences over native properties, sketches and PartDesign features.

The document owns values and expressions. Declarations are binding metadata only.
"""
import json
import math
import re

import FreeCAD as App
import AnthraciteInspect as Inspect

_PROPERTY = "AnthraciteParameters"


def _declarations(obj):
    if _PROPERTY not in obj.PropertiesList:
        return {}
    if obj.getTypeIdOfProperty(_PROPERTY) != "App::PropertyString":
        raise ValueError(f"{obj.Name}.{_PROPERTY} must be a string property")
    data = json.loads(getattr(obj, _PROPERTY) or "{}")
    if not isinstance(data, dict):
        raise ValueError(f"Invalid parameter declarations on {obj.Name}")
    return data


def _bindings(doc):
    result = {}
    for obj in doc.Objects:
        for name, spec in _declarations(obj).items():
            if name in result:
                raise ValueError(f"Parameter name {name!r} is declared more than once")
            result[name] = (obj, spec)
    return result


def _binding(obj, spec):
    path = spec["path"]
    if spec["kind"] == "constraint":
        matches = [i for i, c in enumerate(obj.Constraints) if c.Name == path]
        if len(matches) != 1:
            raise ValueError(f"Named dimension {obj.Name}.{path} is missing or ambiguous")
        index = matches[0]
        c = obj.Constraints[index]
        if not c.Driving or c.Type not in Inspect._DIMENSION_CONSTRAINTS:
            raise ValueError(f"{obj.Name}.{path} is not a driving dimension")
        unit = "deg" if c.Type == "Angle" else "mm"
        expression_path = f"Constraints.{path}"
        expressions = Inspect._expressions(obj)
        expression = expressions.get(expression_path, expressions.get(f"Constraints[{index}]"))
        return index, float(obj.getDatum(index).Value), unit, expression, False
    if spec["kind"] != "property" or path not in obj.PropertiesList:
        raise ValueError(f"Native property {obj.Name}.{path} is missing")
    kind = obj.getTypeIdOfProperty(path)
    if kind not in Inspect._EDITABLE_NUMBERS:
        raise ValueError(f"{obj.Name}.{path} is not a supported numeric property")
    status = {str(s).casefold() for s in obj.getPropertyStatus(path)}
    if status.intersection({"readonly", "immutable", "output"}):
        raise ValueError(f"{obj.Name}.{path} is not an editable input")
    current = getattr(obj, path)
    if isinstance(current, tuple):
        current = current[0]
    current = float(current.Value if hasattr(current, "Value") else current)
    return path, current, Inspect._EDITABLE_NUMBERS[kind], Inspect._expressions(obj).get(path), kind in {
        "App::PropertyInteger", "App::PropertyIntegerConstraint"}


def _quantity(value, unit, native_unit):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("Parameter values must be numbers or quantity strings")
    quantity = App.Units.Quantity(value if isinstance(value, str) else f"{value} {unit}")
    expected = App.Units.Quantity(f"1 {native_unit or ''}")
    if quantity.Unit.Signature != expected.Unit.Signature:
        raise ValueError(f"Expected units compatible with {native_unit or 'a dimensionless number'}")
    if not math.isfinite(quantity.Value):
        raise ValueError("Parameter values must be finite")
    return float(quantity.Value)


def surface(doc):
    entries = []
    for name, (obj, spec) in sorted(_bindings(doc).items()):
        _, current, native_unit, expression, integer = _binding(obj, spec)
        scale = _quantity(1, spec["unit"], native_unit)
        limits = Inspect.native_bounds(obj, spec["path"]) if spec["kind"] == "property" else [None, None]
        entries.append({"name": name, "object": obj.Name, "kind": spec["kind"],
                        "path": spec["path"], "unit": spec["unit"], "value": current / scale,
                        "bounds": spec["bounds"], "expression": expression,
                        "independent": not bool(expression), "integer": integer,
                        "nativeBounds": [None if v is None else v / scale for v in limits]})
    return {"document": doc.Name, "entries": entries,
            "authority": "Native properties and ExpressionEngine; declarations store bindings only."}


def validate(doc):
    for entry in surface(doc)["entries"]:
        lo, hi = entry["bounds"]
        value = entry["value"]
        if not math.isfinite(value) or (lo is not None and value < lo) or (hi is not None and value > hi):
            raise ValueError(f"Recomputed parameter {entry['name']} is outside {entry['bounds']} {entry['unit']}")


def declare(doc, name, target, *, property=None, constraint=None, unit=None, bounds=None):
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError("Parameter name must be a Python-style identifier")
    if (property is None) == (constraint is None):
        raise ValueError("Specify exactly one native property or named sketch constraint")
    obj = Inspect.resolve(doc, target)
    spec = {"kind": "property" if property is not None else "constraint",
            "path": property if property is not None else constraint}
    _, current, native_unit, _, integer = _binding(obj, spec)
    spec["unit"] = native_unit if unit is None else unit
    if not isinstance(spec["unit"], str):
        raise ValueError("unit must be a string")
    scale = _quantity(1, spec["unit"], native_unit)
    if integer and scale != 1:
        raise ValueError("Integer parameters use native units")
    if bounds is None:
        bounds = [None, None]
    if not isinstance(bounds, (tuple, list)) or len(bounds) != 2:
        raise ValueError("bounds must be [minimum, maximum]; either may be None")
    normalized = [None if v is None else _quantity(v, spec["unit"], native_unit) / scale for v in bounds]
    lo, hi = normalized
    if lo is not None and hi is not None and lo > hi:
        raise ValueError("Minimum bound exceeds maximum")
    if spec["kind"] == "property":
        lower, upper = Inspect.native_bounds(obj, spec["path"])
        if ((lo is not None and lower is not None and lo * scale < lower)
                or (hi is not None and upper is not None and hi * scale > upper)):
            hint = "; use App::PropertyDistance for signed offsets" if obj.getTypeIdOfProperty(spec["path"]) == "App::PropertyLength" else ""
            raise ValueError(f"Declared bounds exceed native bounds {lower, upper} {native_unit}{hint}")
    if (lo is not None and current / scale < lo) or (hi is not None and current / scale > hi):
        raise ValueError("Current native value is outside the declared bounds")
    spec["bounds"] = normalized
    existing = _bindings(doc).get(name)
    if existing and (existing[0] is not obj or existing[1]["path"] != spec["path"] or existing[1]["kind"] != spec["kind"]):
        raise ValueError(f"Parameter {name!r} already binds another input")
    data = _declarations(obj)
    data[name] = spec
    if _PROPERTY not in obj.PropertiesList:
        obj.addProperty("App::PropertyString", _PROPERTY, "Anthracite", "Named native parameter bindings, units and bounds")
    setattr(obj, _PROPERTY, json.dumps(data, sort_keys=True, separators=(",", ":")))
    return next(e for e in surface(doc)["entries"] if e["name"] == name)


def set_parameters(doc, values):
    if not isinstance(values, dict) or not values:
        raise ValueError("Supply a nonempty mapping of declared parameter names to values")
    bindings = _bindings(doc)
    edits = []
    for name, value in values.items():
        if name not in bindings:
            raise ValueError(f"Unknown parameter {name!r}; inspect cad.parameters()")
        obj, spec = bindings[name]
        path, current, native_unit, expression, integer = _binding(obj, spec)
        if expression:
            raise ValueError(f"{name} is derived by {expression!r}; edit its independent inputs")
        number = _quantity(value, spec["unit"], native_unit)
        scale = _quantity(1, spec["unit"], native_unit)
        lo, hi = spec["bounds"]
        if (lo is not None and number / scale < lo) or (hi is not None and number / scale > hi):
            raise ValueError(f"{name} must be within {spec['bounds']} {spec['unit']}")
        if integer and number != int(number):
            raise ValueError(f"{name} requires an integer")
        if spec["kind"] == "property":
            lower, upper = Inspect.native_bounds(obj, spec["path"])
            if (lower is not None and number < lower) or (upper is not None and number > upper):
                raise ValueError(f"{name} exceeds native bounds {lower, upper} {native_unit}; use App::PropertyDistance for signed offsets when appropriate")
        # Aliases may bind the same input; reject conflicting assignments.
        key = (obj.Name, spec["kind"], path)
        if any(key == old[0] and number != old[4] for old in edits):
            raise ValueError(f"Conflicting aliases for {obj.Name}.{spec['path']}")
        edits.append((key, obj, path, spec, number, native_unit, integer))
    # Preflight every edit before changing any native value. Recompute and validation
    # belong to the existing executor's one transaction, never a nested transaction.
    for _, obj, path, spec, number, native_unit, integer in edits:
        if spec["kind"] == "constraint":
            obj.setDatum(path, App.Units.Quantity(f"{number} {native_unit}"))
        else:
            setattr(obj, path, int(number) if integer else number)
        actual = _binding(obj, spec)[1]
        if not math.isclose(actual, number, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError(f"Native input {obj.Name}.{spec['path']} did not accept {number} {native_unit}")
    return {"assigned": list(values), "recompute": "At the end of the checked action"}


def circle_sketch(doc, body, name, diameter, *, placement=None):
    import Part
    import Sketcher
    body = Inspect.resolve(doc, body)
    if not body.isDerivedFrom("PartDesign::Body"):
        raise ValueError("A circle sketch needs a native PartDesign Body")
    diameter = _quantity(diameter, "mm", "mm")
    if diameter <= 0:
        raise ValueError("Diameter must be positive")
    if doc.getObject(name):
        raise ValueError(f"Object {name!r} already exists")
    sketch = body.newObject("Sketcher::SketchObject", name)
    if placement is not None:
        sketch.Placement = placement
    sketch.addGeometry(Part.Circle(App.Vector(), App.Vector(0, 0, 1), diameter / 2), False)
    sketch.addConstraint(Sketcher.Constraint("Coincident", 0, 3, -1, 1))
    dimension = sketch.addConstraint(Sketcher.Constraint("Diameter", 0, diameter))
    sketch.renameConstraint(dimension, "Diameter")
    return sketch


def rectangle_sketch(doc, body, name, width, height, *, placement=None):
    import Part
    import Sketcher
    body = Inspect.resolve(doc, body)
    if not body.isDerivedFrom("PartDesign::Body"):
        raise ValueError("A rectangle sketch needs a native PartDesign Body")
    width, height = _quantity(width, "mm", "mm"), _quantity(height, "mm", "mm")
    if min(width, height) <= 0:
        raise ValueError("Width and height must be positive")
    if doc.getObject(name):
        raise ValueError(f"Object {name!r} already exists")
    sketch = body.newObject("Sketcher::SketchObject", name)
    if placement is not None:
        sketch.Placement = placement
    points = [App.Vector(0, 0, 0), App.Vector(width, 0, 0),
              App.Vector(width, height, 0), App.Vector(0, height, 0)]
    sketch.addGeometry([Part.LineSegment(points[i], points[(i+1) % 4]) for i in range(4)], False)
    sketch.addConstraint([Sketcher.Constraint("Coincident", i, 2, (i+1) % 4, 1) for i in range(4)])
    sketch.addConstraint([Sketcher.Constraint("Horizontal" if i % 2 == 0 else "Vertical", i) for i in range(4)])
    sketch.addConstraint(Sketcher.Constraint("Coincident", 0, 1, -1, 1))
    for name, line, size in (("Width", 0, width), ("Height", 1, height)):
        index = sketch.addConstraint(Sketcher.Constraint("Distance", line, size))
        sketch.renameConstraint(index, name)
    return sketch


def extrude(doc, sketch, name, length, *, pocket=False):
    sketch = Inspect.resolve(doc, sketch)
    if not sketch.isDerivedFrom("Sketcher::SketchObject"):
        raise ValueError("Profile must be a native Sketcher sketch")
    body = sketch.getParentGeoFeatureGroup()
    if body is None or not body.isDerivedFrom("PartDesign::Body"):
        raise ValueError("Profile must belong to a PartDesign Body")
    if doc.getObject(name):
        raise ValueError(f"Object {name!r} already exists")
    length = _quantity(length, "mm", "mm")
    if length <= 0:
        raise ValueError("Length must be positive")
    # Native profile/feature creation requires a solved profile now; the executor
    # still performs its final recompute and validation before committing.
    doc.recompute()
    feature = body.newObject("PartDesign::Pocket" if pocket else "PartDesign::Pad", name)
    feature.Profile = sketch
    feature.Length = length
    if getattr(sketch, "ViewObject", None) is not None:
        sketch.Visibility = False
    return feature


def select_geometry(doc, target, revision, *, kind="faces", surface=None, where=None,
                    tolerance=1e-6, unique=False, offset=0, limit=25):
    """Match measured native facts; never resolve ambiguity by list position."""
    if not isinstance(where, (dict, type(None))):
        raise ValueError("where must map measured fact names to required values")
    allowed = {"normal", "axis", "direction", "centerOfMass_mm", "axisPosition",
               "radius_mm", "area_mm2", "length_mm", "majorRadius_mm", "minorRadius_mm"}
    where = where or {}
    if set(where) - allowed:
        raise ValueError(f"Unknown geometry facts: {sorted(set(where) - allowed)}")
    if not isinstance(unique, bool):
        raise ValueError("unique must be a boolean")
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("tolerance must be finite and non-negative")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("Use non-negative offset and limit 1..100")
    for key, wanted in where.items():
        vector = key in {"normal", "axis", "direction", "centerOfMass_mm", "axisPosition"}
        numbers = wanted if vector else [wanted]
        if (vector and (not isinstance(wanted, (list, tuple)) or len(wanted) != 3)) or any(
                isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in numbers):
            raise ValueError(f"Invalid required value for {key}")
        if key in {"normal", "axis", "direction"} and abs(math.sqrt(sum(v*v for v in wanted)) - 1) > 1e-6:
            raise ValueError(f"{key} must be a unit vector")
    matches = []
    page_offset = 0
    while True:
        page = Inspect.topology(doc, target, revision, kind=kind, surface=surface, offset=page_offset, limit=100)
        for item in page["items"]:
            def match(key, wanted):
                actual = item.get(key)
                if actual is None:
                    return False
                if isinstance(wanted, (list, tuple)):
                    return math.dist(actual, wanted) <= tolerance
                return abs(actual - wanted) <= tolerance
            if all(match(key, wanted) for key, wanted in where.items()):
                matches.append(item)
        if page["nextOffset"] is None:
            break
        page_offset = page["nextOffset"]
    if unique and len(matches) != 1:
        raise ValueError(f"Expected one geometry match; found {len(matches)}. Refine where or inspect candidates.")
    return {"object": page["object"], "revision": revision, "items": matches[offset:offset + limit],
            "total": len(matches), "nextOffset": offset + limit if offset + limit < len(matches) else None,
            "coordinateFrame": page["coordinateFrame"],
            "note": "Signed analytic directions, not oriented face normals. References expire on edits."}
