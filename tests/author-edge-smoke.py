# SPDX-License-Identifier: LGPL-2.1-or-later
"""Authoring API boundary cases and isolated-worker failures in stock FreeCAD."""
import base64
import io
import json
import math
import os
from pathlib import Path
import tempfile
import traceback
import unittest
import xml.etree.ElementTree as ET
import zipfile
from unittest.mock import patch

import FreeCAD as App
import AnthraciteExecutor as Executor
import AnthraciteIR


class AuthorEdgeTest(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("AuthorEdge")
        self.doc.UndoMode = 1

    def tearDown(self):
        if self.doc.Name in App.listDocuments():
            App.closeDocument(self.doc.Name)

    def call(self, code, ok=True):
        result = Executor.execute(code)
        self.assertIs(result["ok"], ok, json.dumps(result, default=str))
        return result

    def edit(self, code, ok=True):
        return self.call("cad.feedback(images=False)\n" + code, ok)

    def fixture(self):
        self.edit("box=doc.addObject('Part::Box','Box')\ncad.declare_parameter('length','Box',property='Length',bounds=[1,30])")

    def unchanged_failure(self, code, *, read=False):
        before = Executor._document_snapshot(self.doc)
        response = self.call(code, False) if read else self.edit(code, False)
        self.assertEqual(Executor._document_snapshot(self.doc), before)
        if not read:
            self.assertTrue(response["rolledBack"], response)
        return response

    def test_declaration_and_quantity_boundaries(self):
        self.fixture()
        for options in ["property='Length',constraint='Width'", "", "property='Missing'",
                        "property='Length',unit='deg'", "property='Length',bounds=[20,10]",
                        "property='Length',bounds=[11,20]", "property='Length',bounds=[True,20]",
                        "property='Length',bounds=[1]", "property='Length',bounds=['nan mm',20]"]:
            self.unchanged_failure("cad.declare_parameter('bad','Box'" + ("," + options if options else "") + ")")
        for value in ["True", "None", "[]", "'2 s'", "float('inf')", "float('nan')", "0", "31"]:
            self.unchanged_failure("cad.set_parameters({'length':" + value + "})")
        self.edit("cad.declare_parameter('length_cm','Box',property='Length',unit='cm',bounds=['1 mm','30 mm'])")
        self.edit("cad.set_parameters({'length':20,'length_cm':2})")
        self.assertEqual(self.doc.Box.Length.Value, 20)
        self.unchanged_failure("cad.set_parameters({'length':15,'length_cm':2})")
        self.unchanged_failure("cad.set_parameters({'unknown':2})")
        self.unchanged_failure("cad.set_parameters({})")

    def test_duplicate_bindings_and_missing_dimensions(self):
        self.fixture()
        self.edit("doc.addObject('Part::Box','Other')")
        self.unchanged_failure("cad.declare_parameter('length','Other',property='Length')")
        self.edit("body=doc.addObject('PartDesign::Body','Body')\ns=cad.circle_sketch(body,'Circle',8)\ncad.declare_parameter('diameter',s,constraint='Diameter',bounds=[1,20])")
        self.doc.Circle.renameConstraint(1, 'RenamedDiameter')
        self.doc.recompute()
        rejected = self.unchanged_failure("cad.set_parameters({'diameter':10})")
        self.assertIn('missing', rejected['error']['message'])
        self.doc.Circle.renameConstraint(1, 'Diameter')
        self.doc.recompute()
        self.doc.commitTransaction()
        self.edit("cad.set_parameters({'diameter':10})")
        self.assertAlmostEqual(self.doc.Circle.getDatum('Diameter').Value, 10)

    def test_sketch_angles_and_derived_dimension(self):
        self.edit("""s=doc.addObject('Sketcher::SketchObject','Sketch')
s.addGeometry(Part.LineSegment(App.Vector(),App.Vector(10,10,0)),False)
s.addConstraint(Sketcher.Constraint('Coincident',0,1,-1,1))
s.addConstraint(Sketcher.Constraint('Distance',0,math.sqrt(200)))
i=s.addConstraint(Sketcher.Constraint('Angle',0,math.pi/4))
s.renameConstraint(i,'Angle')
cad.declare_parameter('angle',s,constraint='Angle',unit='deg',bounds=[0,180])
""".replace('math.sqrt(200)', repr(math.sqrt(200))).replace('math.pi/4', repr(math.pi / 4)))
        entry = self.call("cad.parameters('Sketch')")['result']['entries']
        angle = next(e for e in entry if e['label'] == 'Angle')
        self.assertAlmostEqual(angle['value'], 45)
        self.edit("cad.set_parameters({'angle':'1 rad'})")
        self.assertAlmostEqual(self.doc.Sketch.getDatum('Angle').Value, math.degrees(1))
        self.edit("doc.Sketch.setExpression('Constraints.Angle','60 deg')")
        surface = self.call("cad.parameters()")['result']['entries'][0]
        self.assertFalse(surface['independent'], self.doc.Sketch.ExpressionEngine)
        self.assertAlmostEqual(surface['value'], 60)
        discovery = self.call("cad.parameters('Sketch')")['result']['entries']
        self.assertTrue(next(e for e in discovery if e['label'] == 'Angle')['expression'])
        self.unchanged_failure("cad.set_parameters({'angle':90})")

    def test_geometry_filters_paging_and_stale_references(self):
        self.fixture()
        all_faces = self.call("cad.select_geometry('Box',limit=2)")['result']
        self.assertEqual((all_faces['total'], all_faces['nextOffset']), (6, 2))
        next_page = self.call("cad.select_geometry('Box',offset=2,limit=2)")['result']
        self.assertNotEqual(all_faces['items'][0]['reference'], next_page['items'][0]['reference'])
        edge = self.call("cad.select_geometry('Box',kind='edges',surface='Line',where={'length_mm':10})")['result']
        self.assertEqual(edge['total'], 12)
        self.call("cad.select_geometry('Box',where={'radius_mm':99},unique=True)", False)
        for options in ["where={'bad':1}", "where={'axis':[0,0,0]}", "where={'axis':[1,0]}",
                        "where={'area_mm2':True}", "tolerance=-1", "limit=0", "offset=-1",
                        "kind='vertices'", "unique='false'", "offset=True", "tolerance='bad'"]:
            response = self.unchanged_failure("cad.select_geometry('Box'," + options + ")", read=True)
            self.assertEqual(response['error']['type'], 'ValueError', response)
        reference = all_faces['items'][0]['reference']
        self.edit("cad.set_parameters({'length':12})")
        self.call("cad.measure(" + repr(reference) + ")", False)
        self.edit("c=doc.addObject('Part::Cylinder','Cylinder')\nc.Radius=4\nc.Height=8")
        cylinders = self.call("cad.select_geometry('Cylinder',surface='Cylinder',where={'radius_mm':4},unique=True)")['result']
        self.assertEqual(cylinders['total'], 1)
        axis = cylinders['items'][0]['axis']
        self.assertAlmostEqual(abs(axis[2]), 1)
        self.call("cad.select_geometry('Cylinder',surface='Cylinder',where={'axis':" + repr(axis) + "},unique=True)")

    def test_recipe_preflight_rolls_back_and_world_placement(self):
        self.edit("body=doc.addObject('PartDesign::Body','Body')\nbody.Placement.Base=App.Vector(30,20,10)\ns=cad.rectangle_sketch(body,'Sketch',20,10)\ncad.pad(s,'Pad',5)")
        self.assertEqual(self.doc.Sketch.DoF, 0)
        target = self.call("cad.select_geometry('Pad',surface='Plane',where={'centerOfMass_mm':[40,25,15]},unique=True)")['result']
        self.assertEqual(target['total'], 1)
        for code in ["cad.rectangle_sketch('Body','Sketch',10,5)", "cad.circle_sketch('Body','Bad',0)",
                     "cad.rectangle_sketch('Body','Bad',-2,5)", "cad.pad('Sketch','Bad',0)",
                     "cad.pocket('Body','Bad',5)", "cad.circle_sketch('Pad','Bad',4)"]:
            self.unchanged_failure(code)

    def test_evaluation_rejections_and_crashed_worker(self):
        self.fixture()
        for code in ["cad.evaluate([],timeout=60)", "cad.evaluate({},timeout=True)",
                     "cad.evaluate({},timeout=0)", "cad.evaluate({},checks={})",
                     "cad.evaluate({},target='missing')"]:
            self.unchanged_failure(code, read=True)
        self.doc.openTransaction('User native edit')
        self.doc.Box.Width = 11
        self.doc.recompute()
        try:
            self.call("cad.evaluate({})", False)
            self.assertTrue(self.doc.HasPendingTransaction)
        finally:
            self.doc.abortTransaction()
            self.doc.recompute()
        previous = os.environ.get('FREECAD_CMD')
        try:
            os.environ['FREECAD_CMD'] = '/no/such/freecadcmd'
            self.unchanged_failure("cad.evaluate({})", read=True)
            os.environ['FREECAD_CMD'] = '/usr/bin/false'
            crashed = self.unchanged_failure("cad.evaluate({})", read=True)
            self.assertIn('without a response', crashed['error']['message'])
        finally:
            if previous is None:
                os.environ.pop('FREECAD_CMD')
            else:
                os.environ['FREECAD_CMD'] = previous

    def test_local_links_and_transformed_container_evaluate(self):
        self.fixture()
        self.edit("group=doc.addObject('App::Part','Group')\ngroup.addObject(doc.Box)\ngroup.Placement.Base=App.Vector(30,0,0)\nlink=doc.addObject('App::Link','Instance')\nlink.setLink(doc.Box)\nlink.Placement.Base=App.Vector(60,0,0)")
        before = Executor._document_snapshot(self.doc)
        baseline = AnthraciteIR.compile_document(self.doc)['definitionHash']
        evaluated = self.call("cad.evaluate({'length':12},target='Box')")['result']
        self.assertTrue(evaluated['ok'], evaluated)
        self.assertAlmostEqual(evaluated['measurement']['volume_mm3'], 1200)
        self.assertEqual(evaluated['sourceDefinitionHash'], baseline)
        self.assertEqual(Executor._document_snapshot(self.doc), before)

    def test_export_formats_preserve_source_and_report_io_errors(self):
        import Part
        import Mesh
        self.fixture()
        before = Executor._document_snapshot(self.doc)
        with tempfile.TemporaryDirectory() as directory:
            for format in ('step', 'iges', 'brep', 'stl', 'obj'):
                path = str(Path(directory) / ('box.' + format))
                response = self.call("cad.export(" + repr(path) + ",objects=['Box'])")
                self.assertEqual(response['result']['path'], path)
                self.assertGreater(Path(path).stat().st_size, 100)
                if format in ('stl', 'obj'):
                    exported = Mesh.Mesh(path)
                    self.assertAlmostEqual(abs(exported.Volume), 1000, places=5)
                else:
                    exported = Part.read(path)
                    self.assertFalse(exported.isNull())
                    self.assertAlmostEqual(exported.BoundBox.XLength, 10, places=5)
                self.assertEqual(Executor._document_snapshot(self.doc), before)
            self.unchanged_failure("cad.export(" + repr(str(Path(directory) / 'unsupported.bad')) + ")", read=True)
            self.unchanged_failure("cad.export(" + repr(str(Path(directory) / 'missing' / 'box.step')) + ")", read=True)
            forced = str(Path(directory) / 'forced.data')
            self.call("cad.export(" + repr(forced) + ",format='step')")
            self.assertIn('ISO-10303-21', Path(forced).read_text())
            stale = self.call("cad.topology('Box',limit=1)")['result']['items'][0]['reference']
            self.edit("cad.set_parameters({'length':11})")
            self.unchanged_failure("cad.export(" + repr(str(Path(directory) / 'stale.brep')) + ",objects=" + repr(stale) + ")", read=True)

    def test_public_helpers_and_rendered_reviews(self):
        self.edit("body=doc.addObject('PartDesign::Body','Body')\ns=cad.circle_sketch(body,'Sketch',8)\ncad.pad(s,'Pad',5)")
        response = self.edit("cad.assert_valid()\ncad.emit('found',[x.Name for x in cad.find(type_id='PartDesign::Pad')])\n_result=[x.Name for x in cad.find(name='Pad')]")
        self.assertEqual(response['events'][0]['value'], ['Pad'])
        self.assertEqual(response['result'], ['Pad'])
        for kind in ('modules', 'workbenches', 'commands', 'types', 'helpers'):
            gui_only = kind in ('workbenches', 'commands') and not App.GuiUp
            result = self.call("cad.api(kind=" + repr(kind) + ",limit=5)", not gui_only)
            if gui_only:
                self.assertEqual(result['error']['type'], 'ValueError')
                continue
            self.assertIsInstance(result['result'], dict)
        self.call("cad.guide('authoring')")
        self.call("cad.guide('parameters')")
        for topic in ('design-intent', 'profiles', 'arcs', 'engineering'):
            self.call("cad.guide(" + repr(topic) + ")")
        helper = self.call("cad.api(kind='helpers',query='render_views')")['result']['helpers'][0]
        self.assertEqual(helper['name'], 'render_views')
        self.assertIn('width', helper['signature'])
        bodies = self.call("cad.bodies()")['result']['bodies']
        self.assertEqual((bodies[0]['name'], bodies[0]['tip']), ('Body', 'Pad'))
        reference = self.call("cad.topology('Pad',limit=1)")['result']['items'][0]['reference']
        self.edit("_result=cad.resolve_ref(" + repr(reference) + ").ShapeType")
        if not App.GuiUp:
            self.edit("_result=[x.Name for x in cad.selected()]")
            return
        import FreeCADGui as Gui
        from PySide6 import QtWidgets, QtTest
        Gui.activeDocument().activeView().viewAxonometric()
        Gui.activeDocument().activeView().fitAll()
        Gui.updateGui()
        QtWidgets.QApplication.instance().processEvents()
        QtTest.QTest.qWait(100)
        Gui.Selection.addSelection(self.doc.Name, 'Pad', 'Face1')
        selected = self.edit("_result=[x.Name for x in cad.selected()]")
        self.assertEqual(selected['result'], ['Pad'])
        self.assertTrue(self.call("cad.selection()")['result'])
        self.edit("cad.fit_view()")
        before = Executor._document_snapshot(self.doc)
        for code, count in [("cad.review('Sketch')", 1), ("cad.review('Pad')", 2),
                            ("cad.render_sketch('Sketch',width=640,height=400)", 1),
                            ("cad.render_views(['front','top'],width=200,height=150)", 2),
                            ("cad.render_views(['isometric'],width=200,height=150)", 1)]:
            result = self.edit(code)
            self.assertEqual(len(result['images']), count)
            for image in result['images']:
                self.assertTrue(base64.b64decode(image.split(',', 1)[1]).startswith(b'\x89PNG\r\n\x1a\n'))
            self.assertEqual(Executor._document_snapshot(self.doc), before)

    def test_native_limits_and_signed_offsets(self):
        self.fixture()
        self.unchanged_failure("cad.declare_parameter('negative','Box',property='Length',bounds=[-5,20])")
        self.edit("""inputs=doc.addObject('App::DocumentObjectGroup','Inputs')
inputs.addProperty('App::PropertyDistance','Offset')
inputs.Offset=-10
inputs.addProperty('App::PropertyFloatConstraint','Limited')
inputs.Limited=(5,0,10,1)
cad.declare_parameter('offset',inputs,property='Offset',bounds=[-20,0])
cad.declare_parameter('limited',inputs,property='Limited')
""")
        entries = {e['name']: e for e in self.call('cad.parameters()')['result']['entries']}
        self.assertEqual(entries['length']['nativeBounds'], [0, None])
        self.assertEqual(entries['limited']['nativeBounds'], [None, None])
        self.unchanged_failure("cad.set_parameters({'length':12,'limited':11})")
        self.assertEqual(self.doc.Box.Length.Value, 10)
        self.edit("cad.set_parameters({'offset':-15,'limited':8})")
        self.assertEqual(self.doc.Inputs.Offset.Value, -15)

    def test_perpendicular_width_and_scoped_audit(self):
        self.edit("""s=doc.addObject('Sketcher::SketchObject','Segments')
s.addGeometry(Part.LineSegment(App.Vector(0,0,0),App.Vector(2,0,0)),False)
s.addGeometry(Part.LineSegment(App.Vector(5,3,0),App.Vector(7,3,0)),False)
s.addGeometry(Part.LineSegment(App.Vector(0,0,0),App.Vector(0,2,0)),False)
body=doc.addObject('PartDesign::Body','Body')
base=cad.rectangle_sketch(body,'Base',20,10)
cad.pad(base,'BasePad',2)
upper=cad.rectangle_sketch(body,'Upper',10,5,placement=App.Placement(App.Vector(0,0,2),App.Rotation()))
cad.pad(upper,'UpperPad',3)
third=cad.circle_sketch(body,'Third',2,placement=App.Placement(App.Vector(2,2,5),App.Rotation()))
cad.pad(third,'ThirdPad',1)
opaque=doc.addObject('Part::Feature','Opaque')
opaque.Shape=Part.makeBox(1,1,1)
""")
        edges = self.call("cad.topology('Segments',kind='edges')")['result']['items']
        horizontal = [item for item in edges if abs(item['direction'][0]) > 0.99]
        a, b = [item['reference'] for item in horizontal]
        c = next(item['reference'] for item in edges if abs(item['direction'][1]) > 0.99)
        self.assertAlmostEqual(self.call('cad.measure(' + repr(a) + ')')['result']['length_mm'], 2)
        measure = self.call('cad.measure(' + repr(a) + ',other=' + repr(b) + ')')['result']
        self.assertTrue(measure['parallel'])
        self.assertAlmostEqual(measure['lineSeparation_mm'], 3)
        self.assertGreater(measure['distance_mm'], 3)
        angled = self.call('cad.measure(' + repr(a) + ',other=' + repr(c) + ')')['result']
        self.assertIsNone(angled['lineSeparation_mm'])
        scoped = self.call("cad.editability('Body')")['result']
        codes = {f['code'] for f in scoped['findings']}
        self.assertNotIn('opaque-solid', codes)
        self.assertNotIn('pattern-as-copies', codes)
        self.assertIn('opaque-solid', {f['code'] for f in self.call('cad.editability()')['result']['findings']})

    def test_expression_sketch_history_and_copy_identity(self):
        self.edit("""inputs=doc.addObject('App::DocumentObjectGroup','Inputs')
inputs.addProperty('App::PropertyLength','Width')
inputs.addProperty('App::PropertyLength','Depth')
inputs.addProperty('App::PropertyFloat','Slope')
inputs.Width=22.217
inputs.Depth=46.9
inputs.setExpression('Slope','Width / Depth')
body=doc.addObject('PartDesign::Body','Body')
s=cad.rectangle_sketch(body,'Sketch',20,10)
s.setExpression('Constraints.Width','Inputs.Width')
s.setExpression('Constraints.Height','Inputs.Depth')
cad.pad(s,'Pad',2)
doc.recompute()
external=body.newObject('Sketcher::SketchObject','External')
external.addExternal('Pad','Edge1')
external.addExternal('Pad','Edge3')
cad.declare_parameter('width',inputs,property='Width',bounds=[10,40])
cad.declare_parameter('depth',inputs,property='Depth',bounds=[20,60])
""")
        # Older native files omit projection cache indices. Reproduce that native
        # persistence here so copying must preserve identity for multiple links.
        original = self.doc.External.dumpPropertyContent('ExternalGeo', 0)
        rewritten = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(original)) as source, zipfile.ZipFile(rewritten, 'w') as output:
            for member in source.namelist():
                data = source.read(member)
                if member == 'Persistence.xml':
                    tree = ET.fromstring(data)
                    for node in tree.iter():
                        node.attrib.pop('refIndex', None)
                        node.attrib.pop('RefIndex', None)
                    data = ET.tostring(tree)
                output.writestr(member, data)
        self.doc.External.restorePropertyContent('ExternalGeo', rewritten.getvalue())
        self.doc.recompute()
        self.doc.commitTransaction()
        baseline = AnthraciteIR.compile_document(self.doc)['definitionHash']
        before = Executor._document_snapshot(self.doc)
        edited = self.edit("cad.set_parameters({'width':23.781,'depth':49.33})")
        undone = self.call(f"cad.undo(action={edited['action']['id']!r},revision={edited['revision']})")
        import AnthraciteHistory
        self.assertTrue(AnthraciteHistory.equivalent(before, Executor._document_snapshot(self.doc))[0])
        self.assertEqual(AnthraciteIR.compile_document(self.doc)['definitionHash'], baseline)
        self.call(f"cad.redo(action={edited['action']['id']!r},revision={undone['revision']})")
        snapshot = Executor._document_snapshot(self.doc)
        revision = Executor._revisions[self.doc.Name]
        projection = AnthraciteIR._native_xml(self.doc.External, 'ExternalGeo')
        candidate = self.call("cad.evaluate({'width':24},target='Pad')")['result']
        if not candidate['ok']:
            after = Executor._document_snapshot(self.doc)
            differences = [(name, prop, old, after[name]['properties'][prop])
                           for name, item in snapshot.items() for prop, old in item['properties'].items()
                           if old != after[name]['properties'][prop]]
            Path(os.environ.get('FREECAD_USER_HOME', tempfile.gettempdir()), 'evaluation-source-differences.json').write_text(json.dumps(differences, indent=2))
            Path(os.environ.get('FREECAD_USER_HOME', tempfile.gettempdir()), 'evaluation-projection-differences.json').write_text(json.dumps([projection, AnthraciteIR._native_xml(self.doc.External, 'ExternalGeo')], indent=2))
        self.assertTrue(candidate['ok'], candidate)
        self.assertTrue(AnthraciteHistory.equivalent(snapshot, Executor._document_snapshot(self.doc))[0])
        self.assertEqual(Executor._revisions[self.doc.Name], revision)
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'copy.FCStd')
            definition = AnthraciteIR.compile_document(self.doc)['definitionHash']
            self.doc.saveCopy(path)
            reopened = App.openDocument(path)
            try:
                reopened.recompute()
                self.assertEqual(AnthraciteIR.compile_document(reopened)['definitionHash'], definition)
                reopened.External.delExternal(0)
                reopened.External.addExternal('Pad','Edge2')
                reopened.recompute()
                self.assertNotEqual(AnthraciteIR.compile_document(reopened)['definitionHash'], definition)
            finally:
                App.closeDocument(reopened.Name)
                App.setActiveDocument(self.doc.Name)

    def test_evaluation_source_guard_even_on_error(self):
        self.fixture()
        import AnthraciteEvaluate
        before = Executor._revisions[self.doc.Name]
        def corrupt(*args, **kwargs):
            self.doc.Box.Length = 12
            self.doc.recompute()
            raise ValueError('Worker failed after a source mutation')
        with patch.object(AnthraciteEvaluate, 'evaluate', corrupt):
            failed = self.call('cad.evaluate({})', False)
        self.assertIn('inspect the document', failed['error']['message'])
        self.assertEqual(failed['error']['type'], 'SourceChanged')
        self.assertTrue(failed['requiresInspection'])
        self.assertFalse(failed.get('notExecuted', False))
        self.assertEqual(failed['revision'], Executor._revisions[self.doc.Name])
        self.assertGreater(Executor._revisions[self.doc.Name], before)
        self.assertNotIn(self.doc.Name, Executor._executing_docs)


try:
    if os.environ.get('ANTHRACITE_SMOKE') != '1':
        raise RuntimeError('Run with an isolated test profile')
    if App.GuiUp:
        from PySide6 import QtTest
        QtTest.QTest.qWait(1500)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(AuthorEdgeTest))
    if not result.wasSuccessful():
        raise RuntimeError('Authoring edge cases failed')
    os.write(1, b'ANTHRACITE_AUTHOR_EDGE_SMOKE_OK\n')
    os._exit(0)
except BaseException:
    os.write(2, traceback.format_exc().encode())
    os._exit(1)
