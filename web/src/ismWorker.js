import { loadPyodide } from "https://cdn.jsdelivr.net/pyodide/v0.29.4/full/pyodide.mjs";

let pyodideReady = null;

async function initializePyodide() {
  const pyodide = await loadPyodide({
    indexURL: "https://cdn.jsdelivr.net/pyodide/v0.29.4/full/"
  });

  await pyodide.loadPackage("micropip");
  const micropip = pyodide.pyimport("micropip");
  const wheelUrlObject = new URL("../vendor/borish_image_source-1.1.0-py3-none-any.whl", import.meta.url);
  wheelUrlObject.search = "v=room_metrics_20260718";
  const wheelUrl = wheelUrlObject.href;
  await micropip.install(wheelUrl);
  await pyodide.runPythonAsync("from pyodide_api import run_simulation_json, check_mesh_json");
  return pyodide;
}

function ensurePyodide() {
  if (!pyodideReady) pyodideReady = initializePyodide();
  return pyodideReady;
}

function formatWorkerError(error) {
  const text = String(error || "");
  const matches = [...text.matchAll(/ValueError:\s*([^\n]+)/g)];
  if (matches.length) return matches[matches.length - 1][1];
  return text.split("\n").map((x) => x.trim()).filter(Boolean).pop() || text || "Unknown error";
}

self.onmessage = async (event) => {
  const { type, payload } = event.data;
  try {
    self.postMessage({ type: "status", message: "Loading Pyodide/Python package..." });
    const pyodide = await ensurePyodide();
    const payloadJson = JSON.stringify(payload);
    pyodide.globals.set("payload_json", payloadJson);

    if (type === "check") {
      const reportJson = await pyodide.runPythonAsync("check_mesh_json(payload_json)");
      self.postMessage({ type: "check-complete", report: JSON.parse(reportJson) });
    } else if (type === "simulate") {
      self.postMessage({ type: "status", message: "Running image-source solver..." });
      const resultJson = await pyodide.runPythonAsync("run_simulation_json(payload_json)");
      self.postMessage({ type: "simulation-complete", result: JSON.parse(resultJson) });
    }
  } catch (error) {
    self.postMessage({ type: "error", error: formatWorkerError(error), message: formatWorkerError(error), stack: "" });
  }
};
