# SPDX-License-Identifier: LGPL-2.1-or-later
"""Evaluate parameter candidates using the same executor in disposable FreeCAD processes."""
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid

import FreeCAD as App


def evaluate(doc, values, *, checks=None, target=None, timeout=60):
    import AnthraciteIR
    import AnthraciteAuthor
    if not isinstance(values, dict):
        raise ValueError("values must be a parameter mapping")
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not math.isfinite(timeout) or not 1 <= timeout <= 300:
        raise ValueError("timeout must be 1..300 seconds")
    if checks is not None and not isinstance(checks, list):
        raise ValueError("checks must be a list of explicit requirement claims")
    if getattr(doc, "HasPendingTransaction", False):
        raise ValueError("Finish the current native transaction before isolated evaluation")
    if target is not None and (not isinstance(target, str) or doc.getObject(target) is None):
        raise ValueError("target must name an existing native object")
    # Fail closed on Python behavior and external/cross-document inputs: a document
    # copy must be self-contained. This also records which definition was evaluated.
    baseline = AnthraciteIR.compile_document(doc)
    AnthraciteAuthor.surface(doc)
    configured = os.environ.get("FREECAD_CMD")
    candidates = [str(Path(App.ConfigGet("BinPath")) / "freecadcmd"),
                  str(Path(App.ConfigGet("BinPath")) / "FreeCADCmd"), shutil.which("FreeCADCmd"), shutil.which("freecadcmd")]
    if configured:
        candidates = [configured if Path(configured).is_file() else shutil.which(configured)]
    executable = next((p for p in candidates if p and Path(p).is_file()), None)
    if executable is None:
        raise RuntimeError("FreeCAD command-line executable not found; set FREECAD_CMD")
    directory = Path(tempfile.mkdtemp(prefix="anthracite-evaluation-"))
    try:
        for name in ("config", "data", "cache", "state"):
            (directory / name).mkdir()
        # saveCopy on the source canonicalizes its native topology-name maps.
        # Copy objects through FreeCAD's own dependency-aware copier first, then
        # serialize the hidden transfer document. Never save the user's document.
        active = App.ActiveDocument
        selection = []
        if App.GuiUp:
            import FreeCADGui as Gui
            selection = [(s.DocumentName, s.ObjectName, list(s.SubElementNames), list(s.PickedPoints))
                         for s in Gui.Selection.getSelectionEx("*")]
        transfer = App.newDocument("AnthraciteCandidate" + uuid.uuid4().hex, hidden=True)
        try:
            transfer.copyObject(doc.Objects, False)
            transfer.saveCopy(str(directory / "candidate.FCStd"))
        finally:
            App.closeDocument(transfer.Name)
            if active is not None:
                App.setActiveDocument(active.Name)
            if App.GuiUp:
                Gui.Selection.clearSelection()
                for document, obj, subelements, points in selection:
                    for index, subelement in enumerate(subelements or [""]):
                        if index < len(points):
                            point = points[index]
                            Gui.Selection.addSelection(document, obj, subelement, point.x, point.y, point.z)
                        else:
                            Gui.Selection.addSelection(document, obj, subelement)
        (directory / "request.json").write_text(json.dumps({"values": values, "checks": checks or [], "target": target,
                                                           "sourceDefinitionHash": baseline["definitionHash"]}, allow_nan=False))
        environment = dict(os.environ)
        environment.update({"ANTHRACITE_EVALUATION": str(directory),
                            "FREECAD_USER_HOME": str(directory / "config"),
                            "FREECAD_USER_DATA": str(directory / "data"),
                            "FREECAD_USER_TEMP": str(directory / "cache"),
                            "XDG_CONFIG_HOME": str(directory / "config"),
                            "XDG_DATA_HOME": str(directory / "data"),
                            "XDG_STATE_HOME": str(directory / "state"),
                            "XDG_CACHE_HOME": str(directory / "cache")})
        command = [executable, "--user-cfg", str(directory / "config" / "user.cfg"),
                   "--system-cfg", str(directory / "config" / "system.cfg"),
                   "-c", "import sys; sys.path.insert(0, " + repr(str(Path(__file__).resolve().parent)) +
                   "); import AnthraciteEvaluateWorker; AnthraciteEvaluateWorker.main()"]
        with (directory / "worker.log").open("w") as log:
            process = subprocess.Popen(command, env=environment, stdout=log, stderr=log)
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                return {"ok": False, "isolated": True, "error": {"type": "EvaluationTimeout", "message": "Candidate worker timed out and was terminated; the source document was not edited."},
                        "logPath": str(directory / "worker.log")}
        output = directory / "response.json"
        if not output.exists():
            raise RuntimeError(f"Candidate worker exited {process.returncode} without a response")
        result = json.loads(output.read_text())
        result.update({"isolated": True, "sourceDefinitionHash": baseline["definitionHash"],
                       "sourceDocument": doc.Name,
                       "images": [], "visualFeedback": "Headless candidate; render the accepted edit in the GUI."})
        has_requirements = bool(checks) or any(v.get("savedCheck") for v in result.get("verification", []))
        result["accepted"] = (bool(result.get("ok")) and all(v["status"] == "pass" for v in result.get("verification", []))) if has_requirements else None
        if result.get("ok") and process.returncode == 0:
            shutil.rmtree(directory)
        else:
            result["logPath"] = str(directory / "worker.log")
        return result
    except Exception as error:
        raise RuntimeError(f"Isolated evaluation failed: {error}; files retained at {directory}") from error
