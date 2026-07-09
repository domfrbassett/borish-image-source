import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { downloadBlob } from "./downloads.js";

function vectorFromArray(values) {
  return new THREE.Vector3(values[0], values[1], values[2]);
}

function triangulateFaces(mesh) {
  const positions = [];
  const triangleFaceIds = [];

  for (let faceIndex = 0; faceIndex < mesh.faces.length; faceIndex++) {
    const face = mesh.faces[faceIndex];
    const indices = face.indices;
    for (let i = 1; i < indices.length - 1; i++) {
      for (const index of [indices[0], indices[i], indices[i + 1]]) {
        positions.push(...mesh.vertices[index]);
      }
      triangleFaceIds.push(faceIndex);
    }
  }

  return {
    positions: new Float32Array(positions),
    triangleFaceIds,
  };
}

function facePositions(mesh, faceIndex) {
  const face = mesh.faces[faceIndex];
  if (!face || !face.indices || face.indices.length < 3) return new Float32Array();

  const positions = [];
  const indices = face.indices;
  for (let i = 1; i < indices.length - 1; i++) {
    for (const index of [indices[0], indices[i], indices[i + 1]]) {
      positions.push(...mesh.vertices[index]);
    }
  }
  return new Float32Array(positions);
}

function meshBounds(mesh) {
  const box = new THREE.Box3();
  for (const vertex of mesh.vertices) box.expandByPoint(vectorFromArray(vertex));
  return box;
}

function polylineLength(points) {
  let length = 0;
  for (let i = 0; i < points.length - 1; i++) {
    length += vectorFromArray(points[i]).distanceTo(vectorFromArray(points[i + 1]));
  }
  return length;
}

function cutPolyline(points, distance) {
  if (!points || points.length === 0) return [];
  if (distance <= 0) return [points[0]];
  const total = polylineLength(points);
  if (distance >= total) return points;

  const output = [points[0]];
  let remaining = distance;
  for (let i = 0; i < points.length - 1; i++) {
    const a = vectorFromArray(points[i]);
    const b = vectorFromArray(points[i + 1]);
    const segmentLength = a.distanceTo(b);
    if (segmentLength <= 1e-12) continue;
    if (remaining >= segmentLength) {
      output.push(points[i + 1]);
      remaining -= segmentLength;
    } else {
      const p = a.lerp(b, remaining / segmentLength);
      output.push([p.x, p.y, p.z]);
      break;
    }
  }
  return output;
}

function makeLine(points, material) {
  const geometry = new THREE.BufferGeometry();
  if (points.length > 0) geometry.setFromPoints(points.map(vectorFromArray));
  return new THREE.Line(geometry, material);
}

function replaceLineGeometry(line, points) {
  line.geometry.dispose();
  line.geometry = new THREE.BufferGeometry().setFromPoints(points.map(vectorFromArray));
}

function estimateSoundSpeed(paths) {
  const estimates = [];
  for (const path of paths || []) {
    const length = path.path_length_m || polylineLength(path.path_vertices);
    const arrival = path.arrival_time_absolute_s;
    if (Number.isFinite(length) && Number.isFinite(arrival) && length > 1e-9 && arrival > 1e-9) {
      estimates.push(length / arrival);
    }
  }

  if (!estimates.length) return 343.0;
  estimates.sort((a, b) => a - b);
  return estimates[Math.floor(estimates.length / 2)];
}

export function makeViewer(container) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(container.clientWidth, container.clientHeight);
  container.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0a0e13);

  const camera = new THREE.PerspectiveCamera(55, container.clientWidth / container.clientHeight, 0.01, 10000);
  camera.position.set(14, -18, 12);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;

  const light = new THREE.HemisphereLight(0xffffff, 0x223344, 2.0);
  scene.add(light);
  const axes = new THREE.AxesHelper(2.0);
  scene.add(axes);

  const meshGroup = new THREE.Group();
  const pathGroup = new THREE.Group();
  const markerGroup = new THREE.Group();
  const highlightGroup = new THREE.Group();
  scene.add(meshGroup, pathGroup, markerGroup, highlightGroup);

  const sourceMarker = new THREE.Mesh(
    new THREE.SphereGeometry(0.16, 24, 12),
    new THREE.MeshBasicMaterial({ color: 0x65e0ff })
  );
  const receiverMarker = new THREE.Mesh(
    new THREE.SphereGeometry(0.16, 24, 12),
    new THREE.MeshBasicMaterial({ color: 0xffd36e })
  );
  markerGroup.add(sourceMarker, receiverMarker);

  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();

  let currentMesh = null;
  let solidMesh = null;
  let selectedSurfaceIndex = null;
  let surfaceSelectionHandler = null;
  let pointerDown = null;
  let currentPaths = [];
  let animationHandle = null;
  let lastAnimationDurationMs = 9000;

  function clearGroup(group) {
    while (group.children.length) {
      const child = group.children.pop();
      if (child.geometry) child.geometry.dispose();
      if (child.material) child.material.dispose?.();
    }
  }

  function fitToBox(box) {
    const size = new THREE.Vector3();
    const center = new THREE.Vector3();
    box.getSize(size);
    box.getCenter(center);
    const radius = Math.max(size.x, size.y, size.z, 1) * 0.85;
    camera.position.set(center.x + radius, center.y - radius * 1.4, center.z + radius * 0.9);
    camera.near = Math.max(0.01, radius / 1000);
    camera.far = Math.max(1000, radius * 100);
    camera.updateProjectionMatrix();
    controls.target.copy(center);
    controls.update();
  }

  function clearSurfaceHighlight() {
    clearGroup(highlightGroup);
    selectedSurfaceIndex = null;
  }

  function addSurfaceHighlight(faceIndex, opacity = 0.36) {
    if (!currentMesh || faceIndex === null || faceIndex === undefined || faceIndex < 0) return;

    const positions = facePositions(currentMesh, faceIndex);
    if (!positions.length) return;

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geometry.computeVertexNormals();

    const fill = new THREE.Mesh(
      geometry,
      new THREE.MeshBasicMaterial({
        color: 0xffd166,
        transparent: true,
        opacity,
        side: THREE.DoubleSide,
        depthWrite: false,
        polygonOffset: true,
        polygonOffsetFactor: -4,
        polygonOffsetUnits: -4,
      })
    );
    highlightGroup.add(fill);

    const edge = new THREE.LineSegments(
      new THREE.EdgesGeometry(geometry, 1),
      new THREE.LineBasicMaterial({ color: 0xfff2a8, transparent: true, opacity: 0.95 })
    );
    highlightGroup.add(edge);
  }

  function setSurfaceHighlight(faceIndex) {
    clearGroup(highlightGroup);
    selectedSurfaceIndex = faceIndex;
    addSurfaceHighlight(faceIndex, 0.42);
  }

  function setSurfaceHighlights(faceIndices) {
    clearGroup(highlightGroup);
    const indices = Array.isArray(faceIndices) ? faceIndices.filter((x) => Number.isInteger(x) && x >= 0) : [];
    selectedSurfaceIndex = indices.length ? indices[0] : null;
    for (const faceIndex of indices) addSurfaceHighlight(faceIndex, 0.30);
  }

  function showMesh(mesh) {
    clearGroup(meshGroup);
    clearGroup(pathGroup);
    clearSurfaceHighlight();
    currentPaths = [];
    currentMesh = mesh;
    solidMesh = null;

    const data = triangulateFaces(mesh);
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(data.positions, 3));
    geometry.computeVertexNormals();

    const material = new THREE.MeshBasicMaterial({
      color: 0x5e7da4,
      transparent: true,
      opacity: 0.18,
      side: THREE.DoubleSide,
      depthWrite: false,
    });

    solidMesh = new THREE.Mesh(geometry, material);
    solidMesh.userData.triangleFaceIds = data.triangleFaceIds;
    meshGroup.add(solidMesh);

    const edges = new THREE.LineSegments(
      new THREE.EdgesGeometry(geometry, 1),
      new THREE.LineBasicMaterial({ color: 0x8fb3d9, transparent: true, opacity: 0.55 })
    );
    meshGroup.add(edges);
    fitToBox(meshBounds(mesh));
  }

  function setMarkers(source, receiver) {
    sourceMarker.position.set(source[0], source[1], source[2]);
    receiverMarker.position.set(receiver[0], receiver[1], receiver[2]);
  }

  function showPaths(paths) {
    clearGroup(pathGroup);
    currentPaths = paths || [];
    const directMat = new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.95 });
    const firstMat = new THREE.LineBasicMaterial({ color: 0x61dafb, transparent: true, opacity: 0.78 });
    const higherMat = new THREE.LineBasicMaterial({ color: 0xa78bfa, transparent: true, opacity: 0.52 });

    for (const path of currentPaths) {
      const mat = path.order === 0 ? directMat.clone() : path.order === 1 ? firstMat.clone() : higherMat.clone();
      const line = makeLine(path.path_vertices, mat);
      line.userData.path = path;
      pathGroup.add(line);
    }
  }

  function animatePaths(paths = currentPaths, durationMs = 9000) {
    if (animationHandle) cancelAnimationFrame(animationHandle);
    clearGroup(pathGroup);
    currentPaths = paths || [];
    lastAnimationDurationMs = durationMs;

    if (!currentPaths.length) return;

    // Physical-time animation: all rays advance by the same acoustic distance at the same physical time.
    const soundSpeed = estimateSoundSpeed(currentPaths);
    const maxArrival = Math.max(0.001, ...currentPaths.map((p) => p.arrival_time_absolute_s || 0.001));

    const entries = [];
    for (const path of currentPaths) {
      const material = new THREE.LineBasicMaterial({
        color: path.order === 0 ? 0xffffff : path.order === 1 ? 0x61dafb : 0xa78bfa,
        transparent: true,
        opacity: path.order === 0 ? 0.96 : path.order === 1 ? 0.80 : 0.58,
      });
      const line = makeLine([path.path_vertices[0]], material);
      pathGroup.add(line);
      entries.push({
        path,
        line,
        pathLength: path.path_length_m || polylineLength(path.path_vertices),
      });
    }

    const startedAt = performance.now();
    function frame(now) {
      const elapsed = Math.min(durationMs, now - startedAt);
      const phase = Math.min(1, elapsed / durationMs);
      const physicalTime = maxArrival * phase;
      const acousticDistance = soundSpeed * physicalTime;

      for (const entry of entries) {
        const distance = Math.min(entry.pathLength, acousticDistance);
        const fraction = entry.pathLength > 1e-12 ? distance / entry.pathLength : 1.0;
        const partial = cutPolyline(entry.path.path_vertices, distance);
        replaceLineGeometry(entry.line, partial);
        entry.line.material.opacity = fraction >= 1 ? (entry.path.order === 0 ? 0.82 : 0.32) : (entry.path.order === 0 ? 1.0 : 0.78);
      }
      if (elapsed < durationMs) animationHandle = requestAnimationFrame(frame);
    }
    animationHandle = requestAnimationFrame(frame);
  }

  async function recordWebm(filename = "borish_animation.webm", durationMs = lastAnimationDurationMs) {
    animatePaths(currentPaths, durationMs);
    const stream = renderer.domElement.captureStream(30);
    const options = MediaRecorder.isTypeSupported("video/webm;codecs=vp9")
      ? { mimeType: "video/webm;codecs=vp9" }
      : { mimeType: "video/webm" };
    const recorder = new MediaRecorder(stream, options);
    const chunks = [];
    recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
    const finished = new Promise((resolve) => { recorder.onstop = resolve; });
    recorder.start();
    setTimeout(() => recorder.stop(), durationMs + 250);
    await finished;
    downloadBlob(filename, new Blob(chunks, { type: "video/webm" }));
  }

  function pickSurface(event) {
    if (!solidMesh || !currentMesh) return;

    const rect = renderer.domElement.getBoundingClientRect();
    pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(pointer, camera);

    const hits = raycaster.intersectObject(solidMesh, false);

    // Click empty viewer space to clear the yellow selection.
    if (!hits.length) {
      clearSurfaceHighlight();
      if (surfaceSelectionHandler) surfaceSelectionHandler(null, null);
      return;
    }

    const triangleIndex = hits[0].faceIndex;
    const faceIndex = solidMesh.userData.triangleFaceIds?.[triangleIndex];
    if (faceIndex === undefined || faceIndex === null) return;

    // Click the already-selected surface again to toggle it off.
    if (selectedSurfaceIndex === faceIndex) {
      clearSurfaceHighlight();
      if (surfaceSelectionHandler) surfaceSelectionHandler(null, null);
      return;
    }

    setSurfaceHighlight(faceIndex);
    if (surfaceSelectionHandler) surfaceSelectionHandler(faceIndex, currentMesh.faces[faceIndex]);
  }

  renderer.domElement.addEventListener("pointerdown", (event) => {
    pointerDown = { x: event.clientX, y: event.clientY, button: event.button };
  });

  renderer.domElement.addEventListener("pointerup", (event) => {
    if (!pointerDown || pointerDown.button !== 0 || event.button !== 0) return;
    const dx = event.clientX - pointerDown.x;
    const dy = event.clientY - pointerDown.y;
    pointerDown = null;

    // Only select on a click, not on an orbit drag.
    if (Math.hypot(dx, dy) <= 4) pickSurface(event);
  });

  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      clearSurfaceHighlight();
      if (surfaceSelectionHandler) surfaceSelectionHandler(null, null);
    }
  });

  function renderLoop() {
    controls.update();
    renderer.render(scene, camera);
    requestAnimationFrame(renderLoop);
  }
  renderLoop();

  window.addEventListener("resize", () => {
    const width = container.clientWidth;
    const height = container.clientHeight;
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    renderer.setSize(width, height);
  });

  return {
    canvas: renderer.domElement,
    showMesh,
    setMarkers,
    showPaths,
    animatePaths,
    recordWebm,
    setSurfaceSelectionHandler(handler) { surfaceSelectionHandler = handler; },
    setSurfaceHighlight,
    setSurfaceHighlights,
    clearSurfaceHighlight,
    getSelectedSurfaceIndex() { return selectedSurfaceIndex; },
  };
}
