
## 1.2.0-integrated-surface-tools

- Restores the left-column impulse-response panel and right-side ToA/ancestry panel layout.
- Adds surface selection with deselect actions.
- Adds wall/plane renaming and material renaming.
- Adds absorption and scattering assignment per selected surface, same group, connected coplanar plane, or all surfaces.
- Feeds per-surface absorption/scattering into the browser Pyodide solver and exported ancestry/ToA metadata.
- Keeps physical-time ray animation and simple viewer navigation.
- Keeps open-mesh and two-sided diagnostic options.

# Changelog

## 1.0.0 — 2026-07-08

- Complete dependency-free Borish image-source solver.
- OBJ command-line interface and Rhino 8 bridge.
- Reflection ancestry export to JSON and CSV.
- Sparse mono WAV early-reflection output.
- BVH obstruction checks for concave/re-entrant geometry.
- Corrected long-ray inside/outside diagnostic tolerance conversion.
- Dropbox/OneDrive-safe validation that compiles into a temporary directory.
- Convex shoebox and concave L-room regression tests.
- Generic and occlusion-audit animation utilities.

## v1.1.0

- Added `pyodide_api.py` for browser execution through Pyodide.
- Added static `web/` app with OBJ upload, experimental 3DM mesh loading, closure diagnostics, source/receiver fields, ISM run button, ancestry JSON download, ToA CSV download, WAV IR download, impulse-response plot, Three.js path visualization, and WebM animation recording.
- Added GitHub Pages deployment workflow.
- Added browser quickstart documentation.
