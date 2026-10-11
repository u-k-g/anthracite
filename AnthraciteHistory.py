# SPDX-License-Identifier: LGPL-2.1-or-later
"""Guards around FreeCAD's native undo stack and non-destructive document checkpoints."""
import hashlib
import json
import math
import os
import re
import shutil
from pathlib import Path
import uuid

import FreeCAD as App

_states = {}


def stacks(doc):
    return (tuple(doc.UndoNames), tuple(doc.RedoNames))


def pending_transaction(doc):
    """Describe the unfinished native undo step, if any.

    Spreadsheet, Sketcher, and similar editors leave a transaction open for the
    whole view. Mutations must not nest into it; cad.yield_transaction commits
    or aborts it explicitly.
    """
    if doc is None:
        return {"open": False, "name": None}
    open_ = bool(getattr(doc, "HasPendingTransaction", False))
    name = None
    if open_:
        try:
            active = App.getActiveTransaction()
            if isinstance(active, (tuple, list)) and active:
                name = active[0] or None
            elif isinstance(active, str) and active:
                name = active
        except Exception:
            names = list(getattr(doc, "UndoNames", []) or [])
            name = names[0] if names else None
    return {"open": open_, "name": name}


def invalidate(doc, reason="Document was changed outside this agent action."):
    if doc.Name in _states:
        _states[doc.Name]["blocked"] = reason


def forget(doc):
    _states.pop(doc.Name, None)


def shape_digest(shape):
    """Hash full native BRep text at 14 decimal places, normalizing Checked flags.

    OCCT's BRep specification defines the third bit of the seven-bit shape flag
    word as Checked. Require the following subshape record to avoid matching
    numeric geometry data. OCCT renormalizes direction vectors by an ulp when
    restoring native BRep bytes. Normalize floating tokens at a precision far
    below kernel tolerance; retain every geometry record, tolerance and other
    flag. This is not a comparison of rounded bounds or volume summaries.
    https://github.com/Open-Cascade-SAS/OCCT/wiki/brep_format
    """
    text = shape.exportBrepToString()
    text = re.sub(r"(?m)^([01]{2})[01]([01]{4})(\r?\n)(?=[+\-ie*])",
                  r"\g<1>0\g<2>\g<3>", text)
    text = re.sub(r"(?<!\S)-0(?=\s|$)", "0", text)
    text = re.sub(r"(?<!\S)[+-]?(?:\d+\.\d*|\.\d+|\d+[eE][+-]?\d+)(?:[eE][+-]?\d+)?(?=\s|$)",
                  lambda match: format(round(float(match.group()), 14) or 0.0, '.14f').rstrip('0').rstrip('.'), text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def shape_contents(doc):
    """Freeze native topology before OCCT adds mutable pcurves during recompute."""
    return {(obj.Name, prop): obj.dumpPropertyContent(prop, 0)
            for obj in doc.Objects for prop in obj.PropertiesList
            if obj.getTypeIdOfProperty(prop) == "Part::PropertyPartShape"}


def record(doc, action_id, label, before, after, revision, before_shapes, after_shapes):
    state = _states.get(doc.Name)
    if state is None or state["blocked"]:
        state = {"past": [], "future": [], "blocked": None}
        _states[doc.Name] = state
    state["past"].append({"id": action_id, "label": label, "before": before, "after": after,
                          "beforeShapes": before_shapes, "afterShapes": after_shapes})
    state["past"] = state["past"][-20:]
    state["future"] = []
    state["stacks"] = stacks(doc)
    state["revision"] = revision


def describe(doc, revision):
    state = _states.get(doc.Name)
    reason = None
    if state is None:
        reason = "No tracked actions in this open document session."
    elif state["blocked"]:
        reason = state["blocked"]
    elif state["revision"] != revision or state["stacks"] != stacks(doc):
        reason = "Revision or native undo history changed; refusing to undo intervening edits."
    pending = pending_transaction(doc)
    if pending["open"]:
        label = pending["name"] or "unnamed"
        reason = (
            f"A native transaction is open ({label}). Mutations cannot nest into it. "
            "If the user asked you to proceed, cad.yield_transaction(mode='commit') "
            "keeps the GUI edit; mode='abort' discards it."
        )
    result = {"document": doc.Name, "revision": revision, "blockedReason": reason,
              "pendingTransaction": pending,
              "undo": None, "redo": None, "scope": "Last 20 tracked native actions in this open session"}
    if state:
        for direction, key in (("undo", "past"), ("redo", "future")):
            if state[key] and not reason:
                entry = state[key][-1]
                native = list(doc.UndoNames if direction == "undo" else doc.RedoNames)
                if native and entry["id"] in native[0]:
                    result[direction] = {"action": entry["id"], "label": entry["label"], "revision": revision}
    return result


def guard(doc, direction, action, expected_revision, revision, snapshot):
    info = describe(doc, revision)
    available = info[direction]
    if expected_revision != revision or not available or available["action"] != action:
        raise ValueError(info["blockedReason"] or "Action/revision does not match the next recoverable native action.")
    state = _states[doc.Name]
    entry = state["past" if direction == "undo" else "future"][-1]
    if not equivalent(entry["after" if direction == "undo" else "before"], snapshot)[0]:
        invalidate(doc, "Document contents no longer match the tracked action.")
        raise ValueError("Document contents changed; history operation was not executed.")
    return entry


def _geometry_equivalent(before, after):
    """Full sketch persistence, with a 1e-12 absolute bound on solver float drift.

    Pairwise comparison avoids rounding-boundary failures in a quantized digest.
    Integers, geometry modes, dependency order, blobs and all structure stay exact.
    """
    if isinstance(before, float) and isinstance(after, float):
        return math.isclose(before, after, rel_tol=0, abs_tol=1e-12)
    if type(before) is not type(after):
        return False
    if isinstance(before, dict):
        return before.keys() == after.keys() and all(_geometry_equivalent(v, after[k]) for k, v in before.items())
    if isinstance(before, list):
        return len(before) == len(after) and all(_geometry_equivalent(a, b) for a, b in zip(before, after))
    return before == after


def equivalent(expected, actual):
    """Persisted inputs/BRep comparison, with explicit native cache exceptions.

    Native recompute can rewrite FreeCAD's *.Map.txt topology naming tables on undo.
    Topology maps/version and the OCCT Checked bit can change on recompute. Shape
    binary differences require matching full text BRep digests at the declared
    precision, excluding Checked; property XML, values, other members and recompute
    state still match. Sketch geometry uses the IR's declared numeric precision
    and resolved external links instead of document-local projection cache IDs.
    Tessellation-dependent summary bounds are not a guard.
    Never use this to identify an old face/edge in the new revision.
    """
    remapped = []
    if expected.keys() != actual.keys():
        return False, []
    for name, old in expected.items():
        new = actual[name]
        # Bounds can depend on tessellation; area/volume integrate renormalized
        # curve directions and can drift by an ulp on restore. Compare full BRep
        # records below, retaining validity and all topology counts here.
        def metadata(obj):
            result = {k: v for k, v in obj.items() if k != "properties"}
            if isinstance(result.get("shape"), dict):
                derived = {"bounds"}
                if obj["properties"].get("Shape", {}).get("geometryHash"):
                    derived.update({"area", "volume"})
                result["shape"] = {k: v for k, v in result["shape"].items() if k not in derived}
            return result
        if metadata(old) != metadata(new):
            return False, []
        if old["properties"].keys() != new["properties"].keys():
            return False, []
        for prop, previous in old["properties"].items():
            current = new["properties"][prop]
            if previous == current:
                continue
            # Display floats can differ by an ulp even when FreeCAD's complete
            # persisted property bytes are identical. The bytes are authoritative.
            if (previous["type"] == current["type"]
                    and previous["type"] in {"App::PropertyFloat", "App::PropertyFloatConstraint",
                                             "App::PropertyLength", "App::PropertyDistance", "App::PropertyAngle"}
                    and previous["sha256"] == current["sha256"]):
                continue
            if (previous["type"] == current["type"] == "Part::PropertyGeometryList"
                    and previous.get("normalizedHash")
                    and (previous["normalizedHash"] == current.get("normalizedHash")
                         or (previous.get("nativeGeometry") is not None and current.get("nativeGeometry") is not None
                             and _geometry_equivalent(previous["nativeGeometry"], current["nativeGeometry"])))):
                remapped.append({"object": name, "property": prop})
                continue
            if (prop == "_ElementMapVersion" and previous["type"] == current["type"]
                    == "App::PropertyString"):
                remapped.append({"object": name, "property": prop})
                continue
            if (previous.get("type") != "Part::PropertyPartShape"
                    or current.get("type") != previous["type"] or previous["value"] != current["value"]
                    or previous.get("geometryHash") != current.get("geometryHash")):
                return False, []
            members = previous.get("members", {})
            now = current.get("members", {})
            if not members or members.keys() != now.keys():
                return False, []
            differences = [key for key in members if members[key] != now[key]]
            if not differences:
                return False, []
            for key in differences:
                if key.endswith(".Map.txt"):
                    remapped.append({"object": name, "property": prop})
                elif (not key.endswith(".bin") or not previous.get("geometryHash")
                      or previous["geometryHash"] != current.get("geometryHash")):
                    return False, []
    return True, remapped


def moved(doc, direction, revision, snapshot):
    state = _states[doc.Name]
    source, target = ("past", "future") if direction == "undo" else ("future", "past")
    entry = state[source].pop()
    entry["before" if direction == "undo" else "after"] = snapshot
    state[target].append(entry)
    # Adjacent actions share this boundary; keep their guard fingerprints current.
    if state[source]:
        state[source][-1]["after" if direction == "undo" else "before"] = snapshot
    state["revision"] = revision
    state["stacks"] = stacks(doc)


def _directory():
    base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    if not base.is_absolute():
        raise ValueError("XDG_STATE_HOME must be absolute.")
    return base / "anthracite/checkpoints"


def checkpoint(doc, label):
    if not isinstance(label, str) or not label.strip() or len(label) > 120:
        raise ValueError("Checkpoint label must contain 1..120 characters.")
    if doc.HasPendingTransaction:
        raise ValueError(
            "Finish the current native transaction before checkpointing. "
            "If the user asked you to proceed, cad.yield_transaction(mode='commit') "
            "or cad.yield_transaction(mode='abort') first."
        )
    directory = _directory()
    directory.mkdir(parents=True, exist_ok=True)
    identity = str(uuid.uuid4())
    target = directory / f"{identity}.FCStd"
    doc.saveCopy(str(target))
    with target.open("rb") as content:
        digest = hashlib.file_digest(content, "sha256").hexdigest()
    entry = {"id": identity, "label": label, "document": doc.Name,
             "sourceFile": doc.FileName, "sha256": digest, "path": str(target)}
    # This is checkpoint provenance, not an alternative session/event database.
    manifest = directory / f"{identity}.json"
    with manifest.open("x") as output:
        json.dump(entry, output)
    return entry


def checkpoints(offset=0, limit=20):
    if offset < 0 or not 1 <= limit <= 100:
        raise ValueError("Checkpoint limit must be 1..100; offset must be non-negative.")
    files = sorted(_directory().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    records = []
    for path in files[offset:offset + limit]:
        try:
            records.append(json.loads(path.read_text()))
        except (OSError, ValueError) as error:
            records.append({"id": path.stem, "error": str(error)})
    return {"checkpoints": records, "nextOffset": offset + limit if offset + limit < len(files) else None}


def restore(identity):
    identity = str(uuid.UUID(identity))
    directory = _directory()
    target = directory / f"{identity}.FCStd"
    info = json.loads((directory / f"{identity}.json").read_text())
    with target.open("rb") as content:
        digest = hashlib.file_digest(content, "sha256").hexdigest()
    if digest != info["sha256"]:
        raise ValueError("Checkpoint integrity verification failed.")
    # Copy before opening: later saves must not overwrite the checkpoint itself.
    recovered = directory / f"recovered-{uuid.uuid4()}.FCStd"
    with target.open("rb") as content, recovered.open("xb") as output:
        shutil.copyfileobj(content, output)
    document = App.openDocument(str(recovered))
    document.Label = f"Recovered: {info['label']}"
    App.setActiveDocument(document.Name)
    return {"document": document.Name, "file": str(recovered),
            "checkpoint": identity, "originalDocumentPreserved": True}
