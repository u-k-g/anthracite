# SPDX-License-Identifier: LGPL-2.1-or-later
"""Real OCCT comparison regressions, usable in isolated GUI or freecadcmd."""
import os
import sys
import traceback
import unittest

sys.path.insert(0, os.environ.get("ANTHRACITE_TEST_ADDON", os.getcwd()))
import FreeCAD as App
import Part
import AnthraciteCompare as Compare
import AnthraciteHistory as History


class ComparisonTest(unittest.TestCase):
    @staticmethod
    def part():
        return Part.makeBox(20, 14, 8).cut(Part.makeCylinder(2, 12, App.Vector(4, 5, -2))).Solids[0]

    @staticmethod
    def score(candidate, reference, **options):
        return Compare.score(candidate, reference, samples=256, starts=16, **options)

    def test_forty_rigid_poses_and_input_preservation(self):
        reference = self.part()
        original = History.shape_digest(reference)
        for i in range(40):
            candidate = reference.copy()
            candidate.Placement = App.Placement(App.Vector(i-11, 20-i, -i/3),
                                                App.Rotation(App.Vector(1+i%3, 2+i%5, 3+i%7), i*9+1))
            before = History.shape_digest(candidate)
            result = self.score([candidate], [reference], mirror=False)
            self.assertTrue(result['passes'], (i, result['score']))
            self.assertAlmostEqual(result['shape']['volumeIoU'], 1, places=6)
            self.assertAlmostEqual(result['alignment']['determinant'], 1, places=6)
            self.assertEqual(result['alignment']['scale'], 1)
            self.assertEqual(result['alignment']['sampleCoverage']['candidateSamples'], 256)
            self.assertEqual(result['alignment']['sampleCoverage']['referenceSamples'], 256)
            self.assertIn('every sample', result['alignment']['rankingScope'])
            self.assertIn('worstSampleMismatches', result['alignment'])
            self.assertEqual(before, History.shape_digest(candidate))
        self.assertEqual(original, History.shape_digest(reference))

    def test_scale_is_not_fitted(self):
        reference = self.part()
        matrix = App.Matrix()
        matrix.scale(1.3, 1.3, 1.3)
        candidate = reference.transformGeometry(matrix)
        result = self.score(list(candidate.Solids), [reference], mirror=False)
        self.assertFalse(result['passes'])
        self.assertLess(result['shape']['volumeIoU'], .6)
        self.assertEqual(result['alignment']['scale'], 1)

    def test_repeated_principal_moments_do_not_break_alignment(self):
        for reference in (Part.makeBox(8, 8, 8), Part.makeCylinder(4, 13), Part.makeSphere(5)):
            candidate = reference.copy()
            candidate.Placement = App.Placement(App.Vector(11, -7, 23),
                                                App.Rotation(App.Vector(1, 3, 7), 117))
            result = self.score([candidate], [reference], mirror=False)
            self.assertTrue(result['passes'], result['alignment'])

    def test_surface_distance_is_to_boundary_not_solid_interior(self):
        outside, inside = Part.makeBox(10, 10, 10), Part.makeBox(6, 6, 6, App.Vector(2, 2, 2))
        result = Compare._surface(inside, outside, .1, 256, .216)
        self.assertEqual(result['precision'], 0)
        self.assertEqual(result['recall'], 0)
        self.assertEqual(result['f1'], 0)

    def test_missing_hole_and_oversized_hole_fail_interface(self):
        reference = self.part()
        for candidate in (Part.makeBox(20, 14, 8),
                          Part.makeBox(20, 14, 8).cut(Part.makeCylinder(3.5, 12, App.Vector(4, 5, -2)))):
            result = self.score(list(candidate.Solids), [reference], mirror=False)
            self.assertFalse(result['passes'])
            self.assertLess(result['interface']['score'], 1)
            self.assertTrue(result['interface']['jigs'])
        result = self.score([Part.makeBox(20, 14, 8)], [reference], mirror=False)
        self.assertEqual(result['topology']['candidate']['throughHoles'], 0)
        self.assertLess(result['topology']['score'], 1)

    def test_solid_betti_counts_include_periodic_surfaces_and_voids(self):
        cases = [(Part.makeSphere(3), (1, 0, 0)), (Part.makeTorus(5, 1), (1, 1, 0)),
                 (self.part(), (1, 1, 0)),
                 (Part.makeBox(10, 10, 10).cut(Part.makeBox(2, 2, 2, App.Vector(2, 2, 2))), (1, 0, 1)),
                 (Part.makeCompound([Part.makeBox(1, 1, 1), Part.makeSphere(1, App.Vector(5, 0, 0))]), (2, 0, 0))]
        for shape, expected in cases:
            result = Compare.topology(shape)
            self.assertEqual(tuple(result[k] for k in ('bodies', 'throughHoles', 'internalVoids')), expected)

    def test_chiral_mirror_is_diagnostic_and_never_accepted(self):
        reference = Part.makeBox(20, 14, 8).fuse(Part.makeBox(3, 4, 5, App.Vector(2, 1, 8)))
        reference = reference.cut(Part.makeCylinder(1.2, 20, App.Vector(15, 10, -1))).Solids[0]
        reflected = reference.copy()
        matrix = App.Matrix()
        matrix.A11 = -1
        reflected.transformShape(matrix)
        result = self.score([reflected], [reference])
        self.assertFalse(result['passes'])
        self.assertEqual(result['handedness']['status'], 'mirror mismatch')
        self.assertFalse(result['handedness']['reflectionAccepted'])
        self.assertAlmostEqual(result['handedness']['reflectedVolumeIoU'], 1, places=6)

    def test_explicit_keepout_has_opposite_material_shell(self):
        reference = self.part()
        hole = Part.makeCylinder(2, 8, App.Vector(4, 5, 0))
        shell = Part.makeCylinder(2.5, 8, App.Vector(4, 5, 0)).cut(hole)
        jigs = [{'kind': 'keep-out', 'region': hole, 'shell': shell, 'group': 'bolt'}]
        result = self.score([reference], [reference], jigs=jigs, mirror=False)
        self.assertEqual(result['interface']['score'], 1)
        result = self.score([Part.makeBox(20, 14, 8)], [reference], jigs=jigs, mirror=False)
        self.assertEqual(result['interface']['score'], 0)
        with self.assertRaisesRegex(ValueError, 'contradicts'):
            self.score([reference], [reference], jigs=[{'kind': 'keep-in', 'region': hole, 'shell': shell}])

    def test_assembly_global_motion_pairing_and_missing_extra(self):
        reference = [Part.makeBox(10, 8, 6), Part.makeBox(3, 4, 5, App.Vector(20, 2, 0))]
        pose = App.Placement(App.Vector(12, -3, 2), App.Rotation(App.Vector(1, 2, 3), 73))
        candidate = [s.copy() for s in reversed(reference)]
        for shape in candidate:
            shape.Placement = pose.multiply(shape.Placement)
        result = self.score(candidate, reference, mirror=False)
        self.assertTrue(result['passes'], result['assembly'])
        self.assertAlmostEqual(result['assembly']['score'], 1, places=5)
        self.assertEqual([(p['referencePart'], p['candidatePart']) for p in result['assembly']['pairs']], [(0, 1), (1, 0)])
        for candidate, expected in ([reference[:1], 'missing'],
                                    [reference+[Part.makeSphere(1, App.Vector(35, 2, 2))], 'extra']):
            result = self.score(candidate, reference, mirror=False)
            self.assertFalse(result['passes'])
            self.assertEqual(len(result['assembly'][expected]), 1)
            self.assertLess(result['assembly']['score'], 1)

    def test_assembly_relative_pose_uses_tolerance_units(self):
        reference = [Part.makeBox(10, 8, 6), Part.makeBox(3, 4, 5, App.Vector(20, 2, 0))]
        candidate = [s.copy() for s in reference]
        candidate[1].translate(App.Vector(0, .5, 0))
        result = self.score(candidate, reference, mirror=False)
        self.assertFalse(result['passes'])
        self.assertLess(result['assembly']['score'], .9)
        self.assertTrue(any(p['errorToleranceUnits'] > 1 for p in result['assembly']['pairs']))

    def test_assembly_six_degrees_is_one_tolerance_unit(self):
        reference = [Part.makeBox(10, 8, 6), Part.makeBox(3, 4, 5, App.Vector(20, 2, 0))]
        candidate = [s.copy() for s in reference]
        candidate[1].rotate(candidate[1].CenterOfMass, App.Vector(0, 0, 1), 6)
        pose = {'rotation': [[1, 0, 0], [0, 1, 0], [0, 0, 1]], 'translation_mm': [0, 0, 0]}
        result = Compare._assembly(candidate, reference, pose, .1, 6,
                                   {'samples': 256, 'starts': 16, 'tolerance': .1})
        changed = next(p for p in result['pairs'] if p['referencePart'] == 1)
        self.assertAlmostEqual(changed['rotationError_deg'], 6, places=5)
        self.assertAlmostEqual(changed['errorToleranceUnits'], 1, places=5)
        self.assertAlmostEqual(changed['score'], .5, places=5)

    def test_jig_search_uses_one_pose_for_a_mating_group(self):
        reference = self.part()
        hole = Part.makeCylinder(2, 8, App.Vector(4, 5, 0))
        shell = Part.makeCylinder(2.5, 8, App.Vector(4, 5, 0)).cut(hole)
        candidate = reference.copy()
        candidate.translate(App.Vector(.2, 0, 0))
        regions, source = Compare._jigs(reference, [{'kind': 'keep-out', 'region': hole, 'shell': shell}], .2)
        result = Compare._interface(candidate, regions, source, reference, 16)
        self.assertEqual(result['score'], 1)
        pose = result['poseSearch']['groupPoses']['interface']
        self.assertAlmostEqual(pose['translation_mm'][0], -.2)

    def test_invalid_options_and_empty_candidate_fail_closed(self):
        reference = self.part()
        for options in ({'tolerance': 0}, {'threshold': 1.1}, {'angle_tolerance': float('nan')},
                        {'jigs': []}, {'jig_depth': -1}):
            with self.assertRaises(ValueError):
                self.score([reference], [reference], **options)
        result = self.score([], [reference])
        self.assertEqual(result['score'], 0)
        self.assertFalse(result['passes'])


try:
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ComparisonTest))
    if not result.wasSuccessful():
        raise RuntimeError('Geometry comparison regressions failed')
    os.write(1, b'ANTHRACITE_COMPARE_SMOKE_OK\n')
    os._exit(0)
except BaseException:
    traceback.print_exc()
    os._exit(1)
