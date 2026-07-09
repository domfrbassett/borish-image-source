#!/usr/bin/env python3
"""Self-contained validation report for the Borish image-source package.

The validator deliberately writes compiled bytecode into a temporary directory,
not beside the source tree.  This avoids Dropbox/OneDrive file-lock failures on
Windows.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import py_compile
import sys
import tempfile
import traceback
import unittest
import wave

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from borish_core import EarlyReflectionSolver, SimulationConfig, load_obj_scene, save_result_bundle


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def validate_event(event, speed, direct_length):
    require(event.order == len(event.patch_sequence), "order/patch ancestry mismatch")
    require(event.order == len(event.reflection_points), "order/reflection-point mismatch")
    require(len(event.path_vertices) == event.order + 2, "path vertex count mismatch")
    reconstructed = sum(
        math.dist(a, b) for a, b in zip(event.path_vertices, event.path_vertices[1:])
    )
    require(abs(reconstructed - event.path_length_m) < 1.0e-8, "stored path length mismatch")
    require(
        abs(event.arrival_time_absolute_s - event.path_length_m / speed) < 1.0e-12,
        "absolute time mismatch",
    )
    require(
        abs(event.arrival_time_relative_s - (event.path_length_m - direct_length) / speed) < 1.0e-12,
        "relative time mismatch",
    )
    require(math.isfinite(event.amplitude) and event.amplitude >= 0.0, "invalid amplitude")


def compile_sources_to_temp(directory):
    python_files = []
    for root, folder_names, file_names in os.walk(HERE):
        folder_names[:] = [name for name in folder_names if name != "__pycache__"]
        for filename in file_names:
            if filename.endswith(".py"):
                python_files.append(os.path.join(root, filename))

    for index, source_path in enumerate(sorted(python_files)):
        cfile = os.path.join(directory, "compiled_{:03d}.pyc".format(index))
        py_compile.compile(source_path, cfile=cfile, doraise=True)
    return len(python_files)


def main():
    print("BORISH VALIDATION REPORT")
    print("package_version=" + open(os.path.join(HERE, "VERSION"), encoding="utf-8").read().strip())
    print("python=" + sys.version.split()[0])
    print("platform=" + platform.platform())
    for filename in ("borish_core.py", "borish_cli.py", "rhino_pachyderm_bridge.py"):
        path = os.path.join(HERE, filename)
        print("sha256[{}]={}".format(filename, sha256(path)))

    try:
        with tempfile.TemporaryDirectory(prefix="borish_compile_") as compile_dir:
            compiled_count = compile_sources_to_temp(compile_dir)
        print("syntax=PASS python_files={}".format(compiled_count))

        suite = unittest.defaultTestLoader.discover(os.path.join(HERE, "tests"))
        result = unittest.TextTestRunner(stream=open(os.devnull, "w"), verbosity=0).run(suite)
        require(
            result.wasSuccessful(),
            "unit tests failed: failures={} errors={}".format(
                len(result.failures), len(result.errors)
            ),
        )
        print("unit_tests=PASS tests_run={}".format(result.testsRun))

        shoebox_path = os.path.join(HERE, "examples", "shoebox", "borish_test_room.obj")
        scene = load_obj_scene(shoebox_path, default_absorption=(0.0,) * 8)
        source = (2.0, 3.0, 1.2)
        receiver = (6.0, 5.0, 1.2)
        speed = 343.0
        direct_length = math.dist(source, receiver)

        require(scene.point_inside(source), "source should be inside shoebox")
        require(scene.point_inside(receiver), "receiver should be inside shoebox")
        require(not scene.point_inside((-1.0, 3.0, 1.2)), "outside point classified as inside")
        print("inside_diagnostic=PASS source=True receiver=True outside=False")

        first = EarlyReflectionSolver(
            scene,
            source,
            receiver,
            SimulationConfig(max_order=1, max_time_s=0.2, band_index=4),
        ).run(diagnose_inside=True)
        require(len(first.events) == 7, "expected 7 order-1 paths including direct")
        require(first.stats.nodes_reflected == 6, "expected 6 reflected nodes")

        expected_images = [
            (-2.0, 3.0, 1.2),
            (18.0, 3.0, 1.2),
            (2.0, -3.0, 1.2),
            (2.0, 13.0, 1.2),
            (2.0, 3.0, -1.2),
            (2.0, 3.0, 4.8),
        ]
        expected_lengths = sorted(math.dist(image, receiver) for image in expected_images)
        actual_lengths = sorted(event.path_length_m for event in first.events if event.order == 1)
        require(
            all(abs(a - b) < 1.0e-8 for a, b in zip(expected_lengths, actual_lengths)),
            "analytic image lengths differ",
        )
        for event in first.events:
            validate_event(event, speed, direct_length)
        print("order1=PASS paths=7 reflected_nodes=6 analytic_lengths=True ancestry=True")

        second = EarlyReflectionSolver(
            scene,
            source,
            receiver,
            SimulationConfig(max_order=2, max_time_s=0.2, band_index=4),
        ).run()
        require(len(second.events) == 25, "expected 25 order-2 paths including direct")
        require(second.stats.nodes_reflected == 36, "expected 36 reflected nodes")
        for event in second.events:
            validate_event(event, speed, direct_length)
        print("order2=PASS paths=25 reflected_nodes=36 ancestry=True")

        concave_path = os.path.join(
            HERE, "examples", "concave_occlusion", "borish_concave_L_room.obj"
        )
        concave_scene = load_obj_scene(concave_path, default_absorption=(0.05,) * 8)
        concave_source = (10.0, 2.0, 1.2)
        concave_receiver = (2.0, 8.0, 1.2)
        require(concave_scene.point_inside(concave_source), "concave source classified outside")
        require(concave_scene.point_inside(concave_receiver), "concave receiver classified outside")
        require(
            concave_scene.segment_blocked(
                concave_source, concave_receiver, endpoint_epsilon=1.0e-5
            ),
            "concave direct path should be blocked",
        )
        concave = EarlyReflectionSolver(
            concave_scene,
            concave_source,
            concave_receiver,
            SimulationConfig(max_order=4, max_time_s=0.150, band_index=4),
        ).run(diagnose_inside=True)
        order_counts = {}
        for event in concave.events:
            order_counts[event.order] = order_counts.get(event.order, 0) + 1
        require(order_counts == {2: 3, 3: 12, 4: 28}, "unexpected concave path counts")
        require(concave.stats.nodes_reflected == 3131, "unexpected concave node count")
        require(concave.stats.rejected_obstruction == 55, "unexpected obstruction count")
        require(concave.stats.rejected_visibility == 3033, "unexpected visibility count")
        require(not concave.stats.hit_node_limit, "concave validation hit node limit")
        print(
            "concave_occlusion=PASS direct_blocked=True paths=43 "
            "order2=3 order3=12 order4=28 rejected_obstruction=55 "
            "rejected_visibility=3033 nodes=3131"
        )

        with tempfile.TemporaryDirectory(prefix="borish_validate_") as directory:
            outputs = save_result_bundle(os.path.join(directory, "shoebox"), first, scene)
            require(
                all(os.path.isfile(path) and os.path.getsize(path) > 0 for path in outputs.values()),
                "one or more output files missing",
            )
            with wave.open(outputs["wav"], "rb") as wav:
                require(wav.getnchannels() == 1, "WAV is not mono")
                require(wav.getframerate() == 48000, "unexpected WAV sample rate")
                frames = wav.getnframes()
            with open(outputs["json"], "r", encoding="utf-8") as handle:
                ancestry = json.load(handle)
            require(len(ancestry.get("paths", [])) == 7, "JSON ancestry path count mismatch")
            with open(outputs["csv"], "r", encoding="utf-8") as handle:
                csv_rows = sum(1 for _ in handle) - 1
            require(csv_rows == 7, "CSV ancestry row count mismatch")
            print(
                "outputs=PASS wav_mono=True sample_rate=48000 frames={} "
                "json_paths=7 csv_rows=7".format(frames)
            )

        print("RESULT=PASS")
        return 0
    except Exception as exc:
        print("RESULT=FAIL error={}".format(exc))
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
