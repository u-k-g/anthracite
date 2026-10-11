# SPDX-License-Identifier: LGPL-2.1-or-later
"""Axis intent, requirement persistence and annotated feedback in stock FreeCAD."""
import json
import base64
import os
import tempfile
import traceback
import unittest
from pathlib import Path

import FreeCAD as App
import FreeCADGui as Gui
import AnthraciteExecutor as Executor
import AnthraciteInspect as Inspect
import AnthraciteIR as IR
import AnthraciteHistory as History


class MeasurementTest(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("Measurement")
        self.doc.UndoMode = 1
        a = self.doc.addObject("Part::Box", "A")
        b = self.doc.addObject("Part::Box", "B")
        a.Length = b.Length = 10
        a.Width = b.Width = 20
        a.Height = b.Height = 2
        b.Placement.Base = App.Vector(30, 40, 0)
        self.doc.recompute()
        # Fixture construction can join FreeCAD's application-wide auto transaction
        # left by a previous document. Finish only this test's native setup.
        if self.doc.HasPendingTransaction:
            self.doc.commitTransaction()

    def tearDown(self):
        if self.doc.Name in App.listDocuments():
            if self.doc.HasPendingTransaction:
                self.doc.abortTransaction()
            App.closeDocument(self.doc.Name)

    def call(self, code):
        result = Executor.execute(code)
        self.assertTrue(result['ok'], json.dumps(result, default=str))
        return result

    def claim(self, **kwargs):
        return {'object': 'A', 'other': 'B', 'axis': 'y', 'at': 'max', 'other_at': 'min',
                'metric': 'axisDistance_mm', 'expected': 20, 'tolerance': 1e-6, **kwargs}

    def declare(self, **kwargs):
        return self.call('cad.feedback(images=False)\ncad.declare_checks("Fit", '+repr([self.claim(**kwargs)])+')')

    def test_axis_is_not_euclidean_distance(self):
        m = self.call("cad.measure('A', 'B', axis='y', at='max', other_at='min')")['result']
        self.assertAlmostEqual(m['axisDistance_mm'], 20)
        self.assertAlmostEqual(m['axisGap_mm'], 20)
        self.assertAlmostEqual(m['axisOuterSpan_mm'], 60)
        self.assertGreater(m['distance_mm'], m['axisDistance_mm'])
        self.assertEqual(m['dimensionLineWorld_mm'], [[5., 20., 1.], [5., 40., 1.]])
        self.assertEqual(m['delta_mm'], [30., 20., 0.])
        self.assertAlmostEqual(self.call("cad.measure('B',axis='x',at='max')")['result']['extent_mm'], 40)

    def test_rotated_native_frame_and_stale_reference(self):
        self.doc.B.Placement = App.Placement(App.Vector(30, 40, 0), App.Rotation(App.Vector(0, 0, 1), 90))
        self.doc.recompute()
        result = self.call("cad.measure('B',axis='x',at='max',frame='B')")['result']
        self.assertAlmostEqual(result['extent_mm'], 10)
        self.assertEqual(result['coordinateFrame'], 'B')
        ref = Inspect.topology(self.doc, 'B', Executor._revisions.get(self.doc.Name, 0))['items'][0]['reference']
        self.call('cad.feedback(images=False)\ndoc.B.Length=12')
        self.assertFalse(Executor.execute('cad.measure('+repr(ref)+",axis='y')")['ok'])
        self.assertFalse(Executor.execute("cad.measure('A',axis='q')")['ok'])

    def test_saved_checks_report_intervening_failure_and_do_not_change_ir(self):
        before = IR.compile_document(self.doc)['definitionHash']
        self.declare()
        self.assertEqual(IR.compile_document(self.doc)['definitionHash'], before)
        self.assertEqual(self.call("cad.checks('Fit')")['result'][0]['status'], 'pass')
        result = self.call('cad.feedback(images=False)\ndoc.B.Placement.Base.y=42')
        saved = [r for r in result['verification'] if r.get('savedCheck') == 'Fit'][0]
        self.assertEqual(saved['status'], 'fail')
        self.assertAlmostEqual(saved['claims'][0]['measured'], 22)
        self.doc.B.Placement.Base.y = 43  # native/user edit
        self.doc.recompute()
        self.assertAlmostEqual(self.call("cad.checks('Fit')")['result'][0]['claims'][0]['measured'], 23)

    def test_removed_target_never_rebinds_to_next_link_or_same_name(self):
        self.declare()
        self.call("cad.feedback(images=False)\ndoc.removeObject('A')\ndoc.addObject('Part::Box','A')")
        outcome = self.call("cad.checks('Fit')")['result'][0]
        self.assertEqual(outcome['status'], 'unverifiable')
        self.assertIn('removed', outcome['reason'])

    def test_persistence_and_checked_undo_redo(self):
        self.declare()
        edit = self.call('cad.feedback(images=False)\ndoc.B.Placement.Base.y=42')
        undo = self.call(f"cad.undo(action={edit['action']['id']!r}, revision={edit['revision']})")
        self.assertEqual(self.call("cad.checks('Fit')")['result'][0]['status'], 'pass')
        self.call(f"cad.redo(action={edit['action']['id']!r}, revision={undo['revision']})")
        self.assertEqual(self.call("cad.checks('Fit')")['result'][0]['status'], 'fail')
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory)/'checks.FCStd')
            self.doc.saveCopy(path)
            App.closeDocument(self.doc.Name)
            self.doc = App.openDocument(path)
            self.doc.recompute()
            self.assertEqual(self.call("cad.checks('Fit')")['result'][0]['status'], 'fail')
            self.call('cad.feedback(images=False)\ndoc.B.Placement.Base.y=40')
            self.assertEqual(self.call("cad.checks('Fit')")['result'][0]['status'], 'pass')

    def test_preview_keeps_geometry_camera_and_selection(self):
        self.declare()
        view = Gui.activeDocument().activeView()
        camera = view.getCamera()
        before = Executor._document_snapshot(self.doc)
        stacks = History.stacks(self.doc)
        Gui.Selection.addSelection(self.doc.Name, 'A')
        result = self.call("cad.preview_checks('Fit',view='top')")
        (Path(__file__).resolve().parent.parent/'test-results/measurement-preview-pass.png').write_bytes(base64.b64decode(result['images'][0].split(',', 1)[1]))
        self.assertTrue(result['images'])
        annotations = result['observation']['renders'][0]['annotations']['items']
        self.assertEqual(annotations[0]['status'], 'pass')
        self.assertAlmostEqual(annotations[0]['measurement']['axisDistance_mm'], 20)
        self.assertEqual(Executor._document_snapshot(self.doc), before)
        self.assertEqual(History.stacks(self.doc), stacks)
        self.assertEqual(view.getCamera(), camera)
        self.assertEqual([o.Name for o in Gui.Selection.getSelection()], ['A'])
        self.call('cad.feedback(images=False)\ndoc.B.Placement.Base.y=42')
        failed = self.call("cad.preview_checks('Fit',view='top')")
        (Path(__file__).resolve().parent.parent/'test-results/measurement-preview-fail.png').write_bytes(base64.b64decode(failed['images'][0].split(',', 1)[1]))
        self.assertEqual(failed['observation']['renders'][0]['annotations']['items'][0]['status'], 'fail')

    def test_invalid_claims_and_transient_targets_do_not_persist(self):
        before = Executor._document_snapshot(self.doc)
        invalid = Executor.execute('cad.feedback(images=False)\ncad.declare_checks("Fit", '+repr([self.claim(tolerance=-1)])+')')
        self.assertFalse(invalid['ok'])
        self.assertEqual(Executor._document_snapshot(self.doc), before)
        ref = Inspect.topology(self.doc, 'A', Executor._revisions.get(self.doc.Name, 0))['items'][0]['reference']
        invalid = Executor.execute('cad.feedback(images=False)\ncad.declare_checks("Fit", '+repr([self.claim(object=ref)])+')')
        self.assertFalse(invalid['ok'])
        self.assertEqual(Executor._document_snapshot(self.doc), before)

    def test_candidate_acceptance_uses_persisted_requirements(self):
        self.call("cad.feedback(images=False)\ncad.declare_parameter('width','B',property='Width',bounds=[1,30])")
        self.declare(other_at='max', expected=40)
        before = Executor._document_snapshot(self.doc)
        result = self.call("cad.evaluate({'width':22},target='B')")['result']
        self.assertTrue(result['ok'], result)
        self.assertFalse(result['accepted'], result)
        self.assertEqual(result['verification'][0]['savedCheck'], 'Fit')
        self.assertEqual(result['verification'][0]['status'], 'fail')
        self.assertEqual(Executor._document_snapshot(self.doc), before)


try:
    if os.environ.get('ANTHRACITE_SMOKE') != '1':
        raise RuntimeError('Run with an isolated test profile')
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(MeasurementTest))
    if not result.wasSuccessful():
        raise RuntimeError('Measurement tests failed')
    os.write(1, b'ANTHRACITE_MEASUREMENT_SMOKE_OK\n')
    os._exit(0)
except BaseException:
    os.write(2, traceback.format_exc().encode())
    os._exit(1)
