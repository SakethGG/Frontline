# One-time setup: creates the venv, installs dependencies, and adds Frontline
# shortcuts to your Desktop and Start menu. Then just double-click the icon.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path ".venv")) { python -m venv .venv }
.\.venv\Scripts\pip install -q -r requirements.txt
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }
.\.venv\Scripts\python -m frontline.desktop --install
Write-Host "Done - double-click 'Frontline' on your Desktop."
