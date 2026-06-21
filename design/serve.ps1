$ErrorActionPreference = "Stop"

param(
    [int]$Port = 8765
)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    $python = Get-Command py -ErrorAction SilentlyContinue
}

if ($python) {
    Write-Host "Serving Nexus Agents prototype at http://127.0.0.1:$Port/demo.html"
    & $python.Source -m http.server $Port --bind 127.0.0.1
    exit $LASTEXITCODE
}

$npx = Get-Command npx -ErrorAction SilentlyContinue
if ($npx) {
    Write-Host "Serving Nexus Agents prototype at http://127.0.0.1:$Port/demo.html"
    & $npx.Source http-server . -p $Port -a 127.0.0.1
    exit $LASTEXITCODE
}

throw "Neither python nor npx was found. Install one of them to serve the static prototype."

