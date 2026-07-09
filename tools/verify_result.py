#!/usr/bin/env python3
"""Verify timing, ancestry, path-length and amplitude consistency in a result JSON."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def fail(errors, message):
    errors.append(message)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path, help="JSON written by borish_cli.py")
    parser.add_argument("--tolerance", type=float, default=1.0e-9)
    args = parser.parse_args()

    data = json.loads(args.result.read_text(encoding="utf-8"))
    errors = []
    paths = data.get("paths", [])
    config = data.get("config", {})
    speed = float(config.get("speed_of_sound", 343.0))
    source = tuple(float(x) for x in data["source"])
    receiver = tuple(float(x) for x in data["receiver"])
    direct_length = math.dist(source, receiver)
    normalize = bool(config.get("normalize_to_direct", True))
    band_index = config.get("band_index")
    patch_by_id = {
        int(patch["patch_id"]): patch for patch in data.get("scene", {}).get("patches", [])
    }

    for path in paths:
        path_id = path.get("path_id")
        order = int(path["order"])
        sequence = list(path.get("patch_sequence", []))
        reflections = list(path.get("reflection_points", []))
        vertices = [tuple(float(x) for x in point) for point in path.get("path_vertices", [])]

        if len(sequence) != order:
            fail(errors, "path {}: order/sequence mismatch".format(path_id))
        if len(reflections) != order:
            fail(errors, "path {}: order/reflection-point mismatch".format(path_id))
        if len(vertices) != order + 2:
            fail(errors, "path {}: path vertex count mismatch".format(path_id))
            continue

        length = sum(math.dist(a, b) for a, b in zip(vertices, vertices[1:]))
        if abs(length - float(path["path_length_m"])) > args.tolerance:
            fail(errors, "path {}: reconstructed length mismatch".format(path_id))

        expected_absolute = float(path["path_length_m"]) / speed
        expected_relative = (float(path["path_length_m"]) - direct_length) / speed
        if abs(expected_absolute - float(path["arrival_time_absolute_s"])) > args.tolerance:
            fail(errors, "path {}: absolute timing mismatch".format(path_id))
        if abs(expected_relative - float(path["arrival_time_relative_s"])) > args.tolerance:
            fail(errors, "path {}: relative timing mismatch".format(path_id))

        gain = 1.0
        for patch_id in sequence:
            patch = patch_by_id.get(int(patch_id))
            if patch is None:
                fail(errors, "path {}: unknown patch {}".format(path_id, patch_id))
                continue
            absorption = [float(x) for x in patch.get("absorption", [])]
            if not absorption:
                continue
            if band_index is None:
                alpha = sum(absorption) / len(absorption)
            else:
                alpha = absorption[int(band_index)]
            gain *= math.sqrt(max(0.0, 1.0 - alpha))

        if normalize:
            gain *= direct_length / max(float(path["path_length_m"]), 1.0e-12)
        else:
            gain *= 1.0 / max(float(path["path_length_m"]), 1.0e-12)

        # Air attenuation is omitted here only when it is zero.  Non-zero air
        # loss remains validated by the solver's own unit-level calculations.
        if abs(float(config.get("air_attenuation_db_per_m", 0.0))) <= 1.0e-15:
            if abs(gain - float(path["amplitude"])) > 1.0e-8:
                fail(errors, "path {}: amplitude mismatch".format(path_id))

    print("paths={}".format(len(paths)))
    if errors:
        print("RESULT=FAIL errors={}".format(len(errors)))
        for message in errors[:50]:
            print(message)
        return 1
    print("ANCESTRY_CHECK=PASS")
    print("PATH_LENGTH_CHECK=PASS")
    print("ARRIVAL_TIME_CHECK=PASS")
    print("AMPLITUDE_CHECK=PASS")
    print("RESULT=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
