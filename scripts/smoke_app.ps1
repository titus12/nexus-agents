$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$goProcess = $null
$smokeOk = $false

function Get-FreeSmokePort {
  for ($i = 0; $i -lt 40; $i++) {
    $candidate = Get-Random -Minimum 8800 -Maximum 8999
    $listener = Get-NetTCPConnection -LocalPort $candidate -State Listen -ErrorAction SilentlyContinue
    if ($null -eq $listener) {
      return $candidate
    }
  }

  throw "Could not find a free smoke test port."
}

function Stop-PortListeners {
  param([Parameter(Mandatory = $true)][int]$Port)

  $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  foreach ($listener in $listeners) {
    Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue
  }
}

$port = Get-FreeSmokePort
$base = "http://127.0.0.1:$port"

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

function Invoke-Json {
  param(
    [Parameter(Mandatory = $true)][string]$Url,
    [string]$Method = "GET",
    [string]$Body = ""
  )

  $parameters = @{
    Uri = $Url
    Method = $Method
    TimeoutSec = 5
  }
  if ($Body -ne "") {
    $parameters.ContentType = "application/json"
    $parameters.Body = $Body
  }
  try {
    return Invoke-RestMethod @parameters
  }
  catch {
    throw "$Method $Url failed: $($_.Exception.Message)"
  }
}

try {
  $env:GOCACHE = Join-Path $root ".cache\go-build"
  $env:NEXUS_ADDR = ":$port"

  $goProcess = Start-Process -FilePath "go" `
    -ArgumentList @("run", ".\cmd\nexus-agents") `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -PassThru

  Wait-HttpOk "$base/api/health" | Out-Null
  $index = Wait-HttpOk $base
  $fallback = Wait-HttpOk "$base/projects/btd-game-server/workflows"

  if ($index.Content -notmatch '<div id="app"></div>' -or $index.Content -notmatch '/assets/') {
    throw "Embedded Vue index did not expose the app mount or built assets."
  }
  if ($fallback.Content -notmatch '<div id="app"></div>') {
    throw "SPA fallback did not return the embedded Vue index."
  }

  $bootstrap = Wait-HttpOk "$base/api/bootstrap"
  if ($bootstrap.Content -notmatch '"templateLibrary"' -or $bootstrap.Content -notmatch '"projectConfigSets"') {
    throw "Embedded service did not return bootstrap API data."
  }

  $route = Invoke-Json "$base/api/model-routes/resolve?client=codex&model=gpt-5.4-mini"
  if ($route.targetModel -ne "gpt-5.4-mini") {
    throw "Model route resolve returned unexpected target model."
  }

  $createdRule = Invoke-Json "$base/api/templates/rules" "POST" '{"name":"smoke-rule","summary":"Smoke rule template."}'
  if ($createdRule.kind -ne "rule") {
    throw "Template create did not return a rule."
  }
  Invoke-WebRequest -UseBasicParsing -Method Delete -TimeoutSec 5 "$base/api/templates/.claude/rules/$($createdRule.id)" | Out-Null

  $agents = Invoke-Json "$base/api/templates/agents"
  if ($agents[0].modelTier -eq $null -or $agents[0].relatedRules.Count -eq 0 -or $agents[0].content -eq $null) {
    throw "Agent template metadata was incomplete."
  }

  $syncPreview = Invoke-Json "$base/api/projects/btd-game-server/sync-preview"
  if ($syncPreview.Count -eq 0 -or $syncPreview[0].syncMode -ne "manual") {
    throw "Project sync preview did not expose manual sync copies."
  }

  $workerCopy = $syncPreview | Where-Object {
    $_.kind -eq "agent" -and (
      $_.id -eq "proj_agent_btd_go_worker" -or
      $_.name -eq "worker" -or
      ($null -ne $_.origin -and $_.origin.templateId -eq "worker")
    )
  } | Select-Object -First 1
  if ($null -eq $workerCopy) {
    throw "Could not find worker project copy in sync preview."
  }

  $syncedCopy = Invoke-Json "$base/api/projects/btd-game-server/config/$($workerCopy.id)/sync" "POST"
  if ($syncedCopy.status -ne "synced") {
    throw "Project copy sync did not return synced status."
  }

  $workflowCopy = $syncPreview | Where-Object {
    $_.kind -eq "workflow" -and (
      $_.id -eq "proj_workflow_btd_go_feature_development" -or
      ($null -ne $_.origin -and $_.origin.templateId -eq "feature-development")
    )
  } | Select-Object -First 1
  if ($null -eq $workflowCopy) {
    throw "Could not find feature-development project workflow copy."
  }
  $projectGraph = Invoke-Json "$base/api/projects/btd-game-server/config/$($workflowCopy.id)/graph"
  if ($projectGraph.nodes.Count -eq 0 -or $projectGraph.edges.Count -eq 0) {
    throw "Project workflow graph did not expose nodes and edges."
  }

  $duplicatedWorkflow = Invoke-Json "$base/api/workflows/code-review/duplicate" "POST"
  if ($duplicatedWorkflow.id -eq "code-review" -or $duplicatedWorkflow.nodeCount -eq 0) {
    throw "Workflow duplicate did not return a new workflow with graph counts."
  }

  $smokeOk = $true
}
finally {
  if ($null -ne $goProcess -and -not $goProcess.HasExited) {
    try {
      Stop-Process -Id $goProcess.Id -Force
    }
    catch {
      if ($_.Exception.Message -notmatch "Cannot find a process") {
        throw
      }
    }
  }
  Stop-PortListeners $port
}

if ($smokeOk) {
  Write-Output "OK: embedded app smoke verified"
}
