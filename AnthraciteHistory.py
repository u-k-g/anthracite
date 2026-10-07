# SPDX-License-Identifier: LGPL-2.1-or-later
"""Guards around FreeCAD's native undo stack and non-destructive document checkpoints."""
import hashlib
import json
import os
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


def record(doc, action_id, label, before, after, revision):
    state = _states.get(doc.Name)
    if state is None or state["blocked"]:
        state = {"past": [], "future": [], "blocked": None}
        _states[doc.Name] = state
    state["past"].append({"id": action_id, "label": label, "before": before, "after": after})
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
    if snapshot != entry["after" if direction == "undo" else "before"]:
        invalidate(doc, "Document contents no longer match the tracked action.")
        raise ValueError("Document contents changed; history operation was not executed.")
    return entry


def equivalent(expected, actual):
    """Exact inputs/geometry comparison with explicitly reported map-only regeneration.

    Native recompute can rewrite FreeCAD's *.Map.txt topology naming tables on undo.
    Only those archive members may differ: native BREP bytes, property XML, values,
    object membership, shape measurements and recompute state must still match.
    Never use this to identify an old face/edge in the new revision.
    """
    remapped = []
    if expected.keys() != actual.keys():
        return False, []
    for name, old in expected.items():
        new = actual[name]
        if {k: v for k, v in old.items() if k != "properties"} != {k: v for k, v in new.items() if k != "properties"}:
            return False, []
        if old["properties"].keys() != new["properties"].keys():
            return False, []
        for prop, previous in old["properties"].items():
            current = new["properties"][prop]
            if previous == current:
                continue
            if (previous.get("type") != "Part::PropertyPartShape"
                    or current.get("type") != previous["type"] or previous["value"] != current["value"]):
                return False, []
            members = previous.get("members", {})
            now = current.get("members", {})
            if not members or members.keys() != now.keys():
                return False, []
            differences = [key for key in members if members[key] != now[key]]
            if not differences or any(not key.endswith(".Map.txt") for key in differences):
                return False, []
            remapped.append({"object": name, "property": prop})
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
