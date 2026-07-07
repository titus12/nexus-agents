param(
  [int]$Port = 8766,
  [switch]$SkipWebBuild
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$webRoot = Join-Path $root "web"
$goCache = Join-Path $root ".cache\go-build"
$base = "http://127.0.0.1:$Port"

if (-not $SkipWebBuild) {
  if (Test-Path (Join-Path $webRoot "node_modules")) {
    Push-Location $webRoot
    try {
      npm run build
    }
    finally {
      Pop-Location
    }
  }
  elseif (-not (Test-Path (Join-Path $webRoot "dist\index.html"))) {
    throw "web\dist is missing. Run npm install and npm run build in web before starting the Go service."
  }
  else {
    Write-Host "Skipping npm run build because web\node_modules is not installed; using existing web\dist."
  }
}

Write-Output "Starting Nexus Agents in foreground: $base"
Write-Output "Logs will continue in this console. Press Ctrl+C to stop."
Write-Output ""

Push-Location $root
try {
  $env:NEXUS_ADDR = ":$Port"
  $env:GOCACHE = $goCache
  go run .\cmd\nexus-agents
}
finally {
  Pop-Location
}
