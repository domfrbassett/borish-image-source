# Pyodide website quickstart

## 1. Validate the Python solver

```powershell
python validate_install.py
```

Expected final line:

```text
RESULT=PASS
```

## 2. Run the browser app locally

```powershell
cd web
python -m http.server 8000
```

Open:

```text
http://localhost:8000
```

The page loads the shoebox example automatically. Click **Run ISM**.

Expected shoebox summary:

```text
paths=25
nodes_reflected=36
order_0_paths=1
order_1_paths=6
order_2_paths=18
```

Click **Load concave L-room**, then **Run ISM**.

Expected concave summary:

```text
direct_path_blocked=True
paths=43
rejected_obstruction=55
nodes_reflected=12541
```

## 3. Download artifacts from the browser

After a run, use the buttons:

- **JSON ancestry**: full path ancestry and geometry diagnostics.
- **ToA CSV**: arrival time / predelay table.
- **WAV IR**: rendered mono impulse response.
- **Record WebM**: recorded canvas animation.

## 4. Publish to GitHub Pages

Create a new GitHub repository, copy this full folder into it, commit and push to `main`.

Then on GitHub:

1. Open **Settings**.
2. Open **Pages**.
3. Set source to **GitHub Actions**.
4. Push again or run the **Deploy GitHub Pages** workflow manually.

The workflow validates the Python solver and deploys the static `web/` folder.

## 5. Mesh/NURBS notes

The reliable input format is a closed OBJ acoustic mesh in metres. `.3dm` loading is present as an experimental browser convenience, but serious NURBS models should be tessellated and simplified into closed acoustic patches before simulation.
