#!/usr/bin/env python3
"""Standalone command-line interface for the Borish early-reflection solver.

Use this CLI with a triangulated/polygonal OBJ exported from Rhino/Pachyderm.
For direct NURBS input and interactive source/receiver placement, use
``rhino_pachyderm_bridge.py`` inside Rhino 8.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, Sequence

from borish_core import (
    OCTAVE_BANDS_HZ,
    EarlyReflectionSolver,
    SimulationConfig,
    load_obj_scene,
    save_result_bundle,
)


def _vec3(values: Sequence[str]):
    if len(values) != 3:
        raise argparse.ArgumentTypeError("Expected three coordinates")
    return tuple(float(value) for value in values)


def _load_material_map(path: str) -> tuple[Sequence[float], Dict[str, Sequence[float]]]:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    default = data.get("default_absorption", [0.05] * 8)
    by_material = data.get("by_material", {})
    return default, by_material


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Borish image-source early reflections for a closed, outward-wound OBJ room."
    )
    parser.add_argument("room", help="Closed OBJ room mesh")
    parser.add_argument("--source", nargs=3, required=True, metavar=("X", "Y", "Z"), type=float)
    parser.add_argument("--receiver", nargs=3, required=True, metavar=("X", "Y", "Z"), type=float)
    parser.add_argument("--max-order", type=int, default=3)
    parser.add_argument(
        "--max-time-ms", type=float, default=120.0,
        help="Window after direct arrival (default: 120 ms)",
    )
    parser.add_argument("--speed-of-sound", type=float, default=343.0)
    parser.add_argument("--sample-rate", type=int, default=48000)
    parser.add_argument(
        "--band", default="1000",
        help="Octave band in Hz (62.5..8000) or 'broadband' (default: 1000)",
    )
    parser.add_argument("--absorption", type=float, default=0.05, help="Default energy absorption 0..1")
    parser.add_argument("--materials", help="JSON material map; see example_materials.json")
    parser.add_argument("--air-db-per-m", type=float, default=0.0)
    parser.add_argument("--flip-normals", action="store_true")
    parser.add_argument("--max-nodes", type=int, default=2_000_000)
    parser.add_argument("--output", default="borish_early_ir", help="Output basename")
    parser.add_argument("--diagnose-inside", action="store_true")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.materials:
        default_absorption, material_map = _load_material_map(args.materials)
    else:
        default_absorption = [args.absorption] * 8
        material_map = {}

    if str(args.band).lower() in ("broadband", "average", "all"):
        band_index = None
    else:
        requested = float(args.band)
        band_index = min(range(len(OCTAVE_BANDS_HZ)), key=lambda i: abs(OCTAVE_BANDS_HZ[i] - requested))
        if abs(OCTAVE_BANDS_HZ[band_index] - requested) > 1.0e-6:
            print(f"Using nearest octave band: {OCTAVE_BANDS_HZ[band_index]:g} Hz", file=sys.stderr)

    scene = load_obj_scene(
        args.room,
        default_absorption=default_absorption,
        material_absorption=material_map,
        flip_normals=args.flip_normals,
    )
    config = SimulationConfig(
        max_order=args.max_order,
        max_time_s=args.max_time_ms / 1000.0,
        speed_of_sound=args.speed_of_sound,
        sample_rate=args.sample_rate,
        band_index=band_index,
        time_reference="direct",
        max_nodes=args.max_nodes,
        air_attenuation_db_per_m=args.air_db_per_m,
    )

    solver = EarlyReflectionSolver(scene, tuple(args.source), tuple(args.receiver), config)

    def progress(stats, order):
        print(
            f"\rnodes={stats.nodes_reflected:,} order={order} accepted={stats.accepted_reflections:,}",
            end="", file=sys.stderr, flush=True,
        )

    result = solver.run(progress_callback=progress, diagnose_inside=args.diagnose_inside)
    print(file=sys.stderr)
    outputs = save_result_bundle(args.output, result, scene)
    print(f"Scene patches: {result.scene_patch_count}")
    print(f"Scene triangles: {result.scene_triangle_count}")
    if args.diagnose_inside:
        print(f"Source inside: {result.source_inside_scene}")
        print(f"Receiver inside: {result.receiver_inside_scene}")
        if result.source_inside_scene is not True or result.receiver_inside_scene is not True:
            print(
                "WARNING: source or receiver is outside/ambiguous; inspect the closed mesh and normals.",
                file=sys.stderr,
            )
    print(f"Accepted paths: {len(result.events)} (including direct when visible)")
    print(f"Reflected nodes: {result.stats.nodes_reflected:,}")
    print(f"Rejected by visibility: {result.stats.rejected_visibility:,}")
    print(f"Rejected by obstruction: {result.stats.rejected_obstruction:,}")
    print(f"Proximity-pruned nodes: {result.stats.proximity_pruned_nodes:,}")
    if result.stats.hit_node_limit:
        print("WARNING: max-node limit reached; the result is incomplete.", file=sys.stderr)
    for kind, path in outputs.items():
        print(f"{kind.upper()}: {os.path.abspath(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
