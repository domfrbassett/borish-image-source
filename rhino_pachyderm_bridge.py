"""Rhino 8 / Pachyderm bridge for ``borish_core``.

Run this file with Rhino 8's ScriptEditor (Python 3) or ``RunPythonScript``.
It works on selected closed Breps, Extrusions, or closed Meshes in the active
Rhino document.  The source and receiver are placed interactively in a viewport.

The script stores the last result in ``scriptcontext.sticky`` and exposes four
menu actions:

* Simulate: mesh selected NURBS/mesh room geometry and calculate early paths.
* SaveLastAncestry: write JSON and CSV reflection ancestry again.
* WriteLastIR: write a WAV from the stored result again.
* BakeLastPaths: add reflection polylines to a Rhino layer.

This bridge is intentionally independent of Pachyderm's internal C# object
model.  It consumes the same Rhino geometry used by Pachyderm and can parse
simple absorption user-text keys or an explicit JSON material map.
"""

from __future__ import annotations

import json
import math
import os
import sys
import traceback
import System
from typing import Dict, List, Optional, Sequence, Tuple

import Rhino
import Rhino.Geometry as rg
import Rhino.Input.Custom as ric
import Rhino.DocObjects as rdo
import rhinoscriptsyntax as rs
import scriptcontext as sc

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from borish_core import (  # noqa: E402
    OCTAVE_BANDS_HZ,
    EarlyReflectionSolver,
    ReflectorPatch,
    Scene,
    SimulationCancelled,
    SimulationConfig,
    Triangle,
    build_impulse_response,
    save_ancestry_csv,
    save_ancestry_json,
    save_result_bundle,
    write_wav_pcm16,
)

STICKY_KEY = "borish_pachyderm.last"
Vec3 = Tuple[float, float, float]


def _pt(point: rg.Point3d, scale: float = 1.0) -> Vec3:
    return (float(point.X) * scale, float(point.Y) * scale, float(point.Z) * scale)


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _normalize(a: Vec3) -> Vec3:
    length = math.sqrt(_dot(a, a))
    if length <= 1.0e-15:
        raise ValueError("Degenerate triangle")
    return (a[0] / length, a[1] / length, a[2] / length)


def _parse_absorption(value: Optional[str]) -> Optional[Tuple[float, ...]]:
    if not value:
        return None
    cleaned = value.replace("[", " ").replace("]", " ").replace(";", " ").replace(",", " ")
    values: List[float] = []
    for token in cleaned.split():
        try:
            values.append(float(token))
        except ValueError:
            continue
    if len(values) == 1:
        values *= 8
    if len(values) < 8:
        return None
    return tuple(max(0.0, min(1.0, x)) for x in values[:8])


def _load_material_map(path: Optional[str]) -> Dict:
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _object_absorption(rhino_object, material_map: Dict, default_alpha: Sequence[float]) -> Tuple[float, ...]:
    object_name = rhino_object.Name or ""
    layer = sc.doc.Layers[rhino_object.Attributes.LayerIndex]
    layer_name = layer.FullPath if layer is not None else ""

    for collection_name, key in (
        ("by_object_name", object_name),
        ("by_layer", layer_name),
    ):
        collection = material_map.get(collection_name, {})
        if key and key in collection:
            values = tuple(float(x) for x in collection[key])
            return values if len(values) == 8 else tuple(values[0] for _ in range(8))

    material_name = ""
    try:
        material_index = rhino_object.Attributes.MaterialIndex
        if material_index >= 0:
            material = sc.doc.Materials[material_index]
            material_name = material.Name or ""
    except Exception:
        pass
    by_material = material_map.get("by_material", {})
    if material_name and material_name in by_material:
        values = tuple(float(x) for x in by_material[material_name])
        return values if len(values) == 8 else tuple(values[0] for _ in range(8))

    # Several deliberately generic keys are supported because Pachyderm
    # versions and office templates differ in how custom acoustic user text is
    # stored.  An explicit JSON map remains the most reproducible option.
    keys = (
        "PachydermAbsorption", "Pach_Absorption", "PachAbsorption",
        "AcousticAbsorption", "Absorption", "absorption", "Alpha", "alpha",
    )
    for key in keys:
        for owner in (rhino_object.Geometry, rhino_object.Attributes):
            try:
                parsed = _parse_absorption(owner.GetUserString(key))
                if parsed is not None:
                    return parsed
            except Exception:
                pass

    return tuple(float(x) for x in default_alpha)


def _select_room_objects():
    getter = ric.GetObject()
    getter.SetCommandPrompt("Select closed Breps/Extrusions/Meshes used as the Pachyderm room")
    getter.GeometryFilter = (
        rdo.ObjectType.Brep | rdo.ObjectType.Extrusion | rdo.ObjectType.Mesh
    )
    getter.GroupSelect = True
    getter.SubObjectSelect = False
    getter.GetMultiple(1, 0)
    if getter.CommandResult() != Rhino.Commands.Result.Success:
        return []
    return [getter.Object(i).Object() for i in range(getter.ObjectCount)]


def _make_meshing_parameters(max_edge: float, tolerance: float) -> rg.MeshingParameters:
    parameters = rg.MeshingParameters.QualityRenderMesh
    if max_edge > 0.0:
        parameters.MaximumEdgeLength = max_edge
    parameters.MinimumEdgeLength = max(tolerance * 2.0, max_edge * 0.02 if max_edge > 0.0 else tolerance * 2.0)
    parameters.Tolerance = tolerance
    parameters.RefineGrid = True
    parameters.SimplePlanes = True
    return parameters


def _mesh_face(face: rg.BrepFace, parameters: rg.MeshingParameters):
    face_brep = face.DuplicateFace(False)
    meshes = rg.Mesh.CreateFromBrep(face_brep, parameters)
    if not meshes:
        return None
    combined = rg.Mesh()
    for mesh in meshes:
        combined.Append(mesh)
    combined.Faces.ConvertQuadsToTriangles()
    combined.Normals.ComputeNormals()
    combined.Compact()
    return combined


def _face_outward_normal(face: rg.BrepFace, point: rg.Point3d) -> rg.Vector3d:
    ok, u, v = face.ClosestPoint(point)
    if not ok:
        # Mid-domain fallback.
        u = 0.5 * (face.Domain(0).T0 + face.Domain(0).T1)
        v = 0.5 * (face.Domain(1).T0 + face.Domain(1).T1)
    normal = face.NormalAt(u, v)
    if face.OrientationIsReversed:
        normal.Reverse()
    normal.Unitize()
    return normal


def _append_triangle(
    triangles: List[Triangle],
    patch_id: int,
    a: Vec3,
    b: Vec3,
    c: Vec3,
    expected_normal: Vec3,
) -> int:
    tri_normal = _cross(_sub(b, a), _sub(c, a))
    if _dot(tri_normal, expected_normal) < 0.0:
        b, c = c, b
    triangle_id = len(triangles)
    triangles.append(Triangle(triangle_id, a, b, c, patch_id))
    return triangle_id


def _brep_to_scene_parts(
    rhino_object,
    brep: rg.Brep,
    parameters: rg.MeshingParameters,
    absorption: Tuple[float, ...],
    triangles: List[Triangle],
    patches: List[ReflectorPatch],
    tolerance: float,
    scale_to_m: float,
    source_point_model: rg.Point3d,
) -> None:
    if not brep.IsSolid:
        raise ValueError(f"Object '{rhino_object.Name or rhino_object.Id}' is not a closed solid Brep")
    duplicate = brep.DuplicateBrep()
    if duplicate.SolidOrientation == rg.BrepSolidOrientation.Inward:
        duplicate.Flip()
    domain_inside_solid = bool(duplicate.IsPointInside(source_point_model, tolerance, False))
    if not domain_inside_solid:
        # For an interior obstacle, the acoustic-domain normal points into the solid.
        duplicate.Flip()

    layer = sc.doc.Layers[rhino_object.Attributes.LayerIndex]
    layer_name = layer.FullPath if layer is not None else ""

    for face_index, face in enumerate(duplicate.Faces):
        mesh = _mesh_face(face, parameters)
        if mesh is None or mesh.Faces.Count == 0:
            continue
        planar_ok, plane = face.TryGetPlane(tolerance)

        if planar_ok:
            center = mesh.GetBoundingBox(True).Center
            outward = _face_outward_normal(face, center)
            plane_normal = rg.Vector3d(plane.Normal)
            plane_normal.Unitize()
            if rg.Vector3d.Multiply(plane_normal, outward) < 0.0:
                plane_normal.Reverse()
            normal = (float(plane_normal.X), float(plane_normal.Y), float(plane_normal.Z))
            patch_id = len(patches)
            tri_ids: List[int] = []
            for mesh_face in mesh.Faces:
                indices = (mesh_face.A, mesh_face.B, mesh_face.C)
                a, b, c = (_pt(mesh.Vertices[index], scale_to_m) for index in indices)
                tri_ids.append(_append_triangle(triangles, patch_id, a, b, c, normal))
            if tri_ids:
                patches.append(ReflectorPatch(
                    patch_id,
                    normal,
                    _dot(_pt(plane.Origin, scale_to_m), normal),
                    tuple(tri_ids),
                    absorption,
                    {
                        "source": "rhino_brep",
                        "object_id": str(rhino_object.Id),
                        "object_name": rhino_object.Name or "",
                        "layer": layer_name,
                        "face_index": face_index,
                        "planar_face": True,
                        "acoustic_domain_inside_solid": domain_inside_solid,
                    },
                ))
        else:
            # A curved NURBS face is approximated as one planar reflector per
            # triangle, exactly as Borish's piecewise-planar limitation requires.
            for local_triangle_index, mesh_face in enumerate(mesh.Faces):
                indices = (mesh_face.A, mesh_face.B, mesh_face.C)
                points = [mesh.Vertices[index] for index in indices]
                center = rg.Point3d(
                    sum(point.X for point in points) / 3.0,
                    sum(point.Y for point in points) / 3.0,
                    sum(point.Z for point in points) / 3.0,
                )
                outward = _face_outward_normal(face, center)
                normal = (float(outward.X), float(outward.Y), float(outward.Z))
                a, b, c = (_pt(point, scale_to_m) for point in points)
                patch_id = len(patches)
                triangle_id = _append_triangle(triangles, patch_id, a, b, c, normal)
                corrected = triangles[triangle_id]
                normal = _normalize(_cross(_sub(corrected.b, corrected.a), _sub(corrected.c, corrected.a)))
                patches.append(ReflectorPatch(
                    patch_id,
                    normal,
                    _dot(corrected.a, normal),
                    (triangle_id,),
                    absorption,
                    {
                        "source": "rhino_brep",
                        "object_id": str(rhino_object.Id),
                        "object_name": rhino_object.Name or "",
                        "layer": layer_name,
                        "face_index": face_index,
                        "triangle_index": local_triangle_index,
                        "planar_face": False,
                        "acoustic_domain_inside_solid": domain_inside_solid,
                    },
                ))


def _mesh_to_scene_parts(
    rhino_object,
    mesh: rg.Mesh,
    absorption: Tuple[float, ...],
    triangles: List[Triangle],
    patches: List[ReflectorPatch],
    scale_to_m: float,
    tolerance: float,
    source_point_model: rg.Point3d,
) -> None:
    if not mesh.IsClosed:
        raise ValueError(f"Object '{rhino_object.Name or rhino_object.Id}' is not a closed mesh")
    duplicate = mesh.DuplicateMesh()
    duplicate.UnifyNormals()
    orientation = duplicate.SolidOrientation()
    if orientation == 0:
        raise ValueError(
            f"Object '{rhino_object.Name or rhino_object.Id}' is closed but has no consistent solid orientation"
        )
    domain_inside_solid = bool(duplicate.IsPointInside(source_point_model, tolerance, False))
    # Start with solid-outward winding; invert for an obstacle whose acoustic domain is outside.
    flip = (orientation < 0) != (not domain_inside_solid)
    layer = sc.doc.Layers[rhino_object.Attributes.LayerIndex]
    layer_name = layer.FullPath if layer is not None else ""

    for face_index, face in enumerate(duplicate.Faces):
        raw_indices = [face.A, face.B, face.C] if face.IsTriangle else [face.A, face.B, face.C, face.D]
        if flip:
            raw_indices.reverse()
        local_tris = (
            [(raw_indices[0], raw_indices[1], raw_indices[2])]
            if len(raw_indices) == 3
            else [
                (raw_indices[0], raw_indices[1], raw_indices[2]),
                (raw_indices[0], raw_indices[2], raw_indices[3]),
            ]
        )
        patch_id = len(patches)
        tri_ids: List[int] = []
        normal: Optional[Vec3] = None
        first_point: Optional[Vec3] = None
        for ia, ib, ic in local_tris:
            a, b, c = _pt(duplicate.Vertices[ia], scale_to_m), _pt(duplicate.Vertices[ib], scale_to_m), _pt(duplicate.Vertices[ic], scale_to_m)
            tri_normal = _normalize(_cross(_sub(b, a), _sub(c, a)))
            if normal is None:
                normal = tri_normal
                first_point = a
            tri_ids.append(_append_triangle(triangles, patch_id, a, b, c, normal))
        if tri_ids and normal is not None and first_point is not None:
            patches.append(ReflectorPatch(
                patch_id,
                normal,
                _dot(first_point, normal),
                tuple(tri_ids),
                absorption,
                {
                    "source": "rhino_mesh",
                    "object_id": str(rhino_object.Id),
                    "object_name": rhino_object.Name or "",
                    "layer": layer_name,
                    "face_index": face_index,
                    "planar_face": True,
                    "acoustic_domain_inside_solid": domain_inside_solid,
                },
            ))


def build_scene_from_rhino(
    rhino_objects,
    *,
    max_edge_length: float,
    material_map: Dict,
    default_absorption: Sequence[float],
    tolerance: float,
    scale_to_m: float,
    source_point_model: rg.Point3d,
) -> Scene:
    parameters = _make_meshing_parameters(max_edge_length, tolerance)
    triangles: List[Triangle] = []
    patches: List[ReflectorPatch] = []

    for rhino_object in rhino_objects:
        alpha = _object_absorption(rhino_object, material_map, default_absorption)
        geometry = rhino_object.Geometry
        if isinstance(geometry, rg.Extrusion):
            geometry = geometry.ToBrep()
        if isinstance(geometry, rg.Brep):
            _brep_to_scene_parts(
                rhino_object, geometry, parameters, alpha,
                triangles, patches, tolerance, scale_to_m, source_point_model,
            )
        elif isinstance(geometry, rg.Mesh):
            _mesh_to_scene_parts(
                rhino_object, geometry, alpha, triangles, patches,
                scale_to_m, tolerance, source_point_model,
            )
        else:
            raise TypeError(f"Unsupported Rhino geometry: {type(geometry)}")

    return Scene(triangles, patches)


def _ask_band_index() -> Optional[int]:
    labels = ["BroadbandAverage"] + [f"{band:g}Hz" for band in OCTAVE_BANDS_HZ]
    selected = rs.ListBox(labels, "Choose the absorption band used for the mono IR", "Borish band")
    if selected is None:
        raise SimulationCancelled("Cancelled")
    if selected == "BroadbandAverage":
        return None
    return labels.index(selected) - 1


def _default_output_base() -> str:
    document_path = sc.doc.Path
    folder = os.path.dirname(document_path) if document_path else os.path.expanduser("~")
    return os.path.join(folder, "borish_early_ir")


def simulate() -> None:
    rhino_objects = _select_room_objects()
    if not rhino_objects:
        return
    source_point = rs.GetPoint("Place/select the source position")
    if source_point is None:
        return
    receiver_point = rs.GetPoint("Place/select the receiver position")
    if receiver_point is None:
        return

    model_tolerance = max(sc.doc.ModelAbsoluteTolerance, 1.0e-7)
    scale_to_m = Rhino.RhinoMath.UnitScale(sc.doc.ModelUnitSystem, Rhino.UnitSystem.Meters)
    max_order = rs.GetInteger("Maximum reflection order", 3, 0, 12)
    if max_order is None:
        return
    max_time_ms = rs.GetReal("Early-reflection window after direct sound (ms)", 120.0, 0.0)
    if max_time_ms is None:
        return
    sample_rate = rs.GetInteger("Impulse-response sample rate", 48000, 8000, 384000)
    if sample_rate is None:
        return
    speed = rs.GetReal("Speed of sound (m/s)", 343.0, 250.0, 400.0)
    if speed is None:
        return
    default_edge_model_units = 0.5 / scale_to_m
    max_edge = rs.GetReal(
        "Maximum NURBS tessellation edge length (model units)",
        default_edge_model_units,
        model_tolerance * 10.0,
    )
    if max_edge is None:
        return
    default_alpha_value = rs.GetReal("Default energy absorption coefficient", 0.05, 0.0, 1.0)
    if default_alpha_value is None:
        return
    band_index = _ask_band_index()
    max_nodes = rs.GetInteger("Hard virtual-source node limit", 2000000, 1000)
    if max_nodes is None:
        return

    material_path = rs.OpenFileName(
        "Optional material map JSON (Cancel to use user text/default)",
        "JSON files (*.json)|*.json||",
    )
    material_map = _load_material_map(material_path)
    default_alpha = material_map.get("default_absorption", [default_alpha_value] * 8)

    output_base = rs.SaveFileName(
        "Choose output basename (WAV/JSON/CSV will be written)",
        "WAV file (*.wav)|*.wav||",
        filename=_default_output_base() + ".wav",
    )
    if output_base is None:
        return
    output_base = os.path.splitext(output_base)[0]

    Rhino.RhinoApp.WriteLine("Meshing room geometry...")
    scene = build_scene_from_rhino(
        rhino_objects,
        max_edge_length=max_edge,
        material_map=material_map,
        default_absorption=default_alpha,
        tolerance=model_tolerance,
        scale_to_m=scale_to_m,
        source_point_model=source_point,
    )
    Rhino.RhinoApp.WriteLine(
        "Scene: {0} reflection patches, {1} triangles".format(len(scene.patches), len(scene.triangles))
    )

    config = SimulationConfig(
        max_order=max_order,
        max_time_s=max_time_ms / 1000.0,
        speed_of_sound=speed,
        sample_rate=sample_rate,
        band_index=band_index,
        time_reference="direct",
        plane_epsilon=model_tolerance * scale_to_m * 0.01,
        geometry_tolerance=model_tolerance * scale_to_m * 5.0,
        endpoint_epsilon=model_tolerance * scale_to_m * 20.0,
        max_nodes=max_nodes,
    )
    solver = EarlyReflectionSolver(
        scene, _pt(source_point, scale_to_m), _pt(receiver_point, scale_to_m), config
    )

    def progress(stats, order):
        Rhino.RhinoApp.SetCommandPrompt(
            "Borish: {0:,} nodes, order {1}, {2:,} accepted (Esc cancels)".format(
                stats.nodes_reflected, order, stats.accepted_reflections
            )
        )

    def cancel():
        return bool(Rhino.RhinoApp.EscapeKeyPressed)

    try:
        result = solver.run(progress_callback=progress, cancel_callback=cancel, diagnose_inside=True)
    finally:
        Rhino.RhinoApp.SetCommandPrompt("Command")

    outputs = save_result_bundle(output_base, result, scene)
    sc.sticky[STICKY_KEY] = {
        "result": result, "scene": scene, "outputs": outputs,
        "model_units_to_m": scale_to_m,
    }

    Rhino.RhinoApp.WriteLine("Borish simulation complete.")
    Rhino.RhinoApp.WriteLine("Accepted paths (including direct): {0}".format(len(result.events)))
    Rhino.RhinoApp.WriteLine("Reflected virtual-source nodes: {0:,}".format(result.stats.nodes_reflected))
    Rhino.RhinoApp.WriteLine("Source inside closed mesh: {0}".format(result.source_inside_scene))
    Rhino.RhinoApp.WriteLine("Receiver inside closed mesh: {0}".format(result.receiver_inside_scene))
    if result.stats.hit_node_limit:
        Rhino.RhinoApp.WriteLine("WARNING: node limit reached; result is incomplete.")
    for kind, path in outputs.items():
        Rhino.RhinoApp.WriteLine("{0}: {1}".format(kind.upper(), path))

    if rs.GetString("Bake accepted paths into the Rhino document?", "No", ["Yes", "No"]) == "Yes":
        bake_last_paths()


def _last():
    payload = sc.sticky.get(STICKY_KEY)
    if payload is None:
        rs.MessageBox("No Borish result is stored in this Rhino session.", 0, "Borish")
        return None
    return payload


def save_last_ancestry() -> None:
    payload = _last()
    if payload is None:
        return
    result, scene = payload["result"], payload["scene"]
    path = rs.SaveFileName("Save reflection ancestry", "JSON file (*.json)|*.json||", filename="borish_ancestry.json")
    if not path:
        return
    base = os.path.splitext(path)[0]
    save_ancestry_json(base + ".json", result, scene)
    save_ancestry_csv(base + ".csv", result, scene)
    Rhino.RhinoApp.WriteLine("Saved: {0}.json and {0}.csv".format(base))


def write_last_ir() -> None:
    payload = _last()
    if payload is None:
        return
    result = payload["result"]
    path = rs.SaveFileName("Save early impulse response", "WAV file (*.wav)|*.wav||", filename="borish_early_ir.wav")
    if not path:
        return
    samples, _scale = build_impulse_response(result)
    write_wav_pcm16(path, samples, result.config.sample_rate)
    Rhino.RhinoApp.WriteLine("Saved: {0}".format(path))


def _ensure_layer(full_path: str) -> int:
    # A flat layer name avoids assumptions about nested-layer creation APIs.
    name = full_path.replace("::", "_")
    existing = sc.doc.Layers.FindName(name)
    if existing is not None:
        return existing.Index
    layer = rdo.Layer()
    layer.Name = name
    return sc.doc.Layers.Add(layer)


def bake_last_paths() -> None:
    payload = _last()
    if payload is None:
        return
    result = payload["result"]
    scale_to_m = float(payload.get("model_units_to_m", 1.0))
    layer_index = _ensure_layer("Borish_Early_Reflections")
    attributes = rdo.ObjectAttributes()
    attributes.LayerIndex = layer_index
    group_index = sc.doc.Groups.Add("Borish paths")
    count = 0
    for event in result.sorted_events():
        polyline = rg.Polyline([
            rg.Point3d(point[0] / scale_to_m, point[1] / scale_to_m, point[2] / scale_to_m)
            for point in event.path_vertices
        ])
        object_id = sc.doc.Objects.AddPolyline(polyline, attributes)
        if object_id != System.Guid.Empty:
            try:
                sc.doc.Groups.AddToGroup(group_index, object_id)
            except Exception:
                pass
            count += 1
    sc.doc.Views.Redraw()
    Rhino.RhinoApp.WriteLine("Baked {0} paths to layer Borish_Early_Reflections".format(count))


def main() -> None:
    actions = ["Simulate", "SaveLastAncestry", "WriteLastIR", "BakeLastPaths"]
    action = rs.ListBox(actions, "Choose a Borish command", "Borish early reflections")
    if action is None:
        return
    try:
        if action == "Simulate":
            simulate()
        elif action == "SaveLastAncestry":
            save_last_ancestry()
        elif action == "WriteLastIR":
            write_last_ir()
        elif action == "BakeLastPaths":
            bake_last_paths()
    except SimulationCancelled:
        Rhino.RhinoApp.WriteLine("Borish simulation cancelled.")
    except Exception as exc:
        Rhino.RhinoApp.WriteLine("Borish error: {0}".format(exc))
        Rhino.RhinoApp.WriteLine(traceback.format_exc())
        rs.MessageBox(str(exc), 0, "Borish error")


if __name__ == "__main__":
    main()
