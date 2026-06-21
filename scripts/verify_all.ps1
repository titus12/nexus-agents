$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

python scripts\verify_design.py
python scripts\verify_app_scaffold.py
python scripts\verify_template_catalog.py

if (Test-Path "web\node_modules") {
  Push-Location "web"
  try {
    npm run build
  }
  finally {
    Pop-Location
  }
}
elseif (-not (Test-Path "web\dist\index.html")) {
  throw "web\dist is missing. Run npm install and npm run build in web before Go tests."
}
else {
  Write-Host "Skipping npm run build because web\node_modules is not installed; using existing web\dist."
}

$env:GOCACHE = Join-Path $root ".cache\go-build"
go test ./...

if (Test-Path "web\node_modules") {
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\smoke_app.ps1
}
else {
  Write-Host "Skipping scripts\smoke_app.ps1 because web\node_modules is not installed."
}
