"""Browser/Pyodide API for the Borish image-source solver.

The JavaScript front-end passes a plain mesh JSON object into these functions.
They return plain JSON strings so that Pyodide can pass results back to the
browser without exposing Python objects.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import math
import struct
import wave
from collections import defaultdict
from dataclasses import asdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from borish_core import (
    OCTAVE_BANDS_HZ,
    EarlyReflectionSolver,
    ReflectorPatch,
    Scene,
    SimulationConfig,
    SimulationResult,
    Triangle,
    build_impulse_response,
    v_cross,
    v_dot,
    v_length,
    v_normalize,
    v_sub,
)

Vec3 = Tuple[float, float, float]
_PLANE_GROUP_TOLERANCE = 1.0e-7


def _vec3(value: Sequence[float]) -> Vec3:
    if len(value) != 3:
        raise ValueError("Expected a 3D coordinate")
    return (float(value[0]), float(value[1]), float(value[2]))


def _vec_list(v: Vec3) -> List[float]:
    return [float(v[0]), float(v[1]), float(v[2])]


def _weld_mesh_vertices(mesh: Dict[str, Any], tolerance: float = 1.0e-9) -> Dict[str, Any]:
    """Return a copy with vertices welded by coordinate.

    Many OBJ exporters duplicate vertices per face. Closure should be tested on
    geometric edges, not raw OBJ vertex IDs. The default tolerance is tiny and
    only collapses numerically identical coordinates.
    """
    vertices = [_vec3(v) for v in mesh.get("vertices", [])]
    if not vertices:
        return {"units": mesh.get("units", "m"), "vertices": [], "faces": []}

    scale = 1.0 / max(float(tolerance), 1.0e-15)
    index_by_key: Dict[Tuple[int, int, int], int] = {}
    new_vertices: List[List[float]] = []
    remap: Dict[int, int] = {}

    for old_index, vertex in enumerate(vertices):
        key = (round(vertex[0] * scale), round(vertex[1] * scale), round(vertex[2] * scale))
        new_index = index_by_key.get(key)
        if new_index is None:
            new_index = len(new_vertices)
            index_by_key[key] = new_index
            new_vertices.append(_vec_list(vertex))
        remap[old_index] = new_index

    new_faces: List[Any] = []
    for face_index, raw_face in enumerate(mesh.get("faces", [])):
        try:
            raw_indices = _face_indices(raw_face)
        except Exception:
            continue
        mapped: List[int] = []
        for raw_index in raw_indices:
            new_index = remap[raw_index]
            if not mapped or mapped[-1] != new_index:
                mapped.append(new_index)
        if len(mapped) > 1 and mapped[0] == mapped[-1]:
            mapped.pop()
        if len(set(mapped)) < 3:
            continue
        if isinstance(raw_face, dict):
            copied = dict(raw_face)
            copied["indices"] = mapped
            copied.setdefault("original_face_index", face_index)
            new_faces.append(copied)
        else:
            new_faces.append(mapped)

    return {
        "units": mesh.get("units", "m"),
        "vertices": new_vertices,
        "faces": new_faces,
        "welded_vertex_count": len(new_vertices),
        "original_vertex_count": len(vertices),
    }



def _face_indices(face: Any) -> List[int]:
    if isinstance(face, dict):
        indices = face.get("indices")
    else:
        indices = face
    if not isinstance(indices, list) or len(indices) < 3:
        raise ValueError("Each face must contain at least three vertex indices")
    return [int(i) for i in indices]


def _face_metadata(face: Any, index: int) -> Dict[str, Any]:
    if not isinstance(face, dict):
        return {"source": "browser", "face_index": index}

    wall_name = (
        face.get("surface_name")
        or face.get("group")
        or face.get("name")
        or face.get("object")
        or f"Face_{index}"
    )
    material_name = face.get("acoustic_material") or face.get("material") or "Default"
    try:
        scattering = _face_scattering(face)
    except Exception:
        scattering = face.get("scattering", 0.0)

    return {
        "source": "browser",
        "face_index": index,
        "surface_name": wall_name,
        "group_name": wall_name,
        "material_name": material_name,
        "acoustic_material": material_name,
        "object_name": face.get("object"),
        "user_surface_id": face.get("user_surface_id"),
        "user_surface_name": face.get("user_surface_name"),
        "original_group": face.get("original_group"),
        "original_material": face.get("original_material"),
        "input_absorption": face.get("absorption"),
        "scattering": scattering,
    }


def _face_scattering(face: Any) -> float:
    if isinstance(face, dict):
        value = face.get("scattering", face.get("scatter", 0.0))
    else:
        value = 0.0
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return 0.0


def _face_absorption(face: Any, default_absorption: Sequence[float]) -> Tuple[float, ...]:
    if not isinstance(face, dict) or face.get("absorption") is None:
        name = "unnamed face"
        if isinstance(face, dict):
            name = str(face.get("user_surface_name") or face.get("surface_name") or face.get("group") or face.get("name") or face.get("object") or name)
        raise ValueError(
            "Unassigned acoustic material coefficients: "
            + name
            + ". Every surface must have explicit octave-band absorption before ISM."
        )
    values = face["absorption"]
    if isinstance(values, (int, float)):
        values = [float(values)] * len(OCTAVE_BANDS_HZ)
    values = [float(v) for v in values]
    if len(values) == 1:
        values = values * len(OCTAVE_BANDS_HZ)
    if len(values) != len(OCTAVE_BANDS_HZ):
        raise ValueError("Absorption must be one value or eight octave-band values")
    base_absorption = tuple(max(0.0, min(1.0, value)) for value in values)
    scattering = _face_scattering(face)
    # Scattering is treated as specular energy loss for this minimal ISM.
    # Effective retained specular energy = (1 - absorption) * (1 - scattering).
    # Therefore alpha_eff = 1 - retained_specular_energy.
    return tuple(1.0 - (1.0 - alpha) * (1.0 - scattering) for alpha in base_absorption)


def _polygon_normal(vertices: Sequence[Vec3], indices: Sequence[int]) -> Vec3:
    """Newell-style polygon normal; falls back to first non-degenerate fan."""
    nx = ny = nz = 0.0
    for offset, current_index in enumerate(indices):
        next_index = indices[(offset + 1) % len(indices)]
        current = vertices[current_index]
        nxt = vertices[next_index]
        nx += (current[1] - nxt[1]) * (current[2] + nxt[2])
        ny += (current[2] - nxt[2]) * (current[0] + nxt[0])
        nz += (current[0] - nxt[0]) * (current[1] + nxt[1])
    normal = (nx, ny, nz)
    if v_length(normal) > 1.0e-12:
        return v_normalize(normal)
    root = vertices[indices[0]]
    for i in range(1, len(indices) - 1):
        normal = v_cross(v_sub(vertices[indices[i]], root), v_sub(vertices[indices[i + 1]], root))
        if v_length(normal) > 1.0e-12:
            return v_normalize(normal)
    raise ValueError("Degenerate polygon face")


def _triangulate_face(indices: Sequence[int], flip: bool = False) -> Iterable[Tuple[int, int, int]]:
    if len(indices) < 3:
        return
    base = indices[0]
    for i in range(1, len(indices) - 1):
        tri = (base, indices[i], indices[i + 1])
        if flip:
            tri = (tri[0], tri[2], tri[1])
        yield tri


def _plane_group_key(normal: Vec3, offset: float, absorption: Sequence[float]) -> Tuple[Any, ...]:
    scale = 1.0 / _PLANE_GROUP_TOLERANCE
    return (
        round(normal[0] * scale),
        round(normal[1] * scale),
        round(normal[2] * scale),
        round(offset * scale),
        tuple(round(float(value), 10) for value in absorption),
    )


def _merged_patch_metadata(face_metadata: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    first = dict(face_metadata[0])
    if len(face_metadata) == 1:
        first["source_face_indices"] = [first.get("face_index")]
        return first

    face_indices = [metadata.get("face_index") for metadata in face_metadata]
    surface_names = [
        str(metadata.get("surface_name") or metadata.get("group_name") or f"Face_{metadata.get('face_index')}")
        for metadata in face_metadata
    ]
    first["surface_name"] = f"{surface_names[0]} (+{len(surface_names) - 1} coplanar)"
    first["group_name"] = first["surface_name"]
    first["merged_coplanar_faces"] = len(face_metadata)
    first["source_face_indices"] = face_indices
    first["source_surface_names"] = surface_names
    return first


def mesh_closure_report(mesh: Dict[str, Any]) -> Dict[str, Any]:
    """Return closure/orientation diagnostics for a mesh JSON object."""
    mesh = _weld_mesh_vertices(mesh, tolerance=float(mesh.get("weld_tolerance", 1.0e-9)))
    vertices = [_vec3(v) for v in mesh.get("vertices", [])]
    raw_faces = mesh.get("faces", [])
    if len(vertices) < 4:
        return {"closed": False, "errors": ["Mesh has fewer than four vertices"]}
    if len(raw_faces) < 4:
        return {"closed": False, "errors": ["Mesh has fewer than four faces"]}

    directed_edges: Dict[Tuple[int, int], int] = defaultdict(int)
    undirected_edges: Dict[Tuple[int, int], int] = defaultdict(int)
    signed_volume = 0.0
    degenerate_faces = 0

    for face_index, raw_face in enumerate(raw_faces):
        try:
            indices = _face_indices(raw_face)
        except Exception:
            degenerate_faces += 1
            continue
        if len(set(indices)) < 3:
            degenerate_faces += 1
            continue
        for index in indices:
            if index < 0 or index >= len(vertices):
                return {
                    "closed": False,
                    "errors": [f"Face {face_index} references missing vertex index {index}"],
                }
        for i, a in enumerate(indices):
            b = indices[(i + 1) % len(indices)]
            directed_edges[(a, b)] += 1
            undirected_edges[tuple(sorted((a, b)))] += 1
        for a_i, b_i, c_i in _triangulate_face(indices):
            a, b, c = vertices[a_i], vertices[b_i], vertices[c_i]
            signed_volume += v_dot(a, v_cross(b, c)) / 6.0

    boundary_edges = [edge for edge, count in undirected_edges.items() if count == 1]
    nonmanifold_edges = [edge for edge, count in undirected_edges.items() if count > 2]
    duplicated_directed_edges = [edge for edge, count in directed_edges.items() if count > 1]

    errors: List[str] = []
    if degenerate_faces:
        errors.append(f"Mesh has {degenerate_faces} degenerate faces")
    if boundary_edges:
        errors.append(f"Mesh has {len(boundary_edges)} boundary edges")
    if nonmanifold_edges:
        errors.append(f"Mesh has {len(nonmanifold_edges)} non-manifold edges")
    if duplicated_directed_edges:
        errors.append(f"Mesh has {len(duplicated_directed_edges)} duplicate directed edges; face winding may be inconsistent")
    if abs(signed_volume) <= 1.0e-12:
        errors.append("Mesh signed volume is zero or nearly zero")

    closed = not boundary_edges and not nonmanifold_edges and not degenerate_faces and abs(signed_volume) > 1.0e-12

    return {
        "closed": bool(closed),
        "can_simulate": bool(closed),
        "vertex_count": len(vertices),
        "original_vertex_count": mesh.get("original_vertex_count", len(vertices)),
        "welded_vertex_count": mesh.get("welded_vertex_count", len(vertices)),
        "face_count": len(raw_faces),
        "boundary_edges": len(boundary_edges),
        "nonmanifold_edges": len(nonmanifold_edges),
        "duplicate_directed_edges": len(duplicated_directed_edges),
        "degenerate_faces": degenerate_faces,
        "signed_volume_m3": signed_volume,
        "normals_apparently_inward": signed_volume < 0.0,
        "errors": errors,
        "sample_boundary_edges": boundary_edges[:20],
        "sample_nonmanifold_edges": nonmanifold_edges[:20],
    }



def unassigned_material_report(mesh: Dict[str, Any]) -> List[Dict[str, Any]]:
    missing: List[Dict[str, Any]] = []
    for index, face in enumerate(mesh.get("faces", [])):
        if not isinstance(face, dict) or face.get("absorption") is None:
            label = f"Face_{index}"
            if isinstance(face, dict):
                label = str(
                    face.get("user_surface_name")
                    or face.get("surface_name")
                    or face.get("name")
                    or face.get("group")
                    or face.get("object")
                    or label
                )
            missing.append({"face_index": index, "surface": label, "reason": "missing absorption"})
            continue
        try:
            _face_absorption(face, [0.0] * len(OCTAVE_BANDS_HZ))
        except Exception as exc:
            missing.append({"face_index": index, "surface": str(face.get("surface_name") or face.get("group") or f"Face_{index}"), "reason": str(exc)})
    return missing

def scene_from_mesh_json(
    mesh: Dict[str, Any],
    *,
    default_absorption: Sequence[float],
    auto_flip_normals: bool = True,
    allow_open_mesh: bool = False,
) -> Tuple[Scene, Dict[str, Any]]:
    mesh = _weld_mesh_vertices(mesh, tolerance=float(mesh.get("weld_tolerance", 1.0e-9)))
    report = mesh_closure_report(mesh)
    missing_materials = unassigned_material_report(mesh)
    report["unassigned_material_surfaces"] = len(missing_materials)
    report["unassigned_material_preview"] = missing_materials[:16]
    if not report.get("closed"):
        if not allow_open_mesh:
            raise ValueError("Mesh is not closed: " + "; ".join(report.get("errors", [])))
        report["can_simulate"] = True
        report["diagnostic_open_mesh_allowed"] = True
        report["diagnostic_open_mesh_run"] = True
        warnings = list(report.get("warnings", []))
        warnings.append(
            "Open-mesh diagnostic mode: ISM will run using the surfaces present, "
            "but closure, inside/outside, occlusion and path completeness are not guaranteed."
        )
        report["warnings"] = warnings
    else:
        report["diagnostic_open_mesh_allowed"] = False
        report["diagnostic_open_mesh_run"] = False

    vertices = [_vec3(v) for v in mesh.get("vertices", [])]
    raw_faces = mesh.get("faces", [])
    flip = bool(auto_flip_normals and report.get("normals_apparently_inward"))

    triangles: List[Triangle] = []
    patches: List[ReflectorPatch] = []
    triangle_id = 0
    grouped_faces: Dict[Tuple[Any, ...], Dict[str, Any]] = {}

    for face_index, raw_face in enumerate(raw_faces):
        indices = _face_indices(raw_face)
        local_indices = list(reversed(indices)) if flip else list(indices)
        normal = _polygon_normal(vertices, local_indices)
        offset = v_dot(vertices[local_indices[0]], normal)
        absorption = _face_absorption(raw_face, default_absorption)
        key = _plane_group_key(normal, offset, absorption)
        group = grouped_faces.setdefault(
            key,
            {
                "normal": normal,
                "offset": offset,
                "absorption": absorption,
                "faces": [],
                "metadata": [],
            },
        )
        group["faces"].append(local_indices)
        group["metadata"].append(_face_metadata(raw_face, face_index))

    for patch_id, group in enumerate(grouped_faces.values()):
        patch_triangle_ids: List[int] = []

        for local_indices in group["faces"]:
            for a_i, b_i, c_i in _triangulate_face(local_indices):
                triangle = Triangle(
                    id=triangle_id,
                    a=vertices[a_i],
                    b=vertices[b_i],
                    c=vertices[c_i],
                    patch_id=patch_id,
                )
                triangles.append(triangle)
                patch_triangle_ids.append(triangle_id)
                triangle_id += 1

        if not patch_triangle_ids:
            continue
        patches.append(
            ReflectorPatch(
                id=patch_id,
                normal=group["normal"],
                offset=group["offset"],
                triangle_ids=tuple(patch_triangle_ids),
                absorption=group["absorption"],
                metadata=_merged_patch_metadata(group["metadata"]),
            )
        )

    report["normals_flipped"] = flip
    report["patch_count"] = len(patches)
    report["merged_coplanar_faces"] = len(raw_faces) - len(patches)
    report["triangle_count"] = len(triangles)
    return Scene(triangles, patches), report


def _triangle_area(a: Vec3, b: Vec3, c: Vec3) -> float:
    return 0.5 * v_length(v_cross(v_sub(b, a), v_sub(c, a)))


def _patch_area(scene: Scene, patch: ReflectorPatch) -> float:
    return sum(
        _triangle_area(triangle.a, triangle.b, triangle.c)
        for triangle in scene.triangles
        if triangle.patch_id == patch.id
    )


def _normalised_absorption_values(value: Any, fallback: Sequence[float]) -> Tuple[float, ...]:
    try:
        if isinstance(value, (int, float)):
            values = [float(value)] * len(OCTAVE_BANDS_HZ)
        else:
            values = [float(v) for v in value]
    except Exception:
        values = [float(v) for v in fallback]

    if len(values) == 1:
        values = values * len(OCTAVE_BANDS_HZ)
    if len(values) != len(OCTAVE_BANDS_HZ):
        values = [float(v) for v in fallback]
    return tuple(max(0.0, min(1.0, value)) for value in values)


def _patch_material_absorption(patch: ReflectorPatch) -> Tuple[float, ...]:
    raw_absorption = patch.metadata.get("input_absorption")
    return _normalised_absorption_values(raw_absorption, patch.absorption)


def _room_acoustics_estimate(result: SimulationResult, scene: Scene, closure: Dict[str, Any]) -> Dict[str, Any]:
    volume_m3 = abs(float(closure.get("signed_volume_m3") or 0.0))
    patch_rows: List[Dict[str, Any]] = []
    equivalent_absorption = [0.0] * len(OCTAVE_BANDS_HZ)
    surface_area = 0.0
    scattering_area_sum = 0.0

    for patch in scene.patches:
        area = _patch_area(scene, patch)
        absorption = _patch_material_absorption(patch)
        surface_area += area
        for band_index, alpha in enumerate(absorption):
            equivalent_absorption[band_index] += area * alpha
        scattering = float(patch.metadata.get("scattering") or 0.0)
        scattering_area_sum += area * max(0.0, min(1.0, scattering))
        patch_rows.append({
            "patch_id": patch.id,
            "area_m2": area,
            "absorption": list(absorption),
            "scattering": max(0.0, min(1.0, scattering)),
            "metadata": patch.metadata,
        })

    mean_absorption = [
        0.0 if surface_area <= 0.0 else value / surface_area
        for value in equivalent_absorption
    ]
    sabine_rt60: List[Optional[float]] = []
    eyring_rt60: List[Optional[float]] = []
    for area_absorption, alpha_bar in zip(equivalent_absorption, mean_absorption):
        sabine_rt60.append(None if volume_m3 <= 0.0 or area_absorption <= 0.0 else 0.161 * volume_m3 / area_absorption)
        if volume_m3 <= 0.0 or surface_area <= 0.0 or alpha_bar <= 0.0:
            eyring_rt60.append(None)
        elif alpha_bar >= 1.0:
            eyring_rt60.append(0.0)
        else:
            eyring_absorption = -surface_area * math.log(max(1.0e-12, 1.0 - alpha_bar))
            eyring_rt60.append(0.161 * volume_m3 / eyring_absorption)

    bands = []
    for band_index, band_hz in enumerate(OCTAVE_BANDS_HZ):
        bands.append({
            "band_hz": band_hz,
            "mean_absorption": mean_absorption[band_index],
            "equivalent_absorption_area_m2": equivalent_absorption[band_index],
            "sabine_rt60_s": sabine_rt60[band_index],
            "eyring_rt60_s": eyring_rt60[band_index],
        })

    return {
        "method": "closed-mesh statistical room-acoustic estimate",
        "scope": "RT estimates are separate from the deterministic Borish image-source early-reflection tree; no diffuse late-reflection field is currently simulated.",
        "valid_for_rt_estimate": bool(closure.get("closed")) and volume_m3 > 0.0 and surface_area > 0.0,
        "volume_m3": volume_m3,
        "surface_area_m2": surface_area,
        "mean_scattering": 0.0 if surface_area <= 0.0 else scattering_area_sum / surface_area,
        "rt60_formula_constant": 0.161,
        "recommended_rt60": "eyring_rt60_s",
        "octave_bands": bands,
        "patches": patch_rows,
    }


def _linear_regression(xs: Sequence[float], ys: Sequence[float]) -> Optional[Tuple[float, float]]:
    count = len(xs)
    if count < 2 or count != len(ys):
        return None
    mean_x = sum(xs) / count
    mean_y = sum(ys) / count
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator <= 0.0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator
    intercept = mean_y - slope * mean_x
    return slope, intercept


def _schroeder_decay_fit(
    times: Sequence[float],
    decay_db: Sequence[float],
    upper_db: float,
    lower_db: float,
) -> Dict[str, Any]:
    selected = [
        (time_s, db)
        for time_s, db in zip(times, decay_db)
        if lower_db <= db <= upper_db
    ]
    covered_upper = any(db <= upper_db for db in decay_db)
    covered_lower = any(db <= lower_db for db in decay_db)
    if len(selected) < 2 or not covered_upper or not covered_lower:
        return {
            "rt60_s": None,
            "slope_db_per_s": None,
            "fit_sample_count": len(selected),
            "valid": False,
            "reason": "insufficient_decay_range",
        }

    xs = [row[0] for row in selected]
    ys = [row[1] for row in selected]
    regression = _linear_regression(xs, ys)
    if regression is None:
        return {
            "rt60_s": None,
            "slope_db_per_s": None,
            "fit_sample_count": len(selected),
            "valid": False,
            "reason": "degenerate_fit",
        }
    slope, intercept = regression
    if slope >= 0.0:
        return {
            "rt60_s": None,
            "slope_db_per_s": slope,
            "intercept_db": intercept,
            "fit_sample_count": len(selected),
            "valid": False,
            "reason": "non_decaying_fit",
        }
    return {
        "rt60_s": -60.0 / slope,
        "slope_db_per_s": slope,
        "intercept_db": intercept,
        "fit_sample_count": len(selected),
        "valid": True,
        "reason": None,
    }


_DECAY_TARGETS = {
    "edt": {"label": "EDT", "upper_db": 0.0, "lower_db": -10.0, "required_decay_db": 10.0},
    "t20": {"label": "T20", "upper_db": -5.0, "lower_db": -25.0, "required_decay_db": 25.0},
    "t30": {"label": "T30", "upper_db": -5.0, "lower_db": -35.0, "required_decay_db": 35.0},
}


def _ism_decay_estimate(
    result: SimulationResult,
    scene: Scene,
    completeness: Dict[str, Any],
    *,
    target: str = "t30",
) -> Dict[str, Any]:
    sample_rate = result.config.sample_rate
    latest_event_s = max((max(0.0, event.arrival_time_relative_s) for event in result.events), default=0.0)
    duration_s = max(result.config.max_time_s, latest_event_s)
    sample_count = max(1, int(math.ceil(duration_s * sample_rate)) + 1)
    complete = bool(completeness.get("complete_within_time_radius"))
    target_key = str(target or "t30").lower()
    if target_key not in _DECAY_TARGETS:
        target_key = "t30"
    target_spec = _DECAY_TARGETS[target_key]
    bands = []

    for band_index, band_hz in enumerate(OCTAVE_BANDS_HZ):
        energy = [0.0] * sample_count
        for event in result.events:
            index = min(sample_count - 1, max(0, int(round(max(0.0, event.arrival_time_relative_s) * sample_rate))))
            amplitude = _event_band_amplitude(event, scene, result, band_index)
            energy[index] += amplitude * amplitude

        schroeder = [0.0] * sample_count
        running = 0.0
        for index in range(sample_count - 1, -1, -1):
            running += energy[index]
            schroeder[index] = running

        first_index = next((index for index, value in enumerate(energy) if value > 0.0), None)
        if first_index is None:
            bands.append({
                "band_hz": band_hz,
                "valid": False,
                "reason": "no_ism_energy",
                "energy_dynamic_range_db": 0.0,
                "edt_s": None,
                "t20_s": None,
                "t30_s": None,
            })
            continue

        reference_energy = schroeder[first_index]
        times: List[float] = []
        decay_db: List[float] = []
        min_decay_db = 0.0
        for index in range(first_index, sample_count):
            value = schroeder[index]
            if value <= 0.0:
                continue
            db = 10.0 * math.log10(value / reference_energy)
            times.append(index / sample_rate)
            decay_db.append(db)
            min_decay_db = min(min_decay_db, db)

        edt = _schroeder_decay_fit(times, decay_db, 0.0, -10.0)
        t20 = _schroeder_decay_fit(times, decay_db, -5.0, -25.0)
        t30 = _schroeder_decay_fit(times, decay_db, -5.0, -35.0)
        fits = {"edt": edt, "t20": t20, "t30": t30}
        target_fit = fits[target_key]
        coverage_met = -min_decay_db >= float(target_spec["required_decay_db"])
        band_valid = complete and bool(target_fit["valid"]) and coverage_met
        reasons = []
        if not complete:
            reasons.append("incomplete_borish_time_radius")
        if not coverage_met:
            reasons.append("insufficient_decay_depth")
        if not target_fit["valid"]:
            reasons.append("insufficient_decay_range")

        bands.append({
            "band_hz": band_hz,
            "valid": band_valid,
            "reason": "; ".join(reasons) if reasons else None,
            "energy_dynamic_range_db": -min_decay_db,
            "required_decay_db": target_spec["required_decay_db"],
            "target_metric": target_key,
            "target_rt60_s": target_fit["rt60_s"],
            "total_energy": reference_energy,
            "first_energy_time_s": first_index / sample_rate,
            "last_event_time_s": latest_event_s,
            "edt_s": edt["rt60_s"],
            "t20_s": t20["rt60_s"],
            "t30_s": t30["rt60_s"],
            "fits": fits,
        })

    valid_band_count = sum(1 for band in bands if band["valid"])
    return {
        "method": "Borish image-source Schroeder decay",
        "scope": "Computed from the deterministic octave-band image-source impulse responses only; no Sabine/Eyring late-field substitution is used here.",
        "target_metric": target_key,
        "required_decay_db": target_spec["required_decay_db"],
        "valid": complete and valid_band_count == len(bands) and bool(bands),
        "valid_band_count": valid_band_count,
        "band_count": len(bands),
        "complete_within_time_radius": complete,
        "sample_rate": sample_rate,
        "duration_s": duration_s,
        "bands": bands,
    }


def _result_to_dict(
    result: SimulationResult,
    scene: Scene,
    ir_scale: Optional[float],
    *,
    decay_target: str = "t30",
) -> Dict[str, Any]:
    direct_distance = math.dist(result.source, result.receiver)
    if result.config.time_reference == "direct":
        max_path_length = direct_distance + result.config.max_time_s * result.config.speed_of_sound
    else:
        max_path_length = result.config.max_time_s * result.config.speed_of_sound
    completeness_warnings = []
    if result.stats.hit_node_limit:
        completeness_warnings.append("Traversal stopped at max_nodes before the Borish proximity tree was exhausted.")
    if result.stats.order_pruned_nodes:
        completeness_warnings.append(
            "Traversal stopped at max_order for still-proximate virtual sources; paths inside the time radius may be missing."
        )

    payload: Dict[str, Any] = {
        "format": "borish-browser-early-reflections-v1",
        "source": _vec_list(result.source),
        "receiver": _vec_list(result.receiver),
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
                    "normal": _vec_list(patch.normal),
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
            "direct_path_blocked": scene.segment_blocked(
                result.source,
                result.receiver,
                endpoint_epsilon=result.config.endpoint_epsilon,
            ),
            "ir_scale": ir_scale,
            "completeness": {
                "borish_time_radius_s": result.config.max_time_s,
                "max_path_length_m": max_path_length,
                "order_limited": bool(result.stats.order_pruned_nodes),
                "node_limited": bool(result.stats.hit_node_limit),
                "complete_within_time_radius": not result.stats.hit_node_limit and not result.stats.order_pruned_nodes,
                "warnings": completeness_warnings,
            },
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
            "image_source_positions": [_vec_list(v) for v in event.image_source_positions],
            "reflection_points": [_vec_list(v) for v in event.reflection_points],
            "path_vertices": [_vec_list(v) for v in event.path_vertices],
            "path_length_m": event.path_length_m,
            "arrival_time_absolute_s": event.arrival_time_absolute_s,
            "arrival_time_relative_s": event.arrival_time_relative_s,
            "amplitude": event.amplitude,
            "direction_of_arrival": _vec_list(event.direction_of_arrival),
            "azimuth_deg": event.azimuth_deg,
            "elevation_deg": event.elevation_deg,
            "source_relative_azimuth_deg": event.source_relative_azimuth_deg,
            "band_amplitudes": [
                _event_band_amplitude(event, scene, result, band_index)
                for band_index in range(len(OCTAVE_BANDS_HZ))
            ],
        })
    payload["analysis"] = _directional_analysis(result, scene)
    payload["ism_decay"] = _ism_decay_estimate(
        result,
        scene,
        payload["diagnostics"]["completeness"],
        target=decay_target,
    )
    return payload


def _wav_bytes(samples: Sequence[float], sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        frames = bytearray()
        for value in samples:
            clipped = max(-1.0, min(1.0, float(value)))
            frames.extend(struct.pack("<h", int(round(clipped * 32767.0))))
        wav.writeframes(bytes(frames))
    return buffer.getvalue()


def _directional_ir_payload(
    result: SimulationResult,
    scene: Scene,
    *,
    reference: str = "direct",
) -> Dict[str, Any]:
    events = result.sorted_events()

    def event_time(event: Any) -> float:
        return event.arrival_time_relative_s if reference == "direct" else event.arrival_time_absolute_s

    sparse_events: List[Dict[str, Any]] = []
    for event in events:
        time_s = max(0.0, event_time(event))
        sample_position = time_s * result.config.sample_rate
        sparse_events.append({
            "path_id": event.path_id,
            "order": event.order,
            "time_s": time_s,
            "sample_position": sample_position,
            "nearest_sample_index": int(round(sample_position)),
            "amplitude": event.amplitude,
            "direction_of_arrival": _vec_list(event.direction_of_arrival),
            "azimuth_deg": event.azimuth_deg,
            "elevation_deg": event.elevation_deg,
            "source_relative_azimuth_deg": event.source_relative_azimuth_deg,
            "patch_sequence": list(event.patch_sequence),
            "band_amplitudes": [
                _event_band_amplitude(event, scene, result, band_index)
                for band_index in range(len(OCTAVE_BANDS_HZ))
            ],
        })

    duration_s = max((event["time_s"] for event in sparse_events), default=0.0)
    return {
        "format": "borish-directional-sparse-ir-v1",
        "mode": "directional_sparse_ir",
        "sample_rate": result.config.sample_rate,
        "time_reference": reference,
        "duration_s": duration_s,
        "sample_count": int(math.ceil(duration_s * result.config.sample_rate)) + 1,
        "octave_bands_hz": list(OCTAVE_BANDS_HZ),
        "coordinate_convention": {
            "direction_of_arrival": "unit vector pointing from receiver toward the arriving image-source ray",
            "azimuth_deg": "world XY azimuth in degrees",
            "source_relative_azimuth_deg": "azimuth relative to the direct source-to-receiver bearing",
            "elevation_deg": "vertical angle in degrees",
        },
        "rendering_contract": (
            "This is a neutral sparse directional kernel. Directional rendering or auralisation should convolve "
            "these events through an explicitly chosen receiver/source directivity, loudspeaker, Ambisonic, or HRTF model."
        ),
        "events": sparse_events,
    }


def _toa_table(result: SimulationResult, scene: Scene) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for event in result.sorted_events():
        names = []
        for patch_id in event.patch_sequence:
            metadata = scene.patch(patch_id).metadata
            names.append(str(metadata.get("group_name") or metadata.get("material_name") or patch_id))
        rows.append({
            "path_id": event.path_id,
            "order": event.order,
            "arrival_ms_absolute": event.arrival_time_absolute_s * 1000.0,
            "arrival_ms_relative": event.arrival_time_relative_s * 1000.0,
            "path_length_m": event.path_length_m,
            "amplitude": event.amplitude,
            "azimuth_deg": event.azimuth_deg,
            "source_relative_azimuth_deg": event.source_relative_azimuth_deg,
            "elevation_deg": event.elevation_deg,
            "ancestry": "Direct" if not names else " -> ".join(names),
        })
    return rows


def _csv_from_rows(rows: Sequence[Dict[str, Any]]) -> str:
    if not rows:
        return ""
    fields = list(rows[0].keys())
    handle = io.StringIO()
    writer = csv.DictWriter(handle, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return handle.getvalue()


def _sparse_impulse_plot(result: SimulationResult) -> List[Dict[str, float]]:
    return [
        {
            "path_id": event.path_id,
            "order": event.order,
            "time_ms": event.arrival_time_relative_s * 1000.0,
            "amplitude": event.amplitude,
        }
        for event in result.sorted_events()
    ]


def _event_band_amplitude(event: Any, scene: Scene, result: SimulationResult, band_index: int) -> float:
    reflection_gain = 1.0
    for patch_id in event.patch_sequence:
        reflection_gain *= scene.patch(patch_id).reflection_pressure(band_index)
    direct_distance = math.dist(result.source, result.receiver)
    path_length = max(float(event.path_length_m), 1.0e-12)
    if result.config.normalize_to_direct:
        spreading_gain = direct_distance / path_length
        air_distance = max(0.0, path_length - direct_distance)
    else:
        spreading_gain = 1.0 / path_length
        air_distance = path_length
    return spreading_gain * reflection_gain * (10.0 ** (-(result.config.air_attenuation_db_per_m * air_distance) / 20.0))


def _directional_analysis(result: SimulationResult, scene: Scene, bin_degrees: int = 10) -> Dict[str, Any]:
    events = result.sorted_events()
    band_sparse: Dict[str, List[Dict[str, float]]] = {}
    for band_index, band_hz in enumerate(OCTAVE_BANDS_HZ):
        band_sparse[str(band_index)] = [
            {
                "path_id": event.path_id,
                "order": event.order,
                "band_hz": band_hz,
                "time_ms": event.arrival_time_relative_s * 1000.0,
                "amplitude": _event_band_amplitude(event, scene, result, band_index),
                "source_relative_azimuth_deg": event.source_relative_azimuth_deg,
                "elevation_deg": event.elevation_deg,
            }
            for event in events
        ]

    max_power = max((event.amplitude * event.amplitude for event in events), default=0.0)
    angle_plane = [
        {
            "path_id": event.path_id,
            "order": event.order,
            "azimuth_deg": event.source_relative_azimuth_deg,
            "elevation_deg": event.elevation_deg,
            "amplitude": event.amplitude,
            "power": event.amplitude * event.amplitude,
            "relative_marker_radius": 0.0 if max_power <= 0.0 else math.sqrt((event.amplitude * event.amplitude) / max_power),
        }
        for event in events
    ]

    bin_count = max(1, int(math.ceil(360.0 / float(bin_degrees))))
    bins = [{"center_deg": -180.0 + (i + 0.5) * bin_degrees, "power": 0.0, "path_count": 0} for i in range(bin_count)]
    for event in events:
        azimuth = ((event.source_relative_azimuth_deg + 180.0) % 360.0) - 180.0
        index = min(bin_count - 1, max(0, int(math.floor((azimuth + 180.0) / bin_degrees))))
        bins[index]["power"] += event.amplitude * event.amplitude
        bins[index]["path_count"] += 1
    max_bin_power = max((row["power"] for row in bins), default=0.0)
    for row in bins:
        row["relative_power"] = 0.0 if max_bin_power <= 0.0 else row["power"] / max_bin_power
        row["db_relative"] = None if row["power"] <= 0.0 or max_bin_power <= 0.0 else 10.0 * math.log10(row["power"] / max_bin_power)

    return {
        "azimuth_reference": "0 degrees is the direct source direction at the receiver, following Borish's polar plots.",
        "band_sparse_ir": band_sparse,
        "angle_plane": angle_plane,
        "polar_power": {
            "bin_degrees": bin_degrees,
            "bins": bins,
        },
    }


def check_mesh_json(payload_json: str) -> str:
    payload = json.loads(payload_json)
    mesh = payload.get("mesh", payload)
    options = payload.get("options", {})

    missing_materials = unassigned_material_report(mesh)
    if missing_materials:
        preview = "; ".join(f"face {item['face_index']}: {item['surface']}" for item in missing_materials[:16])
        if len(missing_materials) > 16:
            preview += f"; ... {len(missing_materials) - 16} more"
        raise ValueError(
            "Cannot run ISM: unassigned acoustic material coefficients. "
            f"{len(missing_materials)} surface(s) are missing explicit octave-band absorption. "
            "Assign material coefficients to every surface before simulation. "
            + preview
        )
    allow_open_mesh = bool(options.get("allow_open_mesh", payload.get("allow_open_mesh", False)))
    report = mesh_closure_report(mesh)

    if allow_open_mesh and not report.get("closed"):
        report["can_simulate"] = True
        report["diagnostic_open_mesh_allowed"] = True
        warnings = list(report.get("warnings", []))
        warnings.append(
            "Open-mesh diagnostic mode is enabled. Simulation is allowed, but results are diagnostic only."
        )
        report["warnings"] = warnings

    if (report.get("closed") or allow_open_mesh) and payload.get("source") is not None and payload.get("receiver") is not None:
        default_absorption = payload.get("default_absorption", [0.05] * len(OCTAVE_BANDS_HZ))
        scene, report = scene_from_mesh_json(
            mesh,
            default_absorption=default_absorption,
        )
        source = _vec3(payload["source"])
        receiver = _vec3(payload["receiver"])
        report["source_inside_scene"] = scene.point_inside(source)
        report["receiver_inside_scene"] = scene.point_inside(receiver)
        report["direct_path_blocked"] = scene.segment_blocked(source, receiver, endpoint_epsilon=1.0e-5)
    if missing_materials:
        warnings = list(report.get("warnings", []))
        warnings.append(f"{len(missing_materials)} surface(s) are missing explicit material absorption. Run ISM will be blocked until all surfaces are assigned.")
        report["warnings"] = warnings

    return json.dumps(report)


def _is_truthy_option(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "auto"}
    return default


def _run_borish_once(
    scene: Scene,
    source: Vec3,
    receiver: Vec3,
    options: Dict[str, Any],
    *,
    max_order: int,
    max_time_s: float,
    max_nodes: int,
    decay_target: str,
    closure: Dict[str, Any],
) -> Tuple[SimulationResult, List[float], float, Dict[str, Any]]:
    config = SimulationConfig(
        max_order=max_order,
        max_time_s=max_time_s,
        speed_of_sound=float(options.get("speed_of_sound", 343.0)),
        sample_rate=int(options.get("sample_rate", 48000)),
        band_index=None,
        time_reference="direct",
        max_nodes=max_nodes,
        air_attenuation_db_per_m=float(options.get("air_attenuation_db_per_m", 0.0)),
        two_sided_reflectors=False,
    )
    solver = EarlyReflectionSolver(scene, source, receiver, config)
    result = solver.run(diagnose_inside=True)
    samples, ir_scale = build_impulse_response(result, reference="direct")
    result_dict = _result_to_dict(result, scene, ir_scale, decay_target=decay_target)
    result_dict["closure"] = closure
    result_dict["diagnostics"]["two_sided_reflectors"] = bool(result.config.two_sided_reflectors)
    result_dict["diagnostics"]["open_mesh_diagnostic_run"] = bool(closure.get("diagnostic_open_mesh_run", False))
    result_dict["room_acoustics"] = _room_acoustics_estimate(result, scene, closure)
    if closure.get("diagnostic_open_mesh_run"):
        result_dict["diagnostics"]["validity"] = "diagnostic_open_mesh"
        result_dict["diagnostics"]["warning"] = (
            "This result was generated from a non-closed mesh. It is useful for geometry debugging, "
            "but it is not a complete or physically validated acoustic enclosure result."
        )
    else:
        result_dict["diagnostics"]["validity"] = "closed_mesh"
    return result, samples, ir_scale, result_dict


def _auto_solve_borish_decay(
    scene: Scene,
    source: Vec3,
    receiver: Vec3,
    options: Dict[str, Any],
    closure: Dict[str, Any],
    *,
    decay_target: str,
) -> Tuple[SimulationResult, List[float], float, Dict[str, Any], Dict[str, Any]]:
    order_cap = max(0, int(options.get("max_order", 8)))
    max_nodes = max(1, int(options.get("max_nodes", 250000)))
    initial_time_s = max(0.001, float(options.get("max_time_s", 0.120)))
    time_cap_s = max(initial_time_s, float(options.get("auto_max_time_s", 2.0)))
    max_iterations = max(1, int(options.get("auto_max_iterations", 12)))

    order = min(order_cap, max(0, int(options.get("initial_order", min(2, order_cap)))))
    time_s = initial_time_s
    iterations: List[Dict[str, Any]] = []
    final_status = "iteration_limit"
    last: Optional[Tuple[SimulationResult, List[float], float, Dict[str, Any]]] = None
    best_complete_traversal: Optional[Tuple[SimulationResult, List[float], float, Dict[str, Any]]] = None

    for iteration_index in range(max_iterations):
        result, samples, ir_scale, result_dict = _run_borish_once(
            scene,
            source,
            receiver,
            options,
            max_order=order,
            max_time_s=time_s,
            max_nodes=max_nodes,
            decay_target=decay_target,
            closure=closure,
        )
        last = (result, samples, ir_scale, result_dict)
        if not result.stats.hit_node_limit:
            best_complete_traversal = last
        decay = result_dict["ism_decay"]
        completeness = result_dict["diagnostics"]["completeness"]
        min_decay = min((band.get("energy_dynamic_range_db", 0.0) for band in decay["bands"]), default=0.0)
        iterations.append({
            "iteration": iteration_index + 1,
            "max_order": order,
            "max_time_s": time_s,
            "paths": len(result.events),
            "nodes_reflected": result.stats.nodes_reflected,
            "node_limit_hit": result.stats.hit_node_limit,
            "order_pruned_nodes": result.stats.order_pruned_nodes,
            "complete_within_time_radius": completeness["complete_within_time_radius"],
            "decay_valid": decay["valid"],
            "valid_band_count": decay["valid_band_count"],
            "band_count": decay["band_count"],
            "min_energy_dynamic_range_db": min_decay,
        })

        if decay["valid"]:
            final_status = "target_satisfied"
            break
        if result.stats.hit_node_limit:
            final_status = "node_budget_exceeded"
            if best_complete_traversal is not None:
                last = best_complete_traversal
            break
        if not completeness["complete_within_time_radius"]:
            if order < order_cap:
                order += 1
                continue
            final_status = "order_cap_exceeded"
            break
        if time_s < time_cap_s:
            next_time_s = min(time_cap_s, max(time_s + 0.050, time_s * 1.5))
            if next_time_s <= time_s + 1.0e-12:
                final_status = "time_cap_exceeded"
                break
            time_s = next_time_s
            if order < order_cap:
                order += 1
            continue
        final_status = "decay_depth_not_reached"
        break

    if last is None:
        raise RuntimeError("Auto solver did not run")

    selected_result = last[0]
    auto_report = {
        "enabled": True,
        "status": final_status,
        "target_metric": decay_target,
        "order_cap": order_cap,
        "node_cap": max_nodes,
        "initial_time_s": initial_time_s,
        "time_cap_s": time_cap_s,
        "selected_max_order": selected_result.config.max_order,
        "selected_max_time_s": selected_result.config.max_time_s,
        "iterations": iterations,
    }
    return (*last, auto_report)


def run_simulation_json(payload_json: str) -> str:
    payload = json.loads(payload_json)
    mesh = payload["mesh"]
    options = payload.get("options", {})

    missing_materials = unassigned_material_report(mesh)
    if missing_materials:
        preview = "; ".join(f"face {item['face_index']}: {item['surface']}" for item in missing_materials[:16])
        if len(missing_materials) > 16:
            preview += f"; ... {len(missing_materials) - 16} more"
        raise ValueError(
            "Cannot run ISM: unassigned acoustic material coefficients. "
            f"{len(missing_materials)} surface(s) are missing explicit octave-band absorption. "
            "Assign material coefficients to every surface before simulation. "
            + preview
        )
    default_absorption = [0.0] * len(OCTAVE_BANDS_HZ)
    scene, closure = scene_from_mesh_json(
        mesh,
        default_absorption=default_absorption,
        auto_flip_normals=bool(options.get("auto_flip_normals", True)),
    )

    source = _vec3(payload["source"])
    receiver = _vec3(payload["receiver"])
    decay_target = str(options.get("decay_target", "t30"))
    auto_solve_decay = _is_truthy_option(options.get("auto_solve_decay"), default=False)

    if auto_solve_decay:
        result, samples, ir_scale, result_dict, auto_report = _auto_solve_borish_decay(
            scene,
            source,
            receiver,
            options,
            closure,
            decay_target=decay_target,
        )
    else:
        max_order = int(options.get("max_order", 3))
        max_time_s = float(options.get("max_time_s", 0.120))
        max_nodes = int(options.get("max_nodes", 250000))
        result, samples, ir_scale, result_dict = _run_borish_once(
            scene,
            source,
            receiver,
            options,
            max_order=max_order,
            max_time_s=max_time_s,
            max_nodes=max_nodes,
            decay_target=decay_target,
            closure=closure,
        )
        auto_report = {
            "enabled": False,
            "status": "manual",
            "target_metric": decay_target,
            "order_cap": max_order,
            "node_cap": max_nodes,
            "initial_time_s": max_time_s,
            "time_cap_s": max_time_s,
            "iterations": [],
        }

    if result.source_inside_scene is not True or result.receiver_inside_scene is not True:
        # Return the diagnostic result but mark it as unsafe.  The browser UI also blocks this.
        pass

    wav = _wav_bytes(samples, result.config.sample_rate)
    directional_ir = _directional_ir_payload(result, scene, reference="direct")
    result_dict["auto_solver"] = auto_report
    toa = _toa_table(result, scene)

    response = {
        "result": result_dict,
        "toa": toa,
        "toa_csv": _csv_from_rows(toa),
        "impulse_response": {
            "sample_rate": result.config.sample_rate,
            "sample_count": len(samples),
            "duration_s": len(samples) / float(result.config.sample_rate),
            "scale": ir_scale,
            "ir_mode": "broadband_mono",
            "band_aggregation": "energy_average_across_octave_absorption_coefficients",
            "sparse": _sparse_impulse_plot(result),
            "band_sparse": result_dict["analysis"]["band_sparse_ir"],
        },
        "auralization": {
            "status": "not_implemented",
            "reason": "Directional convolution/rendering requires an explicitly selected receiver, loudspeaker, Ambisonic, or HRTF model.",
            "available_input": "directional_ir",
        },
        "wav_base64": base64.b64encode(wav).decode("ascii"),
        "directional_ir": directional_ir,
    }
    return json.dumps(response)
