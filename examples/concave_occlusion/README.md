# Concave occlusion reference example

This is a closed L-shaped enclosure with a re-entrant corner. The source and
receiver are in different wings, so their direct segment crosses the notch wall.
The demo records both valid paths and candidate paths rejected by obstruction.

Source: `(10, 2, 1.2)` m  
Receiver: `(2, 8, 1.2)` m

Run on Windows:

```powershell
.\run_concave_demo.ps1
```

Expected result:

```text
source_inside=True
receiver_inside=True
direct_path_blocked=True
direct_blocker=Notch_South_Wall
accepted_paths=43
rejected_obstruction=55
rejected_visibility=3033
nodes_reflected=3131
```

Build the animated GIF after installing the optional visualization packages:

```powershell
python -m pip install -r ..\..\requirements-visualization.txt
python .\animate_concave_occlusion.py `
  --output .\concave_occlusion_animation.gif `
  --fps 8 --duration 10 --dpi 70
```
