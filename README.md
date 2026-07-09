# Borish image-source early reflections

Version **1.0.0**

A Python implementation of Jeffrey Borish's recursive image-source algorithm
for arbitrary **piecewise-planar, closed 3-D acoustic domains**. The package
includes:

- a dependency-free simulation core;
- a command-line OBJ workflow;
- a Rhino 8 bridge for closed NURBS Breps, Extrusions and Meshes used by
  Pachyderm;
- WAV, JSON and CSV export;
- complete ordered reflection ancestry;
- deterministic shoebox validation;
- a concave L-room test that demonstrates direct-path occlusion and candidate
  path rejection;
- 3-D animation tools for accepted and obstruction-rejected paths.

## Package layout

```text
borish_core.py                  Geometry, BVH, recursion, path validation, IR export
borish_cli.py                   Command-line OBJ interface
rhino_pachyderm_bridge.py       Rhino 8 / Pachyderm geometry bridge
validate_install.py             Full shoebox and concave self-check
tools/verify_result.py          Validate an exported JSON result
tools/animate_reflections.py    Generic accepted-path animation
examples/shoebox/               Convex reference room and run scripts
examples/concave_occlusion/     Re-entrant room, audit and occlusion animation
tests/                          Deterministic unit tests
```

## Requirements

The solver itself uses only the Python standard library and supports Python
3.8 or later.

Animation is optional:

```powershell
python -m pip install -r requirements-visualization.txt
```

MP4 output additionally requires FFmpeg. GIF output requires only the Python
packages in the requirements file.

## Validate first

From the extracted package folder:

```powershell
python validate_install.py
```

The last line must be:

```text
RESULT=PASS
```

The validator writes temporary bytecode outside the package directory, avoiding
common Dropbox and OneDrive `__pycache__` locking errors.

A successful report includes:

```text
syntax=PASS
unit_tests=PASS tests_run=6
inside_diagnostic=PASS source=True receiver=True outside=False
order1=PASS paths=7 reflected_nodes=6 analytic_lengths=True ancestry=True
order2=PASS paths=25 reflected_nodes=36 ancestry=True
concave_occlusion=PASS direct_blocked=True paths=43 ...
outputs=PASS ...
RESULT=PASS
```

## Command-line simulation

The OBJ geometry must be in metres, closed, watertight and consistently wound.
Outer enclosure normals point outward from the room. Use `--flip-normals` only
when the entire OBJ is reversed.

```powershell
python borish_cli.py room.obj `
  --source 2 3 1.2 `
  --receiver 6 5 1.2 `
  --max-order 3 `
  --max-time-ms 120 `
  --sample-rate 48000 `
  --speed-of-sound 343 `
  --band 1000 `
  --materials example_materials.json `
  --max-nodes 2000000 `
  --diagnose-inside `
  --output .\results\room_A
```

Outputs:

- `room_A.wav` — sparse mono early-reflection impulse response;
- `room_A.json` — full path geometry, image positions, reflection ancestry,
  arrival direction, material data and statistics;
- `room_A.csv` — flattened path/ancestry table.

Check an output file:

```powershell
python tools\verify_result.py .\results\room_A.json
```

## Shoebox demonstration

```powershell
cd .\examples\shoebox
.\run_shoebox.ps1
```

Expected result: 25 paths and 36 reflected nodes at order 2.

Create an accepted-path GIF:

```powershell
python -m pip install -r ..\..\requirements-visualization.txt
python ..\..\tools\animate_reflections.py `
  .\output\borish_test_room.json `
  --obj .\borish_test_room.obj `
  --output .\output\borish_test_room.gif
```

## Concave occlusion demonstration

```powershell
cd .\examples\concave_occlusion
.\run_concave_demo.ps1
```

The direct line from source to receiver is blocked by the re-entrant notch. The
reference simulation accepts 43 higher-order paths and records 55 candidates
rejected by obstruction testing.

Create the audit animation:

```powershell
python -m pip install -r ..\..\requirements-visualization.txt
python .\animate_concave_occlusion.py `
  --output .\concave_occlusion_animation.gif `
  --fps 8 --duration 10 --dpi 70
```

The animation shows:

1. the direct ray stopping at the notch wall;
2. selected candidate paths being cut at their first obstructing surface;
3. valid reflected paths reaching the receiver.

## Rhino 8 / Pachyderm workflow

1. Keep `borish_core.py` and `rhino_pachyderm_bridge.py` together.
2. Open the Rhino model used by Pachyderm.
3. Open Rhino 8 ScriptEditor and choose Python 3.
4. Open and run `rhino_pachyderm_bridge.py`.
5. Select `Simulate`, choose the closed room geometry, then place source and
   receiver points.
6. Enter order, time window, tessellation length, material and output settings.

The bridge converts Rhino units to metres, orients closed components relative
to the acoustic source, tessellates curved NURBS faces into planar reflectors,
and can bake the accepted paths back into Rhino.

## Algorithm implemented

For every retained virtual source, the solver:

1. reflects it across every planar reflector;
2. rejects reflections made from the non-reflective side;
3. prunes nodes beyond the selected maximum path length;
4. reconstructs the real path backwards through the ordered image ancestry;
5. verifies every reflection point lies on its finite reflector;
6. tests every real path segment against a triangle BVH for obstruction;
7. records visible, unobstructed paths;
8. continues propagating invisible nodes because descendants can become
   visible, as required by Borish's method.

Traversal is depth-first, limiting live ancestry storage. `max_order` and
`max_nodes` are practical safeguards against exponential tree growth.

## Acoustic model

For path length `R` and reflection surfaces `j`, the normalized pressure event
amplitude is:

```text
(R_direct / R) × product(sqrt(1 - alpha_j))
```

Optional exponential air attenuation can also be applied. Fractional event
delays are linearly split between adjacent samples in the generated WAV.

## Scope and limitations

This is a geometrical-acoustics, specular early-reflection model. It does not
model diffraction, edge scattering, diffuse reflection, frequency-dependent
filter convolution, HRTFs, phase inversion, room modes or statistically
synthesized late reverberation.

Curved NURBS surfaces are represented by piecewise-planar tessellation. Finer
meshes better follow curvature but increase the image-source tree, often very
rapidly. Start with order 2 and a coarse mesh, verify the result, then perform a
mesh/order convergence study.

The command-line OBJ loader assumes its selected surface shell already
represents the acoustic domain correctly. The Rhino bridge is preferable for
mixed outer shells and interior closed obstacles because it can orient each
closed component relative to the source position.

## Reference

Jeffrey Borish, “Extension of the image model to arbitrary polyhedra,”
*Journal of the Acoustical Society of America*, 75(6), 1827–1836 (1984),
DOI: 10.1121/1.390983.

## Browser / Pyodide website

This release includes a static browser app in `web/` that runs the pure-Python solver in Pyodide and visualizes the paths with Three.js.

Local run:

```powershell
cd web
python -m http.server 8000
```

Open `http://localhost:8000`.

The browser app supports closed OBJ acoustic meshes, closure checking, source/receiver XYZ entry, selected reflection order/time window, downloadable ancestry JSON, ToA CSV, WAV impulse response, and WebM animation recording. Experimental `.3dm` loading is included through Three.js `Rhino3dmLoader`, but robust NURBS support still requires tessellating to a closed acoustic mesh before simulation.

See `PYODIDE_WEBSITE_QUICKSTART.md` for full commands.
