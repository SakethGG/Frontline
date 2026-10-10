# Start Frontline and open it in the browser.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path ".venv")) {
    python -m venv .venv
    .\.venv\Scripts\pip install -r requirements.txt
}
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }
Start-Process "http://localhost:8000"
.\.venv\Scripts\python -m uvicorn frontline.app:app --port 8000
