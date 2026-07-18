import { loadMeshFile, loadExampleObj } from "./meshLoaders.js";
import { makeViewer } from "./viewer.js";
import { downloadBase64, downloadJson, downloadText } from "./downloads.js";

const elements = {
  fileInput: document.getElementById("fileInput"),
  loadShoebox: document.getElementById("loadShoebox"),
  loadConcave: document.getElementById("loadConcave"),
  checkButton: document.getElementById("checkButton"),
  runButton: document.getElementById("runButton"),
  downloadJson: document.getElementById("downloadJson"),
  downloadCsv: document.getElementById("downloadCsv"),
  downloadWav: document.getElementById("downloadWav"),
  downloadDirectionalIr: document.getElementById("downloadDirectionalIr"),
  recordWebm: document.getElementById("recordWebm"),
  autoSolveDecay: document.getElementById("autoSolveDecay"),
  decayTarget: document.getElementById("decayTarget"),
  log: document.getElementById("log"),
  roomMetrics: document.getElementById("roomMetrics"),
  toaTable: document.getElementById("toaTable"),
  irCanvas: document.getElementById("irCanvas"),
  surfaceStatus: document.getElementById("surfaceStatus"),
  surfaceName: document.getElementById("surfaceName"),
  surfaceMaterialName: document.getElementById("surfaceMaterialName"),
  surfaceAbsorption: document.getElementById("surfaceAbsorption"),
  surfaceScattering: document.getElementById("surfaceScattering"),
  applySurfaceSelected: document.getElementById("applySurfaceSelected"),
  applySurfaceConnected: document.getElementById("applySurfaceConnected"),
  applySurfaceGroup: document.getElementById("applySurfaceGroup"),
  applySurfaceAll: document.getElementById("applySurfaceAll"),
  clearSurfaceSelection: document.getElementById("clearSurfaceSelection"),
  irBandSelect: document.getElementById("irBandSelect"),
  irToolbar: document.getElementById("irToolbar"),
  plotTitle: document.getElementById("plotTitle"),
  plotPrev: document.getElementById("plotPrev"),
  plotNext: document.getElementById("plotNext"),
  plotViewName: document.getElementById("plotViewName"),
  fftCanvas: document.getElementById("fftCanvas"), // legacy: no longer used after plot toggle
};

const viewer = makeViewer(document.getElementById("viewport"));
const worker = new Worker(new URL("./ismWorker.js?v=stat_rt_validation_20260719", import.meta.url), { type: "module" });

let mesh = null;
let lastSimulation = null;
let lastFilenameBase = "borish_result";
let selectedSurfaceIndex = null;
let selectedSurfaceIndices = [];
let userSurfaceCounter = 1;
let plotView = "ir";
const PLOT_VIEWS = ["ir", "fft", "polar"];
let isSyncingSurfaceUi = false;

const OCTAVE_BANDS_HZ = [63, 125, 250, 500, 1000, 2000, 4000, 8000];
const SURFACE_ABSORPTION_IDS = OCTAVE_BANDS_HZ.map((band) => `surfaceAbsorption${band}`);
const SURFACE_SCATTERING_IDS = OCTAVE_BANDS_HZ.map((band) => `surfaceScattering${band}`);

const L_ROOM_MATERIALS = {
  Wall: {
    surfaceName: "Painted plasterboard walls",
    materialName: "Painted plasterboard",
    absorption: [0.10, 0.08, 0.06, 0.05, 0.04, 0.05, 0.06, 0.08],
    scattering: [0.02, 0.03, 0.04, 0.06, 0.08, 0.10, 0.12, 0.15],
  },
  Floor: {
    surfaceName: "Carpeted floor",
    materialName: "Medium carpet on slab",
    absorption: [0.02, 0.06, 0.14, 0.37, 0.60, 0.65, 0.65, 0.70],
    scattering: [0.03, 0.04, 0.06, 0.10, 0.14, 0.18, 0.22, 0.25],
  },
  Ceiling: {
    surfaceName: "Acoustic tile ceiling",
    materialName: "Suspended acoustic tile",
    absorption: [0.30, 0.45, 0.70, 0.85, 0.80, 0.75, 0.70, 0.65],
    scattering: [0.04, 0.05, 0.08, 0.12, 0.16, 0.20, 0.24, 0.28],
  },
};

function log(message) {
  elements.log.textContent = message;
}

function appendLog(message) {
  elements.log.textContent += `\n${message}`;
}

function formatSeconds(value) {
  if (value === null || value === undefined) return "n/a";
  const number = Number(value);
  if (!Number.isFinite(number)) return "n/a";
  return number.toFixed(number < 10 ? 2 : 1);
}

function formatDecayFitSeconds(decay, band, key) {
  if (!decay?.complete_within_time_radius) return "n/a";
  const metricValidity = band?.metric_validity?.[key];
  if (metricValidity && !metricValidity.valid) return "n/a";
  if (!metricValidity && key === decay?.target_metric && !band?.valid) return "n/a";
  const fit = band?.fits?.[key];
  if (!fit?.valid) return "n/a";
  return formatSeconds(band?.[`${key}_s`]);
}

function formatHz(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "n/a";
  return number >= 1000 ? `${number / 1000}k` : String(number);
}

function formatStatus(value) {
  return String(value || "unknown").replace(/_/g, " ");
}

function renderRoomMetrics(result) {
  const decay = result?.ism_decay;
  const geometry = result?.room_acoustics;
  if (!elements.roomMetrics) return;
  if (!decay) {
    elements.roomMetrics.textContent = "Borish ISM decay metrics appear after simulation.";
    return;
  }

  const rtRows = (decay.bands || []).map((band) => `
    <tr>
      <th>${formatHz(band.band_hz)}</th>
      <td>${formatDecayFitSeconds(decay, band, "edt")}</td>
      <td>${formatDecayFitSeconds(decay, band, "t20")}</td>
      <td>${formatDecayFitSeconds(decay, band, "t30")}</td>
      <td>${Number(band.energy_dynamic_range_db || 0).toFixed(1)}</td>
      <td>${band.valid ? "valid" : (band.reason || "n/a")}</td>
    </tr>
  `).join("");
  const validity = decay.valid ? "valid" : "not valid";
  const auto = result?.auto_solver;
  const autoStatus = auto?.enabled ? formatStatus(auto.status) : "manual";
  const reportedOrder = result?.stats?.radius_completion_order || auto?.selected_radius_completion_order || auto?.selected_max_order || result?.config?.max_order || "n/a";
  elements.roomMetrics.innerHTML = `
    <div class="metric-strip">
      <span>Borish ISM ${String(decay.target_metric || "t30").toUpperCase()}: ${validity}</span>
      <span>solver ${autoStatus}</span>
      <span>search order ${reportedOrder}</span>
      <span>time ${Number((auto?.selected_max_time_s ?? result?.config?.max_time_s ?? 0) * 1000).toFixed(0)} ms</span>
      <span>coverage ${decay.valid_band_count || 0}/${decay.band_count || 0}</span>
      <span>required ${Number(decay.validation_required_decay_db || decay.required_decay_db || 0).toFixed(0)} dB</span>
      <span>V ${Number(geometry?.volume_m3 || 0).toFixed(1)} m3</span>
      <span>S ${Number(geometry?.surface_area_m2 || 0).toFixed(1)} m2</span>
    </div>
    <table class="metrics-table">
      <thead><tr><th>Hz</th><th>EDT</th><th>T20</th><th>T30</th><th>range dB</th><th>status</th></tr></thead>
      <tbody>${rtRows}</tbody>
    </table>
  `;
}

function readNumber(id) {
  const value = Number(document.getElementById(id).value);
  if (!Number.isFinite(value)) throw new Error(`Invalid number in ${id}`);
  return value;
}

function readVector(prefix) {
  return [readNumber(`${prefix}X`), readNumber(`${prefix}Y`), readNumber(`${prefix}Z`)];
}

function readPayload() {
  if (!mesh) throw new Error("Load a mesh first.");
  const source = readVector("source");
  const receiver = readVector("receiver");
  return {
    mesh,
    source,
    receiver,
    options: {
      max_order: readNumber("maxOrder"),
      max_time_s: readNumber("maxTimeMs") / 1000.0,
      speed_of_sound: readNumber("speedOfSound"),
      sample_rate: readNumber("sampleRate"),
      band_hz: "broadband",
      ir_mode: "broadband_mono",
      auto_solve_decay: elements.autoSolveDecay?.checked ?? true,
      decay_target: elements.decayTarget?.value || "t30",
      max_nodes: readNumber("maxNodes"),
      auto_max_time_s: readNumber("autoMaxTimeMs") / 1000.0,
      auto_flip_normals: true,
      surface_material_count: mesh.faces.filter(surfaceHasAssignedMaterial).length,
      air_attenuation: readAirOptions(),
      air_attenuation_db_per_m: broadbandAirAttenuationDbPerM(),
    }
  };
}

function setDefaultShoeboxPoints() {
  document.getElementById("sourceX").value = 2;
  document.getElementById("sourceY").value = 3;
  document.getElementById("sourceZ").value = 1.2;
  document.getElementById("receiverX").value = 6;
  document.getElementById("receiverY").value = 5;
  document.getElementById("receiverZ").value = 1.2;
  document.getElementById("maxOrder").value = 12;
}

function setDefaultConcavePoints() {
  document.getElementById("sourceX").value = 10;
  document.getElementById("sourceY").value = 2;
  document.getElementById("sourceZ").value = 1.2;
  document.getElementById("receiverX").value = 2;
  document.getElementById("receiverY").value = 8;
  document.getElementById("receiverZ").value = 1.2;
  document.getElementById("maxOrder").value = 12;
}

function lRoomMaterialProfile(face) {
  const key = String(face?.material || face?.original_material || face?.group || "").toLowerCase();
  if (key.includes("floor")) return L_ROOM_MATERIALS.Floor;
  if (key.includes("ceiling")) return L_ROOM_MATERIALS.Ceiling;
  return L_ROOM_MATERIALS.Wall;
}

function applyLRoomRealisticDefaults(targetMesh) {
  const counts = { Wall: 0, Floor: 0, Ceiling: 0 };
  for (const [index, face] of targetMesh.faces.entries()) {
    const profile = lRoomMaterialProfile(face);
    const importedSurfaceName = face.group || face.name || face.object || profile.surfaceName || `Face_${index}`;
    setFaceAcousticProperties(
      face,
      profile.absorption,
      profile.scattering,
      importedSurfaceName,
      profile.materialName
    );
    face.acoustic_preset = "l_room_realistic_defaults";
    if (profile === L_ROOM_MATERIALS.Floor) counts.Floor += 1;
    else if (profile === L_ROOM_MATERIALS.Ceiling) counts.Ceiling += 1;
    else counts.Wall += 1;
  }
  return counts;
}

function updateMarkers() {
  try {
    viewer.setMarkers(readVector("source"), readVector("receiver"));
  } catch (_) {}
}

function clamp01(value, fallback = 0) {
  const number = Number(value);
  if (!Number.isFinite(number)) return fallback;
  return Math.max(0, Math.min(1, number));
}

function averageAbsorption(value, fallback = 0.05) {
  if (Array.isArray(value) && value.length) {
    const numbers = value.map(Number).filter(Number.isFinite);
    if (numbers.length) return numbers.reduce((a, b) => a + b, 0) / numbers.length;
  }
  if (Number.isFinite(Number(value))) return Number(value);
  return fallback;
}

function readAirOptions() {
  return {
    enabled: true,
    temperature_c: Number(document.getElementById("airTemperatureC")?.value ?? 20),
    relative_humidity_percent: Number(document.getElementById("airRelativeHumidity")?.value ?? 50),
    pressure_kpa: Number(document.getElementById("airPressureKPa")?.value ?? 101.325),
  };
}

function airAbsorptionDbPerMISO9613(frequencyHz, temperatureC = 20, relativeHumidityPercent = 50, pressureKPa = 101.325) {
  const f = Number(frequencyHz);
  const T = Number(temperatureC) + 273.15;
  const T0 = 293.15;
  const T01 = 273.16;
  const p = Number(pressureKPa);
  const pr = 101.325;
  const rh = Math.max(0, Math.min(100, Number(relativeHumidityPercent))) / 100.0;

  if (!Number.isFinite(f) || f <= 0) return 0;
  if (!Number.isFinite(T) || !Number.isFinite(p) || p <= 0) return 0;

  // Saturation vapour pressure ratio.
  const psatOverPr = Math.pow(
    10,
    -6.8346 * Math.pow(T01 / T, 1.261) + 4.6151
  );

  // Molar concentration of water vapour.
  const h = rh * psatOverPr * (pr / p);

  const frO =
    (p / pr) *
    (24.0 + 4.04e4 * h * (0.02 + h) / (0.391 + h));

  const frN =
    (p / pr) *
    Math.pow(T / T0, -0.5) *
    (9.0 + 280.0 * h * Math.exp(-4.170 * (Math.pow(T / T0, -1.0 / 3.0) - 1.0)));

  const alpha =
    8.686 * f * f *
    (
      1.84e-11 * Math.pow(p / pr, -1) * Math.sqrt(T / T0) +
      Math.pow(T / T0, -2.5) *
      (
        0.01275 * Math.exp(-2239.1 / T) / (frO + f * f / frO) +
        0.1068 * Math.exp(-3352.0 / T) / (frN + f * f / frN)
      )
    );

  return Math.max(0, alpha); // dB per metre
}

function airAbsorptionDbPerMByBand() {
  const air = readAirOptions();

  if (!air.enabled) {
    return OCTAVE_BANDS_HZ.map(() => 0);
  }

  return OCTAVE_BANDS_HZ.map((frequencyHz) =>
    airAbsorptionDbPerMISO9613(
      frequencyHz,
      air.temperature_c,
      air.relative_humidity_percent,
      air.pressure_kpa
    )
  );
}

function broadbandAirAttenuationDbPerM() {
  const coefficients = airAbsorptionDbPerMByBand();
  if (!coefficients.length) return 0;
  return coefficients.reduce((sum, value) => sum + value, 0) / coefficients.length;
}

function airGainForBand(pathLengthM, directDistanceM, bandIndex, normalizeToDirect = true) {
  const air = readAirOptions();
  if (!air.enabled) return 1.0;

  const coefficients = airAbsorptionDbPerMByBand();
  const dbPerM = coefficients[bandIndex] || 0;

  // Because your IR is currently relative to the direct path,
  // only apply air loss to the extra distance beyond the direct path.
  // This keeps the direct path at 0 dB / amplitude 1.
  const distanceForAir =
    normalizeToDirect
      ? Math.max(0, Number(pathLengthM) - Number(directDistanceM))
      : Math.max(0, Number(pathLengthM));

  const totalDbLoss = dbPerM * distanceForAir;

  // dB pressure/amplitude conversion.
  return Math.pow(10, -totalDbLoss / 20.0);
}

function normalizeAbsorptionBands(value, fallback = null) {
  let values = value;
  if (values === undefined || values === null || values === "") {
    if (fallback === null) return null;
    values = fallback;
  }
  if (!Array.isArray(values)) values = [Number(values)];
  values = values.map((x) => Number(x));
  if (values.length === 1 && Number.isFinite(values[0])) values = Array(8).fill(values[0]);
  if (values.length !== 8 || values.some((x) => !Number.isFinite(x) || x < 0 || x > 1)) return null;
  return values.map((x) => Math.max(0, Math.min(1, x)));
}

function normalizeScatteringBands(value, fallback = null) {
  let values = value;
  if (values === undefined || values === null || values === "") {
    if (fallback === null) return null;
    values = fallback;
  }
  if (!Array.isArray(values)) values = [Number(values)];
  values = values.map((x) => Number(x));
  if (values.length === 1 && Number.isFinite(values[0])) values = Array(8).fill(values[0]);
  if (values.length !== 8 || values.some((x) => !Number.isFinite(x) || x < 0 || x > 1)) return null;
  return values.map((x) => Math.max(0, Math.min(1, x)));
}

function readSurfaceAbsorptionBands() {
  const bandValues = SURFACE_ABSORPTION_IDS.map((id) => {
    const element = document.getElementById(id);
    return element ? Number(element.value) : NaN;
  });

  if (bandValues.every((x) => Number.isFinite(x))) {
    const normalized = normalizeAbsorptionBands(bandValues);
    if (!normalized) throw new Error("Absorption bands must be eight values between 0 and 1.");
    return normalized;
  }

  // Backward-compatible fallback only for old DOMs without the band fields.
  const scalar = document.getElementById("surfaceAbsorption")?.value;
  const normalized = normalizeAbsorptionBands(Number(scalar));
  if (!normalized) throw new Error("Surface absorption must be between 0 and 1.");
  return normalized;
}

function writeSurfaceAbsorptionBands(value) {
  const bands = normalizeAbsorptionBands(value, 0.05) || Array(8).fill(0.05);
  for (const [i, id] of SURFACE_ABSORPTION_IDS.entries()) {
    const element = document.getElementById(id);
    if (element) element.value = bands[i].toFixed(2);
  }
  if (elements.surfaceAbsorption) {
    elements.surfaceAbsorption.value = averageAbsorption(bands, 0.05).toFixed(2);
  }
}

function readSurfaceScatteringBands() {
  const bandValues = SURFACE_SCATTERING_IDS.map((id) => {
    const element = document.getElementById(id);
    return element ? Number(element.value) : NaN;
  });

  if (bandValues.every((x) => Number.isFinite(x))) {
    const normalized = normalizeScatteringBands(bandValues);
    if (!normalized) throw new Error("Scattering bands must be eight values between 0 and 1.");
    return normalized;
  }

  const scalar = document.getElementById("surfaceScattering")?.value;
  const normalized = normalizeScatteringBands(Number(scalar), 0);
  if (!normalized) throw new Error("Surface scattering must be between 0 and 1.");
  return normalized;
}

function writeSurfaceScatteringBands(value) {
  const bands = normalizeScatteringBands(value, 0) || Array(8).fill(0);
  for (const [i, id] of SURFACE_SCATTERING_IDS.entries()) {
    const element = document.getElementById(id);
    if (element) element.value = bands[i].toFixed(2);
  }
  if (elements.surfaceScattering) {
    elements.surfaceScattering.value = averageAbsorption(bands, 0).toFixed(2);
  }
}

function surfaceHasAssignedMaterial(face) {
  if (!face) return false;
  const absorption = normalizeAbsorptionBands(face.absorption, null);
  if (!absorption) return false;
  const materialName = String(face.acoustic_material || face.material || face.material_name || "").trim();
  return materialName.length > 0;
}

function findUnassignedSurfaces() {
  if (!mesh) return [];
  const missing = [];
  mesh.faces.forEach((face, index) => {
    if (!surfaceHasAssignedMaterial(face)) {
      missing.push({ index, label: faceLabel(face, index) });
    }
  });
  return missing;
}

function unassignedMaterialMessage(missing) {
  const preview = missing.slice(0, 16).map((item) => `  face ${item.index}: ${item.label}`).join("\n");
  const more = missing.length > 16 ? `\n  ... ${missing.length - 16} more` : "";
  return [
    "ERROR: Unassigned acoustic material coefficients.",
    `${missing.length} surface(s) have no assigned material absorption.`,
    "Assign wall/material name and octave-band absorption to every surface before running ISM.",
    "Use Apply selected, Apply connected plane, Apply same group, or Apply all surfaces.",
    preview + more,
  ].filter(Boolean).join("\n");
}

function faceLabel(face, index) {
  if (!face) return `Face ${index}`;
  return (
    face.user_surface_name ||
    face.surface_name ||
    face.display_name ||
    face.original_name ||
    face.original_group ||
    face.name ||
    face.group ||
    face.object ||
    face.material ||
    `Face ${index}`
  );
}


function vertexKey(vertex, tolerance = 1.0e-6) {
  return vertex.map((x) => Math.round(Number(x) / tolerance)).join(",");
}

function edgeKeyFromVertexKeys(a, b) {
  return a < b ? `${a}|${b}` : `${b}|${a}`;
}

function vectorSub(a, b) {
  return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
}

function cross(a, b) {
  return [
    a[1] * b[2] - a[2] * b[1],
    a[2] * b[0] - a[0] * b[2],
    a[0] * b[1] - a[1] * b[0],
  ];
}

function dot(a, b) {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

function length3(v) {
  return Math.hypot(v[0], v[1], v[2]);
}

function normalize(v) {
  const length = length3(v);
  if (length <= 1e-12) return null;
  return [v[0] / length, v[1] / length, v[2] / length];
}

function faceNormalAndOffset(face) {
  if (!mesh || !face || !face.indices || face.indices.length < 3) return null;
  const p0 = mesh.vertices[face.indices[0]];
  for (let i = 1; i < face.indices.length - 1; i++) {
    const p1 = mesh.vertices[face.indices[i]];
    const p2 = mesh.vertices[face.indices[i + 1]];
    const n = normalize(cross(vectorSub(p1, p0), vectorSub(p2, p0)));
    if (n) return { normal: n, offset: dot(n, p0) };
  }
  return null;
}

function faceEdgeKeys(face) {
  if (!mesh || !face || !face.indices) return [];
  const keys = face.indices.map((index) => vertexKey(mesh.vertices[index]));
  const edges = [];
  for (let i = 0; i < keys.length; i++) {
    edges.push(edgeKeyFromVertexKeys(keys[i], keys[(i + 1) % keys.length]));
  }
  return edges;
}

function coplanarWithReference(face, referencePlane, angleToleranceDeg = 2.0, offsetTolerance = 1.0e-4) {
  const plane = faceNormalAndOffset(face);
  if (!plane || !referencePlane) return false;

  let alignment = dot(referencePlane.normal, plane.normal);
  let offset = plane.offset;
  if (alignment < 0) {
    alignment = -alignment;
    offset = -offset;
  }

  const minAlignment = Math.cos((angleToleranceDeg * Math.PI) / 180.0);
  return alignment >= minAlignment && Math.abs(offset - referencePlane.offset) <= offsetTolerance;
}

function connectedCoplanarFaceIndices(startFaceIndex) {
  if (!mesh || startFaceIndex === null || startFaceIndex === undefined || !mesh.faces[startFaceIndex]) return [];

  const referencePlane = faceNormalAndOffset(mesh.faces[startFaceIndex]);
  if (!referencePlane) return [startFaceIndex];

  const edgeToFaces = new Map();
  mesh.faces.forEach((face, faceIndex) => {
    for (const edgeKey of faceEdgeKeys(face)) {
      if (!edgeToFaces.has(edgeKey)) edgeToFaces.set(edgeKey, []);
      edgeToFaces.get(edgeKey).push(faceIndex);
    }
  });

  const visited = new Set([startFaceIndex]);
  const queue = [startFaceIndex];

  while (queue.length) {
    const currentIndex = queue.shift();
    const currentFace = mesh.faces[currentIndex];

    for (const edgeKey of faceEdgeKeys(currentFace)) {
      for (const neighborIndex of edgeToFaces.get(edgeKey) || []) {
        if (visited.has(neighborIndex)) continue;
        const neighborFace = mesh.faces[neighborIndex];
        if (!coplanarWithReference(neighborFace, referencePlane)) continue;
        visited.add(neighborIndex);
        queue.push(neighborIndex);
      }
    }
  }

  return [...visited].sort((a, b) => a - b);
}

function activeSurfaceIndices() {
  // A selected surface is always only the clicked surface.
  // Connected/group modes are apply targets, not persistent selection groups.
  if (selectedSurfaceIndex !== null && selectedSurfaceIndex !== undefined && mesh?.faces?.[selectedSurfaceIndex]) {
    return [selectedSurfaceIndex];
  }
  return [];
}

function userSurfaceGroupIndices(faceIndex) {
  // Same user name/material must NOT make surfaces behave as one object.
  if (!mesh || faceIndex === null || faceIndex === undefined || !mesh.faces[faceIndex]) return [];
  return [faceIndex];
}

function forceSingleSurfaceSelection(faceIndex) {
  if (faceIndex === null || faceIndex === undefined || !mesh?.faces?.[faceIndex]) {
    selectedSurfaceIndex = null;
    selectedSurfaceIndices = [];
    viewer.clearSurfaceHighlight?.();
    viewer.setSurfaceHighlights?.([]);
    refreshSurfacePanel();
    return;
  }

  selectedSurfaceIndex = faceIndex;
  selectedSurfaceIndices = [faceIndex];

  // Clear every old multi-highlight first, then draw only the clicked face.
  viewer.clearSurfaceHighlight?.();
  viewer.setSurfaceHighlights?.([]);
  viewer.setSurfaceHighlight?.(faceIndex);

  refreshSurfacePanel();
}

function selectSurfaceSet(indices, clickedFaceIndex = null) {
  // Keep this function for compatibility, but make it non-grouping.
  if (clickedFaceIndex !== null && clickedFaceIndex !== undefined && mesh?.faces?.[clickedFaceIndex]) {
    forceSingleSurfaceSelection(clickedFaceIndex);
  } else if (Array.isArray(indices) && indices.length && mesh?.faces?.[indices[0]]) {
    forceSingleSurfaceSelection(indices[0]);
  } else {
    forceSingleSurfaceSelection(null);
  }
}

function selectedSurfaceInfo(faceIndex) {
  if (!mesh || faceIndex === null || faceIndex === undefined || !mesh.faces[faceIndex]) {
    return "No surface selected.";
  }

  const face = mesh.faces[faceIndex];
  const absorption = averageAbsorption(face.absorption, 0.05);
  const scattering = averageAbsorption(face.scattering, 0);
  const label = faceLabel(face, faceIndex);
  const vertices = face.indices ? face.indices.length : 0;
  const connectedCount = connectedCoplanarFaceIndices(faceIndex).length;

  return [
    `Selected surface ${faceIndex}: ${label}`,
    `clicked_face=${faceIndex}`,
    `wall_name=${face.user_surface_name || face.surface_name || face.display_name || face.original_name || face.name || face.group || ""}`,
    `material=${face.acoustic_material || face.material_name || face.user_material_name || face.original_material || face.material || "Default"}`,
    `original_group=${face.original_group || face.imported_group || ""}`,
    `vertices=${vertices}`,
    `connected_coplanar_apply_targets=${connectedCount}`,
    `active_selection_faces=1`,
    `absorption_avg=${averageAbsorption(absorption, 0.05).toFixed(3)}`,
    `scattering_avg=${scattering.toFixed(3)}`,
  ].join("\n");
}

function refreshSurfacePanel() {
  if (!elements.surfaceStatus) return;

  const hasSelection = mesh && selectedSurfaceIndex !== null && mesh.faces[selectedSurfaceIndex];
  elements.surfaceStatus.textContent = selectedSurfaceInfo(selectedSurfaceIndex);

  for (const id of ["applySurfaceSelected", "applySurfaceConnected", "applySurfaceGroup"]) {
    if (elements[id]) elements[id].disabled = !hasSelection;
  }

  if (elements.applySurfaceAll) elements.applySurfaceAll.disabled = !mesh;
  if (elements.clearSurfaceSelection) elements.clearSurfaceSelection.disabled = !hasSelection;
}

function syncSurfaceUiFromFace(face, faceIndex) {
  isSyncingSurfaceUi = true;

  try {
    if (elements.surfaceName) {
      elements.surfaceName.value =
        face.user_surface_name ||
        face.surface_name ||
        face.display_name ||
        face.original_name ||
        face.original_group ||
        face.name ||
        face.group ||
        face.object ||
        `Face_${faceIndex}`;
    }

    if (elements.surfaceMaterialName) {
      elements.surfaceMaterialName.value =
        face.acoustic_material ||
        face.material_name ||
        face.user_material_name ||
        face.original_material ||
        face.material ||
        "Default";
    }

    writeSurfaceAbsorptionBands(face.absorption);

    writeSurfaceScatteringBands(face.scattering);
  } finally {
    isSyncingSurfaceUi = false;
  }
}

function persistCurrentSurfaceInputsToSelectedFace() {
  if (isSyncingSurfaceUi) return;
  if (!mesh || selectedSurfaceIndex === null || selectedSurfaceIndex === undefined) return;
  if (!mesh.faces[selectedSurfaceIndex]) return;

  setFaceAcousticProperties(
    mesh.faces[selectedSurfaceIndex],
    readSurfaceAbsorptionBands(),
    readSurfaceScatteringBands(),
    elements.surfaceName?.value || "",
    elements.surfaceMaterialName?.value || ""
  );

  refreshSurfacePanel();
}

function selectSurface(faceIndex) {
  if (faceIndex === null || faceIndex === undefined || !mesh?.faces?.[faceIndex]) {
    forceSingleSurfaceSelection(null);
    return;
  }

  const face = mesh.faces[faceIndex];

  // Always select only the clicked face/plane.
  forceSingleSurfaceSelection(faceIndex);
  syncSurfaceUiFromFace(face, faceIndex);
  refreshSurfacePanel();
}

function clearSurfaceSelection() {
  forceSingleSurfaceSelection(null);
}

function setFaceAcousticProperties(face, absorption, scattering, wallName, materialName) {
  const cleanWallName = String(wallName || "").trim();
  const cleanMaterialName = String(materialName || "").trim();

  // Preserve original/imported identity forever.
  // These are what keep surfaces distinct.
  if (!face.original_group) {
    face.original_group = face.group || face.name || face.object || "";
  }

  if (!face.original_name) {
    face.original_name = face.name || face.group || face.object || "";
  }

  if (!face.original_material) {
    face.original_material = face.material || face.acoustic_material || "";
  }

  face.absorption = normalizeAbsorptionBands(absorption, null);
  if (!face.absorption) {
    throw new Error("Absorption must be eight octave-band values between 0 and 1.");
  }

  face.scattering = normalizeScatteringBands(scattering, null);
  if (!face.scattering) {
    throw new Error("Scattering must be eight octave-band values between 0 and 1.");
  }

  // Delete any old artificial grouping ID from previous broken versions.
  delete face.user_surface_id;

  // IMPORTANT:
  // User wall names are display/acoustic names only.
  // Do NOT overwrite face.name, face.group, or face.group_name.
  // Otherwise separate planes become glued together by shared identity.
  if (cleanWallName) {
    face.user_surface_name = cleanWallName;
    face.surface_name = cleanWallName;
    face.display_name = cleanWallName;
  }

  if (cleanMaterialName) {
    face.acoustic_material = cleanMaterialName;
    face.material_name = cleanMaterialName;

    // Keep face.material if you want imported material identity preserved.
    // Do not use material as a grouping key after user edits.
    face.user_material_name = cleanMaterialName;
  } else {
    face.acoustic_material =
      face.acoustic_material ||
      face.material_name ||
      face.material ||
      "User material";
  }
}

function applySurfaceProperties(mode) {
  if (!mesh) return;

  if (selectedSurfaceIndex !== null && selectedSurfaceIndex !== undefined && !mesh.faces[selectedSurfaceIndex]) {
    selectedSurfaceIndex = null;
    selectedSurfaceIndices = [];
  }

  const absorption = readSurfaceAbsorptionBands();
  const scattering = readSurfaceScatteringBands();
  const wallName = elements.surfaceName?.value || "";
  const materialName = elements.surfaceMaterialName?.value || "";
  const originalClickedIndex = selectedSurfaceIndex;

  let indices = [];

  if (mode === "selected") {
    if (selectedSurfaceIndex === null || !mesh.faces[selectedSurfaceIndex]) return;
    indices = [selectedSurfaceIndex];

  } else if (mode === "connected") {
    if (selectedSurfaceIndex === null || !mesh.faces[selectedSurfaceIndex]) return;

    // Apply to connected coplanar surfaces,
    // but do not make them a persistent selected/grouped object.
    indices = connectedCoplanarFaceIndices(selectedSurfaceIndex);

  } else if (mode === "group") {
    if (selectedSurfaceIndex === null || !mesh.faces[selectedSurfaceIndex]) return;

    const selected = mesh.faces[selectedSurfaceIndex];

    // Same group uses original imported identity only.
    // It must NOT use user_surface_name, display_name, material_name,
    // or anything created by Apply connected plane.
    const selectedGroup =
      selected.original_group ||
      selected.imported_group ||
      selected.original_name ||
      selected.object ||
      selected.original_material ||
      null;

    mesh.faces.forEach((face, index) => {
      const key =
        face.original_group ||
        face.imported_group ||
        face.original_name ||
        face.object ||
        face.original_material ||
        null;

      if (key === selectedGroup) indices.push(index);
    });

  } else if (mode === "all") {
    indices = mesh.faces.map((_, index) => index);
  }

  indices = [...new Set(indices)].sort((a, b) => a - b);

  if (!indices.length) {
    appendLog(`No surfaces matched mode=${mode}.`);
    return;
  }

  // Clean old artificial grouping from the entire mesh.
  mesh.faces.forEach((face) => {
    delete face.user_surface_id;
  });

  for (const index of indices) {
    setFaceAcousticProperties(mesh.faces[index], absorption, scattering, wallName, materialName);
  }

  // Critical:
  // Even if attributes were applied to many faces,
  // the yellow selection must return to only the originally clicked face.
  if (originalClickedIndex !== null && originalClickedIndex !== undefined && mesh.faces[originalClickedIndex]) {
    forceSingleSurfaceSelection(originalClickedIndex);
    syncSurfaceUiFromFace(mesh.faces[originalClickedIndex], originalClickedIndex);
  } else {
    forceSingleSurfaceSelection(null);
  }

  appendLog(
    `Applied name=${wallName || "(unchanged)"}, material=${materialName || "(unchanged)"}, absorption_avg=${averageAbsorption(absorption, 0.05).toFixed(3)}, scattering_avg=${averageAbsorption(scattering, 0).toFixed(3)} to ${indices.length} surface(s) using mode=${mode}. Rerun ISM to use these values.`
  );
}

viewer.setSurfaceSelectionHandler?.((faceIndex) => {
  if (faceIndex === null || faceIndex === undefined) {
    selectedSurfaceIndex = null;
    selectedSurfaceIndices = [];
    refreshSurfacePanel();
  } else {
    selectSurface(faceIndex);
  }
});

async function loadMesh(newMesh, name, options = {}) {
  mesh = newMesh;
  userSurfaceCounter = 1;
  mesh.faces.forEach((face, index) => {
    if (!face.original_group) face.original_group = face.group || face.name || face.object || `Face_${index}`;
    if (!face.original_material) face.original_material = face.material || face.acoustic_material || "Default";
  });
  lastFilenameBase = name.replace(/\.[^.]+$/, "") || "borish_result";
  viewer.showMesh(mesh);
  selectedSurfaceIndex = null;
  selectedSurfaceIndices = [];
  refreshSurfacePanel();
  updateMarkers();
  lastSimulation = null;
  setDownloadsEnabled(false);
  renderRoomMetrics(null);
  log(`Loaded ${name}
vertices=${mesh.vertices.length}
faces=${mesh.faces.length}
unassigned_material_surfaces=${findUnassignedSurfaces().length}
${options.presetMessage || "Assign material coefficients to every surface before Run ISM."}`);
}

function setDownloadsEnabled(enabled) {
  for (const id of ["downloadJson", "downloadCsv", "downloadWav", "downloadDirectionalIr", "recordWebm"]) {
    elements[id].disabled = !enabled;
  }
}

function renderToaTable(rows) {
  if (!rows || rows.length === 0) {
    elements.toaTable.innerHTML = "<p>No arrival paths.</p>";
    return;
  }
  const limited = rows.slice(0, 250);
  const html = [
    "<table><thead><tr>",
    "<th>ID</th><th>Order</th><th>Abs ms</th><th>Rel ms</th><th>Length m</th><th>Amp</th><th>Ancestry</th>",
    "</tr></thead><tbody>",
    ...limited.map((row) => `<tr>
      <td>${row.path_id}</td>
      <td>${row.order}</td>
      <td>${row.arrival_ms_absolute.toFixed(3)}</td>
      <td>${row.arrival_ms_relative.toFixed(3)}</td>
      <td>${row.path_length_m.toFixed(3)}</td>
      <td>${row.amplitude.toFixed(4)}</td>
      <td>${escapeHtml(row.ancestry)}</td>
    </tr>`),
    "</tbody></table>"
  ].join("");
  elements.toaTable.innerHTML = html;
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));
}

function renderImpulsePlot(sparse, fixedMaxA = null) {
  const canvas = elements.irCanvas;
  const rect = canvas.getBoundingClientRect();
  const scale = window.devicePixelRatio || 1;

  canvas.width = Math.max(600, Math.floor(rect.width * scale));
  canvas.height = Math.max(180, Math.floor(rect.height * scale));

  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  ctx.fillStyle = "#0c1117";
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  if (!sparse || sparse.length === 0) return;

  const padL = 72 * scale;
  const padR = 16 * scale;
  const padT = 24 * scale;
  const padB = 36 * scale;

  const w = canvas.width - padL - padR;
  const h = canvas.height - padT - padB;

  const maxT = Math.max(...sparse.map((x) => Number(x.time_ms) || 0), 1e-9);

  const refA =
    Number.isFinite(Number(fixedMaxA)) && Number(fixedMaxA) > 0
      ? Number(fixedMaxA)
      : Math.max(...sparse.map((x) => Math.abs(Number(x.amplitude) || 0)), 1e-9);

  const dbFloor = -60;

  function ampToDb(amp) {
    const a = Math.max(Math.abs(Number(amp) || 0), 1e-12);
    return 20 * Math.log10(a / refA);
  }

  function yForDb(db) {
    const clamped = Math.max(dbFloor, Math.min(0, db));
    return padT + ((0 - clamped) / (0 - dbFloor)) * h;
  }

  // axes
  ctx.strokeStyle = "#293544";
  ctx.lineWidth = 1 * scale;
  ctx.beginPath();
  ctx.moveTo(padL, padT);
  ctx.lineTo(padL, padT + h);
  ctx.lineTo(padL + w, padT + h);
  ctx.stroke();

  ctx.font = `${11 * scale}px ui-monospace, monospace`;
  ctx.fillStyle = "#9fb0c1";
  ctx.textBaseline = "middle";

  // y-axis ticks in dB relative to plot full-scale/reference
  const yTicks = [0, -6, -12, -24, -36, -48, -60];

  for (const db of yTicks) {
    const y = yForDb(db);

    ctx.strokeStyle = db === 0 || db === dbFloor ? "#293544" : "#1d2733";
    ctx.beginPath();
    ctx.moveTo(padL, y);
    ctx.lineTo(padL + w, y);
    ctx.stroke();

    ctx.strokeStyle = "#9fb0c1";
    ctx.beginPath();
    ctx.moveTo(padL - 5 * scale, y);
    ctx.lineTo(padL, y);
    ctx.stroke();

    ctx.fillStyle = "#9fb0c1";
    ctx.textAlign = "right";
    ctx.fillText(`${db} dB`, padL - 8 * scale, y);
  }

  // x-axis labels
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  ctx.fillText("0 ms", padL, padT + h + 22 * scale);

  ctx.textAlign = "right";
  ctx.fillText(`${maxT.toFixed(1)} ms`, padL + w, padT + h + 22 * scale);

  // y-axis title
  ctx.save();
  ctx.translate(14 * scale, padT + h / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText("dBrFS", 0, 0);
  ctx.restore();

  // impulse spikes
  for (const point of sparse) {
    const x = padL + ((Number(point.time_ms) || 0) / maxT) * w;
    const db = ampToDb(point.amplitude);
    const y0 = padT + h;
    const y1 = yForDb(db);

    ctx.strokeStyle =
      point.order === 0 ? "#ffffff" : point.order === 1 ? "#61dafb" : "#a78bfa";

    ctx.beginPath();
    ctx.moveTo(x, y0);
    ctx.lineTo(x, y1);
    ctx.stroke();
  }
}

function nextPowerOfTwo(value) {
  let n = 1;
  while (n < value) n <<= 1;
  return n;
}

function fftRadix2(real, imag) {
  const n = real.length;

  let j = 0;
  for (let i = 1; i < n; i++) {
    let bit = n >> 1;
    while (j & bit) {
      j ^= bit;
      bit >>= 1;
    }
    j ^= bit;

    if (i < j) {
      [real[i], real[j]] = [real[j], real[i]];
      [imag[i], imag[j]] = [imag[j], imag[i]];
    }
  }

  for (let len = 2; len <= n; len <<= 1) {
    const angle = (-2 * Math.PI) / len;
    const wLenR = Math.cos(angle);
    const wLenI = Math.sin(angle);

    for (let i = 0; i < n; i += len) {
      let wR = 1;
      let wI = 0;

      for (let k = 0; k < len / 2; k++) {
        const uR = real[i + k];
        const uI = imag[i + k];

        const vR = real[i + k + len / 2] * wR - imag[i + k + len / 2] * wI;
        const vI = real[i + k + len / 2] * wI + imag[i + k + len / 2] * wR;

        real[i + k] = uR + vR;
        imag[i + k] = uI + vI;
        real[i + k + len / 2] = uR - vR;
        imag[i + k + len / 2] = uI - vI;

        const nextWR = wR * wLenR - wI * wLenI;
        const nextWI = wR * wLenI + wI * wLenR;
        wR = nextWR;
        wI = nextWI;
      }
    }
  }
}

function sparseToTimeDomain(sparse, sampleRate) {
  if (!sparse || !sparse.length) return new Float64Array(0);

  const maxTimeMs = Math.max(...sparse.map((x) => Number(x.time_ms) || 0), 1e-9);
  const neededSamples = Math.ceil((maxTimeMs / 1000) * sampleRate) + 1;

  // Pad a bit so the FFT is not absurdly tiny.
  const n = nextPowerOfTwo(Math.max(neededSamples, 2048));
  const signal = new Float64Array(n);

  for (const point of sparse) {
    const sampleIndex = Math.round(((Number(point.time_ms) || 0) / 1000) * sampleRate);
    if (sampleIndex >= 0 && sampleIndex < signal.length) {
      signal[sampleIndex] += Number(point.amplitude) || 0;
    }
  }

  return signal;
}

function computeMagnitudeSpectrumDb(sparse, sampleRate) {
  const signal = sparseToTimeDomain(sparse, sampleRate);
  if (!signal.length) return [];

  const real = Array.from(signal);
  const imag = new Array(real.length).fill(0);

  fftRadix2(real, imag);

  const half = Math.floor(real.length / 2);
  const mags = [];

  let maxMag = 1e-12;
  for (let i = 1; i < half; i++) {
    const mag = Math.hypot(real[i], imag[i]);
    if (mag > maxMag) maxMag = mag;
  }

  for (let i = 1; i < half; i++) {
    const frequency = (i * sampleRate) / real.length;
    if (frequency < 20 || frequency > Math.min(20000, sampleRate / 2)) continue;

    const mag = Math.hypot(real[i], imag[i]);
    const db = 20 * Math.log10(Math.max(mag, 1e-12) / maxMag);

    mags.push({ frequency, db });
  }

  return mags;
}

function smoothSpectrumFractionalOctave(spectrum, bandsPerOctave = 3, outputPointCount = 220) {
  if (!Array.isArray(spectrum) || spectrum.length < 3) return spectrum || [];

  const points = spectrum
    .map((point) => ({
      frequency: Number(point.frequency),
      db: Number(point.db),
    }))
    .filter((point) =>
      Number.isFinite(point.frequency) &&
      point.frequency > 0 &&
      Number.isFinite(point.db)
    )
    .sort((a, b) => a.frequency - b.frequency);

  if (points.length < 3) return points;

  const minF = Math.max(20, points[0].frequency);
  const maxF = Math.min(20000, points[points.length - 1].frequency);

  const halfOctaveWidth = 1 / (2 * bandsPerOctave);
  const lowerFactor = Math.pow(2, -halfOctaveWidth);
  const upperFactor = Math.pow(2, halfOctaveWidth);

  const output = [];

  for (let i = 0; i < outputPointCount; i++) {
    const t = outputPointCount <= 1 ? 0 : i / (outputPointCount - 1);
    const centerFrequency = minF * Math.pow(maxF / minF, t);

    const lowFrequency = centerFrequency * lowerFactor;
    const highFrequency = centerFrequency * upperFactor;

    let powerSum = 0;
    let count = 0;

    for (const point of points) {
      if (point.frequency < lowFrequency) continue;
      if (point.frequency > highFrequency) break;

      // Average linear power, not dB.
      powerSum += Math.pow(10, point.db / 10);
      count++;
    }

    if (count > 0) {
      const meanPower = powerSum / count;
      output.push({
        frequency: centerFrequency,
        db: 10 * Math.log10(Math.max(meanPower, 1e-12)),
      });
    }
  }

  if (!output.length) return [];

  const maxDb = Math.max(...output.map((point) => point.db));

  return output.map((point) => ({
    frequency: point.frequency,
    db: point.db - maxDb,
  }));
}

function smoothSpectrumThirdOctave(spectrum) {
  return smoothSpectrumFractionalOctave(spectrum, 3, 220);
}

function renderFftPlot(sparse) {
  const canvas = elements.irCanvas;
  if (!canvas) return;

  const sampleRate = readNumber("sampleRate");
  const rawSpectrum = computeMagnitudeSpectrumDb(sparse, sampleRate);
  const spectrum = smoothSpectrumThirdOctave(rawSpectrum);

  const rect = canvas.getBoundingClientRect();
  const scale = window.devicePixelRatio || 1;

  canvas.width = Math.max(600, Math.floor(rect.width * scale));
  canvas.height = Math.max(180, Math.floor(rect.height * scale));

  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  ctx.fillStyle = "#0c1117";
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  if (!spectrum.length) return;

  const padL = 70 * scale;
  const padR = 18 * scale;
  const padT = 22 * scale;
  const padB = 38 * scale;

  const w = canvas.width - padL - padR;
  const h = canvas.height - padT - padB;

  const minF = 20;
  const maxF = Math.min(20000, sampleRate / 2);
  const minDb = -60;
  const maxDb = 0;

  function xForFreq(freq) {
    const minLog = Math.log10(minF);
    const maxLog = Math.log10(maxF);
    return padL + ((Math.log10(freq) - minLog) / (maxLog - minLog)) * w;
  }

  function yForDb(db) {
    const clamped = Math.max(minDb, Math.min(maxDb, db));
    return padT + ((maxDb - clamped) / (maxDb - minDb)) * h;
  }

  // axes
  ctx.strokeStyle = "#293544";
  ctx.lineWidth = 1 * scale;
  ctx.beginPath();
  ctx.moveTo(padL, padT);
  ctx.lineTo(padL, padT + h);
  ctx.lineTo(padL + w, padT + h);
  ctx.stroke();

  ctx.font = `${11 * scale}px ui-monospace, monospace`;
  ctx.fillStyle = "#9fb0c1";

  // y ticks
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  for (const db of [0, -12, -24, -36, -48, -60]) {
    const y = yForDb(db);

    ctx.strokeStyle = db === 0 || db === -60 ? "#293544" : "#1d2733";
    ctx.beginPath();
    ctx.moveTo(padL, y);
    ctx.lineTo(padL + w, y);
    ctx.stroke();

    ctx.fillStyle = "#9fb0c1";
    ctx.fillText(`${db} dB`, padL - 8 * scale, y);
  }

  // x ticks
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  for (const freq of [31.5, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]) {
    if (freq < minF || freq > maxF) continue;

    const x = xForFreq(freq);
    ctx.strokeStyle = "#1d2733";
    ctx.beginPath();
    ctx.moveTo(x, padT);
    ctx.lineTo(x, padT + h);
    ctx.stroke();

    const label = freq >= 1000 ? `${freq / 1000}k` : `${freq}`;
    ctx.fillStyle = "#9fb0c1";
    ctx.fillText(label, x, padT + h + 8 * scale);
  }

  // y-axis title
  ctx.save();
  ctx.translate(14 * scale, padT + h / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText("dB", 0, 0);
  ctx.restore();

  // plot spectrum
  ctx.strokeStyle = "#61dafb";
  ctx.lineWidth = 1.5 * scale;
  ctx.beginPath();

  let started = false;
  for (const point of spectrum) {
    const x = xForFreq(point.frequency);
    const y = yForDb(point.db);

    if (!started) {
      ctx.moveTo(x, y);
      started = true;
    } else {
      ctx.lineTo(x, y);
    }
  }

  ctx.stroke();
}

function renderPolarPowerPlot(result) {
  const canvas = elements.irCanvas;
  if (!canvas) return;

  const analysis = result?.analysis || {};
  const bins = analysis?.polar_power?.bins || [];
  const rect = canvas.getBoundingClientRect();
  const scale = window.devicePixelRatio || 1;

  canvas.width = Math.max(1, Math.floor(rect.width * scale));
  canvas.height = Math.max(1, Math.floor(rect.height * scale));

  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#0c1117";
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  const cx = canvas.width / 2;
  const cy = canvas.height / 2;
  const labelGutter = 34 * scale;
  const radius = Math.max(20 * scale, (Math.min(canvas.width, canvas.height) - 2 * labelGutter) * 0.5);

  ctx.strokeStyle = "#293544";
  ctx.lineWidth = 1 * scale;
  for (const factor of [0.25, 0.5, 0.75, 1.0]) {
    ctx.beginPath();
    ctx.arc(cx, cy, radius * factor, 0, Math.PI * 2);
    ctx.stroke();
  }

  for (const angleDeg of [-90, 0, 90, 180]) {
    const angle = ((angleDeg - 90) * Math.PI) / 180;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx + Math.cos(angle) * radius, cy + Math.sin(angle) * radius);
    ctx.stroke();
  }

  ctx.font = `${11 * scale}px ui-monospace, monospace`;
  ctx.fillStyle = "#9fb0c1";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText("0", cx, cy - radius - 12 * scale);
  ctx.fillText("-90", cx - radius - 20 * scale, cy);
  ctx.fillText("+90", cx + radius + 22 * scale, cy);
  ctx.fillText("180", cx, cy + radius + 14 * scale);

  if (!bins.length) return;

  ctx.strokeStyle = "#61dafb";
  ctx.fillStyle = "rgba(97, 218, 251, 0.24)";
  ctx.lineWidth = 2 * scale;
  ctx.beginPath();
  let started = false;
  for (const bin of bins.concat(bins[0])) {
    const relativePower = Math.max(0, Math.min(1, Number(bin.relative_power) || 0));
    const r = Math.sqrt(relativePower) * radius;
    const angle = ((Number(bin.center_deg) - 90) * Math.PI) / 180;
    const x = cx + Math.cos(angle) * r;
    const y = cy + Math.sin(angle) * r;
    if (!started) {
      ctx.moveTo(x, y);
      started = true;
    } else {
      ctx.lineTo(x, y);
    }
  }
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
}

function distance3(a, b) {
  return Math.hypot(
    Number(a[0]) - Number(b[0]),
    Number(a[1]) - Number(b[1]),
    Number(a[2]) - Number(b[2])
  );
}

function pathBandAmplitude(path, bandIndex, result) {
  // Direct path has no reflection losses.
  const source = result.source;
  const receiver = result.receiver;
  const directDistance = distance3(source, receiver);
  const pathLength = Math.max(Number(path.path_length_m || 0), 1.0e-12);

  // Current solver normally normalizes to direct distance.
  const normalizeToDirect = result.config?.normalize_to_direct !== false;
  const spreading = normalizeToDirect
    ? directDistance / pathLength
    : 1.0 / pathLength;

  let reflectionGain = 1.0;

  for (const step of path.ancestry || []) {
    const absorption = step.absorption;
    const scattering = step.scattering;

    let alpha = 0.05;
    if (Array.isArray(absorption) && absorption.length) {
      alpha = Number(absorption[bandIndex]);
      if (!Number.isFinite(alpha)) {
        const valid = absorption.map(Number).filter(Number.isFinite);
        alpha = valid.length ? valid.reduce((a, b) => a + b, 0) / valid.length : 0.05;
      }
    } else if (Number.isFinite(Number(absorption))) {
      alpha = Number(absorption);
    }

    alpha = Math.max(0, Math.min(1, alpha));
    let scatter = 0.0;
    if (Array.isArray(scattering) && scattering.length) {
      scatter = Number(scattering[bandIndex]);
      if (!Number.isFinite(scatter)) {
        const valid = scattering.map(Number).filter(Number.isFinite);
        scatter = valid.length ? valid.reduce((a, b) => a + b, 0) / valid.length : 0.0;
      }
    } else if (Number.isFinite(Number(scattering))) {
      scatter = Number(scattering);
    }
    scatter = Math.max(0, Math.min(1, scatter));
    reflectionGain *= Math.sqrt(Math.max(0, (1 - alpha) * (1 - scatter)));
  }

  const airGain = airGainForBand(
    pathLength,
    directDistance,
    bandIndex,
    normalizeToDirect
  );

  return spreading * reflectionGain * airGain;
}

function sparseIrForBand(simulation, bandValue) {
  if (!simulation) return [];

  const result = simulation.result;
  const paths = result?.paths || [];

  if (!paths.length) return [];

  if (bandValue === "broadband") {
    return paths.map((path) => {
      const bandAmplitudes = OCTAVE_BANDS_HZ.map((_, bandIndex) =>
        pathBandAmplitude(path, bandIndex, result)
      );

      const rmsAmplitude = Math.sqrt(
        bandAmplitudes.reduce((sum, value) => sum + value * value, 0) / bandAmplitudes.length
      );

      return {
        path_id: path.path_id,
        order: path.order,
        time_ms: Number(path.arrival_time_relative_s || 0) * 1000.0,
        amplitude: rmsAmplitude,
      };
    });
  }

  const bandIndex = Number(bandValue);

  if (!Number.isInteger(bandIndex) || bandIndex < 0 || bandIndex > 7) {
    return sparseIrForBand(simulation, "broadband");
  }

  return paths.map((path) => ({
    path_id: path.path_id,
    order: path.order,
    time_ms: Number(path.arrival_time_relative_s || 0) * 1000.0,
    amplitude: pathBandAmplitude(path, bandIndex, result),
  }));
}

function updatePlotControls() {
  const viewIndex = Math.max(0, PLOT_VIEWS.indexOf(plotView));
  const showingIr = plotView === "ir";

  if (elements.plotTitle) {
    elements.plotTitle.textContent =
      plotView === "ir" ? "Impulse response" :
      plotView === "fft" ? "Frequency response" :
      "Polar power";
  }

  if (elements.plotViewName) {
    elements.plotViewName.textContent =
      plotView === "ir" ? "IR" :
      plotView === "fft" ? "FFT" :
      "Polar";
  }

  if (elements.plotPrev) elements.plotPrev.disabled = viewIndex <= 0;
  if (elements.plotNext) elements.plotNext.disabled = viewIndex >= PLOT_VIEWS.length - 1;

  if (elements.irToolbar) {
    elements.irToolbar.classList.toggle("hidden", !showingIr);
  }
}

function redrawSelectedIrView() {
  updatePlotControls();

  const view = elements.irBandSelect?.value || "broadband";

  const allViews = [
    sparseIrForBand(lastSimulation, "broadband"),
    sparseIrForBand(lastSimulation, "0"),
    sparseIrForBand(lastSimulation, "1"),
    sparseIrForBand(lastSimulation, "2"),
    sparseIrForBand(lastSimulation, "3"),
    sparseIrForBand(lastSimulation, "4"),
    sparseIrForBand(lastSimulation, "5"),
    sparseIrForBand(lastSimulation, "6"),
    sparseIrForBand(lastSimulation, "7"),
  ];

  const fixedMaxA = Math.max(
    ...allViews.flat().map((x) => Math.abs(Number(x.amplitude) || 0)),
    1e-9
  );

  const selectedSparse = sparseIrForBand(lastSimulation, view);

  if (plotView === "polar") {
    renderPolarPowerPlot(lastSimulation?.result);
  } else if (plotView === "fft") {
    renderFftPlot(selectedSparse);
  } else {
    renderImpulsePlot(selectedSparse, fixedMaxA);
  }
}

worker.onmessage = (event) => {
  const { type } = event.data;
  if (type === "status") {
    appendLog(event.data.message);
  } else if (type === "error") {
    log(`ERROR\n${event.data.error}\n${event.data.stack || ""}`);
    setDownloadsEnabled(false);
    renderRoomMetrics(null);
  } else if (type === "check-complete") {
    const report = event.data.report;
    const lines = [
      "CLOSURE REPORT",
      `closed=${report.closed}`,
      `can_simulate=${report.can_simulate}`,
      `diagnostic_open_mesh_allowed=${report.diagnostic_open_mesh_allowed}`,
      `vertices=${report.vertex_count}`,
      `faces=${report.face_count}`,
      ...(Number.isFinite(Number(report.patch_count)) ? [`reflector_patches=${report.patch_count}`] : []),
      ...(Number.isFinite(Number(report.merged_coplanar_faces)) ? [`merged_coplanar_faces=${report.merged_coplanar_faces}`] : []),
      `boundary_edges=${report.boundary_edges}`,
      `nonmanifold_edges=${report.nonmanifold_edges}`,
      `signed_volume_m3=${Number(report.signed_volume_m3 || 0).toFixed(6)}`,
      `normals_apparently_inward=${report.normals_apparently_inward}`,
      `normals_flipped=${report.normals_flipped}`,
      `source_inside=${report.source_inside_scene}`,
      `receiver_inside=${report.receiver_inside_scene}`,
      `direct_path_blocked=${report.direct_path_blocked}`,
      ...(report.errors || []).map((e) => `ERROR: ${e}`),
      ...(report.warnings || []).map((w) => `WARNING: ${w}`),
    ];
    log(lines.join("\n"));
  } else if (type === "simulation-complete") {
    lastSimulation = event.data.result;
    const result = lastSimulation.result;
    viewer.showPaths(result.paths);
    viewer.animatePaths(result.paths, 9000);
    renderToaTable(lastSimulation.toa);
    renderRoomMetrics(result);
    redrawSelectedIrView();
    setDownloadsEnabled(true);
    const orderCounts = new Map();
    for (const path of result.paths) orderCounts.set(path.order, (orderCounts.get(path.order) || 0) + 1);
    const orderText = [...orderCounts.entries()].sort((a, b) => a[0] - b[0]).map(([order, count]) => `order_${order}_paths=${count}`).join("\n");
    const ir = lastSimulation.impulse_response || {};
    log([
      "SIMULATION COMPLETE",
      `source_inside=${result.diagnostics.source_inside_scene}`,
      `receiver_inside=${result.diagnostics.receiver_inside_scene}`,
      `direct_path_blocked=${result.diagnostics.direct_path_blocked}`,
      `validity=${result.diagnostics.validity}`,
      `mesh_faces=${result.closure?.face_count}`,
      `patches=${result.scene.patch_count}`,
      `merged_coplanar_faces=${result.closure?.merged_coplanar_faces ?? 0}`,
      `triangles=${result.scene.triangle_count}`,
      `paths=${result.paths.length}`,
      `nodes_reflected=${result.stats.nodes_reflected}`,
      `invalid_nodes=${result.stats.invalid_nodes}`,
      `proximity_pruned=${result.stats.proximity_pruned_nodes}`,
      `order_pruned=${result.stats.order_pruned_nodes}`,
      `rejected_visibility=${result.stats.rejected_visibility}`,
      `rejected_obstruction=${result.stats.rejected_obstruction}`,
      `node_limit_hit=${result.stats.hit_node_limit}`,
      `complete_within_time_radius=${result.diagnostics.completeness?.complete_within_time_radius}`,
      `auto_solver=${result.auto_solver?.status}`,
      `auto_iterations=${result.auto_solver?.iterations?.length || 0}`,
      `auto_selected_order=${result.auto_solver?.selected_max_order ?? result.config.max_order}`,
      `radius_completion_order=${result.stats.radius_completion_order || 0}`,
      `unique_image_sources=${result.stats.unique_image_sources || 0}`,
      `unique_frontier_states=${result.stats.unique_frontier_states || 0}`,
      `manual_order_ceiling=${result.auto_solver?.manual_order_ceiling ?? result.config.max_order}`,
      `search_order_ceiling=${result.auto_solver?.search_order_ceiling ?? result.auto_solver?.order_cap ?? result.config.max_order}`,
      `auto_selected_time_ms=${Number((result.auto_solver?.selected_max_time_s ?? result.config.max_time_s) * 1000).toFixed(1)}`,
      `ism_decay_valid=${result.ism_decay?.valid}`,
      `ism_decay_complete=${result.ism_decay?.complete_within_time_radius}`,
      `ism_decay_required_db=${Number(result.ism_decay?.validation_required_decay_db ?? result.ism_decay?.required_decay_db ?? 0).toFixed(1)}`,
      `wav_mode=${ir.ir_mode || "unknown"}`,
      `wav_duration_ms=${Number((ir.duration_s || 0) * 1000).toFixed(1)}`,
      `last_event_ms=${Number((ir.last_event_time_s || 0) * 1000).toFixed(1)}`,
      ...(ir.warnings || []).map((warning) => `WARNING: ${warning}`),
      ...(result.diagnostics.completeness?.warnings || []).map((warning) => `WARNING: ${warning}`),
      orderText,
    ].join("\n"));
  }
};

if (elements.irBandSelect) {
  elements.irBandSelect.addEventListener("change", () => {
    redrawSelectedIrView();
  });
}
if (elements.applySurfaceSelected) {
  elements.applySurfaceSelected.addEventListener("click", () => applySurfaceProperties("selected"));
}
if (elements.applySurfaceConnected) {
  elements.applySurfaceConnected.addEventListener("click", () => applySurfaceProperties("connected"));
}
if (elements.applySurfaceGroup) {
  elements.applySurfaceGroup.addEventListener("click", () => applySurfaceProperties("group"));
}
if (elements.applySurfaceAll) {
  elements.applySurfaceAll.addEventListener("click", () => applySurfaceProperties("all"));
}
if (elements.clearSurfaceSelection) {
  elements.clearSurfaceSelection.addEventListener("click", () => clearSurfaceSelection());
}
refreshSurfacePanel();

if (elements.surfaceAbsorption) {
  elements.surfaceAbsorption.addEventListener("input", () => {
    const value = Number(elements.surfaceAbsorption.value);
    if (Number.isFinite(value)) {
      writeSurfaceAbsorptionBands(Array(8).fill(value));
      persistCurrentSurfaceInputsToSelectedFace();
    }
  });
}

for (const id of SURFACE_ABSORPTION_IDS) {
  const element = document.getElementById(id);
  if (!element) continue;
  element.addEventListener("input", () => {
    const bands = readSurfaceAbsorptionBands();
    if (elements.surfaceAbsorption) {
      elements.surfaceAbsorption.value = averageAbsorption(bands, 0.05).toFixed(2);
    }
    persistCurrentSurfaceInputsToSelectedFace();
  });
}

if (elements.surfaceScattering) {
  elements.surfaceScattering.addEventListener("input", () => {
    const value = Number(elements.surfaceScattering.value);
    if (Number.isFinite(value)) {
      writeSurfaceScatteringBands(Array(8).fill(value));
      persistCurrentSurfaceInputsToSelectedFace();
    }
  });
}

for (const id of SURFACE_SCATTERING_IDS) {
  const element = document.getElementById(id);
  if (!element) continue;
  element.addEventListener("input", () => {
    const bands = readSurfaceScatteringBands();
    if (elements.surfaceScattering) {
      elements.surfaceScattering.value = averageAbsorption(bands, 0).toFixed(2);
    }
    persistCurrentSurfaceInputsToSelectedFace();
  });
}

for (const element of [elements.surfaceName, elements.surfaceMaterialName]) {
  if (!element) continue;
  element.addEventListener("input", persistCurrentSurfaceInputsToSelectedFace);
}

if (elements.plotPrev) {
  elements.plotPrev.addEventListener("click", () => {
    const index = Math.max(0, PLOT_VIEWS.indexOf(plotView));
    plotView = PLOT_VIEWS[Math.max(0, index - 1)];
    redrawSelectedIrView();
  });
}

if (elements.plotNext) {
  elements.plotNext.addEventListener("click", () => {
    const index = Math.max(0, PLOT_VIEWS.indexOf(plotView));
    plotView = PLOT_VIEWS[Math.min(PLOT_VIEWS.length - 1, index + 1)];
    redrawSelectedIrView();
  });
}


for (const id of ["sourceX", "sourceY", "sourceZ", "receiverX", "receiverY", "receiverZ"]) {
  document.getElementById(id).addEventListener("input", updateMarkers);
}

elements.fileInput.addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    await loadMesh(await loadMeshFile(file), file.name);
  } catch (error) {
    log(`ERROR loading file\n${error.message || error}`);
  }
});

elements.loadShoebox.addEventListener("click", async () => {
  setDefaultShoeboxPoints();
  await loadMesh(await loadExampleObj("./examples/shoebox.obj"), "shoebox.obj");
});

elements.loadConcave.addEventListener("click", async () => {
  setDefaultConcavePoints();
  const lRoomMesh = await loadExampleObj("./examples/concave_l_room.obj");
  const counts = applyLRoomRealisticDefaults(lRoomMesh);
  await loadMesh(lRoomMesh, "concave_l_room.obj", {
    presetMessage: `L-room realistic defaults applied: walls=${counts.Wall}, floors=${counts.Floor}, ceilings=${counts.Ceiling}. Ready to Run ISM.`,
  });
});

elements.checkButton.addEventListener("click", () => {
  try {
    const payload = readPayload();
    log("Checking mesh closure in Pyodide...");
    worker.postMessage({ type: "check", payload });
  } catch (error) {
    log(`ERROR\n${error.message || error}`);
  }
});

elements.runButton.addEventListener("click", () => {
  try {
    const missing = findUnassignedSurfaces();
    if (missing.length) {
      log(unassignedMaterialMessage(missing));
      setDownloadsEnabled(false);
      return;
    }
    const payload = readPayload();
    viewer.setMarkers(payload.source, payload.receiver);
    log("Starting Pyodide image-source simulation...");
    worker.postMessage({ type: "simulate", payload });
  } catch (error) {
    log(`ERROR\n${error.message || error}`);
  }
});

elements.downloadJson.addEventListener("click", () => {
  if (lastSimulation) downloadJson(`${lastFilenameBase}_ancestry.json`, lastSimulation.result);
});

elements.downloadCsv.addEventListener("click", () => {
  if (lastSimulation) downloadText(`${lastFilenameBase}_toa.csv`, lastSimulation.toa_csv, "text/csv");
});

elements.downloadWav.addEventListener("click", () => {
  if (lastSimulation) downloadBase64(`${lastFilenameBase}_borish_event_train.wav`, lastSimulation.wav_base64, "audio/wav");
});

elements.downloadDirectionalIr.addEventListener("click", () => {
  if (lastSimulation) downloadJson(`${lastFilenameBase}_directional_ir.json`, lastSimulation.directional_ir);
});

elements.recordWebm.addEventListener("click", async () => {
  if (!lastSimulation) return;
  await viewer.recordWebm(`${lastFilenameBase}_animation.webm`, 9500);
});

updatePlotControls();
redrawSelectedIrView();

// Load the reference case by default so a visitor can immediately press Run.
setDefaultShoeboxPoints();
loadMesh(await loadExampleObj("./examples/shoebox.obj"), "shoebox.obj").catch((error) => log(String(error)));
