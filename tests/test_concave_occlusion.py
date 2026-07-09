import collections
import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from borish_core import EarlyReflectionSolver, SimulationConfig, load_obj_scene  # noqa: E402


class ConcaveOcclusionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        room = os.path.join(ROOT, "examples", "concave_occlusion", "borish_concave_L_room.obj")
        cls.scene = load_obj_scene(room, default_absorption=(0.05,) * 8)
        cls.source = (10.0, 2.0, 1.2)
        cls.receiver = (2.0, 8.0, 1.2)

    def test_direct_path_is_occluded(self):
        self.assertTrue(self.scene.point_inside(self.source))
        self.assertTrue(self.scene.point_inside(self.receiver))
        self.assertTrue(
            self.scene.segment_blocked(self.source, self.receiver, endpoint_epsilon=1.0e-5)
        )

    def test_concave_reference_counts_and_paths(self):
        result = EarlyReflectionSolver(
            self.scene,
            self.source,
            self.receiver,
            SimulationConfig(
                max_order=4,
                max_time_s=0.150,
                speed_of_sound=343.0,
                band_index=4,
                max_nodes=2_000_000,
            ),
        ).run(diagnose_inside=True)

        self.assertTrue(result.source_inside_scene)
        self.assertTrue(result.receiver_inside_scene)
        self.assertFalse(any(event.order == 0 for event in result.events))
        self.assertFalse(any(event.order == 1 for event in result.events))

        counts = collections.Counter(event.order for event in result.events)
        self.assertEqual({2: 3, 3: 12, 4: 28}, dict(counts))
        self.assertEqual(43, len(result.events))
        self.assertEqual(3131, result.stats.nodes_reflected)
        self.assertEqual(55, result.stats.rejected_obstruction)
        self.assertEqual(3033, result.stats.rejected_visibility)
        self.assertFalse(result.stats.hit_node_limit)

        direct_distance = math.dist(self.source, self.receiver)
        for event in result.events:
            self.assertEqual(event.order, len(event.patch_sequence))
            self.assertEqual(event.order, len(event.reflection_points))
            self.assertEqual(event.order + 2, len(event.path_vertices))
            reconstructed = sum(
                math.dist(a, b)
                for a, b in zip(event.path_vertices, event.path_vertices[1:])
            )
            self.assertAlmostEqual(event.path_length_m, reconstructed, places=9)
            self.assertAlmostEqual(
                event.arrival_time_relative_s,
                (event.path_length_m - direct_distance) / 343.0,
                places=12,
            )
            for start, end in zip(event.path_vertices, event.path_vertices[1:]):
                self.assertFalse(
                    self.scene.segment_blocked(start, end, endpoint_epsilon=1.0e-5)
                )


if __name__ == "__main__":
    unittest.main()
