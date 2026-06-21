param(
  [int]$Port = 8766,
  [switch]$SkipWebBuild
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$webRoot = Join-Path $root "web"
$goCache = Join-Path $root ".cache\go-build"
$base = "http://127.0.0.1:$Port"

function Wait-HttpOk {
  param(
    [Parameter(Mandatory = $true)][string]$Url,
    [int]$Attempts = 60
  )

  for ($i = 0; $i -lt $Attempts; $i++) {
    try {
      $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 $Url
      if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
        return $response
      }
    }
    catch {
      Start-Sleep -Seconds 1
    }
  }

  throw "Timed out waiting for $Url"
}

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

$command = "`$env:NEXUS_ADDR=':$Port'; `$env:GOCACHE='$goCache'; go run .\cmd\nexus-agents"

$process = Start-Process -FilePath "powershell" `
  -ArgumentList @("-NoProfile", "-Command", $command) `
  -WorkingDirectory $root `
  -WindowStyle Hidden `
  -PassThru

try {
  Wait-HttpOk "$base/api/health" | Out-Null
  Wait-HttpOk $base | Out-Null
}
catch {
  if ($null -ne $process -and -not $process.HasExited) {
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
  }
  throw
}

Write-Output "Nexus Agents PID $($process.Id) $base"
Write-Output "API $base/api/health"
