# SPDX-License-Identifier: LGPL-2.1-or-later

"""Transactional execution surface exposed to coding agents inside FreeCAD."""

from __future__ import annotations

import ast
import base64
import contextlib
import hashlib
import io
import json
import math
import os
import tempfile
import traceback
import zipfile
import uuid
from typing import Any

import FreeCAD as App
import AnthraciteInspect as Inspect
import AnthraciteHistory as History

try:  # Geometry modules nearly every program needs; prebind them.
    import Part
except Exception:  # pragma: no cover - always present in a normal FreeCAD build
    Part = None

try:
    import Sketcher
except Exception:  # pragma: no cover
    Sketcher = None

try:
    import FreeCADGui as Gui
except ImportError:
    Gui = None


class PendingTransaction(Exception):
    """A native FreeCAD undo step is open; mutations cannot nest into it."""

    def __init__(self, pending, message=None):
        pending = pending or {"open": True, "name": None, "workbench": None}
        label = pending.get("name") or "unnamed"
        workbench = pending.get("workbench")
        extra = f", {workbench}" if workbench else ""
        default = (
            f"A native transaction is open ({label}{extra}). "
            "Mutations cannot nest into it. If the user asked you to proceed, "
            "cad.yield_transaction(mode='commit') keeps the GUI edit, or "
            "cad.yield_transaction(mode='abort') discards it; then mutate in a new call."
        )
        super().__init__(message or default)
        self.pending = pending


_MAX_TEXT = 12_000
_MAX_VALUE = 500
_MAX_RENDER_DIMENSION = 2_048
_MAX_RENDER_PIXELS = 4_194_304
_MAX_RENDER_BYTES = 8_000_000
_revisions: dict[str, int] = {}
_executing_docs: set[str] = set()
_last_changes = {}
_recompute_writes = {}
_observed_snapshots = {}
# True while a document-creating program runs, so cad.verify() schedules instead of
# resolving immediately (there is no document yet when execution starts).
_creation_in_progress = False


def _bump_revision(doc: Any) -> None:
    if doc is None:
        return
    name = str(doc.Name)
    if name not in _executing_docs:
        _last_changes.pop(name, None)
        _revisions[name] = _revisions.get(name, 0) + 1
        History.invalidate(doc)


def _bump_object_revision(obj: Any) -> None:
    _bump_revision(getattr(obj, "Document", None))


class _RevisionObserver:
    """Make an agent's expected revision stale after any non-agent CAD edit."""

    def slotCreatedObject(self, obj):
        _bump_object_revision(obj)

    def slotDeletedObject(self, obj):
        _bump_object_revision(obj)

    def slotChangedObject(self, obj, _property):
        if obj.Document.Name in _recompute_writes:
            _recompute_writes[obj.Document.Name].add((obj.Name, _property))
        _bump_object_revision(obj)

    def slotChangedDocument(self, doc, _property):
        _bump_revision(doc)

    def slotUndoDocument(self, doc):
        _bump_revision(doc)

    def slotRedoDocument(self, doc):
        _bump_revision(doc)

    def slotDeletedDocument(self, doc):
        Inspect.forget(doc)
        _last_changes.pop(str(doc.Name), None)
        _observed_snapshots.pop(str(doc.Name), None)
        _revisions.pop(str(doc.Name), None)
        History.forget(doc)


def _document_exists(name: str) -> bool:
    """Whether a document is open; App.getDocument() raises instead of returning None."""
    try:
        return name in App.listDocuments()
    except Exception:
        return False


def _open_documents():
    """Open documents as (name, document) pairs; FreeCAD returns a dict keyed by name."""
    documents = App.listDocuments()
    if isinstance(documents, dict):
        return list(documents.items())
    return [(document.Name, document) for document in documents]


def revision(document=None) -> int:
    """Current revision for a document (the active one by default)."""
    document = document if document is not None else App.ActiveDocument
    return _revisions.get(document.Name, 0) if document is not None else 0


def open_document_names() -> list[str]:
    """Names of open documents, for the bridge's live status cache."""
    return [name for name, _ in _open_documents()]


_revision_observer = _RevisionObserver()
App.addDocumentObserver(_revision_observer)


def _bounded(text: str, limit: int = _MAX_TEXT) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n… <truncated {len(text) - limit} characters>"


def _value_summary(value: Any) -> str:
    try:
        result = repr(value)
    except Exception as error:  # A broken property must not break observation.
        result = f"<unrepresentable: {error}>"
    return _bounded(result, _MAX_VALUE)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value[:100]]
    if isinstance(value, dict):
        return {
            str(key): _json_safe(item)
            for key, item in list(value.items())[:100]
        }
    return _value_summary(value)


def _topology_summary(shape: Any) -> dict[str, Any]:
    try:
        if shape.isNull():
            return {"null": True}
        bounds = shape.BoundBox
        return {
            "null": False,
            "shapeType": str(shape.ShapeType),
            "valid": bool(shape.isValid()),
            "solids": len(shape.Solids),
            "faces": len(shape.Faces),
            "edges": len(shape.Edges),
            "volume": float(shape.Volume),
            "area": float(shape.Area),
            "bounds": {
                "x": [float(bounds.XMin), float(bounds.XMax)],
                "y": [float(bounds.YMin), float(bounds.YMax)],
                "z": [float(bounds.ZMin), float(bounds.ZMax)],
            },
        }
    except Exception as error:
        return {"error": str(error)}


def _shape_summary(obj: Any) -> dict[str, Any] | None:
    if not hasattr(obj, "Shape"):
        return None
    return _topology_summary(obj.Shape)


def _selection_snapshot(doc: Any) -> list[dict[str, Any]]:
    if Gui is None:
        return []
    try:
        selections = Gui.Selection.getSelectionEx(str(doc.Name))
    except Exception:
        return []

    result = []
    for selection in selections:
        obj = getattr(selection, "Object", None)
        if obj is None:
            continue
        subelement_names = list(getattr(selection, "SubElementNames", []))
        subobjects = list(getattr(selection, "SubObjects", []))
        subelements = []
        for index, name in enumerate(subelement_names):
            subelements.append(
                {
                    "name": str(name),
                    "topology": (
                        _topology_summary(subobjects[index])
                        if index < len(subobjects)
                        else None
                    ),
                }
            )
        result.append(
            {
                "document": str(doc.Name),
                "object": str(obj.Name),
                "label": str(obj.Label),
                "type": str(obj.TypeId),
                "subelements": subelements,
            }
        )
    return result


def _active_workbench() -> str | None:
    if Gui is None:
        return None
    try:
        return str(Gui.activeWorkbench().name())
    except Exception:
        return None


def _object_snapshot(obj: Any) -> dict[str, Any]:
    properties = {}
    for name in obj.PropertiesList:
        try:
            # Native wrappers may have address-based repr(), and bounded repr
            # can hide changes. Compare FreeCAD's complete persisted contents.
            # Hash ZIP members, not the archive's timestamps/compression metadata.
            content = obj.dumpPropertyContent(name, 0)
            digest = hashlib.sha256()
            members = {}
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                for member in sorted(archive.namelist()):
                    data = archive.read(member)
                    members[member] = hashlib.sha256(data).hexdigest()
                    digest.update(member.encode("utf-8"))
                    digest.update(len(data).to_bytes(8, "big"))
                    digest.update(data)
            value = getattr(obj, name)
            display = Inspect.value(value)
            properties[name] = {"type": obj.getTypeIdOfProperty(name), "value": display,
                                "sha256": digest.hexdigest(), "members": members}
        except Exception as error:
            # Never confirm restoration when a property's content is unknown.
            raise RuntimeError(f"Cannot inspect {obj.Name}.{name}: {error}") from error

    state = []
    try:
        state = [str(item) for item in obj.State]
    except Exception:
        pass

    return {
        "type": str(obj.TypeId),
        "label": str(obj.Label),
        "state": state,
        "properties": properties,
        "shape": _shape_summary(obj),
    }


def _document_snapshot(doc: Any) -> dict[str, dict[str, Any]]:
    return {
        str(obj.Name): _object_snapshot(obj)
        for obj in sorted(doc.Objects, key=lambda item: item.Name)
    }


def _changed_properties(
    before: dict[str, Any], after: dict[str, Any]
) -> list[str]:
    old = before.get("properties", {})
    new = after.get("properties", {})
    return sorted(
        name
        for name in set(old) | set(new)
        if old.get(name) != new.get(name)
    )


def _observation(
    doc: Any,
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    before_names = set(before)
    after_names = set(after)
    changed = []
    for name in sorted(before_names & after_names):
        if before[name] == after[name]:
            continue
        changed.append(
            {
                "name": name,
                "label": after[name]["label"],
                "type": after[name]["type"],
                "properties": _changed_properties(before[name], after[name]),
                "parameterChanges": {
                    key: {"before": before[name]["properties"].get(key, {}).get("value"),
                          "after": after[name]["properties"].get(key, {}).get("value")}
                    for key in _changed_properties(before[name], after[name])
                    if key not in ("Shape", "ShapeMaterial")
                },
                "shape": after[name]["shape"],
                "state": after[name]["state"],
            }
        )

    created = []
    for name in sorted(after_names - before_names):
        item = after[name]
        created.append(
            {
                "name": name,
                "label": item["label"],
                "type": item["type"],
                "shape": item["shape"],
                "state": item["state"],
            }
        )

    editability = Inspect.diagnostics(doc)
    if any(item["type"] in ("Part::Feature", "PartDesign::Feature") for item in created):
        removed = [name for name in before_names - after_names
                   if before[name]["type"].startswith(("PartDesign::", "Sketcher::"))]
        if removed:
            editability["warnings"].append({"code": "feature-history-replaced", "severity": "warning",
                                           "objects": sorted(removed)[:100],
                                           "message": "Native features were removed while an opaque shape was created. Confirm that losing editability was intended."})
    return {
        "document": str(doc.Name),
        "workbench": _active_workbench(),
        "selection": _selection_snapshot(doc),
        "created": created,
        "deleted": sorted(before_names - after_names),
        "changed": changed,
        "objectCount": len(after),
        "tree": Inspect.tree(doc),
        "editability": editability,
    }


def _invalid_objects(doc: Any) -> list[dict[str, str]]:
    invalid = []
    for obj in doc.Objects:
        state = []
        try:
            state = [str(item) for item in obj.State]
        except Exception:
            pass
        errors = [item for item in state if item.casefold() in {"invalid", "error", "touched"}]
        try:
            if not obj.isValid():
                errors.append("Native object is invalid")
        except Exception as error:
            errors.append(f"Native validity unavailable: {error}")

        shape = _shape_summary(obj)
        if shape and shape.get("null") is False and shape.get("valid") is False:
            errors.append("Invalid shape")

        if errors:
            try:
                status = obj.getStatusString()
                if status:
                    errors.append(str(status))
            except Exception:
                pass
            invalid.append({"name": str(obj.Name), "error": ", ".join(errors)})
    return invalid


def _render_spec(
    width: int,
    height: int,
    view: str,
    fit: bool,
) -> dict[str, Any]:
    width = int(width)
    height = int(height)
    view = str(view).lower()
    supported_views = {
        "current",
        "axonometric",
        "front",
        "rear",
        "left",
        "right",
        "top",
        "bottom",
    }
    if width < 64 or height < 64:
        raise ValueError("Render dimensions must each be at least 64 pixels.")
    if width > _MAX_RENDER_DIMENSION or height > _MAX_RENDER_DIMENSION:
        raise ValueError(
            f"Render dimensions cannot exceed {_MAX_RENDER_DIMENSION} pixels."
        )
    if width * height > _MAX_RENDER_PIXELS:
        raise ValueError(
            f"Render area cannot exceed {_MAX_RENDER_PIXELS} pixels."
        )
    if view not in supported_views:
        raise ValueError(
            f"Unsupported render view {view!r}; expected one of "
            f"{', '.join(sorted(supported_views))}."
        )
    return {"width": width, "height": height, "view": view, "fit": bool(fit)}


def _render_view(spec: dict[str, Any]) -> dict[str, Any]:
    if "sketch" in spec:
        return _render_sketch(spec)
    if Gui is None or Gui.activeDocument() is None:
        raise RuntimeError("Viewport rendering requires the FreeCAD GUI.")

    active_view = Gui.activeDocument().activeView()
    view_method = {
        "axonometric": "viewAxonometric",
        "front": "viewFront",
        "rear": "viewRear",
        "left": "viewLeft",
        "right": "viewRight",
        "top": "viewTop",
        "bottom": "viewBottom",
    }.get(spec["view"])
    if view_method is not None:
        getattr(active_view, view_method)()
    if spec["fit"]:
        active_view.fitAll()

    descriptor, path = tempfile.mkstemp(prefix="anthracite-view-", suffix=".png")
    os.close(descriptor)
    try:
        with _visual_scene(active_view, spec):
            active_view.saveImage(path, spec["width"], spec["height"], "Current")
            if any(spec.get(key) for key in ("labels", "axes", "dimensions", "references")):
                spec = {**spec, "annotations": _annotate_view(path, active_view, spec)}
        with open(path, "rb") as image_file:
            image = image_file.read(_MAX_RENDER_BYTES + 1)
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass

    if not image:
        raise RuntimeError("FreeCAD did not produce a viewport image.")
    if len(image) > _MAX_RENDER_BYTES:
        raise RuntimeError(
            f"Viewport image exceeded the {_MAX_RENDER_BYTES}-byte limit."
        )
    return {
        **spec,
        "mimeType": "image/png",
        "dataUrl": "data:image/png;base64,"
        + base64.b64encode(image).decode("ascii"),
    }


def _annotate_view(path, view, spec):
    """Pixel overlays only: no document objects, camera edits or persistent styling."""
    from PySide6 import QtCore, QtGui
    from pivy import coin
    doc = App.ActiveDocument
    revision = spec.get("revision", _revisions.get(doc.Name, 0))
    width, height = spec["width"], spec["height"]
    image = QtGui.QImage(path)
    if image.isNull():
        raise RuntimeError("Cannot annotate an empty viewport image.")
    volume = view.getCameraNode().getViewVolume(width / height)
    def project(point):
        screen = volume.projectToScreen(coin.SbVec3f(point.x, point.y, point.z))
        return QtCore.QPointF(float(screen[0]) * width, (1 - float(screen[1])) * height)
    targets = []
    candidates = [obj for obj in doc.Objects if hasattr(obj, "Shape") and not obj.Shape.isNull()
                  and obj.ViewObject.Visibility and not obj.isDerivedFrom("PartDesign::Body")]
    if spec.get("focus"):
        candidates = [Inspect.resolve(doc, spec["focus"])]
    if spec.get("labels"):
        for obj in candidates[:12]:
            # Projection uses the same global placement as native measurements.
            try:
                shape = Inspect.native_shape(doc, obj, revision)
            except Exception:
                continue
            targets.append((obj.Name, shape.BoundBox.Center, {"document": doc.Name, "documentToken": Inspect.document_token(doc), "object": obj.Name, "revision": revision}))
    dropped = []
    for ref in spec.get("references", []):
        try:
            shape = Inspect.native_shape(doc, ref, revision)
        except Exception:
            # A stale annotation reference must not fail the whole render.
            dropped.append(ref)
            continue
        targets.append((f"{ref['object']}.{ref['subelement']}", shape.CenterOfMass, ref))
    painter = QtGui.QPainter(image)
    annotations = []
    try:
        painter.setFont(QtGui.QFont("Helvetica", 10))
        for index, (label, center, reference) in enumerate(targets):
            point = project(center)
            visible = 0 <= point.x() < width and 0 <= point.y() < height
            text = f"{index + 1}: {label}"
            y = 18 + index * 19
            painter.fillRect(4, y - 13, min(width - 8, len(text) * 8 + 12), 18, QtGui.QColor(255, 255, 255, 220))
            painter.setPen(QtGui.QColor("#111111"))
            painter.drawText(8, y, text)
            if visible:
                painter.setPen(QtGui.QColor("#e08000"))
                painter.drawLine(QtCore.QPointF(min(width - 8, len(text) * 8 + 12), y - 4), point)
                painter.drawEllipse(point, 3, 3)
            annotations.append({"label": text, "reference": reference,
                                "pixel": [point.x(), point.y()], "inFrame": visible,
                                "occlusion": "not tested; label points at geometric center, not a selectable pixel"})
        if spec.get("axes"):
            rotation = view.getCameraOrientation().inverted()
            origin = QtCore.QPointF(width - 50, height - 50)
            for axis, vector, color in [("X", App.Vector(1, 0, 0), "#e44"), ("Y", App.Vector(0, 1, 0), "#2b2"), ("Z", App.Vector(0, 0, 1), "#48f")]:
                direction = rotation.multVec(vector)
                end = origin + QtCore.QPointF(direction.x * 30, -direction.y * 30)
                painter.setPen(QtGui.QColor(color))
                painter.drawLine(origin, end)
                painter.drawText(end, axis)
        if spec.get("dimensions"):
            bounds = App.BoundBox()
            for obj in candidates:
                bounds.add(Inspect.native_shape(doc, obj, revision).BoundBox)
            if candidates:
                text = f"World-axis bounds (mm): X {bounds.XLength:.4g} × Y {bounds.YLength:.4g} × Z {bounds.ZLength:.4g}"
                painter.fillRect(0, height - 22, width, 22, QtGui.QColor("white"))
                painter.setPen(QtGui.QColor("black"))
                painter.drawText(6, height - 6, text)
    finally:
        painter.end()
    if not image.save(path, "PNG"):
        raise RuntimeError("Could not save annotated viewport image.")
    result = {"items": annotations, "omittedObjects": max(0, len(candidates) - 12) if spec.get("labels") else 0,
              "document": doc.Name, "revision": revision, "units": "mm"}
    if dropped:
        result["droppedReferences"] = dropped
        result["droppedNote"] = "Those annotation references were stale at this revision; re-inspect and retry them."
    return result


@contextlib.contextmanager
def _visual_scene(view, spec):
    """Temporary Coin nodes and camera framing; do not edit document/view properties."""
    from pivy import coin
    nodes = []
    try:
        if spec.get("focus"):
            obj = Inspect.resolve(App.ActiveDocument, spec["focus"])
            if not hasattr(obj, "Shape") or obj.Shape.isNull():
                raise ValueError("Focus requires an object with non-null native geometry.")
            bounds = Inspect.native_shape(App.ActiveDocument, obj, spec.get("revision", 0)).BoundBox
            center = bounds.Center
            extent = max(bounds.DiagonalLength, 0.01)
            camera = view.getCameraNode()
            direction = view.getCameraOrientation().multVec(App.Vector(0, 0, 1))
            distance = extent * 3
            position = center + direction * distance
            camera.position.setValue(position.x, position.y, position.z)
            camera.focalDistance.setValue(distance)
            camera.nearDistance.setValue(max(0.0001, distance - extent * 2))
            camera.farDistance.setValue(distance + extent * 2)
            if hasattr(camera, "height"):
                camera.height.setValue(extent * 1.2 * max(1, spec["height"] / spec["width"]))
        highlighted = spec.get("highlight", [])
        if highlighted:
            targets = [Inspect.resolve(App.ActiveDocument, name) for name in highlighted]
            # Include a Body's children, so its Tip is not independently ghosted.
            names = {obj.Name for obj in targets}
            for obj in targets:
                names.update(child.Name for child in getattr(obj, "Group", []))
            for obj in App.ActiveDocument.Objects:
                vp = getattr(obj, "ViewObject", None)
                if vp is None or not hasattr(vp, "RootNode") or not hasattr(obj, "Shape"):
                    continue
                material = coin.SoMaterial()
                material.setOverride(True)
                material.diffuseColor.setValue(1.0, 0.55, 0.1) if obj.Name in names else material.diffuseColor.setValue(0.7, 0.7, 0.7)
                material.transparency.setValue(0.0 if obj.Name in names else 0.85)
                vp.RootNode.insertChild(material, 0)
                nodes.append((vp.RootNode, material))
        if spec.get("section"):
            axis, offset = spec["section"]
            normal = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}[axis]
            clip = coin.SoClipPlane()
            clip.plane.setValue(coin.SbPlane(coin.SbVec3f(*normal), offset))
            root = view.getSceneGraph()
            root.insertChild(clip, 0)
            nodes.append((root, clip))
        yield
    finally:
        for root, node in reversed(nodes):
            root.removeChild(node)


def _render_sketch(spec):
    from PySide6 import QtCore, QtGui
    obj = Inspect.resolve(App.ActiveDocument, spec["sketch"])
    details = Inspect.sketch(obj, spec["constraintOffset"], 12)
    if obj.GeometryCount > 200:
        raise ValueError("Sketch diagram is limited to 200 geometry elements.")
    curves = []
    for index, geometry in enumerate(obj.Geometry):
        shape = geometry.toShape()
        points = shape.discretize(Number=48) if shape.Edges else [v.Point for v in shape.Vertexes]
        curves.append((index, points, obj.getConstruction(index)))
    points = [point for _, curve, _ in curves for point in curve]
    if not points:
        raise ValueError("Sketch has no drawable geometry.")
    width, height = spec["width"], spec["height"]
    xmin, xmax = min(p.x for p in points), max(p.x for p in points)
    ymin, ymax = min(p.y for p in points), max(p.y for p in points)
    scale = min((width * 0.58 - 40) / max(xmax - xmin, 0.01), (height - 80) / max(ymax - ymin, 0.01))
    def pixel(p):
        return QtCore.QPointF(20 + (p.x - xmin) * scale, height - 35 - (p.y - ymin) * scale)
    image = QtGui.QImage(width, height, QtGui.QImage.Format_ARGB32)
    image.fill(QtGui.QColor("white"))
    painter = QtGui.QPainter(image)
    try:
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtGui.QColor("black"))
        painter.drawText(12, 22, f"{obj.Name} — local XY; geometry indices; DoF={obj.DoF}")
        for index, curve, construction in curves:
            painter.setPen(QtGui.QPen(QtGui.QColor("#888888" if construction else "#116699"), 2))
            for start, end in zip(curve, curve[1:]):
                painter.drawLine(pixel(start), pixel(end))
            if len(curve) == 1:
                painter.drawEllipse(pixel(curve[0]), 3, 3)
            painter.drawText(pixel(curve[len(curve) // 2]), f"g{index}")
        painter.setPen(QtGui.QColor("black"))
        x = int(width * 0.6)
        painter.drawText(x, 45, "Native constraints (indices):")
        for row, constraint in enumerate(details["constraints"]):
            text = f"c{constraint['index']} {constraint['name'] or constraint['type']} g{constraint['First']}"
            painter.drawText(x, 65 + row * 22, text)
    finally:
        painter.end()
    buffer = QtCore.QBuffer()
    buffer.open(QtCore.QIODevice.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError("Could not encode sketch diagram.")
    return {**spec, "view": "sketch-local-XY", "constraints": details,
            "mimeType": "image/png", "dataUrl": "data:image/png;base64," + base64.b64encode(bytes(buffer.data())).decode("ascii")}


def _render_views(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not specs:
        return []
    if Gui is None or Gui.activeDocument() is None:
        raise RuntimeError("Viewport rendering requires the FreeCAD GUI.")
    active_view = Gui.activeDocument().activeView()
    camera = active_view.getCamera()
    try:
        images = []
        total_bytes = 0
        for spec in specs:
            active_view.setCamera(camera)
            image = _render_view(spec)
            total_bytes += len(image["dataUrl"])
            if total_bytes > _MAX_RENDER_BYTES:
                raise RuntimeError("Combined viewport images exceed the 8 MB payload limit.")
            images.append(image)
        return images
    finally:
        active_view.setCamera(camera)


def _visual_options(focus, highlight, section, labels=False, axes=False, dimensions=False, highlight_changed=False, references=()):
    options = {}
    if focus is not None:
        if not isinstance(focus, str):
            raise ValueError("focus must be an internal object name.")
        options["focus"] = focus
    if highlight:
        if not isinstance(highlight, (list, tuple)) or len(highlight) > 20 or not all(isinstance(n, str) for n in highlight):
            raise ValueError("highlight must contain at most 20 internal object names.")
        options["highlight"] = list(highlight)
    if section is not None:
        if (not isinstance(section, (list, tuple)) or len(section) != 2
                or section[0] not in ("x", "y", "z") or not math.isfinite(float(section[1]))):
            raise ValueError("section must be (axis, offset_mm), e.g. ('z', 5); keeps the positive half-space.")
        options["section"] = [section[0], float(section[1])]
    if not isinstance(references, (list, tuple)) or len(references) > 8 or not all(isinstance(ref, dict) for ref in references):
        raise ValueError("references must contain at most eight revision-bound topology references.")
    options.update({key: value for key, value in dict(labels=bool(labels), axes=bool(axes), dimensions=bool(dimensions),
                   highlightChanged=bool(highlight_changed), references=list(references)).items() if value})
    return options


def _compile(source: str):
    tree = ast.parse(source, filename="<anthracite-freecad>", mode="exec")
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        tree.body[-1] = ast.Assign(
            targets=[ast.Name(id="_result", ctx=ast.Store())],
            value=tree.body[-1].value,
        )
        ast.fix_missing_locations(tree)
    return compile(tree, "<anthracite-freecad>", "exec")


class Cad:
    """Small inspection helper; creation and editing stay ordinary FreeCAD Python."""

    def __init__(self, doc: Any):
        self._doc = doc
        self.events: list[dict[str, Any]] = []
        self.check_requests = []
        self.render_request: dict[str, Any] | None = None
        self.render_views_request: list[dict[str, Any]] = []
        self.reference_baseline = None
        self.automatic_images = True

    @property
    def doc(self):
        # Resolve the active document lazily so helpers work inside a call that creates
        # the document before using them.
        return self._doc if self._doc is not None else App.ActiveDocument

    def feedback(self, *, images=True):
        """Control automatic images for this call only; explicit renders still run."""
        if not isinstance(images, bool):
            raise TypeError("images must be True or False")
        self.automatic_images = images

    def _reference_revision(self):
        if self.reference_baseline is not None and _document_snapshot(self.doc) != self.reference_baseline:
            raise ValueError("Topology changed within this action; finish the action and inspect topology in a new call.")
        return _revisions.get(self.doc.Name, 0)

    def find(
        self,
        *,
        name: str | None = None,
        label: str | None = None,
        type_id: str | None = None,
    ) -> list[Any]:
        objects = self.doc.Objects
        if name is not None:
            objects = [obj for obj in objects if obj.Name == name]
        if label is not None:
            objects = [obj for obj in objects if obj.Label == label]
        if type_id is not None:
            objects = [obj for obj in objects if obj.TypeId == type_id]
        return list(objects)

    def inspect(self, obj: Any | None = None) -> Any:
        if obj is None:
            return Inspect.tree(self.doc)
        return Inspect.node(Inspect.resolve(self.doc, obj), detailed=True)

    def tree(self, root=None, offset=0, limit=40):
        return Inspect.tree(self.doc, root, offset, limit)

    def sketch(self, obj, offset=0, limit=40):
        return Inspect.sketch(Inspect.resolve(self.doc, obj), offset, limit)

    def api(self, obj=None, query="", offset=0, limit=40, kind="types"):
        return Inspect.api(self.doc, obj, query, offset, limit, kind)

    def guide(self, topic=None):
        return Inspect.guide(topic)

    def diagnostics(self, obj=None):
        return Inspect.diagnostics(self.doc, None if obj is None else [Inspect.resolve(self.doc, obj)])

    def explain(self, obj, offset=0, limit=20):
        return Inspect.explain(self.doc, obj, self._reference_revision(), offset, limit)

    def frame(self, obj):
        return Inspect.frame(self.doc, obj)

    def review(self, obj):
        """Explain an exact target and attach a focused view without editing geometry."""
        explanation = self.explain(obj)
        name = explanation["selectedObject"]
        target = Inspect.resolve(self.doc, name)
        if target.isDerivedFrom("Sketcher::SketchObject"):
            self.render_sketch(name)
        else:
            refs = [obj] if isinstance(obj, dict) else []
            self.render_views(["current", "axonometric"], width=640, height=480,
                              focus=name, highlight=[name], labels=True, axes=True, references=refs)
        return explanation

    def topology(self, obj, *, kind="faces", surface=None, offset=0, limit=25):
        return Inspect.topology(self.doc, obj, self._reference_revision(),
                                kind=kind, surface=surface, offset=offset, limit=limit)

    def resolve_ref(self, reference):
        return Inspect.native_shape(self.doc, reference, self._reference_revision())

    def measure(self, obj, other=None):
        if isinstance(obj, dict) or isinstance(other, dict):
            self._reference_revision()
        return Inspect.measure(self.doc, obj, _revisions.get(self.doc.Name, 0), other)

    def parameters(self, obj):
        """List editable design parameters (native properties and sketch dimensions)."""
        return Inspect.parameters(self.doc, obj)

    def documents(self):
        """List open documents, which one is active, and how many objects each holds.

        Works with no active document, so it answers "is my project even open?".
        """
        active = App.ActiveDocument
        entries = []
        for _name, document in _open_documents():
            try:
                entries.append({
                    "name": document.Name,
                    "label": document.Label,
                    "active": document is active,
                    "objects": len(document.Objects),
                    "revision": _revisions.get(document.Name, 0),
                    "fileName": document.FileName or None,
                })
            except Exception:
                continue
        return {"documents": entries, "active": active.Name if active is not None else None,
                "count": len(entries)}

    def editability(self):
        """Audit how parametric the native feature tree is (the E half of a G/E verdict)."""
        return Inspect.editability(self.doc)

    def export(self, path, objects=None, *, format=None):
        """Write geometry to a file: step, iges, brep, stl, or obj."""
        return Inspect.export(self.doc, path, objects, format)

    def compare(self, reference, target=None):
        """Compare a solid against a reference file, allowing translation and rotation only."""
        if target is None:
            target = self._default_solid()
        return Inspect.compare(self.doc, reference, target, _revisions.get(self.doc.Name, 0))

    def remap(self, reference, facts=None):
        """Find the face or edge at the current revision that best matches a stale reference."""
        return Inspect.remap(self.doc, reference, _revisions.get(self.doc.Name, 0), facts)

    def _default_solid(self):
        for obj in self.doc.Objects:
            if obj.isDerivedFrom("PartDesign::Body") and getattr(obj, "Tip", None) is not None:
                return obj.Tip.Name
        solids = [obj for obj in self.doc.Objects
                  if hasattr(obj, "Shape") and not obj.Shape.isNull() and obj.Shape.Solids]
        if not solids:
            raise ValueError("No solid to compare; name a target explicitly.")
        return solids[-1].Name

    def verify(self, checks):
        if self.doc is not None and (self.doc.Name in _executing_docs or _creation_in_progress):
            self.check_requests.append(checks)
            return {"scheduled": True, "note": "Checks run after final recompute; see verification in the result."}
        return Inspect.verify(self.doc, checks, _revisions.get(self.doc.Name, 0))

    def history(self):
        return History.describe(self.doc, _revisions.get(self.doc.Name, 0))

    def checkpoints(self, offset=0, limit=20):
        return History.checkpoints(offset, limit)

    def action(self, label):
        """Put cad.action('Meaningful edit name') first in a mutating call."""
        return {"label": label}

    def undo(self, *args, **kwargs):
        raise ValueError("History/checkpoint actions must be the only statement, with literal arguments.")

    redo = undo
    checkpoint = undo
    restore_checkpoint = undo
    yield_transaction = undo

    def selected(self) -> list[Any]:
        if Gui is None:
            return []
        return list(Gui.Selection.getSelection())

    def selection(self) -> list[dict[str, Any]]:
        """Describe selected objects and momentary face/edge references."""

        return _selection_snapshot(self.doc)

    def emit(self, label: str, value: Any) -> None:
        self.events.append({"label": str(label), "value": _json_safe(value)})

    def assert_valid(self) -> None:
        invalid = _invalid_objects(self.doc)
        if invalid:
            details = "; ".join(
                f"{item['name']}: {item['error']}" for item in invalid
            )
            raise RuntimeError(f"FreeCAD validation failed: {details}")

    def fit_view(self) -> None:
        if Gui is not None and Gui.activeDocument() is not None:
            Gui.activeDocument().activeView().viewAxonometric()
            Gui.activeDocument().activeView().fitAll()

    def render(
        self,
        *,
        width: int = 960,
        height: int = 720,
        view: str = "axonometric",
        fit: bool = True,
        focus=None,
        highlight=(),
        section=None,
        labels=False, axes=False, dimensions=False, highlight_changed=False, references=(),
    ) -> dict[str, Any]:
        """Attach one bounded viewport render to this tool observation."""

        self.render_request = _render_spec(width, height, view, fit)
        self.render_request.update(_visual_options(focus, highlight, section, labels, axes, dimensions, highlight_changed, references))
        self.render_views_request = []
        return {"scheduled": True, **self.render_request}

    def render_views(
        self,
        views=("axonometric", "front", "right", "top"),
        *,
        width: int = 640,
        height: int = 480,
        fit: bool = True,
        focus=None,
        highlight=(),
        section=None,
        compare: bool = False,
        labels=False, axes=False, dimensions=False, highlight_changed=False, references=(),
    ) -> dict[str, Any]:
        """Attach up to six ordered views of the final model; restore the user's camera.

        Images are paired with observation.renders metadata in the same order.
        Like render(), the last render request replaces earlier requests in this call.
        """
        if not isinstance(views, (list, tuple)) or not 1 <= len(views) <= 6:
            raise ValueError("views must be a list or tuple of one to six view names.")
        specs = [_render_spec(width, height, view, fit) for view in views]
        options = _visual_options(focus, highlight, section, labels, axes, dimensions, highlight_changed, references)
        for spec in specs:
            spec.update(options)
        if len({spec["view"] for spec in specs}) != len(specs):
            raise ValueError("Render views must be unique.")
        if sum(spec["width"] * spec["height"] for spec in specs) * (2 if compare else 1) > _MAX_RENDER_PIXELS:
            raise ValueError("Combined render area exceeds the pixel limit.")
        self.render_views_request = specs
        self.render_request = None
        return {"scheduled": True, "views": specs}

    def render_sketch(self, obj, *, width=960, height=480, constraint_offset=0):
        if width < 640 or height < 400 or constraint_offset < 0:
            raise ValueError("Sketch diagrams require at least 640x400 pixels and a non-negative constraint offset.")
        target = Inspect.resolve(self.doc, obj)
        if not target.isDerivedFrom("Sketcher::SketchObject"):
            raise ValueError("Expected a Sketcher object.")
        self.render_request = {**_render_spec(width, height, "current", False),
                               "sketch": target.Name, "constraintOffset": constraint_offset}
        self.render_views_request = []
        return {"scheduled": True, **self.render_request}


def _resolve_picked_object(doc, object_name, subelement):
    """FreeCAD reports sub-feature picks as 'Feature.FaceN'; reference the owning feature."""
    if not subelement:
        return object_name, ""
    parts = subelement.split(".")
    if len(parts) < 2:
        return object_name, subelement
    owner, element = parts[-2], parts[-1]
    if doc.getObject(owner) is not None:
        return owner, element
    return object_name, subelement


def picked_reference_json(document, object_name, subelement, x, y, z) -> str:
    """Native picker feedback; labels are display only, references are exact and expiring."""
    doc = App.ActiveDocument
    if doc is None or doc.Name != document:
        raise ValueError("Pick geometry in the active document.")
    object_name, subelement = _resolve_picked_object(doc, object_name, subelement)
    reference = {"document": doc.Name, "documentToken": Inspect.document_token(doc),
                 "object": object_name, "subelement": subelement,
                 "revision": _revisions.get(doc.Name, 0)}
    shape = Inspect.native_shape(doc, reference, reference["revision"])
    obj = doc.getObject(object_name)
    return json.dumps({"type": "cad", "name": f"{obj.Label} ({obj.Name}) · {subelement or 'whole object'}",
                       "reference": reference, "point_mm": [x, y, z],
                       "coordinateFrame": "document/world",
                       "inspect": f"cad.review({reference!r})",
                       "geometry": _topology_summary(shape)})


def validate_picks_json(encoded) -> str:
    doc = App.ActiveDocument
    for attachment in json.loads(encoded):
        if attachment.get("type") == "cad":
            if doc is None:
                raise ValueError("The picked document is closed. Remove the reference and pick again.")
            Inspect.native_shape(doc, attachment["reference"], _revisions.get(doc.Name, 0))
    return "ok"


def context_json() -> str:
    """Read-only preflight for durable operation preparation; never create a document."""
    doc = App.ActiveDocument
    return json.dumps({
        "documentName": doc.Name if doc else None,
        "revision": _revisions.get(doc.Name, 0) if doc else 0,
    })


_READ_HELPERS = {"inspect", "tree", "sketch", "api", "guide", "diagnostics", "history", "selection", "checkpoints", "topology", "measure", "verify", "explain", "frame", "parameters", "editability", "export", "compare", "remap", "documents"}
_HISTORY_HELPERS = {"undo", "redo", "checkpoint", "restore_checkpoint", "yield_transaction"}


def pending_transaction(doc: Any) -> dict[str, Any]:
    """Native undo-step lock plus the active workbench, for status and rejects."""
    info = History.pending_transaction(doc)
    workbench = None
    if Gui is not None:
        try:
            active = Gui.activeWorkbench()
            workbench = active.name() if active is not None else None
        except Exception:
            workbench = None
    return {"open": info["open"], "name": info["name"], "workbench": workbench}


def _standalone_helper(tree):
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Expr):
        return None
    call = tree.body[0].value
    if (not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute)
            or not isinstance(call.func.value, ast.Name) or call.func.value.id != "cad"
            or call.func.attr not in _READ_HELPERS | _HISTORY_HELPERS):
        return None
    if any(keyword.arg is None for keyword in call.keywords):
        return None
    try:
        return (call.func.attr, [ast.literal_eval(arg) for arg in call.args],
                {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords})
    except (ValueError, TypeError):
        return None


def _yield_transaction(doc, args, kwargs, revision):
    if args or set(kwargs) != {"mode"} or kwargs["mode"] not in ("commit", "abort"):
        raise ValueError("Use cad.yield_transaction(mode='commit') or cad.yield_transaction(mode='abort').")
    pending = pending_transaction(doc)
    if not pending["open"]:
        raise ValueError("No native transaction is open.")
    mode = kwargs["mode"]
    before = _document_snapshot(doc)
    _executing_docs.add(doc.Name)
    try:
        if mode == "commit":
            doc.commitTransaction()
        else:
            doc.abortTransaction()
        doc.recompute()
        after = _document_snapshot(doc)
        revision += 1
        _revisions[doc.Name] = revision
        History.invalidate(doc, "Native transaction was yielded; undo history changed.")
        return {
            "ok": True,
            "revisionBefore": revision - 1,
            "revision": revision,
            "result": {"operation": "yield_transaction", "mode": mode, "name": pending["name"]},
            "pendingTransaction": pending_transaction(doc),
            "observation": _observation(doc, before, after),
            "history": History.describe(doc, revision),
            "images": [],
        }
    finally:
        _executing_docs.discard(doc.Name)


def _history_execute(doc, method, args, kwargs, revision):
    if method == "yield_transaction":
        return _yield_transaction(doc, args, kwargs, revision)
    before = _document_snapshot(doc)
    if doc.HasPendingTransaction:
        raise PendingTransaction(pending_transaction(doc))
    if method == "checkpoint":
        result = History.checkpoint(doc, *args, **kwargs)
        return {"ok": True, "revision": revision, "revisionBefore": revision,
                "result": result, "history": History.describe(doc, revision), "images": []}
    if method == "restore_checkpoint":
        if args or set(kwargs) != {"checkpoint", "revision"} or kwargs["revision"] != revision:
            raise ValueError("Use cad.restore_checkpoint(checkpoint='id', revision=current_revision).")
        try:
            result = History.restore(kwargs["checkpoint"])
        except Exception as error:
            active = App.ActiveDocument
            return {"ok": False, "requiresInspection": True,
                    "revision": _revisions.get(active.Name, 0) if active else 0,
                    "error": {"type": type(error).__name__, "message": str(error)}}
        active = App.ActiveDocument
        return {"ok": True, "revisionBefore": revision,
                "revision": _revisions.get(active.Name, 0), "result": result,
                "observation": _observation(active, {}, _document_snapshot(active)), "images": []}
    if args or set(kwargs) != {"action", "revision"}:
        raise ValueError("Use cad.undo(action='id', revision=N) or cad.redo(action='id', revision=N).")
    entry = History.guard(doc, method, kwargs["action"], kwargs["revision"], revision, before)
    native_before = History.stacks(doc)
    _executing_docs.add(doc.Name)
    try:
        getattr(doc, method)()
        immediate = _document_snapshot(doc)
        target = entry["before" if method == "undo" else "after"]
        immediate_equivalent, _ = History.equivalent(target, immediate)
        if not immediate_equivalent:
            doc.recompute()
        after = _document_snapshot(doc)
        target = entry["before" if method == "undo" else "after"]
        equivalent, remapped = History.equivalent(target, after)
        if not equivalent:
            # Rebuild dependency geometry as well: an incremental Pad recompute
            # may retain location chains on a reused sketch's native BRep.
            for obj in doc.Objects:
                if hasattr(obj, "Shape"):
                    obj.touch()
            doc.recompute(None, True)
            after = _document_snapshot(doc)
            equivalent, remapped = History.equivalent(target, after)
        if not equivalent:
            raise RuntimeError("Native history did not restore the recorded parameters and native geometry.")
        revision += 1
        _revisions[doc.Name] = revision
        History.moved(doc, method, revision, after)
        return {"ok": True, "revisionBefore": revision - 1, "revision": revision,
                "result": {"operation": method, "action": entry["id"], "label": entry["label"]},
                "observation": _observation(doc, before, after),
                "requiresInspection": bool(remapped), "topologyMappingsChanged": remapped,
                "history": History.describe(doc, revision), "images": []}
    except Exception as error:
        restored = False
        try:
            if History.stacks(doc) != native_before:
                getattr(doc, "redo" if method == "undo" else "undo")()
                if _document_snapshot(doc) != before:
                    doc.recompute()
            restored, _ = History.equivalent(before, _document_snapshot(doc))
        except Exception:
            pass
        _revisions[doc.Name] = revision + 1
        History.invalidate(doc, "History verification failed; inspect the model before further edits.")
        return {"ok": False, "rolledBack": restored, "requiresInspection": True,
                "revision": revision + 1, "error": {"type": "HistoryVerificationError", "message": str(error)}}
    finally:
        _executing_docs.discard(doc.Name)


_DOCUMENT_OPENERS = {"newDocument", "openDocument", "open"}


def _base_namespace() -> dict[str, Any]:
    """Names prebound in every submitted program."""
    namespace: dict[str, Any] = {"__builtins__": __builtins__, "App": App, "FreeCAD": App, "Gui": Gui}
    if Part is not None:
        namespace["Part"] = Part
    if Sketcher is not None:
        namespace["Sketcher"] = Sketcher
    return namespace


def _opens_or_creates_document(tree) -> bool:
    """True when a program can establish the active document itself."""
    for item in ast.walk(tree):
        if (isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute)
                and item.func.attr in _DOCUMENT_OPENERS):
            return True
    return False


def _execute_document_creation(source: str, progress=lambda stage: None) -> dict[str, Any]:
    """Run a program that creates or opens the document when none is active.

    With no active document there is nothing to open a native transaction on, so this
    path cannot roll back partial edits. It says so instead of implying a rollback, and
    leaves whatever was created in place for inspection.
    """
    before_documents = {document.Name: _document_snapshot(document) for document in App.listDocuments()}
    stdout = io.StringIO()
    stderr = io.StringIO()
    cad = Cad(None)
    namespace = {**_base_namespace(), "doc": None, "cad": cad}
    try:
        code = _compile(source)
        progress("python")
        # Nothing was edited through a transaction, so suppress the external-change
        # observer: the whole program is this one call, not a series of user edits.
        App.removeDocumentObserver(_revision_observer)
        global _creation_in_progress
        _creation_in_progress = True
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exec(code, namespace, namespace)
        finally:
            _creation_in_progress = False
            App.addDocumentObserver(_revision_observer)
    except Exception as error:
        return {
            "ok": False,
            "requiresInspection": True,
            "revision": 0,
            "revisionBefore": 0,
            "stdout": _bounded(stdout.getvalue()),
            "stderr": _bounded(stderr.getvalue()),
            "error": {"type": type(error).__name__, "message": str(error),
                      "traceback": _bounded(traceback.format_exc())},
            "observation": None,
            "events": cad.events,
            "note": ("No document was active, so this call ran without a transaction and cannot "
                     "be rolled back. Inspect the document before retrying."),
        }

    document = App.ActiveDocument
    if document is None:
        return {
            "ok": False,
            "notExecuted": True,
            "revision": 0,
            "stdout": _bounded(stdout.getvalue()),
            "stderr": _bounded(stderr.getvalue()),
            "error": {"type": "DocumentMissing",
                      "message": "The program did not create or open a document."},
        }

    name = document.Name
    revision = _revisions.setdefault(name, 0)
    try:
        progress("recompute")
        document.recompute()
        progress("validation")
        Cad(document).assert_valid()
    except Exception as error:
        return {
            "ok": False,
            "requiresInspection": True,
            "revision": revision,
            "revisionBefore": revision,
            "stdout": _bounded(stdout.getvalue()),
            "stderr": _bounded(stderr.getvalue()),
            "error": {"type": type(error).__name__, "message": str(error),
                      "traceback": _bounded(traceback.format_exc())},
            "observation": None,
            "events": cad.events,
            "note": ("The new document could not be validated and no transaction was open, so it "
                     "was left in place for inspection."),
        }

    before = before_documents.get(name, {})
    after = _document_snapshot(document)
    changed = before != after
    revision += int(changed)
    _revisions[name] = revision
    verification = [Inspect.verify(document, checks, revision) for checks in cad.check_requests]
    observation = _observation(document, before, after)
    images = []
    specs = [cad.render_request] if cad.render_request is not None else cad.render_views_request
    for spec in specs:
        spec["revision"] = revision
    if specs:
        try:
            progress("rendering")
            rendered = _render_views(specs)
        except Exception as error:
            observation["visualFeedback"] = {"status": "failed", "reason": str(error)}
        else:
            observation["renders"] = [
                {"imageIndex": index, **{key: value for key, value in image.items() if key != "dataUrl"}}
                for index, image in enumerate(rendered)
            ]
            if len(rendered) == 1:
                observation["render"] = observation["renders"][0]
            images = [image["dataUrl"] for image in rendered]
    return {
        "ok": True,
        "revisionBefore": revision - int(changed),
        "revision": revision,
        "stdout": _bounded(stdout.getvalue()),
        "stderr": _bounded(stderr.getvalue()),
        "result": _json_safe(namespace.get("_result")),
        "observation": observation,
        "events": cad.events,
        "verification": verification,
        "images": images,
        "createdDocument": name,
    }


def _execute_impl(source: str, expected_revision: int | None = None, progress=lambda stage: None) -> dict[str, Any]:
    """Execute source once, returning a structured result suitable for an LLM."""

    if not isinstance(source, str) or not source.strip():
        return {
            "ok": False,
            "notExecuted": True,
            "error": {"type": "ValueError", "message": "Python source is empty."},
        }

    try:
        parsed = ast.parse(source)
        standalone = _standalone_helper(parsed)
    except Exception as error:
        return {"ok": False, "notExecuted": True,
                "error": {"type": type(error).__name__, "message": str(error)}}
    doc = App.ActiveDocument
    if doc is None:
        if standalone:
            method, args, kwargs = standalone
            try:
                if method in {"guide", "checkpoints", "documents"} or (method == "api" and kwargs.get("kind") in {"modules", "commands", "workbenches"}):
                    result = getattr(Cad(None), method)(*args, **kwargs)
                elif method in {"tree", "inspect"} and not args and not kwargs:
                    result = {"document": None, "nodes": [], "total": 0, "nextOffset": None}
                else:
                    raise ValueError("Open or create a document before using this helper.")
                return {"ok": True, "readOnly": True, "revision": 0, "result": result, "images": []}
            except Exception as error:
                return {"ok": False, "notExecuted": True,
                        "error": {"type": type(error).__name__, "message": str(error)}}
        # A program that creates or opens a document is the one legitimate case for
        # starting with no active document. Anything else is rejected so a stray edit
        # never produces an accidental scratch document.
        if _opens_or_creates_document(parsed):
            return _execute_document_creation(source, progress)
        return {
            "ok": False,
            "notExecuted": True,
            "revision": 0,
            "error": {
                "type": "DocumentMissing",
                "message": ("No active FreeCAD document. Create one explicitly first, "
                            "for example App.newDocument('Part')."),
            },
        }
    document_name = doc.Name

    revision = _revisions.get(document_name, 0)
    revision_before = revision
    if expected_revision is not None and expected_revision != revision:
        return {
            "ok": False,
            "conflict": True,
            "revision": revision,
            "revisionBefore": revision_before,
            "error": {
                "type": "RevisionConflict",
                "message": (
                    f"Expected document revision {expected_revision}, "
                    f"but the current revision is {revision}."
                ),
            },
        }

    try:
        tree = parsed
        helper = standalone
        if helper:
            method, args, kwargs = helper
            if method in _HISTORY_HELPERS:
                return _history_execute(doc, method, args, kwargs, revision)
            outcome = getattr(Cad(doc), method)(*args, **kwargs)
            response = {"ok": True, "readOnly": True, "revision": revision, "revisionBefore": revision,
                        "result": outcome,
                        "history": History.describe(doc, revision), "images": []}
            if method == "verify":
                # A read-only verify is sufficient on its own: surface the claims the
                # same way a mutation does, so callers need no fake edit.
                response["verification"] = [outcome]
            return response
        if doc.HasPendingTransaction:
            raise PendingTransaction(pending_transaction(doc))
        label = "Agent edit"
        first = tree.body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Call)
                and isinstance(first.value.func, ast.Attribute)
                and isinstance(first.value.func.value, ast.Name)
                and first.value.func.value.id == "cad" and first.value.func.attr == "action"):
            label = ast.literal_eval(first.value.args[0])
            if not isinstance(label, str) or not label.strip() or len(label) > 80:
                raise ValueError("Action label must contain 1..80 characters.")
        # Do not let a history helper sneak into an ordinary mutation transaction.
        for item in ast.walk(tree):
            if isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute):
                if item.func.attr in _HISTORY_HELPERS:
                    raise ValueError("History operations must be standalone literal cad calls, not nested in mutations.")
    except PendingTransaction as error:
        return {"ok": False, "notExecuted": True, "revision": revision,
                "error": {"type": "PendingTransaction", "message": str(error)},
                "pendingTransaction": error.pending}
    except Exception as error:
        rejected = {"ok": False, "notExecuted": True, "revision": revision,
                    "error": {"type": type(error).__name__, "message": str(error)}}
        if doc.HasPendingTransaction:
            rejected["pendingTransaction"] = pending_transaction(doc)
        return rejected

    # Transactions retain rollback state only when undo is enabled. Do not change
    # this setting for read-only helpers.
    if doc.UndoMode == 0:
        doc.UndoMode = 1
    before = _document_snapshot(doc)
    stdout = io.StringIO()
    stderr = io.StringIO()
    cad = Cad(doc)
    cad.reference_baseline = before
    namespace = {**_base_namespace(), "doc": doc, "cad": cad}

    action_id = str(uuid.uuid4())
    doc.openTransaction(f"Anthracite: {label} [{action_id}]")
    _executing_docs.add(document_name)
    try:
        baseline_images = []
        compare_calls = [item for item in ast.walk(tree)
                         if isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute)
                         and isinstance(item.func.value, ast.Name) and item.func.value.id == "cad"
                         and item.func.attr == "render_views"
                         and any(kw.arg == "compare" for kw in item.keywords)]
        if len(compare_calls) > 1:
            raise ValueError("Only one compare render request is allowed per action.")
        if compare_calls:
            call = compare_calls[0]
            if not isinstance(tree.body[-1], ast.Expr) or tree.body[-1].value is not call:
                raise ValueError("The compare render request must be the final top-level statement.")
            options = {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords}
            if options.get("compare"):
                baseline = Cad(doc)
                baseline.render_views(*[ast.literal_eval(arg) for arg in call.args], **options)
                baseline_images = [{**image, "phase": "before", "revision": revision}
                                   for image in _render_views(baseline.render_views_request)]
        code = _compile(source)
        progress("python")
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exec(code, namespace, namespace)

        if not _document_exists(document_name):
            raise RuntimeError(
                "The call closed the active document, so nothing was rolled back. Open or "
                "activate a document in a separate call."
            )
        if App.ActiveDocument is not doc:
            # A transaction cannot span a document switch, so the observation and
            # revision bookkeeping would describe the wrong document.
            raise RuntimeError(
                "The active document changed during the call; create or activate documents "
                "in a separate call."
            )

        # A lightweight parameter receipt does not need another serialized BRep.
        authored = {obj.Name: {key: Inspect.value(getattr(obj, key)) for key in obj.PropertiesList
                              if obj.getTypeIdOfProperty(key) != "Part::PropertyPartShape"}
                    for obj in doc.Objects}
        _recompute_writes[document_name] = set()
        progress("recompute")
        try:
            doc.recompute()
        finally:
            recompute_writes = _recompute_writes.pop(document_name)
        progress("validation")
        cad.assert_valid()
        after = _document_snapshot(doc)
        final_revision = revision + int(before != after)
        touched = sorted(name for name in after if name not in before or before[name] != after[name])
        specs = [cad.render_request] if cad.render_request is not None else cad.render_views_request
        for spec in specs:
            spec["revision"] = final_revision
            if spec.get("highlightChanged") and not spec.get("highlight"):
                names = touched if before != after else _last_changes.get(document_name, {}).get("names", [])
                if before == after and _last_changes.get(document_name, {}).get("revision") != revision:
                    names = []
                spec["highlight"] = [name for name in names if doc.getObject(name) is not None
                                     and hasattr(doc.getObject(name), "Shape") and not doc.getObject(name).Shape.isNull()]
                if not spec["highlight"]:
                    raise ValueError("No current changed geometry to highlight; make an edit or specify highlight explicitly.")
        verification = [Inspect.verify(doc, checks, final_revision) for checks in cad.check_requests]
        progress("rendering")
        rendered = _render_views(
            specs
        )
        if baseline_images:
            rendered = baseline_images + [{**image, "phase": "after"} for image in rendered]
            if sum(image["width"] * image["height"] for image in rendered) > _MAX_RENDER_PIXELS:
                raise ValueError("Combined before/after views exceed the pixel limit.")
            if sum(len(image["dataUrl"]) for image in rendered) > _MAX_RENDER_BYTES:
                raise ValueError("Combined before/after images exceed the 8 MB payload limit.")
        progress("commit")
        doc.commitTransaction()
    except Exception as error:
        error_traceback = traceback.format_exc()
        rolled_back = False
        remapped = []
        observation = None
        # A program may close the document it started with. Nothing can be rolled back
        # then, and touching the deleted object would raise a confusing error that
        # masks the real one.
        document_alive = _document_exists(document_name)
        if not document_alive:
            error_traceback += ("\nThe call closed the active document, so its work could "
                                "not be rolled back.")
        if document_alive:
            try:
                progress("rollback")
                doc.abortTransaction()
                doc.recompute()
                rolled_back = True
            except Exception:
                error_traceback += "\nRollback failure:\n" + traceback.format_exc()
            try:
                after_rollback = _document_snapshot(doc)
                observation = _observation(doc, before, after_rollback)
                equivalent, remapped = History.equivalent(before, after_rollback)
                rolled_back = rolled_back and equivalent
            except Exception:
                rolled_back = False
                observation = None
                error_traceback += "\nPost-rollback inspection failure:\n" + traceback.format_exc()
            if not rolled_back or remapped:
                # Invalidate previously issued references when restoration is uncertain.
                revision += 1
                _revisions[document_name] = revision
                History.invalidate(doc, "Rollback failed or could not be verified.")
        _executing_docs.discard(document_name)
        return {
            "ok": False,
            "revision": revision,
            "rolledBack": rolled_back,
            "revisionBefore": revision_before,
            "requiresInspection": not rolled_back or bool(remapped),
            "topologyMappingsChanged": remapped,
            "stdout": _bounded(stdout.getvalue()),
            "stderr": _bounded(stderr.getvalue()),
            "error": {
                "type": type(error).__name__,
                "message": str(error),
                "traceback": _bounded(error_traceback),
            },
            "observation": observation,
            "events": cad.events,
        }

    changed = before != after
    revision += int(changed)
    _revisions[document_name] = revision
    _executing_docs.discard(document_name)
    if changed:
        _last_changes[document_name] = {"revision": revision, "names": touched}
        History.record(doc, action_id, label, before, after, revision)
    observation = _observation(doc, before, after)
    if not cad.automatic_images:
        observation["visualFeedback"] = {
            "status": "skipped", "reason": "Automatic images disabled for this call.",
            "nextAction": "Use cad.render() or cad.review('Name') for final visual inspection.",
        }
    observation["editReceipt"] = {
        "beforeFinalRecompute": {name: [key for key, value in item.items()
                                         if before.get(name, {}).get("properties", {}).get(key, {}).get("value") != value]
                                 for name, item in authored.items()
                                 if any(before.get(name, {}).get("properties", {}).get(key, {}).get("value") != value for key, value in item.items())},
        "geometryChangedByFinalRecompute": sorted({name for name, prop in recompute_writes if prop == "Shape"}),
        "topologyChangedObjects": [name for name in after if after[name].get("properties", {}).get("Shape")
                                   != before.get(name, {}).get("properties", {}).get("Shape")],
        "referencePolicy": "All previously issued topology references expire when the document revision changes.",
        "note": "Before-recompute writes include native side effects during Python execution; they are not asserted to be direct assignments.",
    }
    images = []
    if rendered:
        observation["renders"] = [
            {"imageIndex": index, **{key: value for key, value in image.items() if key != "dataUrl"}}
            for index, image in enumerate(rendered)
        ]
        if len(rendered) == 1:
            observation["render"] = observation["renders"][0]
        images = [image["dataUrl"] for image in rendered]
    return {
        "ok": True,
        "revisionBefore": revision_before,
        "revision": revision,
        "stdout": _bounded(stdout.getvalue()),
        "stderr": _bounded(stderr.getvalue()),
        "result": _json_safe(namespace.get("_result")),
        "observation": observation,
        "events": cad.events,
        "verification": verification,
        "images": images,
        "action": {"id": action_id, "label": label} if changed else None,
        "history": History.describe(doc, revision),
    }


def _intervening_changes(doc):
    previous = _observed_snapshots.get(doc.Name)
    if previous is None:
        return {"baseline": "first observation in this application session", "changes": None}
    current = _document_snapshot(doc)
    before, revision = previous
    names = sorted(set(before) | set(current))
    entries = []
    for name in names:
        if before.get(name) == current.get(name):
            continue
        old, new = before.get(name), current.get(name)
        entry = {"object": name, "kind": "created" if old is None else "deleted" if new is None else "changed"}
        if old and new:
            changed = _changed_properties(old, new)
            entry["properties"] = {key: {"before": old["properties"].get(key, {}).get("value"),
                                          "after": new["properties"].get(key, {}).get("value")}
                                   for key in changed[:20] if key != "Shape"}
            entry["propertyCount"] = len(changed)
            entry["geometryChanged"] = "Shape" in changed
        entries.append(entry)
    return {"sinceRevision": revision, "revision": _revisions.get(doc.Name, 0),
            "changes": entries[:40], "remaining": max(0, len(entries) - 40),
            "source": "Changes outside this executor since its last call: user, native undo/redo, or other automation; author is not inferred.",
            "referencePolicy": "Revision changes invalidate topology references even when geometry later returns to the same appearance."}


def _automatic_feedback(doc, result):
    observation = result.get("observation") or {}
    if not result.get("ok") or result.get("readOnly") or result.get("images"):
        return
    if observation.get("visualFeedback", {}).get("status") == "skipped":
        return
    changed = observation.get("created", []) + observation.get("changed", [])
    geometry = [entry["name"] for entry in changed if entry.get("shape") and
                (entry in observation.get("created", []) or
                 set(entry.get("properties", [])) & {"Shape", "Placement"})]
    if not geometry and not observation.get("deleted"):
        return
    if Gui is None or Gui.activeDocument() is None:
        observation["visualFeedback"] = {"status": "unavailable", "reason": "No native GUI viewport."}
        return
    # Prefer the visible final Body to hidden intermediate features. Multiple
    # independent results get an overview; never arbitrarily choose one of them.
    candidates = [doc.getObject(name) for name in geometry]
    bodies = [obj for obj in candidates if obj.isDerivedFrom("PartDesign::Body")]
    candidates = bodies or [obj for obj in candidates if getattr(getattr(obj, "ViewObject", None), "Visibility", False)]
    focus = candidates[0].Name if len(candidates) == 1 else None
    specs = [{**_render_spec(640, 480, "current", True), "labels": True, "axes": True,
              "revision": result["revision"], "automatic": True, "purpose": "post-edit overview"}]
    if focus:
        specs.append({**_render_spec(640, 480, "axonometric", False), "focus": focus,
                      "highlight": [focus], "labels": True, "axes": True, "revision": result["revision"],
                      "automatic": True, "purpose": "post-edit focused inspection"})
    try:
        rendered = _render_views(specs)
        observation["renders"] = [{"imageIndex": i, **{k: v for k, v in image.items() if k != "dataUrl"}}
                                  for i, image in enumerate(rendered)]
        result["images"] = [image["dataUrl"] for image in rendered]
        observation["visualFeedback"] = {"status": "captured", "note": "Images are observations, not proof of design correctness."}
    except Exception as error:
        # Optional feedback must not undo an already validated and committed CAD edit.
        observation["visualFeedback"] = {"status": "failed", "reason": str(error),
                                         "retry": "cad.render_views()", "editCommitted": True}


def execute(source: str, expected_revision: int | None = None, progress=None) -> dict[str, Any]:
    # Telemetry is optional and must never change transaction outcomes.
    def report(stage):
        if progress is not None:
            try:
                progress(stage)
            except Exception:
                pass
    report("inspection")
    doc = App.ActiveDocument
    try:
        intervening = _intervening_changes(doc) if doc else None
    except Exception as error:
        intervening = {"status": "unavailable", "reason": str(error)}
    result = _execute_impl(source, expected_revision, report)
    result["interveningChanges"] = intervening
    current = App.ActiveDocument
    if current is not None and (doc is None or current is doc):
        observation = result.get("observation") or {}
        changes = observation.get("created", []) + observation.get("changed", [])
        if result.get("ok") and changes:
            # Native undo/redo is also an observed edit, not a reason to retain
            # or guess the pre-undo geometry highlight.
            _last_changes[current.Name] = {"revision": result["revision"],
                                           "names": [entry["name"] for entry in changes]}
        try:
            report("feedback")
            _automatic_feedback(current, result)
        except Exception as error:
            if isinstance(result.get("observation"), dict):
                result["observation"]["visualFeedback"] = {"status": "failed", "reason": str(error),
                                                           "editCommitted": bool(result.get("ok"))}
        try:
            _observed_snapshots[current.Name] = (_document_snapshot(current), _revisions.get(current.Name, 0))
        except Exception as error:
            result["changeTracking"] = {"status": "unavailable", "reason": str(error)}
    if not result.get("ok"):
        error_type = result.get("error", {}).get("type")
        if error_type == "PendingTransaction":
            pending = result.get("pendingTransaction") or {}
            label = pending.get("name") or "unnamed"
            workbench = pending.get("workbench")
            where = f"{label}, {workbench}" if workbench else label
            actions = [
                {"code": "cad.yield_transaction(mode='commit')",
                 "reason": f"A native undo step is open ({where}). If the user asked you to proceed, commit keeps the GUI edit; then mutate in a new call."},
                {"code": "cad.yield_transaction(mode='abort')",
                 "reason": "Discard the unfinished GUI edit, then mutate in a new call. Do not retry the rejected mutation while the transaction is open."},
            ]
        elif result.get("conflict") or "topology" in result.get("error", {}).get("message", "").lower():
            actions = [{"code": "cad.tree()", "reason": "Refresh current object names and revision; then query cad.topology('Name') for new references. Never reuse guessed face/edge indices."}]
        elif error_type in ("SyntaxError", "IndentationError"):
            actions = [{"reason": "Correct the Python syntax; the rejected source was not executed."}]
        elif error_type in ("AttributeError", "TypeError"):
            actions = [{"code": "cad.api()", "reason": "Inspect installed native APIs and argument types before retrying."}]
        else:
            actions = [{"code": "cad.diagnostics()", "reason": "Inspect native status and dependencies; do not retry the same failing edit blindly."}]
        if result.get("requiresInspection"):
            actions.insert(0, {"code": "cad.history()", "reason": "Restoration or topology identity requires inspection. Check recovery state before another mutation."})
        result["nextActions"] = actions
    return result


def execute_json(source: str, expected_revision: int | None = None, progress=None) -> str:
    """JSON convenience boundary for the native provider bridge."""

    return json.dumps(
        execute(source, expected_revision, progress),
        ensure_ascii=False,
        separators=(",", ":"),
    )
