#!/usr/bin/env python3
"""Run the Borish solver and save detailed occlusion-rejection diagnostics."""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Sequence

HERE = Path(__file__).resolve().parent
# Allow this script to be placed beside or one folder away from borish_core.py.
for candidate in [HERE, HERE.parents[1], HERE.parent / "borish_pachyderm", Path.cwd()]:
    if (candidate / "borish_core.py").exists():
        sys.path.insert(0, str(candidate))
        break

from borish_core import (  # type: ignore
    EarlyReflectionSolver,
    SimulationConfig,
    load_obj_scene,
    save_result_bundle,
    v_add,
    v_distance,
    v_dot,
    v_lerp,
    v_sub,
)


def material_map(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("default_absorption", [0.05] * 8), data.get("by_material", {})


def segment_hit(scene, start, end, endpoint_epsilon):
    length = v_distance(start, end)
    if length <= endpoint_epsilon * 2.0:
        return None
    t_eps = min(0.25, endpoint_epsilon / length)
    return scene.first_segment_hit(start, end, t_min=t_eps, t_max=1.0 - t_eps)


class AuditSolver(EarlyReflectionSolver):
    """Solver subclass that retains the geometry of obstruction-rejected paths."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.obstruction_records = []

    def _reconstruct_path(self, patch_sequence: Sequence[int], image_positions):
        current = self.receiver
        reflection_points_reversed = []

        for sequence_index in range(len(patch_sequence) - 1, -1, -1):
            patch = self.scene.patch(patch_sequence[sequence_index])
            target_image = image_positions[sequence_index + 1]
            ray = v_sub(target_image, current)
            denominator = v_dot(ray, patch.normal)
            if abs(denominator) <= self.config.plane_epsilon:
                return None, "visibility"
            t = (patch.offset - v_dot(current, patch.normal)) / denominator
            if t <= self.config.plane_epsilon or t >= 1.0 - self.config.plane_epsilon:
                return None, "visibility"
            point = v_lerp(current, target_image, t)
            if not self.scene.patch_contains(patch.id, point, self.config.geometry_tolerance):
                return None, "visibility"
            reflection_points_reversed.append(point)
            current = point

        reflection_points = list(reversed(reflection_points_reversed))
        vertices = tuple([self.source] + reflection_points + [self.receiver])

        for segment_index, (start, end) in enumerate(zip(vertices, vertices[1:])):
            hit = segment_hit(self.scene, start, end, self.config.endpoint_epsilon)
            if hit is None:
                continue
            t_hit, triangle = hit
            hit_point = v_lerp(start, end, t_hit)
            blocker = self.scene.patch(triangle.patch_id)
            blocked_prefix = list(vertices[: segment_index + 1]) + [hit_point]
            self.obstruction_records.append({
                "order": len(patch_sequence),
                "patch_sequence": list(patch_sequence),
                "ancestry": [self.scene.patch(pid).metadata for pid in patch_sequence],
                "image_source_positions": [list(v) for v in image_positions],
                "reflection_points": [list(v) for v in reflection_points],
                "candidate_path_vertices": [list(v) for v in vertices],
                "blocked_prefix_vertices": [list(v) for v in blocked_prefix],
                "candidate_path_length_m": sum(v_distance(a, b) for a, b in zip(vertices, vertices[1:])),
                "blocked_segment_index": segment_index,
                "blocking_point": list(hit_point),
                "blocking_triangle_id": triangle.id,
                "blocking_patch_id": triangle.patch_id,
                "blocking_patch_metadata": blocker.metadata,
            })
            return None, "obstruction"

        return vertices, "ok"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--room", type=Path, default=HERE / "borish_concave_L_room.obj")
    parser.add_argument("--materials", type=Path, default=HERE / "borish_concave_materials.json")
    parser.add_argument("--output", type=Path, default=HERE / "concave_run")
    parser.add_argument("--diagnostics", type=Path, default=HERE / "concave_occlusion_diagnostics.json")
    parser.add_argument("--max-order", type=int, default=4)
    parser.add_argument("--max-time-ms", type=float, default=150.0)
    args = parser.parse_args()

    default_abs, by_material = material_map(args.materials)
    scene = load_obj_scene(str(args.room), default_absorption=default_abs, material_absorption=by_material)
    source = (10.0, 2.0, 1.2)
    receiver = (2.0, 8.0, 1.2)
    config = SimulationConfig(
        max_order=args.max_order,
        max_time_s=args.max_time_ms / 1000.0,
        speed_of_sound=343.0,
        sample_rate=48000,
        band_index=4,
        max_nodes=2_000_000,
        time_reference="direct",
    )
    solver = AuditSolver(scene, source, receiver, config)
    result = solver.run(diagnose_inside=True)
    outputs = save_result_bundle(str(args.output), result, scene)

    direct_hit = segment_hit(scene, source, receiver, config.endpoint_epsilon)
    direct_record = None
    if direct_hit is not None:
        t_hit, triangle = direct_hit
        point = v_lerp(source, receiver, t_hit)
        direct_record = {
            "source": list(source),
            "receiver": list(receiver),
            "blocking_point": list(point),
            "blocking_triangle_id": triangle.id,
            "blocking_patch_id": triangle.patch_id,
            "blocking_patch_metadata": scene.patch(triangle.patch_id).metadata,
        }

    rejected = sorted(solver.obstruction_records, key=lambda item: (item["candidate_path_length_m"], item["order"]))
    diagnostics = {
        "format": "borish-occlusion-audit-v1",
        "room": str(args.room),
        "source": list(source),
        "receiver": list(receiver),
        "source_inside_scene": result.source_inside_scene,
        "receiver_inside_scene": result.receiver_inside_scene,
        "direct_path_blocked": direct_record is not None,
        "direct_occlusion": direct_record,
        "solver_stats": result.stats.__dict__,
        "accepted_path_count": len(result.events),
        "obstruction_rejections": rejected,
        "outputs": outputs,
    }
    args.diagnostics.write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")

    print(f"source_inside={result.source_inside_scene}")
    print(f"receiver_inside={result.receiver_inside_scene}")
    print(f"direct_path_blocked={direct_record is not None}")
    if direct_record:
        print("direct_blocker=" + str(direct_record["blocking_patch_metadata"].get("group_name")))
        print("direct_blocking_point=" + ",".join(f"{x:.6g}" for x in direct_record["blocking_point"]))
    print(f"accepted_paths={len(result.events)}")
    print(f"rejected_obstruction={result.stats.rejected_obstruction}")
    print(f"rejected_visibility={result.stats.rejected_visibility}")
    print(f"nodes_reflected={result.stats.nodes_reflected}")
    print(f"diagnostics={args.diagnostics.resolve()}")
    for kind, path in outputs.items():
        print(f"{kind}={Path(path).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
