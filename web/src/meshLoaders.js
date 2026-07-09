import * as THREE from "three";

function parseFaceVertex(token, vertexCount) {
  const raw = token.split("/")[0];
  let index = Number.parseInt(raw, 10);
  if (!Number.isFinite(index)) throw new Error(`Invalid face token: ${token}`);
  if (index > 0) return index - 1;
  return vertexCount + index;
}

export function parseObjText(text) {
  const vertices = [];
  const faces = [];
  let objectName = "OBJ";
  let groupName = "Default";
  let materialName = "Default";

  const lines = text.split(/\r?\n/);
  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const fields = line.split(/\s+/);
    const kind = fields[0].toLowerCase();

    if (kind === "v" && fields.length >= 4) {
      vertices.push([Number(fields[1]), Number(fields[2]), Number(fields[3])]);
    } else if (kind === "o") {
      objectName = fields.slice(1).join(" ") || objectName;
    } else if (kind === "g") {
      groupName = fields.slice(1).join(" ") || groupName;
    } else if (kind === "usemtl") {
      materialName = fields.slice(1).join(" ") || materialName;
    } else if (kind === "f" && fields.length >= 4) {
      faces.push({
        indices: fields.slice(1).map((token) => parseFaceVertex(token, vertices.length)),
        object: objectName,
        group: groupName,
        material: materialName
      });
    }
  }

  if (vertices.length === 0 || faces.length === 0) {
    throw new Error("No vertices/faces found in OBJ file.");
  }
  return { units: "m", vertices, faces };
}

function addThreeObjectMesh(child, vertices, faces, namePrefix) {
  if (!child.isMesh || !child.geometry) return;
  child.updateWorldMatrix(true, false);
  const geometry = child.geometry.index ? child.geometry.toNonIndexed() : child.geometry.clone();
  const position = geometry.getAttribute("position");
  if (!position) return;

  const groupName = child.name || namePrefix || "RhinoMesh";
  const materialName = Array.isArray(child.material)
    ? child.material.map((m) => m?.name || "Material").join("+")
    : child.material?.name || "Material";

  for (let i = 0; i < position.count; i += 3) {
    const base = vertices.length;
    for (let j = 0; j < 3; j++) {
      const v = new THREE.Vector3(position.getX(i + j), position.getY(i + j), position.getZ(i + j));
      child.localToWorld(v);
      vertices.push([v.x, v.y, v.z]);
    }
    faces.push({ indices: [base, base + 1, base + 2], object: namePrefix, group: groupName, material: materialName });
  }
}

export async function load3dmFile(file) {
  const { Rhino3dmLoader } = await import("three/addons/loaders/3DMLoader.js");
  const loader = new Rhino3dmLoader();
  loader.setLibraryPath("https://cdn.jsdelivr.net/npm/rhino3dm@8.4.0/");
  const buffer = await file.arrayBuffer();

  const object = await new Promise((resolve, reject) => {
    loader.parse(buffer, resolve, reject);
  });

  const vertices = [];
  const faces = [];
  object.updateMatrixWorld(true);
  object.traverse((child) => addThreeObjectMesh(child, vertices, faces, file.name));

  if (vertices.length === 0 || faces.length === 0) {
    throw new Error("No mesh geometry was available in the .3dm file. Export an acoustic OBJ mesh or add render meshes.");
  }
  return { units: "m", vertices, faces, note: "Experimental .3dm route: extracted mesh geometry with Rhino3dmLoader." };
}

export async function loadMeshFile(file) {
  const lower = file.name.toLowerCase();
  if (lower.endsWith(".obj")) return parseObjText(await file.text());
  if (lower.endsWith(".3dm")) return load3dmFile(file);
  throw new Error("Unsupported file type. Use OBJ for the reliable MVP path, or .3dm experimentally.");
}

export async function loadExampleObj(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Could not load ${url}: ${response.status}`);
  return parseObjText(await response.text());
}
