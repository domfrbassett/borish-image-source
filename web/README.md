# Browser / Pyodide demo

This folder is a static GitHub Pages-ready web application. It runs the pure-Python Borish image-source solver inside Pyodide and renders geometry/path results with Three.js.

## Local run

From the repository root:

```powershell
cd web
python -m http.server 8000
```

Open:

```text
http://localhost:8000
```

Do not open `index.html` directly with `file://`; browser workers, WebAssembly and wheel loading need HTTP.

## What it supports

- OBJ acoustic mesh upload.
- Experimental `.3dm` loading through Three.js `Rhino3dmLoader`; this uses mesh/render geometry exposed by the loader. For reliable acoustic simulation, export the NURBS model to a closed OBJ acoustic mesh.
- Closure diagnostics.
- Source/receiver XYZ entry.
- Selected maximum reflection order and time window.
- Pyodide image-source simulation.
- Downloadable ancestry JSON.
- Downloadable ToA CSV.
- Downloadable mono WAV impulse response.
- WebGL path animation and downloadable WebM recording.

## Limits to keep the browser responsive

Start with order 2 or 3 and keep `max_nodes` below about 250,000. High reflection orders on highly tessellated curved models can generate millions of virtual sources.
