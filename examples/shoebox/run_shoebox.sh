#!/usr/bin/env sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../.." && pwd)
mkdir -p "$HERE/output"

python3 "$ROOT/borish_cli.py" "$HERE/borish_test_room.obj" \
  --source 2 3 1.2 \
  --receiver 6 5 1.2 \
  --max-order 2 \
  --max-time-ms 120 \
  --sample-rate 48000 \
  --speed-of-sound 343 \
  --band 1000 \
  --materials "$HERE/borish_test_materials.json" \
  --max-nodes 2000000 \
  --diagnose-inside \
  --output "$HERE/output/borish_test_room"

python3 "$ROOT/tools/verify_result.py" "$HERE/output/borish_test_room.json"
