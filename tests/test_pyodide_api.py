import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from pyodide_api import run_simulation_json  # noqa: E402


class PyodideApiTests(unittest.TestCase):
    def test_directional_sparse_ir_is_exported_without_fake_auralisation(self):
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


if __name__ == "__main__":
    unittest.main()
