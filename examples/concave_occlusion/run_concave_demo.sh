#!/usr/bin/env sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../.." && pwd)
cd "$HERE"
python3 ./concave_occlusion_audit.py
python3 "$ROOT/tools/verify_result.py" ./concave_run.json
printf '%s\n' "To build the GIF:" \
  "  python3 -m pip install -r '$ROOT/requirements-visualization.txt'" \
  "  python3 ./animate_concave_occlusion.py --output ./concave_occlusion_animation.gif --fps 8 --duration 10 --dpi 70"
