$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "uv is not installed or is not on PATH." -ForegroundColor Red
    Write-Host "Install uv from https://docs.astral.sh/uv/getting-started/installation/ and try again."
    Read-Host "Press Enter to close"
    exit 1
}

Write-Host "Preparing Sandbox..." -ForegroundColor Cyan
uv sync
if ($LASTEXITCODE -ne 0) {
    Read-Host "Setup failed. Press Enter to close"
    exit $LASTEXITCODE
}

Write-Host "Opening Sandbox Job Wizard..." -ForegroundColor Green
uv run streamlit run app.py
if ($LASTEXITCODE -ne 0) {
    Read-Host "The app stopped with an error. Press Enter to close"
    exit $LASTEXITCODE
}
