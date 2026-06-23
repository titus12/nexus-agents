param(
    [Parameter(Mandatory = $true)]
    [string]$PayloadFile,

    [string]$Endpoint = "http://127.0.0.1:8766/api/task-runs",

    [switch]$UseStore,

    [string]$StorePath = ""
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $PayloadFile)) {
    throw "Payload file not found: $PayloadFile"
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\\..\\..\\..\\..")
$repoRoot = $repoRoot.Path

$args = @(
    "run",
    ".\\cmd\\nexus-agents",
    "submit-task-run",
    "--file",
    $PayloadFile,
    "--endpoint",
    $Endpoint
)

if ($UseStore) {
    $args += "--use-store"
    if ($StorePath -ne "") {
        $args += "--store"
        $args += $StorePath
    }
}

Push-Location $repoRoot
try {
    & go @args
    if ($LASTEXITCODE -ne 0) {
        throw "submit-task-run failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}
