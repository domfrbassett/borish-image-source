#!/usr/bin/env python3
"""
Animate Borish early-reflection paths exported by borish_cli.py.

Examples
--------
MP4:
    python animate_reflections.py results/borish_test_room.json \
        --obj borish_test_room.obj \
        --output results/borish_test_room_animation.mp4

GIF:
    python animate_reflections.py results/borish_test_room.json \
        --obj borish_test_room.obj \
        --output results/borish_test_room_animation.gif
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Iterable

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, FFMpegWriter, PillowWriter
import numpy as np


def parse_obj(path: Path) -> tuple[np.ndarray, list[tuple[int, ...]]]:
    """Read OBJ vertices and polygon indices. Negative indices are supported."""
    vertices: list[list[float]] = []
    faces: list[tuple[int, ...]] = []

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue

            fields = line.split()
            if fields[0] == "v" and len(fields) >= 4:
                vertices.append([float(fields[1]), float(fields[2]), float(fields[3])])

            elif fields[0] == "f" and len(fields) >= 4:
                face: list[int] = []
                for token in fields[1:]:
                    raw_index = int(token.split("/")[0])
                    index = raw_index - 1 if raw_index > 0 else len(vertices) + raw_index
                    face.append(index)
                faces.append(tuple(face))

    if not vertices:
        raise ValueError(f"No OBJ vertices found in {path}")

    return np.asarray(vertices, dtype=float), faces


def unique_obj_edges(faces: Iterable[tuple[int, ...]]) -> list[tuple[int, int]]:
    """Return undirected unique polygon edges."""
    edges: set[tuple[int, int]] = set()
    for face in faces:
        for index, first in enumerate(face):
            second = face[(index + 1) % len(face)]
            edges.add(tuple(sorted((first, second))))
    return sorted(edges)


def polyline_partial(points: np.ndarray, distance: float) -> np.ndarray:
    """Return a polyline cut at a requested cumulative distance."""
    if len(points) < 2:
        return points.copy()

    segment_vectors = np.diff(points, axis=0)
    segment_lengths = np.linalg.norm(segment_vectors, axis=1)
    total = float(segment_lengths.sum())

    if distance <= 0.0:
        return points[:1].copy()
    if distance >= total:
        return points.copy()

    output = [points[0]]
    remaining = distance

    for start, vector, length in zip(points[:-1], segment_vectors, segment_lengths):
        length = float(length)
        if length <= 0.0:
            continue
        if remaining >= length:
            output.append(start + vector)
            remaining -= length
        else:
            output.append(start + vector * (remaining / length))
            break

    return np.asarray(output, dtype=float)


def set_axes_equal(ax, xyz: np.ndarray) -> None:
    """Use equal scaling in x, y and z."""
    minimum = xyz.min(axis=0)
    maximum = xyz.max(axis=0)
    centre = (minimum + maximum) / 2.0
    span = maximum - minimum
    radius = max(float(span.max()) / 2.0, 0.5) * 1.08

    ax.set_xlim(centre[0] - radius, centre[0] + radius)
    ax.set_ylim(centre[1] - radius, centre[1] + radius)
    ax.set_zlim(centre[2] - radius, centre[2] + radius)
    ax.set_box_aspect((1.0, 1.0, 1.0))


def build_animation(
    json_file: Path,
    obj_file: Path | None,
    output_file: Path,
    fps: int,
    duration: float,
    dpi: int,
) -> None:
    with json_file.open("r", encoding="utf-8") as handle:
        result = json.load(handle)

    paths = sorted(
        result["paths"],
        key=lambda item: (
            float(item["arrival_time_absolute_s"]),
            int(item["order"]),
            int(item["path_id"]),
        ),
    )
    if not paths:
        raise ValueError("The JSON contains no paths.")

    source = np.asarray(result["source"], dtype=float)
    receiver = np.asarray(result["receiver"], dtype=float)

    obj_vertices = None
    obj_edges: list[tuple[int, int]] = []
    if obj_file is not None:
        obj_vertices, obj_faces = parse_obj(obj_file)
        obj_edges = unique_obj_edges(obj_faces)

    path_arrays = [
        np.asarray(path["path_vertices"], dtype=float)
        for path in paths
    ]
    path_lengths = np.asarray(
        [float(path["path_length_m"]) for path in paths],
        dtype=float,
    )
    arrival_times = np.asarray(
        [float(path["arrival_time_absolute_s"]) for path in paths],
        dtype=float,
    )
    orders = np.asarray([int(path["order"]) for path in paths], dtype=int)

    all_xyz = np.vstack(
        ([source, receiver] + path_arrays + ([obj_vertices] if obj_vertices is not None else []))
    )

    figure = plt.figure(figsize=(10, 7.5))
    axis = figure.add_subplot(111, projection="3d")

    if obj_vertices is not None:
        for first, second in obj_edges:
            edge = obj_vertices[[first, second]]
            axis.plot(edge[:, 0], edge[:, 1], edge[:, 2], linewidth=1.0, alpha=0.65)

    axis.scatter(
        [source[0]], [source[1]], [source[2]],
        marker="*", s=150, label="Source",
    )
    axis.scatter(
        [receiver[0]], [receiver[1]], [receiver[2]],
        marker="X", s=100, label="Receiver",
    )

    ray_lines = []
    wavefront_markers = []

    for order in orders:
        linewidth = 2.8 if order == 0 else max(0.8, 2.0 - 0.35 * order)
        line, = axis.plot([], [], [], linewidth=linewidth, alpha=0.70)
        marker, = axis.plot([], [], [], linestyle="", marker="o", markersize=3.5, alpha=0.85)
        ray_lines.append(line)
        wavefront_markers.append(marker)

    time_text = axis.text2D(
        0.02, 0.96, "",
        transform=axis.transAxes,
    )
    status_text = axis.text2D(
        0.02, 0.90,
        (
            f"{len(paths)} paths: "
            f"{sum(orders == 0)} direct, "
            f"{sum(orders == 1)} first order, "
            f"{sum(orders == 2)} second order"
        ),
        transform=axis.transAxes,
    )

    axis.set_xlabel("X (m)")
    axis.set_ylabel("Y (m)")
    axis.set_zlabel("Z (m)")
    axis.set_title("Borish image-source simulation — test room")
    axis.legend(loc="upper right")
    set_axes_equal(axis, all_xyz)

    frame_count = max(2, int(round(duration * fps)))
    physical_end = float(arrival_times.max())
    hold_fraction = 0.14
    moving_frames = max(2, int(round(frame_count * (1.0 - hold_fraction))))

    def frame_time(frame_index: int) -> float:
        if frame_index >= moving_frames:
            return physical_end
        progress = frame_index / max(moving_frames - 1, 1)
        # Slightly ease the timeline so early surface interactions remain visible.
        eased = 0.5 - 0.5 * math.cos(math.pi * progress)
        return physical_end * eased

    def update(frame_index: int):
        current_time = frame_time(frame_index)

        for index, (points, full_length, arrival) in enumerate(
            zip(path_arrays, path_lengths, arrival_times)
        ):
            fraction = min(max(current_time / max(arrival, 1e-12), 0.0), 1.0)
            partial = polyline_partial(points, full_length * fraction)

            ray_lines[index].set_data_3d(
                partial[:, 0], partial[:, 1], partial[:, 2]
            )

            if fraction < 1.0:
                endpoint = partial[-1]
                wavefront_markers[index].set_data_3d(
                    [endpoint[0]], [endpoint[1]], [endpoint[2]]
                )
                ray_lines[index].set_alpha(0.72)
                wavefront_markers[index].set_alpha(0.90)
            else:
                wavefront_markers[index].set_data_3d([], [], [])
                ray_lines[index].set_alpha(0.32 if orders[index] else 0.65)

        arrived = int(np.count_nonzero(arrival_times <= current_time + 1e-12))
        time_text.set_text(
            f"Propagation time: {current_time * 1000.0:6.2f} ms\n"
            f"Paths at receiver: {arrived}/{len(paths)}"
        )

        progress = frame_index / max(frame_count - 1, 1)
        axis.view_init(elev=22.0, azim=-58.0 + 32.0 * progress)

        return [*ray_lines, *wavefront_markers, time_text, status_text]

    animation = FuncAnimation(
        figure,
        update,
        frames=frame_count,
        interval=1000.0 / fps,
        blit=False,
    )

    output_file.parent.mkdir(parents=True, exist_ok=True)
    suffix = output_file.suffix.lower()

    if suffix == ".gif":
        animation.save(
            output_file,
            writer=PillowWriter(fps=fps),
            dpi=dpi,
        )
    elif suffix == ".mp4":
        animation.save(
            output_file,
            writer=FFMpegWriter(
                fps=fps,
                codec="libx264",
                bitrate=2400,
                extra_args=["-pix_fmt", "yuv420p"],
            ),
            dpi=dpi,
        )
    else:
        raise ValueError("Output must end in .mp4 or .gif")

    plt.close(figure)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Animate Borish reflection paths from an exported JSON file."
    )
    parser.add_argument("json_file", type=Path)
    parser.add_argument("--obj", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--duration", type=float, default=8.0)
    parser.add_argument("--dpi", type=int, default=110)
    arguments = parser.parse_args()

    if arguments.fps <= 0:
        parser.error("--fps must be positive")
    if arguments.duration <= 0:
        parser.error("--duration must be positive")
    if not arguments.json_file.is_file():
        parser.error(f"JSON file not found: {arguments.json_file}")
    if arguments.obj is not None and not arguments.obj.is_file():
        parser.error(f"OBJ file not found: {arguments.obj}")

    build_animation(
        json_file=arguments.json_file,
        obj_file=arguments.obj,
        output_file=arguments.output,
        fps=arguments.fps,
        duration=arguments.duration,
        dpi=arguments.dpi,
    )
    print(f"Animation written to: {arguments.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
