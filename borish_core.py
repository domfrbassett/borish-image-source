"""Borish-style image-source solver for piecewise-planar closed 3-D rooms.

The module is dependency-free and is designed to run both in normal CPython and
inside Rhino 8's Python 3 runtime.  Curved NURBS geometry must be tessellated by
an adapter (see ``rhino_pachyderm_bridge.py``).

The implementation follows the main structure in Jeffrey Borish's 1984 paper:

* recursively reflect every valid virtual source across every reflecting patch;
* prune by maximum path length (and, pragmatically, maximum order/node count);
* retain invisible virtual sources for propagation;
* reconstruct a candidate path backwards with virtual listeners / image points;
* reject paths whose reflection points miss their finite patches or whose real
  path segments are obstructed.

This is a geometrical-acoustics early-reflection model.  It models specular
reflection only; diffraction, diffuse scattering, phase changes, wave effects,
and source/receiver directivity are outside its scope.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
import csv
import json
import math
import os
import struct
import wave

Vec3 = Tuple[float, float, float]
OCTAVE_BANDS_HZ: Tuple[float, ...] = (62.5, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0)


# ---------------------------------------------------------------------------
# Vector utilities
# ---------------------------------------------------------------------------

def v_add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def v_sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def v_mul(a: Vec3, s: float) -> Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


def v_dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def v_cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def v_length_sq(a: Vec3) -> float:
    return v_dot(a, a)


def v_length(a: Vec3) -> float:
    return math.sqrt(v_length_sq(a))


def v_distance(a: Vec3, b: Vec3) -> float:
    return v_length(v_sub(a, b))


def v_normalize(a: Vec3, eps: float = 1.0e-15) -> Vec3:
    length = v_length(a)
    if length <= eps:
        raise ValueError("Cannot normalize a zero-length vector")
    return v_mul(a, 1.0 / length)


def v_lerp(a: Vec3, b: Vec3, t: float) -> Vec3:
    return (
        a[0] + (b[0] - a[0]) * t,
        a[1] + (b[1] - a[1]) * t,
        a[2] + (b[2] - a[2]) * t,
    )


def _vec_to_list(v: Vec3) -> List[float]:
    return [float(v[0]), float(v[1]), float(v[2])]


# ---------------------------------------------------------------------------
# Geometry data
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Triangle:
    id: int
    a: Vec3
    b: Vec3
    c: Vec3
    patch_id: int

    @property
    def bounds_min(self) -> Vec3:
        return (
            min(self.a[0], self.b[0], self.c[0]),
            min(self.a[1], self.b[1], self.c[1]),
            min(self.a[2], self.b[2], self.c[2]),
        )

    @property
    def bounds_max(self) -> Vec3:
        return (
            max(self.a[0], self.b[0], self.c[0]),
            max(self.a[1], self.b[1], self.c[1]),
            max(self.a[2], self.b[2], self.c[2]),
        )

    @property
    def centroid(self) -> Vec3:
        return (
            (self.a[0] + self.b[0] + self.c[0]) / 3.0,
            (self.a[1] + self.b[1] + self.c[1]) / 3.0,
            (self.a[2] + self.b[2] + self.c[2]) / 3.0,
        )


@dataclass
class ReflectorPatch:
    """A finite planar reflector made from one or more coplanar triangles.

    ``normal`` must point toward the non-reflective side (outward from a closed
    room).  With the plane equation ``dot(x, normal) = offset``, an interior
    source must satisfy ``offset - dot(source, normal) > 0``.
    """

    id: int
    normal: Vec3
    offset: float
    triangle_ids: Tuple[int, ...]
    absorption: Tuple[float, ...] = (0.05,) * 8
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.normal = v_normalize(tuple(float(x) for x in self.normal))  # type: ignore[assignment]
        if len(self.absorption) == 1:
            self.absorption = tuple(float(self.absorption[0]) for _ in OCTAVE_BANDS_HZ)
        elif len(self.absorption) != len(OCTAVE_BANDS_HZ):
            raise ValueError("Absorption must contain one value or eight octave-band values")
        self.absorption = tuple(min(1.0, max(0.0, float(a))) for a in self.absorption)

    def reflection_pressure(self, band_index: Optional[int]) -> float:
        """Return a pressure reflection coefficient from energy absorption."""
        if band_index is None:
            alpha = sum(self.absorption) / len(self.absorption)
        else:
            alpha = self.absorption[band_index]
        return math.sqrt(max(0.0, 1.0 - alpha))


@dataclass
class _BVHNode:
    bounds_min: Vec3
    bounds_max: Vec3
    triangle_ids: Optional[Tuple[int, ...]] = None
    left: Optional["_BVHNode"] = None
    right: Optional["_BVHNode"] = None

    @property
    def is_leaf(self) -> bool:
        return self.triangle_ids is not None


class Scene:
    """Triangle scene plus planar reflection patches and an internal BVH."""

    def __init__(
        self,
        triangles: Sequence[Triangle],
        patches: Sequence[ReflectorPatch],
        *,
        bvh_leaf_size: int = 8,
    ) -> None:
        if not triangles:
            raise ValueError("A scene needs at least one triangle")
        if not patches:
            raise ValueError("A scene needs at least one reflector patch")

        self.triangles: Tuple[Triangle, ...] = tuple(triangles)
        self.patches: Tuple[ReflectorPatch, ...] = tuple(patches)
        self._triangle_by_id = {t.id: t for t in self.triangles}
        self._patch_by_id = {p.id: p for p in self.patches}

        if len(self._triangle_by_id) != len(self.triangles):
            raise ValueError("Triangle IDs must be unique")
        if len(self._patch_by_id) != len(self.patches):
            raise ValueError("Patch IDs must be unique")
        for patch in self.patches:
            for tri_id in patch.triangle_ids:
                tri = self._triangle_by_id.get(tri_id)
                if tri is None:
                    raise ValueError(f"Patch {patch.id} references missing triangle {tri_id}")
                if tri.patch_id != patch.id:
                    raise ValueError(f"Triangle {tri.id} / patch {patch.id} mismatch")

        self._bvh_leaf_size = max(1, int(bvh_leaf_size))
        self._bvh = self._build_bvh(tuple(t.id for t in self.triangles))

    def patch(self, patch_id: int) -> ReflectorPatch:
        return self._patch_by_id[patch_id]

    def triangle(self, triangle_id: int) -> Triangle:
        return self._triangle_by_id[triangle_id]

    def _build_bvh(self, triangle_ids: Tuple[int, ...]) -> _BVHNode:
        bounds_min, bounds_max = _bounds_for_triangles(self._triangle_by_id, triangle_ids)
        if len(triangle_ids) <= self._bvh_leaf_size:
            return _BVHNode(bounds_min, bounds_max, triangle_ids=triangle_ids)

        extents = v_sub(bounds_max, bounds_min)
        axis = max(range(3), key=lambda i: extents[i])
        sorted_ids = sorted(triangle_ids, key=lambda tid: self._triangle_by_id[tid].centroid[axis])
        middle = len(sorted_ids) // 2
        if middle <= 0 or middle >= len(sorted_ids):
            return _BVHNode(bounds_min, bounds_max, triangle_ids=triangle_ids)
        left = self._build_bvh(tuple(sorted_ids[:middle]))
        right = self._build_bvh(tuple(sorted_ids[middle:]))
        return _BVHNode(bounds_min, bounds_max, left=left, right=right)

    def patch_contains(self, patch_id: int, point: Vec3, tolerance: float) -> bool:
        patch = self.patch(patch_id)
        # Plane test first.  Tolerance is in model units.
        if abs(v_dot(point, patch.normal) - patch.offset) > max(tolerance, 1.0e-10):
            return False
        for triangle_id in patch.triangle_ids:
            if _point_in_triangle_3d(point, self.triangle(triangle_id), tolerance):
                return True
        return False

    def first_segment_hit(
        self,
        start: Vec3,
        end: Vec3,
        *,
        t_min: float = 0.0,
        t_max: float = 1.0,
        ignored_patch_ids: Optional[Iterable[int]] = None,
    ) -> Optional[Tuple[float, Triangle]]:
        ignored = set(ignored_patch_ids or ())
        direction = v_sub(end, start)
        if v_length_sq(direction) <= 1.0e-30:
            return None

        best_t = t_max
        best_triangle: Optional[Triangle] = None
        stack = [self._bvh]
        while stack:
            node = stack.pop()
            if not _segment_intersects_aabb(start, direction, node.bounds_min, node.bounds_max, t_min, best_t):
                continue
            if node.is_leaf:
                assert node.triangle_ids is not None
                for tri_id in node.triangle_ids:
                    tri = self.triangle(tri_id)
                    if tri.patch_id in ignored:
                        continue
                    hit_t = _segment_triangle_t(start, direction, tri, t_min, best_t)
                    if hit_t is not None and hit_t < best_t:
                        best_t = hit_t
                        best_triangle = tri
            else:
                if node.left is not None:
                    stack.append(node.left)
                if node.right is not None:
                    stack.append(node.right)

        if best_triangle is None:
            return None
        return best_t, best_triangle

    def segment_blocked(
        self,
        start: Vec3,
        end: Vec3,
        *,
        endpoint_epsilon: float,
    ) -> bool:
        length = v_distance(start, end)
        if length <= endpoint_epsilon * 2.0:
            return False
        t_epsilon = min(0.25, endpoint_epsilon / length)
        return self.first_segment_hit(start, end, t_min=t_epsilon, t_max=1.0 - t_epsilon) is not None

    def point_inside(self, point: Vec3, *, tolerance: float = 1.0e-7) -> bool:
        """Odd/even ray test for a closed, consistently oriented triangle shell.

        The test is intentionally used only as a diagnostic; it is not part of
        candidate generation.  A non-manifold mesh or a point on the boundary
        can make any parity test ambiguous.
        """
        ray_end = (point[0] + 1.0e9, point[1] + 12345.6789, point[2] + 9876.5432)
        hits = 0
        cursor = point
        remaining_start_t = 0.0
        # Repeatedly find the next intersection.  Move past each hit to avoid
        # counting a shared triangle edge twice.
        for _ in range(len(self.triangles) + 1):
            # ``first_segment_hit`` expects a parametric segment fraction.
            # Convert the world-space tolerance to that fraction; using the
            # raw tolerance here would skip nearby room boundaries because
            # the diagnostic ray is intentionally very long.
            remaining_length = v_distance(cursor, ray_end)
            if remaining_length <= tolerance:
                break
            t_min = min(0.25, tolerance / remaining_length)
            hit = self.first_segment_hit(cursor, ray_end, t_min=t_min, t_max=1.0)
            if hit is None:
                break
            t, _tri = hit
            hits += 1
            hit_point = v_lerp(cursor, ray_end, t)
            direction = v_normalize(v_sub(ray_end, cursor))
            cursor = v_add(hit_point, v_mul(direction, tolerance * 10.0))
            remaining_start_t = t
            if remaining_start_t >= 1.0:
                break
        return (hits % 2) == 1


# ---------------------------------------------------------------------------
# Simulation data
# ---------------------------------------------------------------------------

@dataclass
class SimulationConfig:
    max_order: int = 3
    max_time_s: float = 0.120
    speed_of_sound: float = 343.0
    sample_rate: int = 48000
    band_index: Optional[int] = 4  # 1 kHz; None means average absorption
    include_direct: bool = True
    time_reference: str = "direct"  # "direct" or "absolute"
    plane_epsilon: float = 1.0e-8
    geometry_tolerance: float = 1.0e-6
    endpoint_epsilon: float = 1.0e-5
    max_nodes: int = 2_000_000
    air_attenuation_db_per_m: float = 0.0
    normalize_to_direct: bool = True
    two_sided_reflectors: bool = False  # diagnostic mode for open/inconsistently wound meshes

    def validate(self) -> None:
        if self.max_order < 0:
            raise ValueError("max_order must be non-negative")
        if self.max_time_s < 0.0:
            raise ValueError("max_time_s must be non-negative")
        if self.speed_of_sound <= 0.0:
            raise ValueError("speed_of_sound must be positive")
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if self.band_index is not None and not 0 <= self.band_index < len(OCTAVE_BANDS_HZ):
            raise ValueError("band_index must be 0..7 or None")
        if self.time_reference not in ("direct", "absolute"):
            raise ValueError("time_reference must be 'direct' or 'absolute'")
        if self.max_nodes <= 0:
            raise ValueError("max_nodes must be positive")


@dataclass
class ReflectionEvent:
    path_id: int
    order: int
    patch_sequence: Tuple[int, ...]
    image_source_positions: Tuple[Vec3, ...]
    reflection_points: Tuple[Vec3, ...]
    path_vertices: Tuple[Vec3, ...]
    path_length_m: float
    arrival_time_absolute_s: float
    arrival_time_relative_s: float
    amplitude: float
    direction_of_arrival: Vec3
    azimuth_deg: float
    elevation_deg: float
    source_relative_azimuth_deg: float


@dataclass
class SimulationStats:
    nodes_reflected: int = 0
    invalid_nodes: int = 0
    proximity_pruned_nodes: int = 0
    visible_candidates: int = 0
    rejected_visibility: int = 0
    rejected_obstruction: int = 0
    accepted_reflections: int = 0
    order_pruned_nodes: int = 0
    hit_node_limit: bool = False


@dataclass
class SimulationResult:
    source: Vec3
    receiver: Vec3
    config: SimulationConfig
    events: List[ReflectionEvent]
    stats: SimulationStats
    scene_patch_count: int
    scene_triangle_count: int
    source_inside_scene: Optional[bool] = None
    receiver_inside_scene: Optional[bool] = None

    def sorted_events(self) -> List[ReflectionEvent]:
        return sorted(self.events, key=lambda e: (e.arrival_time_relative_s, e.order, e.path_length_m))


class SimulationCancelled(RuntimeError):
    pass


ProgressCallback = Callable[[SimulationStats, int], None]
CancelCallback = Callable[[], bool]


class EarlyReflectionSolver:
    """Depth-first Borish image-source traversal."""

    def __init__(self, scene: Scene, source: Vec3, receiver: Vec3, config: Optional[SimulationConfig] = None) -> None:
        self.scene = scene
        self.source = tuple(float(x) for x in source)  # type: ignore[assignment]
        self.receiver = tuple(float(x) for x in receiver)  # type: ignore[assignment]
        self.config = config or SimulationConfig()
        self.config.validate()
        self.stats = SimulationStats()
        self._events: List[ReflectionEvent] = []
        self._direct_distance = v_distance(self.source, self.receiver)
        if self.config.time_reference == "direct":
            self._max_path_length = self._direct_distance + self.config.max_time_s * self.config.speed_of_sound
        else:
            self._max_path_length = self.config.max_time_s * self.config.speed_of_sound
        self._progress_callback: Optional[ProgressCallback] = None
        self._cancel_callback: Optional[CancelCallback] = None
        self._last_progress_node = 0

    def run(
        self,
        *,
        progress_callback: Optional[ProgressCallback] = None,
        cancel_callback: Optional[CancelCallback] = None,
        diagnose_inside: bool = False,
    ) -> SimulationResult:
        self._progress_callback = progress_callback
        self._cancel_callback = cancel_callback
        self.stats = SimulationStats()
        self._events = []

        source_inside = self.scene.point_inside(self.source, tolerance=self.config.geometry_tolerance) if diagnose_inside else None
        receiver_inside = self.scene.point_inside(self.receiver, tolerance=self.config.geometry_tolerance) if diagnose_inside else None

        if (
            self.config.include_direct
            and self._direct_distance <= self._max_path_length + self.config.geometry_tolerance
            and not self.scene.segment_blocked(
                self.source, self.receiver, endpoint_epsilon=self.config.endpoint_epsilon
            )
        ):
            direction = v_normalize(v_sub(self.source, self.receiver)) if self._direct_distance > 0.0 else (0.0, 0.0, 0.0)
            azimuth, elevation = _azimuth_elevation(direction)
            source_relative_azimuth = _relative_azimuth_deg(direction, direction)
            if self.config.normalize_to_direct:
                direct_amplitude = 1.0
            else:
                direct_amplitude = 1.0 / max(self._direct_distance, 1.0e-12)
                direct_amplitude *= _air_pressure_gain(self._direct_distance, self.config.air_attenuation_db_per_m)
            self._events.append(
                ReflectionEvent(
                    path_id=-1,
                    order=0,
                    patch_sequence=(),
                    image_source_positions=(self.source,),
                    reflection_points=(),
                    path_vertices=(self.source, self.receiver),
                    path_length_m=self._direct_distance,
                    arrival_time_absolute_s=self._direct_distance / self.config.speed_of_sound,
                    arrival_time_relative_s=0.0,
                    amplitude=direct_amplitude,
                    direction_of_arrival=direction,
                    azimuth_deg=azimuth,
                    elevation_deg=elevation,
                    source_relative_azimuth_deg=source_relative_azimuth,
                )
            )

        if self.config.max_order > 0:
            self._propagate(parent_image=self.source, patch_sequence=[], image_positions=[self.source])

        sorted_events = sorted(self._events, key=lambda e: (e.arrival_time_relative_s, e.order, e.path_length_m))
        for path_id, event in enumerate(sorted_events):
            event.path_id = path_id

        return SimulationResult(
            source=self.source,
            receiver=self.receiver,
            config=self.config,
            events=sorted_events,
            stats=self.stats,
            scene_patch_count=len(self.scene.patches),
            scene_triangle_count=len(self.scene.triangles),
            source_inside_scene=source_inside,
            receiver_inside_scene=receiver_inside,
        )

    def _propagate(self, parent_image: Vec3, patch_sequence: List[int], image_positions: List[Vec3]) -> None:
        if len(patch_sequence) >= self.config.max_order:
            if v_distance(parent_image, self.receiver) <= self._max_path_length + self.config.geometry_tolerance:
                self.stats.order_pruned_nodes += 1
            return

        for patch in self.scene.patches:
            if self.stats.nodes_reflected >= self.config.max_nodes:
                self.stats.hit_node_limit = True
                return
            if patch_sequence and patch.id == patch_sequence[-1]:
                # Reflecting an image immediately back across the same plane is
                # necessarily invalid with correctly oriented room normals.
                continue

            if self._cancel_callback is not None and self.stats.nodes_reflected % 2048 == 0:
                if self._cancel_callback():
                    raise SimulationCancelled("Simulation cancelled by user")

            d = patch.offset - v_dot(parent_image, patch.normal)
            if self.config.two_sided_reflectors:
                # Diagnostic open-mesh mode: reflect from either side of a patch.
                # This helps with imported render meshes that are open or inconsistently wound.
                # It is not a physically validated closed-enclosure assumption.
                if abs(d) <= self.config.plane_epsilon:
                    self.stats.invalid_nodes += 1
                    continue
            else:
                # Normal closed-room validity test. With outward normals, an interior
                # image source satisfies offset - dot(P, n) > 0.
                if d <= self.config.plane_epsilon:
                    self.stats.invalid_nodes += 1
                    continue

            new_image = v_add(parent_image, v_mul(patch.normal, 2.0 * d))
            self.stats.nodes_reflected += 1
            if v_distance(new_image, self.receiver) > self._max_path_length + self.config.geometry_tolerance:
                self.stats.proximity_pruned_nodes += 1
                self._emit_progress(len(patch_sequence) + 1)
                continue

            new_sequence = patch_sequence + [patch.id]
            new_images = image_positions + [new_image]
            path, rejection = self._reconstruct_path(new_sequence, new_images)
            if path is not None:
                self.stats.visible_candidates += 1
                event = self._make_event(new_sequence, new_images, path)
                if event is not None:
                    self._events.append(event)
                    self.stats.accepted_reflections += 1
            elif rejection == "obstruction":
                self.stats.rejected_obstruction += 1
            else:
                self.stats.rejected_visibility += 1

            self._emit_progress(len(new_sequence))
            # Important Borish detail: invisible nodes are retained for
            # propagation; descendants can become visible.
            self._propagate(new_image, new_sequence, new_images)
            if self.stats.hit_node_limit:
                return

    def _emit_progress(self, order: int) -> None:
        if self._progress_callback is None:
            return
        if self.stats.nodes_reflected - self._last_progress_node >= 4096:
            self._last_progress_node = self.stats.nodes_reflected
            self._progress_callback(self.stats, order)

    def _reconstruct_path(
        self,
        patch_sequence: Sequence[int],
        image_positions: Sequence[Vec3],
    ) -> Tuple[Optional[Tuple[Vec3, ...]], str]:
        current = self.receiver
        reflection_points_reversed: List[Vec3] = []

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
        vertices: Tuple[Vec3, ...] = tuple([self.source] + reflection_points + [self.receiver])
        for start, end in zip(vertices, vertices[1:]):
            if self.scene.segment_blocked(start, end, endpoint_epsilon=self.config.endpoint_epsilon):
                return None, "obstruction"
        return vertices, "ok"

    def _make_event(
        self,
        patch_sequence: Sequence[int],
        image_positions: Sequence[Vec3],
        path_vertices: Sequence[Vec3],
    ) -> Optional[ReflectionEvent]:
        path_length = sum(v_distance(a, b) for a, b in zip(path_vertices, path_vertices[1:]))
        if path_length > self._max_path_length + self.config.geometry_tolerance:
            return None

        reflection_gain = 1.0
        for patch_id in patch_sequence:
            reflection_gain *= self.scene.patch(patch_id).reflection_pressure(self.config.band_index)

        if self.config.normalize_to_direct:
            spreading_gain = self._direct_distance / max(path_length, 1.0e-12)
            air_distance = max(0.0, path_length - self._direct_distance)
        else:
            spreading_gain = 1.0 / max(path_length, 1.0e-12)
            air_distance = path_length
        amplitude = spreading_gain * reflection_gain * _air_pressure_gain(
            air_distance, self.config.air_attenuation_db_per_m
        )

        absolute_time = path_length / self.config.speed_of_sound
        relative_time = (path_length - self._direct_distance) / self.config.speed_of_sound
        if relative_time < -self.config.geometry_tolerance / self.config.speed_of_sound:
            # A valid specular path cannot precede the Euclidean direct path.
            return None
        relative_time = max(0.0, relative_time)

        previous_point = path_vertices[-2]
        direction = v_normalize(v_sub(previous_point, self.receiver))
        azimuth, elevation = _azimuth_elevation(direction)
        direct_direction = v_normalize(v_sub(self.source, self.receiver)) if self._direct_distance > 0.0 else direction
        source_relative_azimuth = _relative_azimuth_deg(direct_direction, direction)

        return ReflectionEvent(
            path_id=-1,
            order=len(patch_sequence),
            patch_sequence=tuple(patch_sequence),
            image_source_positions=tuple(image_positions),
            reflection_points=tuple(path_vertices[1:-1]),
            path_vertices=tuple(path_vertices),
            path_length_m=path_length,
            arrival_time_absolute_s=absolute_time,
            arrival_time_relative_s=relative_time,
            amplitude=amplitude,
            direction_of_arrival=direction,
            azimuth_deg=azimuth,
            elevation_deg=elevation,
            source_relative_azimuth_deg=source_relative_azimuth,
        )


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def build_impulse_response(
    result: SimulationResult,
    *,
    reference: Optional[str] = None,
    duration_s: Optional[float] = None,
    normalize_peak: bool = True,
) -> Tuple[List[float], float]:
    """Build a sparse mono IR with linearly interpolated fractional delays.

    Returns ``(samples, applied_scale)``.  The IR is an event train, not an
    octave-band-filtered waveform.  Reflection amplitudes are evaluated at the
    selected band in ``SimulationConfig.band_index`` (or with the average
    absorption when that value is ``None``).
    """
    reference = reference or result.config.time_reference
    if reference not in ("direct", "absolute"):
        raise ValueError("reference must be 'direct' or 'absolute'")

    events = result.sorted_events()
    if not events:
        return [0.0], 1.0

    def event_time(event: ReflectionEvent) -> float:
        return event.arrival_time_relative_s if reference == "direct" else event.arrival_time_absolute_s

    latest = max(event_time(event) for event in events)
    if duration_s is None:
        duration_s = latest + 0.01
    duration_s = max(duration_s, latest + 2.0 / result.config.sample_rate)
    sample_count = max(1, int(math.ceil(duration_s * result.config.sample_rate)) + 2)
    samples = [0.0] * sample_count

    for event in events:
        position = max(0.0, event_time(event)) * result.config.sample_rate
        index = int(math.floor(position))
        fraction = position - index
        if index < sample_count:
            samples[index] += event.amplitude * (1.0 - fraction)
        if index + 1 < sample_count:
            samples[index + 1] += event.amplitude * fraction

    scale = 1.0
    peak = max(abs(value) for value in samples)
    if normalize_peak and peak > 0.999:
        scale = 0.999 / peak
        samples = [value * scale for value in samples]
    return samples, scale


def write_wav_pcm16(path: str, samples: Sequence[float], sample_rate: int) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        frame_data = bytearray()
        for value in samples:
            clipped = max(-1.0, min(1.0, float(value)))
            integer = int(round(clipped * 32767.0))
            frame_data.extend(struct.pack("<h", integer))
        wav.writeframes(bytes(frame_data))


def save_ancestry_json(path: str, result: SimulationResult, scene: Scene, *, ir_scale: Optional[float] = None) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    payload: Dict[str, Any] = {
        "format": "borish-early-reflections-v1",
        "source": _vec_to_list(result.source),
        "receiver": _vec_to_list(result.receiver),
        "octave_bands_hz": list(OCTAVE_BANDS_HZ),
        "selected_band_hz": None if result.config.band_index is None else OCTAVE_BANDS_HZ[result.config.band_index],
        "config": asdict(result.config),
        "stats": asdict(result.stats),
        "scene": {
            "patch_count": result.scene_patch_count,
            "triangle_count": result.scene_triangle_count,
            "patches": [
                {
                    "patch_id": patch.id,
                    "normal": _vec_to_list(patch.normal),
                    "offset": patch.offset,
                    "triangle_ids": list(patch.triangle_ids),
                    "absorption": list(patch.absorption),
                    "metadata": patch.metadata,
                }
                for patch in scene.patches
            ],
        },
        "diagnostics": {
            "source_inside_scene": result.source_inside_scene,
            "receiver_inside_scene": result.receiver_inside_scene,
            "ir_scale": ir_scale,
        },
        "paths": [],
    }

    for event in result.sorted_events():
        ancestry = []
        for patch_id in event.patch_sequence:
            patch = scene.patch(patch_id)
            ancestry.append({
                "patch_id": patch.id,
                "metadata": patch.metadata,
                "absorption": list(patch.absorption),
            })
        payload["paths"].append({
            "path_id": event.path_id,
            "order": event.order,
            "patch_sequence": list(event.patch_sequence),
            "ancestry": ancestry,
            "image_source_positions": [_vec_to_list(v) for v in event.image_source_positions],
            "reflection_points": [_vec_to_list(v) for v in event.reflection_points],
            "path_vertices": [_vec_to_list(v) for v in event.path_vertices],
            "path_length_m": event.path_length_m,
            "arrival_time_absolute_s": event.arrival_time_absolute_s,
            "arrival_time_relative_s": event.arrival_time_relative_s,
            "amplitude": event.amplitude,
            "direction_of_arrival": _vec_to_list(event.direction_of_arrival),
            "azimuth_deg": event.azimuth_deg,
            "elevation_deg": event.elevation_deg,
            "source_relative_azimuth_deg": event.source_relative_azimuth_deg,
        })

    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False)


def save_ancestry_csv(path: str, result: SimulationResult, scene: Scene) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fieldnames = [
        "path_id", "order", "arrival_time_relative_s", "arrival_time_absolute_s",
        "path_length_m", "amplitude", "azimuth_deg", "elevation_deg", "source_relative_azimuth_deg",
        "patch_sequence", "owner_sequence", "face_sequence", "reflection_points",
    ]
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for event in result.sorted_events():
            owners: List[str] = []
            faces: List[str] = []
            for patch_id in event.patch_sequence:
                metadata = scene.patch(patch_id).metadata
                owners.append(str(metadata.get("object_name") or metadata.get("object_id") or ""))
                faces.append(str(metadata.get("face_index", "")))
            writer.writerow({
                "path_id": event.path_id,
                "order": event.order,
                "arrival_time_relative_s": f"{event.arrival_time_relative_s:.12g}",
                "arrival_time_absolute_s": f"{event.arrival_time_absolute_s:.12g}",
                "path_length_m": f"{event.path_length_m:.12g}",
                "amplitude": f"{event.amplitude:.12g}",
                "azimuth_deg": f"{event.azimuth_deg:.9g}",
                "elevation_deg": f"{event.elevation_deg:.9g}",
                "source_relative_azimuth_deg": f"{event.source_relative_azimuth_deg:.9g}",
                "patch_sequence": "|".join(str(x) for x in event.patch_sequence),
                "owner_sequence": "|".join(owners),
                "face_sequence": "|".join(faces),
                "reflection_points": "|".join(
                    ",".join(f"{coordinate:.12g}" for coordinate in point)
                    for point in event.reflection_points
                ),
            })


def save_result_bundle(output_base: str, result: SimulationResult, scene: Scene) -> Dict[str, str]:
    """Write WAV, JSON, and CSV with a shared output basename."""
    base, extension = os.path.splitext(output_base)
    if extension.lower() in (".wav", ".json", ".csv"):
        output_base = base
    samples, scale = build_impulse_response(result)
    wav_path = output_base + ".wav"
    json_path = output_base + ".json"
    csv_path = output_base + ".csv"
    write_wav_pcm16(wav_path, samples, result.config.sample_rate)
    save_ancestry_json(json_path, result, scene, ir_scale=scale)
    save_ancestry_csv(csv_path, result, scene)
    return {"wav": wav_path, "json": json_path, "csv": csv_path}


# ---------------------------------------------------------------------------
# OBJ adapter (standalone / testing)
# ---------------------------------------------------------------------------

def load_obj_scene(
    path: str,
    *,
    default_absorption: Sequence[float] = (0.05,) * 8,
    material_absorption: Optional[Dict[str, Sequence[float]]] = None,
    flip_normals: bool = False,
    planarity_tolerance: float = 1.0e-6,
) -> Scene:
    """Read a basic OBJ file and make one patch per planar OBJ face.

    Supported records: ``v``, ``f``, ``o``, ``g``, and ``usemtl``.  Polygonal
    faces are fan-triangulated.  Vertices with texture/normal suffixes are
    accepted.  The OBJ face winding must point outward unless ``flip_normals``
    is true.
    """
    vertices: List[Vec3] = []
    faces: List[Tuple[List[int], str, str, str]] = []
    current_object = ""
    current_group = ""
    current_material = ""

    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            tag = parts[0].lower()
            if tag == "v" and len(parts) >= 4:
                vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))
            elif tag == "o":
                current_object = " ".join(parts[1:])
            elif tag == "g":
                current_group = " ".join(parts[1:])
            elif tag == "usemtl":
                current_material = " ".join(parts[1:])
            elif tag == "f" and len(parts) >= 4:
                indices: List[int] = []
                for token in parts[1:]:
                    raw_index = int(token.split("/")[0])
                    index = raw_index - 1 if raw_index > 0 else len(vertices) + raw_index
                    if index < 0 or index >= len(vertices):
                        raise ValueError(f"OBJ face references invalid vertex {raw_index}")
                    indices.append(index)
                faces.append((indices, current_object, current_group, current_material))

    if not vertices or not faces:
        raise ValueError("OBJ contains no usable vertices/faces")

    material_absorption = material_absorption or {}
    triangles: List[Triangle] = []
    patches: List[ReflectorPatch] = []
    triangle_id = 0
    patch_id = 0

    for face_index, (indices, object_name, group_name, material_name) in enumerate(faces):
        face_points = [vertices[index] for index in indices]
        normal = _polygon_normal(face_points)
        if flip_normals:
            normal = v_mul(normal, -1.0)
            indices = list(reversed(indices))
            face_points = [vertices[index] for index in indices]
        offset = v_dot(face_points[0], normal)
        planar = all(abs(v_dot(point, normal) - offset) <= planarity_tolerance for point in face_points)
        alpha = tuple(material_absorption.get(material_name, default_absorption))

        # Fan triangulation.  A non-planar polygon becomes one patch per triangle.
        generated: List[Tuple[Vec3, Vec3, Vec3]] = []
        for i in range(1, len(indices) - 1):
            a, b, c = vertices[indices[0]], vertices[indices[i]], vertices[indices[i + 1]]
            if v_length(v_cross(v_sub(b, a), v_sub(c, a))) <= 1.0e-14:
                continue
            generated.append((a, b, c))

        if planar:
            tri_ids: List[int] = []
            for a, b, c in generated:
                if v_dot(v_cross(v_sub(b, a), v_sub(c, a)), normal) < 0.0:
                    b, c = c, b
                triangles.append(Triangle(triangle_id, a, b, c, patch_id))
                tri_ids.append(triangle_id)
                triangle_id += 1
            if tri_ids:
                patches.append(ReflectorPatch(
                    id=patch_id,
                    normal=normal,
                    offset=offset,
                    triangle_ids=tuple(tri_ids),
                    absorption=alpha,
                    metadata={
                        "source": "obj",
                        "object_name": object_name,
                        "group_name": group_name,
                        "material_name": material_name,
                        "face_index": face_index,
                    },
                ))
                patch_id += 1
        else:
            for local_triangle_index, (a, b, c) in enumerate(generated):
                tri_normal = v_normalize(v_cross(v_sub(b, a), v_sub(c, a)))
                triangles.append(Triangle(triangle_id, a, b, c, patch_id))
                patches.append(ReflectorPatch(
                    id=patch_id,
                    normal=tri_normal,
                    offset=v_dot(a, tri_normal),
                    triangle_ids=(triangle_id,),
                    absorption=alpha,
                    metadata={
                        "source": "obj",
                        "object_name": object_name,
                        "group_name": group_name,
                        "material_name": material_name,
                        "face_index": face_index,
                        "triangle_index": local_triangle_index,
                    },
                ))
                triangle_id += 1
                patch_id += 1

    return Scene(triangles, patches)


# ---------------------------------------------------------------------------
# Internal geometry helpers
# ---------------------------------------------------------------------------

def _bounds_for_triangles(triangle_by_id: Dict[int, Triangle], triangle_ids: Sequence[int]) -> Tuple[Vec3, Vec3]:
    first = triangle_by_id[triangle_ids[0]]
    minimum = list(first.bounds_min)
    maximum = list(first.bounds_max)
    for triangle_id in triangle_ids[1:]:
        tri = triangle_by_id[triangle_id]
        tri_min, tri_max = tri.bounds_min, tri.bounds_max
        for axis in range(3):
            minimum[axis] = min(minimum[axis], tri_min[axis])
            maximum[axis] = max(maximum[axis], tri_max[axis])
    return (minimum[0], minimum[1], minimum[2]), (maximum[0], maximum[1], maximum[2])


def _segment_intersects_aabb(
    start: Vec3,
    direction: Vec3,
    bounds_min: Vec3,
    bounds_max: Vec3,
    t_min: float,
    t_max: float,
) -> bool:
    lo, hi = t_min, t_max
    for axis in range(3):
        origin = start[axis]
        delta = direction[axis]
        if abs(delta) <= 1.0e-18:
            if origin < bounds_min[axis] or origin > bounds_max[axis]:
                return False
            continue
        inv = 1.0 / delta
        t0 = (bounds_min[axis] - origin) * inv
        t1 = (bounds_max[axis] - origin) * inv
        if t0 > t1:
            t0, t1 = t1, t0
        lo = max(lo, t0)
        hi = min(hi, t1)
        if hi < lo:
            return False
    return True


def _segment_triangle_t(
    start: Vec3,
    direction: Vec3,
    triangle: Triangle,
    t_min: float,
    t_max: float,
) -> Optional[float]:
    edge1 = v_sub(triangle.b, triangle.a)
    edge2 = v_sub(triangle.c, triangle.a)
    pvec = v_cross(direction, edge2)
    determinant = v_dot(edge1, pvec)
    if abs(determinant) <= 1.0e-14:
        return None
    inv_det = 1.0 / determinant
    tvec = v_sub(start, triangle.a)
    u = v_dot(tvec, pvec) * inv_det
    if u < -1.0e-10 or u > 1.0 + 1.0e-10:
        return None
    qvec = v_cross(tvec, edge1)
    v = v_dot(direction, qvec) * inv_det
    if v < -1.0e-10 or u + v > 1.0 + 1.0e-10:
        return None
    t = v_dot(edge2, qvec) * inv_det
    if t < t_min or t > t_max:
        return None
    return t


def _point_in_triangle_3d(point: Vec3, triangle: Triangle, tolerance: float) -> bool:
    v0 = v_sub(triangle.b, triangle.a)
    v1 = v_sub(triangle.c, triangle.a)
    v2 = v_sub(point, triangle.a)
    dot00 = v_dot(v0, v0)
    dot01 = v_dot(v0, v1)
    dot02 = v_dot(v0, v2)
    dot11 = v_dot(v1, v1)
    dot12 = v_dot(v1, v2)
    denominator = dot00 * dot11 - dot01 * dot01
    if abs(denominator) <= 1.0e-24:
        return False
    inv = 1.0 / denominator
    u = (dot11 * dot02 - dot01 * dot12) * inv
    v = (dot00 * dot12 - dot01 * dot02) * inv
    # Convert a world-space tolerance to a conservative barycentric tolerance.
    scale = max(math.sqrt(dot00), math.sqrt(dot11), 1.0e-12)
    bary_tol = max(1.0e-10, tolerance / scale)
    return u >= -bary_tol and v >= -bary_tol and u + v <= 1.0 + bary_tol


def _polygon_normal(points: Sequence[Vec3]) -> Vec3:
    # Newell's method is stable for arbitrary planar polygons.
    nx = ny = nz = 0.0
    for current, following in zip(points, points[1:] + points[:1]):  # type: ignore[operator]
        nx += (current[1] - following[1]) * (current[2] + following[2])
        ny += (current[2] - following[2]) * (current[0] + following[0])
        nz += (current[0] - following[0]) * (current[1] + following[1])
    return v_normalize((nx, ny, nz))


def _air_pressure_gain(distance_m: float, db_per_m: float) -> float:
    return 10.0 ** (-(max(0.0, db_per_m) * max(0.0, distance_m)) / 20.0)


def _azimuth_elevation(direction: Vec3) -> Tuple[float, float]:
    horizontal = math.hypot(direction[0], direction[1])
    azimuth = math.degrees(math.atan2(direction[1], direction[0]))
    elevation = math.degrees(math.atan2(direction[2], horizontal))
    return azimuth, elevation


def _relative_azimuth_deg(reference_direction: Vec3, direction: Vec3) -> float:
    ref_x, ref_y = reference_direction[0], reference_direction[1]
    dir_x, dir_y = direction[0], direction[1]
    if math.hypot(ref_x, ref_y) <= 1.0e-15 or math.hypot(dir_x, dir_y) <= 1.0e-15:
        return 0.0
    cross_z = ref_x * dir_y - ref_y * dir_x
    dot_xy = ref_x * dir_x + ref_y * dir_y
    return math.degrees(math.atan2(cross_z, dot_xy))


__all__ = [
    "Vec3", "OCTAVE_BANDS_HZ", "Triangle", "ReflectorPatch", "Scene",
    "SimulationConfig", "ReflectionEvent", "SimulationStats", "SimulationResult",
    "SimulationCancelled", "EarlyReflectionSolver", "build_impulse_response",
    "write_wav_pcm16", "save_ancestry_json", "save_ancestry_csv", "save_result_bundle",
    "load_obj_scene",
]
