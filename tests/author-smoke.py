# SPDX-License-Identifier: LGPL-2.1-or-later
"""Native authoring and disposable candidate evaluation in stock FreeCAD."""
import json
import math
import os
import tempfile
import traceback

try:
    if os.environ.get("ANTHRACITE_SMOKE") != "1":
        raise RuntimeError("Run through the isolated test profile")
    import FreeCAD as App
    import AnthraciteExecutor as Executor
    import AnthraciteIR
    if App.GuiUp:
        import FreeCADGui as Gui
        from PySide6 import QtWidgets
        QtWidgets.QApplication.instance().processEvents()

    doc = App.newDocument("Authoring")

    def run(source, ok=True):
        result = Executor.execute(source)
        assert result["ok"] is ok, json.dumps(result, default=str)
        return result

    run("""cad.feedback(images=False)
body = doc.addObject('PartDesign::Body', 'Body')
sketch = cad.rectangle_sketch(body, 'BaseSketch', '20 mm', '10 mm')
pad = cad.pad(sketch, 'Pad', '5 mm')
cad.declare_parameter('width', sketch, constraint='Width', unit='cm', bounds=[1, 4])
cad.declare_parameter('height', sketch, constraint='Height', bounds=[5, 20])
cad.declare_parameter('thickness', pad, property='Length', bounds=[1, 10])
""")
    assert doc.BaseSketch.DoF == 0
    assert abs(doc.Pad.Shape.Volume - 1000) < 1e-7
    if App.GuiUp:
        Gui.activeDocument().activeView().viewAxonometric()
        Gui.activeDocument().activeView().fitAll()
        Gui.updateGui()
        QtWidgets.QApplication.instance().processEvents()
    entries = run("cad.parameters()")["result"]["entries"]
    assert entries[0]["independent"]
    assert next(e for e in entries if e["name"] == "width")["value"] == 2
    before = Executor._document_snapshot(doc)
    invalid = run("cad.set_parameters({'width': 3, 'height': '8 deg'})", False)
    assert invalid["rolledBack"] and Executor._document_snapshot(doc) == before
    run("cad.set_parameters({'width': 5})", False)
    assert Executor._document_snapshot(doc) == before
    edited = run("cad.set_parameters({'width': 3, 'height': '12 mm'})")
    assert abs(doc.Pad.Shape.Volume - 1800) < 1e-7
    if App.GuiUp:
        assert edited["images"], edited
    undone = run(f"cad.undo(action={edited['action']['id']!r}, revision={edited['revision']})")
    assert abs(doc.Pad.Shape.Volume - 1000) < 1e-7
    run(f"cad.redo(action={edited['action']['id']!r}, revision={undone['revision']})")
    assert abs(doc.Pad.Shape.Volume - 1800) < 1e-7
    run("""cad.feedback(images=False)
doc.Pad.setExpression('Length', 'BaseSketch.Constraints.Width / 10')
""")
    derived = next(e for e in run("cad.parameters()")["result"]["entries"] if e["name"] == "thickness")
    assert not derived["independent"] and derived["expression"]
    run("cad.set_parameters({'thickness': 4})", False)
    run("cad.set_parameters({'width': '25 mm'})")
    assert abs(doc.Pad.Length.Value - 2.5) < 1e-9
    run("cad.declare_parameter('thickness', 'Pad', property='Length', bounds=[1,3])")
    bounded = Executor._document_snapshot(doc)
    violation = run("cad.set_parameters({'width': '40 mm'})", False)
    assert violation['rolledBack'] and Executor._document_snapshot(doc) == bounded
    # A user edit is read from the native property, never overwritten from metadata.
    doc.BaseSketch.setDatum('Height', App.Units.Quantity('11 mm'))
    doc.recompute()
    assert next(e for e in run("cad.parameters()")["result"]["entries"] if e["name"] == "height")["value"] == 11
    # Label changes and native constraint reordering retain the named binding.
    doc.BaseSketch.Label = "Renamed by user"
    import Sketcher
    doc.BaseSketch.delConstraint(0)
    doc.BaseSketch.addConstraint(Sketcher.Constraint('Coincident', 0, 2, 1, 1))
    doc.recompute()
    assert doc.BaseSketch.DoF == 0
    assert next(e for e in run("cad.parameters()")["result"]["entries"] if e["name"] == "width")["value"] == 2.5
    selected = run("cad.select_geometry('Pad', surface='Plane', where={'centerOfMass_mm':[12.5,5.5,2.5]}, unique=True)")["result"]
    assert selected["total"] == 1
    ref = selected["items"][0]["reference"]
    run("cad.select_geometry('Pad', surface='Plane', unique=True)", False)
    snapshot = Executor._document_snapshot(doc)
    source_hash = AnthraciteIR.compile_document(doc)["definitionHash"]
    documents = set(App.listDocuments())
    if App.GuiUp:
        Gui.Selection.addSelection(doc.Name, 'Pad', selected['items'][0]['reference']['subelement'])
        selected_before = [(s.DocumentName, s.ObjectName, list(s.SubElementNames)) for s in Gui.Selection.getSelectionEx()]
        camera_before = Gui.activeDocument().activeView().getCamera()
    filename, modified, undo_count = doc.FileName, doc.LastModifiedDate, doc.UndoCount
    candidate = run("cad.evaluate({'width': '30 mm'}, target='Pad', checks=[{'object':'Pad','metric':'volume_mm3','expected':990,'tolerance':0.001}])")["result"]
    assert candidate["ok"], json.dumps(candidate)
    assert candidate["accepted"]
    assert abs(candidate["measurement"]["volume_mm3"] - 990) < 1e-7
    assert candidate["definitionHash"] != source_hash
    assert candidate["sourceDefinitionHash"] == source_hash
    after = Executor._document_snapshot(doc)
    assert after == snapshot, json.dumps({name: {"before": snapshot.get(name), "after": after.get(name)} for name in set(after) | set(snapshot) if snapshot.get(name) != after.get(name)}, default=str)
    assert (doc.FileName, doc.LastModifiedDate, doc.UndoCount) == (filename, modified, undo_count)
    assert set(App.listDocuments()) == documents and App.ActiveDocument is doc
    if App.GuiUp:
        assert [(s.DocumentName, s.ObjectName, list(s.SubElementNames)) for s in Gui.Selection.getSelectionEx()] == selected_before
        assert Gui.activeDocument().activeView().getCamera() == camera_before
    assert run("cad.measure(" + repr(ref) + ")")["ok"]
    rejected = run("cad.evaluate({'width': '50 mm'})")["result"]
    assert not rejected["ok"] and rejected["rolledBack"]
    assert Executor._document_snapshot(doc) == snapshot
    failed_claim = run("cad.evaluate({}, checks=[{'object':'Pad','metric':'volume_mm3','expected':999,'tolerance':0.001}])")["result"]
    assert failed_claim["ok"] and not failed_claim["accepted"]
    assert failed_claim["definitionHash"] == source_hash
    assert Executor._document_snapshot(doc) == snapshot
    with tempfile.TemporaryDirectory() as directory:
        sleeper = os.path.join(directory, "worker")
        with open(sleeper, "w") as output:
            output.write("#!/usr/bin/env python3\nimport time\ntime.sleep(10)\n")
        os.chmod(sleeper, 0o700)
        previous = os.environ.get("FREECAD_CMD")
        os.environ["FREECAD_CMD"] = sleeper
        try:
            timed_out = run("cad.evaluate({}, timeout=1)")["result"]
            assert not timed_out["ok"] and timed_out["error"]["type"] == "EvaluationTimeout"
            assert Executor._document_snapshot(doc) == snapshot
        finally:
            if previous is None:
                os.environ.pop("FREECAD_CMD")
            else:
                os.environ["FREECAD_CMD"] = previous
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "native.FCStd")
        doc.saveCopy(path)
        saved_name = doc.Name
        App.closeDocument(saved_name)
        doc = App.openDocument(path)
        assert len(run("cad.parameters()")["result"]["entries"]) == 3
        run("cad.set_parameters({'width': '20 mm'})")
        assert abs(doc.Pad.Length.Value - 2) < 1e-9
    # Circle + pocket remain real constrained native objects.
    run("""cad.feedback(images=False)
hole = cad.circle_sketch('Body', 'HoleSketch', '2 mm', placement=App.Placement(App.Vector(5,5,2),App.Rotation()))
pocket = cad.pocket(hole, 'Pocket', '1 mm')
""")
    assert doc.HoleSketch.DoF == 0
    assert doc.Pocket.TypeId == "PartDesign::Pocket"
    assert abs(doc.Pocket.Shape.Volume - (20 * 11 * 2 - math.pi)) < 1e-6
    # Native numeric kinds, angle units, and native constraint clamping are checked.
    run("""cad.feedback(images=False)
inputs = doc.addObject('App::DocumentObjectGroup', 'Inputs')
inputs.addProperty('App::PropertyAngle', 'Angle')
inputs.addProperty('App::PropertyInteger', 'Count')
inputs.Count = 2
inputs.addProperty('App::PropertyFloatConstraint', 'Limited')
inputs.Limited = (5, 0, 10, 1)
cad.declare_parameter('angle', inputs, property='Angle', unit='rad', bounds=[0,2])
cad.declare_parameter('count', inputs, property='Count', bounds=[1,10])
cad.declare_parameter('limited', inputs, property='Limited')
cad.set_parameters({'angle':'90 deg','count':4})
""")
    assert abs(doc.Inputs.Angle.Value - 90) < 1e-9
    run("cad.set_parameters({'count': 2.5})", False)
    run("cad.set_parameters({'limited': 100})", False)
    assert float(doc.Inputs.Limited) == 5
    # A numerically allowed candidate can still fail the kernel; restore its tree.
    run("""cad.feedback(images=False)
box = doc.addObject('Part::Box','Box')
cad.declare_parameter('box_length', box, property='Length', bounds=[0,20])
""")
    snapshot = Executor._document_snapshot(doc)
    invalid_geometry = run("cad.set_parameters({'box_length': 0})", False)
    assert invalid_geometry['rolledBack'] and Executor._document_snapshot(doc) == snapshot
    os.write(1, b"ANTHRACITE_AUTHOR_SMOKE_OK\n")
    os._exit(0)
except BaseException:
    failure = traceback.format_exc()
    with open(os.path.join(os.environ.get("FREECAD_USER_HOME", tempfile.gettempdir()), "author-failure.log"), "w") as output:
        output.write(failure)
    os.write(2, failure.encode())
    os._exit(1)
