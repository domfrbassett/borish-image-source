$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Resolve-Path (Join-Path $Here "..\..")
$Output = Join-Path $Here "output\borish_test_room"
New-Item -ItemType Directory -Force (Split-Path -Parent $Output) | Out-Null

python (Join-Path $Root "borish_cli.py") `
  (Join-Path $Here "borish_test_room.obj") `
  --source 2 3 1.2 `
  --receiver 6 5 1.2 `
  --max-order 2 `
  --max-time-ms 120 `
  --sample-rate 48000 `
  --speed-of-sound 343 `
  --band 1000 `
  --materials (Join-Path $Here "borish_test_materials.json") `
  --max-nodes 2000000 `
  --diagnose-inside `
  --output $Output

python (Join-Path $Root "tools\verify_result.py") "$Output.json"
Write-Host "Result files are in $(Split-Path -Parent $Output)"
