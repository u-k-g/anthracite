# SPDX-License-Identifier: LGPL-2.1-or-later
"""Private worker entry point. Reuses AnthraciteExecutor; never opens the user's document."""
import json
import os
from pathlib import Path
import sys
import traceback


def main():
    directory = Path(os.environ["ANTHRACITE_EVALUATION"])
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import FreeCAD as App
        import Part
        import AnthraciteExecutor as Executor
        import AnthraciteIR
        import AnthraciteInspect as Inspect
        import AnthraciteAuthor

        if int(App.Version()[0]) < 1:
            raise RuntimeError("Candidate evaluation requires FreeCAD >= 1.0")
        request = json.loads((directory / "request.json").read_text())
        doc = App.openDocument(str(directory / "candidate.FCStd"))
        doc.recompute()
        if AnthraciteIR.compile_document(doc)["definitionHash"] != request["sourceDefinitionHash"]:
            raise RuntimeError("Native document copy changed the definition; candidate evaluation refused")
        source = "cad.feedback(images=False)\n"
        if request["values"]:
            source += "cad.set_parameters(" + repr(request["values"]) + ")\n"
        if request["checks"]:
            source += "cad.verify(" + repr(request["checks"]) + ")\n"
        result = Executor.execute(source)
        result["evaluator"] = {"name": "FreeCAD", "version": App.Version()[:3],
                               "kernelVersion": Part.OCC_VERSION}
        if result["ok"]:
            result["parameters"] = AnthraciteAuthor.surface(doc)
            result["definitionHash"] = AnthraciteIR.compile_document(doc)["definitionHash"]
            result["editability"] = Inspect.editability(doc)
            if request["target"]:
                result["measurement"] = Inspect.measure(doc, request["target"], revision=result["revision"])
        (directory / "response.json").write_text(json.dumps(result, allow_nan=False))
        os._exit(0 if result["ok"] else 1)
    except BaseException as error:
        traceback.print_exc()
        (directory / "response.json").write_text(json.dumps({"ok": False, "error": {
            "type": type(error).__name__, "message": str(error)}}))
        os._exit(1)


if __name__ == "__main__":
    main()
