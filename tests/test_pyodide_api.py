import json
import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from pyodide_api import run_simulation_json  # noqa: E402


class PyodideApiTests(unittest.TestCase):
    def _shoebox_payload(self):
        absorption = [0.05] * 8
        mesh = {
            "vertices": [
                [0, 0, 0], [4, 0, 0], [4, 3, 0], [0, 3, 0],
                [0, 0, 2.5], [4, 0, 2.5], [4, 3, 2.5], [0, 3, 2.5],
            ],
            "faces": [
                {"indices": [0, 3, 7, 4], "absorption": absorption, "acoustic_material": "wall"},
                {"indices": [1, 5, 6, 2], "absorption": absorption, "acoustic_material": "wall"},
                {"indices": [0, 4, 5, 1], "absorption": absorption, "acoustic_material": "wall"},
                {"indices": [3, 2, 6, 7], "absorption": absorption, "acoustic_material": "wall"},
                {"indices": [0, 1, 2, 3], "absorption": absorption, "acoustic_material": "floor"},
                {"indices": [4, 7, 6, 5], "absorption": absorption, "acoustic_material": "ceiling"},
            ],
        }
        payload = {
            "mesh": mesh,
            "source": [1, 1, 1],
            "receiver": [3, 2, 1],
            "options": {
                "max_order": 1,
                "max_time_s": 0.05,
                "speed_of_sound": 343,
                "sample_rate": 48000,
                "max_nodes": 1000,
                "air_attenuation_db_per_m": 0,
            },
        }
        return payload

    def test_directional_sparse_ir_is_exported_without_fake_auralisation(self):
        payload = self._shoebox_payload()

        result = json.loads(run_simulation_json(json.dumps(payload)))
        self.assertIn("auralization", result)
        self.assertEqual("not_implemented", result["auralization"]["status"])
        self.assertNotIn("stereo_wav_base64", result)

        directional_ir = result["directional_ir"]
        self.assertEqual("directional_sparse_ir", directional_ir["mode"])
        self.assertEqual(48000, directional_ir["sample_rate"])
        self.assertGreater(len(directional_ir["events"]), 0)
        event = directional_ir["events"][0]
        self.assertIn("sample_position", event)
        self.assertIn("source_relative_azimuth_deg", event)
        self.assertEqual(8, len(event["band_amplitudes"]))

    def test_room_acoustic_rt_estimates_are_exported(self):
        payload = self._shoebox_payload()

        result = json.loads(run_simulation_json(json.dumps(payload)))
        decay = result["result"]["ism_decay"]
        self.assertEqual("Borish image-source Schroeder decay", decay["method"])
        self.assertEqual(8, len(decay["bands"]))
        self.assertIn("edt_s", decay["bands"][0])
        self.assertIn("t20_s", decay["bands"][0])
        self.assertIn("t30_s", decay["bands"][0])
        self.assertIn("energy_dynamic_range_db", decay["bands"][0])

        metrics = result["result"]["room_acoustics"]

        self.assertTrue(metrics["valid_for_rt_estimate"])
        self.assertAlmostEqual(30.0, metrics["volume_m3"])
        self.assertAlmostEqual(59.0, metrics["surface_area_m2"])
        first_band = metrics["octave_bands"][0]
        self.assertAlmostEqual(0.05, first_band["mean_absorption"])
        self.assertAlmostEqual(2.95, first_band["equivalent_absorption_area_m2"])
        self.assertAlmostEqual(0.161 * 30.0 / 2.95, first_band["sabine_rt60_s"])
        expected_eyring = 0.161 * 30.0 / (-59.0 * math.log(1.0 - 0.05))
        self.assertAlmostEqual(expected_eyring, first_band["eyring_rt60_s"])


if __name__ == "__main__":
    unittest.main()
