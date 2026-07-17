import math
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from borish_core import (  # noqa: E402
    EarlyReflectionSolver,
    ReflectorPatch,
    Scene,
    SimulationConfig,
    Triangle,
    build_impulse_response,
)


def make_box_scene(x=10.0, y=8.0, z=3.0):
    triangles = []
    patches = []

    def add_quad(points, normal):
        patch_id = len(patches)
        tri_ids = []
        for indices in ((0, 1, 2), (0, 2, 3)):
            a, b, c = [points[i] for i in indices]
            tri_id = len(triangles)
            triangles.append(Triangle(tri_id, a, b, c, patch_id))
            tri_ids.append(tri_id)
        offset = sum(points[0][i] * normal[i] for i in range(3))
        patches.append(ReflectorPatch(patch_id, normal, offset, tuple(tri_ids), (0.0,) * 8, {"name": str(patch_id)}))

    # Outward winding/normals.
    add_quad([(0, 0, 0), (0, 0, z), (0, y, z), (0, y, 0)], (-1, 0, 0))
    add_quad([(x, 0, 0), (x, y, 0), (x, y, z), (x, 0, z)], (1, 0, 0))
    add_quad([(0, 0, 0), (x, 0, 0), (x, 0, z), (0, 0, z)], (0, -1, 0))
    add_quad([(0, y, 0), (0, y, z), (x, y, z), (x, y, 0)], (0, 1, 0))
    add_quad([(0, 0, 0), (0, y, 0), (x, y, 0), (x, 0, 0)], (0, 0, -1))
    add_quad([(0, 0, z), (x, 0, z), (x, y, z), (0, y, z)], (0, 0, 1))
    return Scene(triangles, patches)


class ShoeboxTests(unittest.TestCase):
    def test_first_order_has_six_walls_plus_direct(self):
        scene = make_box_scene()
        source = (2.0, 3.0, 1.2)
        receiver = (6.0, 5.0, 1.2)
        result = EarlyReflectionSolver(
            scene,
            source,
            receiver,
            SimulationConfig(max_order=1, max_time_s=0.2, band_index=4),
        ).run()
        self.assertEqual(7, len(result.events))
        self.assertEqual([0, 1, 1, 1, 1, 1, 1], sorted(event.order for event in result.events))
        for event in result.events:
            self.assertGreaterEqual(event.arrival_time_relative_s, -1.0e-12)

    def test_first_order_image_lengths(self):
        scene = make_box_scene()
        source = (2.0, 3.0, 1.2)
        receiver = (6.0, 5.0, 1.2)
        result = EarlyReflectionSolver(
            scene, source, receiver,
            SimulationConfig(max_order=1, max_time_s=0.2, band_index=4),
        ).run()
        reflected = [event for event in result.events if event.order == 1]
        expected_images = [
            (-2.0, 3.0, 1.2), (18.0, 3.0, 1.2),
            (2.0, -3.0, 1.2), (2.0, 13.0, 1.2),
            (2.0, 3.0, -1.2), (2.0, 3.0, 4.8),
        ]
        expected_lengths = sorted(math.dist(image, receiver) for image in expected_images)
        actual_lengths = sorted(event.path_length_m for event in reflected)
        for expected, actual in zip(expected_lengths, actual_lengths):
            self.assertAlmostEqual(expected, actual, places=8)

    def test_directional_azimuth_is_relative_to_direct_sound(self):
        scene = make_box_scene()
        source = (2.0, 3.0, 1.2)
        receiver = (6.0, 5.0, 1.2)
        result = EarlyReflectionSolver(
            scene, source, receiver,
            SimulationConfig(max_order=1, max_time_s=0.2, band_index=4),
        ).run()

        direct = next(event for event in result.events if event.order == 0)
        self.assertAlmostEqual(0.0, direct.source_relative_azimuth_deg, places=12)
        for event in result.events:
            self.assertLessEqual(abs(event.source_relative_azimuth_deg), 180.0)

    def test_order_limit_reports_potential_completeness_loss(self):
        scene = make_box_scene()
        result = EarlyReflectionSolver(
            scene,
            (2.0, 3.0, 1.2),
            (6.0, 5.0, 1.2),
            SimulationConfig(max_order=1, max_time_s=0.2, band_index=4),
        ).run()

        self.assertGreater(result.stats.order_pruned_nodes, 0)

    def test_ir_has_direct_at_zero_relative_time(self):
        scene = make_box_scene()
        result = EarlyReflectionSolver(
            scene, (2.0, 3.0, 1.2), (6.0, 5.0, 1.2),
            SimulationConfig(max_order=1, max_time_s=0.2, sample_rate=48000),
        ).run()
        samples, _scale = build_impulse_response(result, reference="direct")
        self.assertGreater(samples[0], 0.0)

    def test_air_attenuation_is_relative_when_normalized_to_direct(self):
        scene = make_box_scene()
        source = (2.0, 3.0, 1.2)
        receiver = (6.0, 5.0, 1.2)
        result = EarlyReflectionSolver(
            scene,
            source,
            receiver,
            SimulationConfig(
                max_order=1,
                max_time_s=0.2,
                band_index=4,
                air_attenuation_db_per_m=1.0,
                normalize_to_direct=True,
            ),
        ).run()

        direct = next(event for event in result.events if event.order == 0)
        self.assertAlmostEqual(1.0, direct.amplitude, places=12)

        reflection = next(event for event in result.events if event.order == 1)
        expected_air_distance = reflection.path_length_m - math.dist(source, receiver)
        no_air_amplitude = math.dist(source, receiver) / reflection.path_length_m
        expected = no_air_amplitude * (10.0 ** (-expected_air_distance / 20.0))
        self.assertAlmostEqual(expected, reflection.amplitude, places=12)

    def test_point_inside_diagnostic(self):
        scene = make_box_scene()
        self.assertTrue(scene.point_inside((2.0, 3.0, 1.2)))
        self.assertTrue(scene.point_inside((6.0, 5.0, 1.2)))
        self.assertFalse(scene.point_inside((-1.0, 3.0, 1.2)))


if __name__ == "__main__":
    unittest.main()
