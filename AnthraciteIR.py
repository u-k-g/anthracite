# SPDX-License-Identifier: LGPL-2.1-or-later
"""Read-only native feature projection; FreeCAD remains authoritative."""
import hashlib
import io
import json
import math
import re
import xml.etree.ElementTree as ET
import zipfile

import FreeCAD as App


_IGNORED = {"Label", "Label2", "Visibility", "ShapeMaterial", "_ElementMapVersion",
            "AttacherEngine"}  # UI shadow; the authoritative AttacherType remains.
_OPAQUE_TYPES = {"Part::Feature", "PartDesign::Feature"}
_STRING_OR_REFERENCE = re.compile(
    r"""(?P<string>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')|(?P<label><<[^>]+>>)\.|(?P<name>\b[A-Za-z_][A-Za-z_0-9]*)\.""")


_EXPRESSION_TOKENS = re.compile(
    r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\$[a-f0-9]{64}|\$self|'''
    r'(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?|[^\W\d]\w*|\*\*|==|!=|<=|>=|\S')


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _hash(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _number(value, precision=12):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("IR cannot represent a non-finite number")
    if precision is not None:
        number = round(number, precision)
    return 0.0 if number == 0 else number


def _quaternion(rotation):
    quaternion = [_number(number) for number in rotation.Q]
    # q and -q describe the same rotation. Prefer positive w, then x/y/z.
    first = next((quaternion[index] for index in (3, 0, 1, 2)
                  if quaternion[index] != 0), 0)
    if first < 0:
        quaternion = [-number for number in quaternion]
    # FreeCAD normalizes rotations on restore, which can move a component one
    # ulp. This declared precision makes those persistence artifacts stable.
    return [_number(number) for number in quaternion]


def _native_xml(obj, name, *, precision=12):
    """Native persistence normalized at declared precision, excluding cache identities."""
    with zipfile.ZipFile(io.BytesIO(obj.dumpPropertyContent(name, 0))) as archive:
        blobs = {member: hashlib.sha256(archive.read(member)).hexdigest()
                 for member in archive.namelist() if member != "Persistence.xml"}

        def attribute(value):
            if value in blobs:
                return {"blob": blobs[value]}
            if re.fullmatch(r"[-+]?\d+", value):
                return int(value)
            if re.fullmatch(r"[-+]?(?:\d+\.\d*|\.\d+)(?:[eE][-+]?\d+)?", value):
                return _number(value, precision)
            return value

        def element(node):
            # These sketch constraint attributes only place/show dimension labels.
            excluded = {"LabelDistance", "LabelPosition", "IsVisible"} if node.tag == "Constrain" else set()
            # The resolved ExternalGeometry links already encode these dependencies.
            # Cached projection Ref strings contain document-local element-map hashes
            # that FreeCAD rewrites while copying/saving, without changing geometry.
            if name == "ExternalGeo" and obj.isDerivedFrom("Sketcher::SketchObject"):
                if node.tag == "Geometry":
                    excluded = excluded | {"ref", "refIndex"}
                if node.tag == "GeoExtension" and node.attrib.get("type") == "Sketcher::ExternalGeometryExtension":
                    # Older projections omit the cache index; copying writes it
                    # explicitly. ExternalGeometry's ordered resolved links encode
                    # the dependencies. Preserve projection order, flags and geometry.
                    excluded = excluded | {"Ref", "RefIndex"}
            return {"tag": node.tag,
                    "attributes": {key: attribute(value) for key, value in node.attrib.items()
                                   if key not in excluded},
                    "text": node.text.strip() if node.text and node.text.strip() else None,
                    "children": [element(child) for child in node]}

        return {"nativeXml": element(ET.fromstring(archive.read("Persistence.xml"))),
                "blobs": sorted(blobs.values())}


def compile_document(doc, target=None):
    """Build a Merkle projection of native feature inputs, never mutate/recompute.

    This conservative identity is not proof of geometric equivalence. Unknown
    Python behavior and external references fail rather than receiving a hash.
    """
    if doc is None:
        raise ValueError("Open a FreeCAD document before compiling IR")
    if target is not None and not isinstance(target, str):
        raise ValueError("IR target must be a native object name")
    roots = list(doc.Objects) if target is None else [doc.getObject(target)]
    if target is None:
        # Verification metadata is not a model input. Its native links exist only
        # to preserve requirement targets across renames/save/reopen.
        roots = [obj for obj in roots if not (obj.TypeId == "App::DocumentObjectGroup"
                 and not obj.Group and "AnthraciteChecks" in obj.PropertiesList)]
    if any(obj is None for obj in roots):
        raise ValueError(f"No object named {target!r} in the active document")
    nodes, sources, hashes = {}, {}, {}
    visiting = set()
    consumed = set()

    def reference(obj):
        consumed.add(obj.Name)
        return {"node": visit(obj)}

    def value(item):
        if item is None or isinstance(item, (bool, str, int)):
            return item
        if isinstance(item, float):
            return _number(item)
        if hasattr(item, "Document") and hasattr(item, "Name"):
            return reference(item)
        if isinstance(item, App.Units.Quantity):
            dimensions = list(item.Unit.Signature)
            # FreeCAD's internal angular base is degrees; expose SI radians.
            number = _number(item.Value * (math.pi / 180) ** dimensions[7])
            return {"quantity": number, "dimensions": dimensions}
        if isinstance(item, App.Vector):
            return {"vector": [_number(item.x), _number(item.y), _number(item.z)]}
        if isinstance(item, App.Placement):
            return {"position": value(item.Base), "quaternion": _quaternion(item.Rotation)}
        if isinstance(item, App.Rotation):
            return {"quaternion": _quaternion(item)}
        if isinstance(item, (list, tuple)):
            return [value(entry) for entry in item]
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("IR dictionary keys must be strings")
            return {key: value(entry) for key, entry in item.items()}
        raise TypeError(f"No native IR value encoder for {type(item).__name__}")

    def expressions(obj):
        result = {}
        for path, expression in obj.ExpressionEngine:
            expression = str(expression)
            # Cross-document expression syntax is not a local Merkle dependency.
            without_strings = _STRING_OR_REFERENCE.sub(
                lambda match: "" if match.group("string") else match.group(0), expression)
            if "#" in without_strings:
                raise ValueError("IR does not support cross-document expressions")

            def replace(match):
                if match.group("string"):
                    return match.group(0)
                if match.group("label"):
                    label = match.group("label")[2:-2]
                    matches = [candidate for candidate in doc.Objects if candidate.Label == label]
                    if len(matches) != 1:
                        raise ValueError(f"Ambiguous expression label {label!r}")
                    dependency = matches[0]
                else:
                    dependency = doc.getObject(match.group("name"))
                    if dependency is None or (dependency is not obj and dependency not in obj.OutList):
                        # Unqualified local property paths, e.g. Placement.Base.x.
                        return match.group(0)
                if dependency is obj:
                    return "$self."
                consumed.add(dependency.Name)
                return "$" + visit(dependency) + "."
            normalized = _STRING_OR_REFERENCE.sub(replace, expression)
            result[str(path)] = " ".join(_EXPRESSION_TOKENS.findall(normalized))
        return result

    def visit(obj):
        if obj.Document is not doc:
            raise ValueError("IR does not support cross-document links")
        if obj.Name in visiting:
            raise ValueError("IR dependency graph contains a cycle")
        if obj.Name in hashes:
            return hashes[obj.Name]
        if "Python" in obj.TypeId or "Proxy" in obj.PropertiesList:
            raise ValueError(f"IR cannot capture arbitrary Python behavior on {obj.Name}")
        if "Touched" in obj.State or not obj.isValid():
            raise ValueError(f"Recompute and validate {obj.Name} before compiling IR")
        visiting.add(obj.Name)
        properties = {}
        for name in sorted(obj.PropertiesList):
            if name in _IGNORED or name == "ExpressionEngine":
                continue
            kind = obj.getTypeIdOfProperty(name)
            status = set(obj.getPropertyStatus(name))
            if kind == "Part::PropertyPartShape":
                if obj.TypeId in _OPAQUE_TYPES and name == "Shape":
                    shape = getattr(obj, name)
                    properties[name] = {"type": kind, "opaqueBrepHash":
                                        hashlib.sha256(shape.exportBrepToString().encode("utf-8")).hexdigest()}
                continue
            # Editor ReadOnly/runtime Output flags can be set on real inputs;
            # only static native output flags classify computed properties.
            if status.intersection({"Transient", "NoPersist", 4, 22, 25, 27}):
                continue
            if kind == "App::PropertyPythonObject":
                raise ValueError(f"IR cannot capture Python property {obj.Name}.{name}")
            if kind in {"App::PropertyFile", "App::PropertyFileList", "App::PropertyFileIncluded"} and getattr(obj, name):
                raise ValueError(f"IR cannot capture external file input {obj.Name}.{name}")
            try:
                encoded = value(getattr(obj, name))
            except TypeError:
                # Native binary/XML serializers cover sketches and constraints
                # without truncation or wrapper repr/address dependence.
                encoded = _native_xml(obj, name)
            properties[name] = {"type": kind, "value": encoded}
        payload = {"type": obj.TypeId, "properties": properties, "expressions": expressions(obj)}
        # Include the native dependency graph as well as property link roles.
        # This covers dependencies discovered by FreeCAD's expression engine.
        payload["dependencies"] = sorted({reference(dependency)["node"]
                                          for dependency in obj.OutList if dependency is not obj})
        if hasattr(obj, "getGlobalPlacement"):
            payload["worldPlacement"] = value(obj.getGlobalPlacement())
        digest = _hash(payload)
        nodes[digest] = payload
        hashes[obj.Name] = digest
        sources.setdefault(digest, []).append(obj.Name)
        visiting.remove(obj.Name)
        return digest

    for obj in roots:
        visit(obj)
    output_hashes = ([hashes[obj.Name] for obj in roots if obj.Name not in consumed]
                     if target is None else [hashes[target]])
    definition = {"format": "anthracite.feature-ir", "version": 1,
                  "normalization": {"rotationDecimalPlaces": 12, "numberDecimalPlaces": 12,
                                    "externalProjectionReferences": "resolved native links"},
                  "units": ["mm", "kg", "s", "A", "K", "mol", "cd", "rad"],
                  "roots": sorted(output_hashes), "nodes": nodes,
                  "instances": sorted(hashes.values())}
    encoded = canonical_bytes(definition)
    return {"definition": definition, "canonical": encoded.decode("utf-8"),
            "definitionHash": hashlib.sha256(encoded).hexdigest(),
            "source": {"document": doc.Name, "objects": sources,
                       "scope": "document" if target is None else "target"},
            "identity": "native input content; not a geometric-equivalence proof",
            "evaluation": "FreeCAD; no independent evaluator"}
