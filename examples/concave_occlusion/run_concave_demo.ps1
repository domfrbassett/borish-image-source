$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Resolve-Path (Join-Path $Here "..\..")
Set-Location $Here

python ".\concave_occlusion_audit.py"
python (Join-Path $Root "tools\verify_result.py") ".\concave_run.json"

Write-Host "To build the GIF, install the optional dependencies once:"
Write-Host "  python -m pip install -r `"$Root\requirements-visualization.txt`""
Write-Host "Then run:"
Write-Host "  python .\animate_concave_occlusion.py --output .\concave_occlusion_animation.gif --fps 8 --duration 10 --dpi 70"
