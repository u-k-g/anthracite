# SPDX-License-Identifier: LGPL-2.1-or-later

import unittest
import json
import tempfile
import os
import AnthraciteInspect
import AnthraciteHistory
from types import SimpleNamespace
from unittest.mock import patch

import FreeCAD as App

import AnthraciteExecutor


class ExecutorTest(unittest.TestCase):
    def test_native_validity_and_incomplete_state_diagnostics(self):
        obj = SimpleNamespace(Name='Incomplete', State=['Touched'], isValid=lambda: True,
                              getStatusString=lambda: 'Missing required input')
        errors = AnthraciteExecutor._invalid_objects(SimpleNamespace(Objects=[obj]))
        self.assertIn('Touched', errors[0]['error'])
        self.assertIn('Missing required input', errors[0]['error'])
        obj.State = []
        obj.isValid = lambda: False
        self.assertIn('Native object is invalid', AnthraciteExecutor._invalid_objects(
            SimpleNamespace(Objects=[obj]))[0]['error'])

    def test_shapeless_native_objects_are_valid(self):
        self.run_code("import Sketcher, Spreadsheet\ndoc.addObject('App::DocumentObjectGroup', 'Group')\n"
                      "doc.addObject('Sketcher::SketchObject', 'EmptySketch')\n"
                      "doc.addObject('Spreadsheet::Sheet', 'Sheet')")
        self.assertEqual(AnthraciteExecutor._invalid_objects(self.doc), [])

    def test_incomplete_pad_rolls_back_with_native_diagnostic(self):
        result = AnthraciteExecutor.execute("body = doc.addObject('PartDesign::Body', 'Body')\n"
                                           "body.newObject('PartDesign::Pad', 'MissingProfile')")
        self.assertFalse(result['ok'])
        self.assertTrue(result['rolledBack'], result)
        self.assertIn('MissingProfile', result['error']['message'])
        self.assertIsNone(self.doc.getObject('Body'))

    def test_feedback_is_per_call_and_does_not_disable_validation(self):
        with patch.object(AnthraciteExecutor, '_render_views', return_value=[]) as render:
            result = self.run_code("cad.feedback(images=False)\ndoc.addObject('Part::Box', 'Box')")
            self.assertTrue(all(not call.args[0] for call in render.call_args_list))
        self.assertEqual(result['observation']['visualFeedback']['status'], 'skipped')
        self.assertEqual(result['images'], [])
        followup = self.run_code("doc.getObject('Box').Length = 22")
        self.assertNotEqual(followup['observation'].get('visualFeedback', {}).get('status'), 'skipped')
        failed = AnthraciteExecutor.execute("cad.feedback(images=False)\ndoc.getObject('Box').Length = -1")
        self.assertFalse(failed['ok'])
        self.assertTrue(failed['rolledBack'])
        self.assertEqual(self.doc.getObject('Box').Length.Value, 22)
        with self.assertRaises(TypeError):
            AnthraciteExecutor.Cad(self.doc).feedback(images='no')

    def test_execution_stages_and_telemetry_failure_are_observational(self):
        stages = []
        result = AnthraciteExecutor.execute("cad.feedback(images=False)\ndoc.addObject('Part::Box', 'Box')",
                                           progress=stages.append)
        self.assertTrue(result['ok'], result)
        self.assertEqual(stages, ['inspection', 'python', 'recompute', 'validation', 'rendering', 'commit', 'feedback'])
        stages.clear()
        failed = AnthraciteExecutor.execute("raise ValueError('test')", progress=stages.append)
        self.assertTrue(failed['rolledBack'])
        self.assertIn('rollback', stages)
        def broken(stage):
            raise RuntimeError('telemetry unavailable')
        self.assertTrue(AnthraciteExecutor.execute("doc.getObject('Box').Length = 23", progress=broken)['ok'])

    def test_explain_follows_native_history_without_claiming_face_provenance(self):
        self.run_code(AnthraciteInspect.guide('partdesign')['code'])
        ref = self.run_code("cad.topology('BasePad')")['result']['items'][0]['reference']
        explanation = self.run_code(f"cad.explain({ref!r}, limit=1)")
        self.assertTrue(explanation['readOnly'])
        result = explanation['result']
        self.assertEqual(result['body'], 'Body')
        self.assertEqual(result['displayedTip'], 'BasePad')
        self.assertEqual(result['profile']['name'], 'BaseSketch')
        self.assertEqual(result['nextOffset'], 1)
        self.assertIn('not face-generation provenance', result['topologyPolicy'])
        self.assertEqual(self.run_code("cad.explain('Body')")['result']['feature']['name'], 'BasePad')
        self.run_code("doc.getObject('BasePad').Length = 12")
        self.assertFalse(AnthraciteExecutor.execute(f"cad.explain({ref!r})")['ok'])

    def test_frames_include_rotated_container_placement(self):
        self.run_code(AnthraciteInspect.guide('partdesign')['code'])
        self.run_code("doc.getObject('Body').Placement = App.Placement(App.Vector(30,40,50), App.Rotation(App.Vector(0,0,1),90))")
        frame = self.run_code("cad.frame('BaseSketch')")['result']
        self.assertEqual(frame['origin_world_mm'], {'x': 30.0, 'y': 40.0, 'z': 50.0})
        self.assertAlmostEqual(frame['axes_world']['x']['x'], 0, places=7)
        self.assertAlmostEqual(frame['axes_world']['x']['y'], 1, places=7)
        measured = self.run_code("cad.measure('BasePad')")['result']
        self.assertAlmostEqual(measured['centerOfMass_mm'][0], 30)
        self.assertIn('world', measured['coordinateFrame'])

    def test_intervening_changes_report_native_edits_once_and_not_agent_edits(self):
        self.run_code("doc.addObject('Part::Box', 'Box')")
        self.doc.openTransaction('Manual dimension edit')
        self.doc.getObject('Box').Length = 25
        self.doc.recompute()
        self.doc.commitTransaction()
        report = self.run_code("cad.tree()")['interveningChanges']
        entry = next(x for x in report['changes'] if x['object'] == 'Box')
        self.assertEqual(entry['properties']['Length']['before']['value'], 10)
        self.assertEqual(entry['properties']['Length']['after']['value'], 25)
        self.assertEqual(self.run_code("cad.tree()")['interveningChanges']['changes'], [])
        self.doc.undo()
        self.doc.recompute()
        self.assertTrue(self.run_code("cad.tree()")['interveningChanges']['changes'])
        self.run_code("doc.getObject('Box').Length = 30")
        self.assertEqual(self.run_code("cad.tree()")['interveningChanges']['changes'], [])

    def test_native_api_discovery_is_paged_and_does_not_invoke_methods(self):
        self.run_code(AnthraciteInspect.guide('partdesign')['code'])
        before = AnthraciteExecutor._document_snapshot(self.doc)
        result = self.run_code("cad.api('BaseSketch', kind='methods', query='setDatum', limit=1)")['result']
        self.assertEqual(result['methods'][0]['name'], 'setDatum')
        self.assertIn('documentation', result['methods'][0])
        self.assertEqual(before, AnthraciteExecutor._document_snapshot(self.doc))
        modules = self.run_code("cad.api(kind='modules')")['result']['modules']
        self.assertIn('Sketcher', modules)

    def test_editability_checks_are_explicit_and_fail_closed(self):
        self.run_code(AnthraciteInspect.guide('partdesign')['code'])
        checks = [{'object': 'Body', 'metric': 'bodyTip', 'expected': 'BasePad'},
                  {'object': 'Body', 'metric': 'nativeFeatureHistory', 'expected': True},
                  {'object': 'BaseSketch', 'metric': 'sketchDegreesOfFreedom', 'expected': 0, 'tolerance': 0}]
        self.assertEqual(self.run_code(f"cad.verify({checks!r})")['result']['status'], 'pass')
        self.run_code("opaque = doc.getObject('Body').newObject('PartDesign::Feature', 'Frozen')\nopaque.Shape = doc.getObject('BasePad').Shape.copy()\ndoc.getObject('Body').Tip = opaque")
        failed = self.run_code(f"cad.verify({checks!r})")['result']
        self.assertEqual(failed['claims'][0]['status'], 'fail')
        self.assertEqual(failed['claims'][1]['status'], 'fail')

    def test_optional_visual_feedback_failure_never_reverts_committed_edit(self):
        def unavailable(specs):
            if specs:
                raise RuntimeError('Camera unavailable')
            return []
        with patch.object(AnthraciteExecutor, 'Gui', SimpleNamespace(activeDocument=lambda: object())), \
                patch.object(AnthraciteExecutor, '_render_views', side_effect=unavailable):
            result = self.run_code("doc.addObject('Part::Box', 'Box')")
        self.assertIsNotNone(self.doc.getObject('Box'))
        self.assertTrue(result['observation']['visualFeedback']['editCommitted'])
        self.assertEqual(result['observation']['visualFeedback']['status'], 'failed')

    def test_reference_cannot_cross_document_recreation(self):
        self.run_code("doc.addObject('Part::Box', 'Box')")
        ref = self.run_code("cad.topology('Box')")['result']['items'][0]['reference']
        name = self.doc.Name
        App.closeDocument(name)
        self.doc = App.newDocument(name)
        self.run_code("doc.addObject('Part::Box', 'Box')")
        rejected = AnthraciteExecutor.execute(f"cad.measure({ref!r})")
        self.assertFalse(rejected['ok'])
        self.assertIn('Stale topology', rejected['error']['message'])

    def test_native_measurements_and_revision_bound_topology(self):
        self.run_code("doc.addObject('Part::Cylinder', 'Pin')\ndoc.getObject('Pin').Radius = 3\n"
                      "doc.addObject('Part::Box', 'Other')\ndoc.getObject('Other').Placement.Base.x = 20")
        faces = self.run_code("cad.topology('Pin', surface='Cylinder', limit=1)")['result']
        self.assertEqual(faces['total'], 1)
        self.assertAlmostEqual(faces['items'][0]['radius_mm'], 3)
        ref = faces['items'][0]['reference']
        self.assertTrue(self.run_code(f"cad.measure({ref!r})")['readOnly'])
        distance = self.run_code("cad.measure('Pin', 'Other')")['result']
        self.assertAlmostEqual(distance['distance_mm'], 17)
        self.assertEqual(distance['overlapVolume_mm3'], 0)
        self.run_code("doc.getObject('Pin').Radius = 4")
        rejected = AnthraciteExecutor.execute(f"cad.measure({ref!r})")
        self.assertFalse(rejected['ok'])
        self.assertIn('nextActions', rejected)
        current = self.run_code("cad.topology('Pin')")['result']['items'][0]['reference']
        rejected = AnthraciteExecutor.execute(f"doc.getObject('Pin').Radius = 5\ndoc.recompute()\ncad.resolve_ref({current!r})")
        self.assertFalse(rejected['ok'])
        self.assertTrue(rejected['rolledBack'])

    def test_topology_paging_and_edges(self):
        self.run_code("doc.addObject('Part::Box', 'Box')")
        result = self.run_code("cad.topology('Box', limit=2)")['result']
        self.assertEqual(result['total'], 6)
        self.assertEqual(result['nextOffset'], 2)
        second = self.run_code("cad.topology('Box', offset=2, limit=2)")['result']
        self.assertNotEqual(result['items'][0]['reference'], second['items'][0]['reference'])
        edges = self.run_code("cad.topology('Box', kind='edges')")['result']
        self.assertEqual(edges['total'], 12)

    def test_declared_checks_fail_closed_and_measure_after_recompute(self):
        self.run_code("doc.addObject('Part::Box', 'Box')")
        self.assertEqual(self.run_code("cad.verify([])")['result']['status'], 'unverifiable')
        unknown = self.run_code("cad.verify([{'object':'Box', 'metric':'imaginary', 'expected':3}])")
        self.assertEqual(unknown['result']['status'], 'unverifiable')
        edited = self.run_code("doc.getObject('Box').Length = 25\n"
            "cad.verify([{'object':'Box','metric':'size_mm','expected':[25,10,10],'tolerance':0.001}])")
        self.assertEqual(edited['verification'][0]['status'], 'pass')
        failed = self.run_code("cad.verify([{'object':'Box','metric':'size_mm','expected':[10,10,10],'tolerance':0.001}])")
        self.assertEqual(failed['result']['status'], 'fail')
        self.assertTrue(failed['readOnly'])
        self.assertIn('Box', edited['observation']['editReceipt']['beforeFinalRecompute'])
        self.assertIn('Box', edited['observation']['editReceipt']['topologyChangedObjects'])

    def test_edit_verify_undo_and_intervening_user_edit(self):
        self.run_code(AnthraciteInspect.guide('partdesign')['code'])
        edited = self.run_code("cad.action('Resize pad')\ndoc.getObject('BasePad').Length = 18\n"
            "cad.verify([{'object':'BasePad','metric':'type','expected':'PartDesign::Pad'},"
            "{'object':'BaseSketch','metric':'fullyConstrained','expected':True}])")
        self.assertEqual(edited['verification'][0]['status'], 'pass')
        undo = edited['history']['undo']
        undone = self.run_code(f"cad.undo(action={undo['action']!r}, revision={undo['revision']})")
        redo = undone['history']['redo']
        redone = self.run_code(f"cad.redo(action={redo['action']!r}, revision={redo['revision']})")
        self.assertEqual(self.doc.getObject('BasePad').Length.Value, 18)
        undo = redone['history']['undo']
        undone = self.run_code(f"cad.undo(action={undo['action']!r}, revision={undo['revision']})")
        redo = undone['history']['redo']
        self.doc.getObject('BasePad').Length = 19
        self.doc.recompute()
        blocked = AnthraciteExecutor.execute(f"cad.redo(action={redo['action']!r}, revision={redo['revision']})")
        self.assertFalse(blocked['ok'])
        self.assertIn('nextActions', blocked)
        self.assertEqual(self.doc.getObject('BasePad').Length.Value, 19)

    def test_read_helpers_do_not_create_documents_or_change_undo_mode(self):
        self.doc.UndoMode = 0
        revision = AnthraciteExecutor._revisions.get(self.doc.Name, 0)
        self.run_code("cad.tree()")
        self.assertEqual(self.doc.UndoMode, 0)
        self.assertEqual(AnthraciteExecutor._revisions.get(self.doc.Name, 0), revision)
        App.closeDocument(self.doc.Name)
        self.run_code("cad.tree()")
        self.run_code("cad.guide()")
        self.assertIsNone(App.ActiveDocument)

    def test_history_verification_never_ignores_geometry_changes(self):
        self.run_code("doc.addObject('Part::Box', 'Box')")
        before = AnthraciteExecutor._document_snapshot(self.doc)
        self.run_code("doc.getObject('Box').Length = 22")
        equivalent, _ = AnthraciteHistory.equivalent(before, AnthraciteExecutor._document_snapshot(self.doc))
        self.assertFalse(equivalent)

    def test_readable_property_paging(self):
        self.run_code("doc.addObject('Part::Box', 'Box')")
        result = self.run_code("cad.api('Box', limit=2)")["result"]
        self.assertEqual(len(result["properties"]), 2)
        self.assertEqual(result["nextOffset"], 2)

    def test_comparison_cannot_be_hidden_in_unexecuted_code(self):
        result = AnthraciteExecutor.execute("if False:\n    cad.render_views(compare=True)")
        self.assertFalse(result["ok"])
        self.assertTrue(result["rolledBack"])
        self.assertIn("final top-level statement", result["error"]["message"])

    def run_code(self, source):
        result = AnthraciteExecutor.execute(source)
        self.assertTrue(result["ok"], result)
        return result

    def test_editable_partdesign_followup_and_guarded_history(self):
        made = self.run_code("cad.action('Create editable pin')\n" + AnthraciteInspect.guide("partdesign")["code"])
        sketch = self.doc.getObject("BaseSketch")
        pad = self.doc.getObject("BasePad")
        self.assertEqual(sketch.DoF, 0)
        self.assertEqual(self.doc.getObject("Body").Tip.Name, "BasePad")
        original = pad.Shape.Volume
        changed = self.run_code("cad.action('Resize pin')\n" + AnthraciteInspect.guide("edit")["code"])
        self.assertGreater(pad.Shape.Volume, original)
        volume = pad.Shape.Volume
        info = self.run_code("cad.inspect('BasePad')")
        self.assertTrue(info["readOnly"])
        self.assertEqual(info["revision"], changed["revision"])
        self.assertEqual(info["result"]["parameters"]["Length"]["value"], 12)
        self.assertEqual(info["result"]["parameters"]["Profile"][0]["object"], "BaseSketch")
        history = info["history"]["undo"]
        undone = self.run_code(f"cad.undo(action={history['action']!r}, revision={history['revision']})")
        self.assertAlmostEqual(self.doc.getObject("BasePad").Shape.Volume, original)
        history = undone["history"]["redo"]
        redone = self.run_code(f"cad.redo(action={history['action']!r}, revision={history['revision']})")
        self.assertAlmostEqual(self.doc.getObject("BasePad").Shape.Volume, volume)
        # Intervening user changes are never silently undone, even if the model asks again.
        self.doc.openTransaction("User edit")
        self.doc.getObject("BasePad").Length = 15
        self.doc.recompute()
        self.doc.commitTransaction()
        failed = AnthraciteExecutor.execute(f"cad.undo(action={redone['history']['undo']['action']!r}, revision={redone['revision']})")
        self.assertFalse(failed["ok"])
        self.assertTrue(failed["notExecuted"])
        self.assertEqual(self.doc.getObject("BasePad").Length.Value, 15)

    def test_guides_discovery_and_editability(self):
        self.run_code(AnthraciteInspect.guide("partdesign")["code"])
        self.run_code(AnthraciteInspect.guide("expressions")["code"])
        self.assertEqual(self.doc.getObject("BasePad").Length.Value, 10)
        tree = self.run_code("cad.tree('Body')")["result"]
        self.assertIn("BasePad", [n["name"] for n in tree["nodes"]])
        sketch = self.run_code("cad.sketch('BaseSketch')")["result"]
        self.assertEqual(sketch["degreesOfFreedom"], 0)
        types = self.run_code("cad.api(query='PartDesign::Pad')")["result"]
        self.assertIn("PartDesign::Pad", types["objectTypes"])
        self.run_code("doc.getObject('BaseSketch').delConstraint(0)")
        warnings = self.run_code("cad.diagnostics('BaseSketch')")["result"]["warnings"]
        self.assertIn("underconstrained", [w["code"] for w in warnings])

    def test_native_pattern_followup_and_parameter_rollback(self):
        self.run_code(AnthraciteInspect.guide("partdesign")["code"])
        self.run_code(AnthraciteInspect.guide("patterns")["code"])
        pattern = self.doc.getObject("PinPattern")
        self.assertEqual(pattern.Occurrences, 3)
        self.assertTrue(pattern.Shape.isValid())
        self.run_code("doc.getObject('PinPattern').Occurrences = 4")
        self.assertEqual(pattern.Occurrences, 4)
        baseline = AnthraciteExecutor._document_snapshot(self.doc)
        failed = AnthraciteExecutor.execute("doc.getObject('PinPattern').Occurrences = 5\ndoc.recompute()\nraise RuntimeError('cancel')")
        self.assertFalse(failed["ok"])
        self.assertTrue(failed["rolledBack"], failed)
        equivalent, remapped = AnthraciteHistory.equivalent(baseline, AnthraciteExecutor._document_snapshot(self.doc))
        self.assertTrue(equivalent)
        self.assertEqual(failed["requiresInspection"], bool(remapped))

    def test_history_rejects_wrong_action_open_transaction_and_nested_undo(self):
        result = self.run_code("doc.addObject('Part::Box', 'Box')")
        for source in (f"cad.undo(action='wrong', revision={result['revision']})",
                       "cad.undo(action='wrong', revision=0)\ndoc.addObject('Part::Box', 'Bad')"):
            failed = AnthraciteExecutor.execute(source)
            self.assertFalse(failed["ok"])
            self.assertTrue(failed["notExecuted"])
        self.doc.openTransaction("User is editing")
        self.doc.getObject("Box").Length = 22
        failed = AnthraciteExecutor.execute("doc.addObject('Part::Box', 'Bad')")
        self.assertTrue(failed["notExecuted"])
        self.assertTrue(self.doc.HasPendingTransaction)
        self.assertEqual(failed["error"]["type"], "PendingTransaction")
        self.assertTrue(failed["pendingTransaction"]["open"])
        self.assertEqual(failed["pendingTransaction"]["name"], "User is editing")
        self.assertIsNone(self.doc.getObject("Bad"))
        codes = [action["code"] for action in failed["nextActions"]]
        self.assertIn("cad.yield_transaction(mode='commit')", codes)
        self.assertIn("cad.yield_transaction(mode='abort')", codes)
        nested = AnthraciteExecutor.execute("cad.yield_transaction(mode='abort')\ndoc.addObject('Part::Box', 'Bad')")
        self.assertTrue(nested["notExecuted"])
        self.assertTrue(self.doc.HasPendingTransaction)
        self.doc.abortTransaction()

    def test_yield_transaction_commits_or_aborts_then_allows_mutation(self):
        self.run_code("doc.addObject('Part::Box', 'Box')")
        self.assertEqual(self.doc.getObject("Box").Length.Value, 10)
        empty = AnthraciteExecutor.execute("cad.yield_transaction(mode='commit')")
        self.assertTrue(empty["notExecuted"])
        self.assertEqual(empty["error"]["type"], "ValueError")
        self.doc.openTransaction("User is editing")
        self.doc.getObject("Box").Length = 22
        aborted = self.run_code("cad.yield_transaction(mode='abort')")
        self.assertEqual(aborted["result"]["mode"], "abort")
        self.assertEqual(aborted["result"]["name"], "User is editing")
        self.assertFalse(aborted["pendingTransaction"]["open"])
        self.assertFalse(self.doc.HasPendingTransaction)
        self.assertEqual(self.doc.getObject("Box").Length.Value, 10)
        self.doc.openTransaction("Keep this")
        self.doc.getObject("Box").Length = 25
        committed = self.run_code("cad.yield_transaction(mode='commit')")
        self.assertEqual(committed["result"]["mode"], "commit")
        self.assertFalse(self.doc.HasPendingTransaction)
        self.assertEqual(self.doc.getObject("Box").Length.Value, 25)
        follow = self.run_code("doc.getObject('Box').Length = 30")
        self.assertTrue(follow["ok"])
        self.assertEqual(self.doc.getObject("Box").Length.Value, 30)
        history = self.run_code("cad.history()")["result"]
        self.assertIn("pendingTransaction", history)
        self.assertFalse(history["pendingTransaction"]["open"])

    def test_checkpoint_restores_into_separate_document(self):
        with tempfile.TemporaryDirectory() as state, patch.dict(os.environ, {"XDG_STATE_HOME": state}):
            self.run_code("doc.addObject('Part::Box', 'Box')")
            saved = self.run_code("cad.checkpoint('Before resize')")["result"]
            changed = self.run_code("doc.getObject('Box').Length = 25")
            original = self.doc
            recovered = self.run_code(f"cad.restore_checkpoint(checkpoint={saved['id']!r}, revision={changed['revision']})")
            self.assertNotEqual(recovered["result"]["document"], original.Name)
            self.assertEqual(original.getObject("Box").Length.Value, 25)
            self.assertEqual(App.ActiveDocument.getObject("Box").Length.Value, 10)
            self.assertNotEqual(App.ActiveDocument.FileName, saved["path"])
            App.closeDocument(App.ActiveDocument.Name)
            App.setActiveDocument(original.Name)

    def test_native_properties_have_stable_content_snapshots(self):
        box = self.doc.addObject("Part::Box", "Box")
        self.doc.recompute()
        before = AnthraciteExecutor._object_snapshot(box)
        self.assertEqual(before, AnthraciteExecutor._object_snapshot(box))
        result = AnthraciteExecutor.execute(
            "doc.addObject('Part::Box', 'Temporary')\nraise RuntimeError('abort')"
        )
        self.assertTrue(result["rolledBack"], result)
        self.assertEqual(before, AnthraciteExecutor._object_snapshot(box))
        box.Length = 27
        self.doc.recompute()
        self.assertNotEqual(before["properties"]["Shape"], AnthraciteExecutor._object_snapshot(box)["properties"]["Shape"])

    def test_snapshot_detects_changes_beyond_display_limit(self):
        obj = self.doc.addObject("App::FeaturePython", "Text")
        obj.addProperty("App::PropertyString", "Text")
        obj.Text = "x" * 1000 + "a"
        before = AnthraciteExecutor._object_snapshot(obj)
        obj.Text = "x" * 1000 + "b"
        self.assertNotEqual(before, AnthraciteExecutor._object_snapshot(obj))

    def setUp(self):
        for name in list(App.listDocuments()):
            App.closeDocument(name)
        self.doc = App.newDocument("AnthraciteExecutorTest")
        AnthraciteExecutor._revisions.clear()

    def tearDown(self):
        for name in list(App.listDocuments()):
            App.closeDocument(name)

    def test_success_returns_change_observation_and_final_expression(self):
        result = AnthraciteExecutor.execute(
            "\n".join(
                [
                    "obj = doc.addObject('App::FeaturePython', 'Thing')",
                    "obj.addProperty('App::PropertyInteger', 'Count')",
                    "obj.Count = 42",
                    "cad.emit('created', obj.Name)",
                    "obj.Count",
                ]
            )
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["revision"], 1)
        self.assertEqual(result["revisionBefore"], 0)
        self.assertEqual(result["result"], 42)
        self.assertEqual(result["events"], [{"label": "created", "value": "Thing"}])
        self.assertEqual(result["observation"]["selection"], [])
        self.assertEqual(
            [item["name"] for item in result["observation"]["created"]],
            ["Thing"],
        )

    def test_preflight_is_read_only_and_stale_preparation_rejects_execution(self):
        context = json.loads(AnthraciteExecutor.context_json())
        self.assertEqual(context, {"documentName": self.doc.Name, "revision": 0})
        AnthraciteExecutor.execute("doc.addObject('App::FeaturePython', 'First')")
        result = AnthraciteExecutor.execute("doc.addObject('App::FeaturePython', 'Stale')", context["revision"])
        self.assertTrue(result["conflict"])
        self.assertIsNone(self.doc.getObject("Stale"))
        App.closeDocument(self.doc.Name)
        self.assertEqual(json.loads(AnthraciteExecutor.context_json()), {"documentName": None, "revision": 0})
        self.assertEqual(len(App.listDocuments()), 0)

    def test_exception_rolls_back_entire_tool_call(self):
        result = AnthraciteExecutor.execute(
            "\n".join(
                [
                    "doc.addObject('App::FeaturePython', 'MustDisappear')",
                    "raise RuntimeError('deliberate failure')",
                ]
            )
        )

        self.assertFalse(result["ok"])
        self.assertTrue(result["rolledBack"])
        self.assertEqual(result["revision"], 0)
        self.assertIsNone(self.doc.getObject("MustDisappear"))
        self.assertEqual(result["observation"]["objectCount"], 0)

    def test_failed_rollback_is_reported_and_invalidates_revision(self):
        document = self.doc

        class FailedRollback:
            def __getattr__(self, name):
                return getattr(document, name)

            def abortTransaction(self):
                raise RuntimeError("deliberate rollback failure")

        proxy = FailedRollback()
        app = SimpleNamespace(ActiveDocument=proxy, getDocument=lambda name: proxy,
                              listDocuments=lambda: {document.Name: proxy})
        with patch.object(AnthraciteExecutor, "App", app):
            result = AnthraciteExecutor.execute(
                "doc.addObject('App::FeaturePython', 'Remaining')\n"
                "raise RuntimeError('deliberate action failure')"
            )
        self.assertFalse(result["ok"])
        self.assertFalse(result["rolledBack"])
        self.assertTrue(result["requiresInspection"])
        self.assertEqual(result["revision"], 1)
        self.assertIn("deliberate rollback failure", result["error"]["traceback"])
        self.assertIsNotNone(document.getObject("Remaining"))
        document.abortTransaction()

    def test_stale_revision_does_not_execute(self):
        first = AnthraciteExecutor.execute("doc.addObject('App::FeaturePython', 'First')")
        stale = AnthraciteExecutor.execute(
            "doc.addObject('App::FeaturePython', 'NeverCreated')",
            expected_revision=0,
        )

        self.assertTrue(first["ok"])
        self.assertFalse(stale["ok"])
        self.assertTrue(stale["conflict"])
        self.assertEqual(stale["revision"], 1)
        self.assertIsNone(self.doc.getObject("NeverCreated"))

    def test_external_edit_invalidates_agent_revision(self):
        first = AnthraciteExecutor.execute("41 + 1")
        self.doc.addObject("App::FeaturePython", "UserEdit")

        stale = AnthraciteExecutor.execute(
            "doc.addObject('App::FeaturePython', 'NeverCreated')",
            expected_revision=first["revision"],
        )

        self.assertFalse(stale["ok"])
        self.assertTrue(stale["conflict"])
        self.assertGreater(stale["revision"], first["revision"])
        self.assertIsNone(self.doc.getObject("NeverCreated"))

    def test_render_request_is_bounded_and_keeps_one_final_view(self):
        cad = AnthraciteExecutor.Cad(self.doc)
        scheduled = cad.render(
            width=800,
            height=600,
            view="front",
            fit=False,
        )

        self.assertEqual(
            scheduled,
            {
                "scheduled": True,
                "width": 800,
                "height": 600,
                "view": "front",
                "fit": False,
            },
        )
        with self.assertRaises(ValueError):
            cad.render(width=4096, height=4096)
        with self.assertRaises(ValueError):
            cad.render(view="invented")

    def test_selection_context_is_empty_without_a_gui_selection(self):
        cad = AnthraciteExecutor.Cad(self.doc)
        self.assertEqual(cad.selection(), [])

    def test_multiview_requests_are_bounded_and_replace_previous_requests(self):
        cad = AnthraciteExecutor.Cad(self.doc)
        cad.render()
        request = cad.render_views()
        self.assertEqual([s["view"] for s in request["views"]],
                         ["axonometric", "front", "right", "top"])
        self.assertIsNone(cad.render_request)
        for views in ([], ["front"] * 7, ["front", "front"], ["invented"], "front"):
            with self.assertRaises(ValueError):
                cad.render_views(views)
        with self.assertRaises(ValueError):
            cad.render_views(width=2048, height=2048)
        cad.render(view="top")
        self.assertEqual(cad.render_views_request, [])


if __name__ == "__main__":
    unittest.main()
