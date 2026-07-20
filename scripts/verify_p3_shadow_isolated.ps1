param(
  [Parameter(Mandatory = $true)][string]$Executable,
  [Parameter(Mandatory = $true)][string]$ProjectRoot,
  [Parameter(Mandatory = $true)][string]$GBrainExecutable,
  [string]$ReportPath = "",
  [int]$SequentialQueries = 200,
  [switch]$KeepArtifacts
)

$ErrorActionPreference = "Stop"

function Get-FreeLoopbackPort {
  $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
  try {
    $listener.Start()
    return ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
  }
  finally {
    $listener.Stop()
  }
}

function Invoke-JSON {
  param(
    [Parameter(Mandatory = $true)][string]$Uri,
    [string]$Method = "GET",
    [object]$Body = $null,
    [int]$TimeoutSec = 60
  )

  $parameters = @{
    Uri = $Uri
    Method = $Method
    TimeoutSec = $TimeoutSec
  }
  if ($null -ne $Body) {
    $json = $Body | ConvertTo-Json -Depth 10 -Compress
    $parameters.ContentType = "application/json; charset=utf-8"
    $parameters.Body = [System.Text.Encoding]::UTF8.GetBytes($json)
  }
  return Invoke-RestMethod @parameters
}

function Wait-HTTPReady {
  param(
    [Parameter(Mandatory = $true)][string]$BaseURL,
    [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process,
    [int]$TimeoutSeconds = 60
  )

  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    if ($Process.HasExited) {
      throw "Isolated Nexus exited before HTTP became ready (exit $($Process.ExitCode))."
    }
    try {
      $health = Invoke-RestMethod -Uri "$BaseURL/api/health" -TimeoutSec 2
      if ($health.status -eq "ok") {
        return
      }
    }
    catch {
      Start-Sleep -Milliseconds 500
    }
  }
  throw "Timed out waiting for isolated Nexus HTTP readiness."
}

function Wait-KnowledgeGraphReady {
  param(
    [Parameter(Mandatory = $true)][string]$LogRoot,
    [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process,
    [int]$TimeoutSeconds = 240
  )

  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    if ($Process.HasExited) {
      throw "Isolated Nexus exited before GBrain became ready (exit $($Process.ExitCode))."
    }
    $logs = Get-ChildItem -LiteralPath $LogRoot -Recurse -File -Filter *.log -ErrorAction SilentlyContinue
    foreach ($log in $logs) {
      $content = Get-Content -LiteralPath $log.FullName -Raw -ErrorAction SilentlyContinue
      if ($content -match "knowledge graph ready provider=gbrain engine=pglite") {
        return
      }
      if ($content -match "knowledge graph unavailable") {
        throw "Isolated GBrain startup failed: $($Matches[0]). See $($log.FullName)."
      }
    }
    Start-Sleep -Seconds 1
  }
  throw "Timed out waiting for isolated GBrain/PGLite readiness."
}

function Decode-UTF8Base64([string]$Value) {
  return [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($Value))
}

$resolvedExecutable = [System.IO.Path]::GetFullPath($Executable)
$resolvedProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
$resolvedGBrainExecutable = [System.IO.Path]::GetFullPath($GBrainExecutable)
foreach ($required in @($resolvedExecutable, $resolvedGBrainExecutable)) {
  if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
    throw "Required executable is missing: $required"
  }
}
if (-not (Test-Path -LiteralPath $resolvedProjectRoot -PathType Container)) {
  throw "Project root is missing: $resolvedProjectRoot"
}

$tempBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$runRoot = Join-Path $tempBase ("nexus-p3-isolated-" + [Guid]::NewGuid().ToString("N"))
$isolatedHome = Join-Path $runRoot "home"
$gbrainHome = Join-Path $runRoot "gbrain"
$graphState = Join-Path $runRoot "knowledge-graph"
$graphSources = Join-Path $runRoot "gbrain-sources"
$syncState = Join-Path $runRoot "knowledge-sync"
$knowledgeExports = Join-Path $runRoot "knowledge-exports"
$logs = Join-Path $runRoot "logs"
foreach ($directory in @($isolatedHome, $gbrainHome, $graphState, $graphSources, $syncState, $knowledgeExports, $logs)) {
  New-Item -ItemType Directory -Path $directory -Force | Out-Null
}

$syntheticProjectRoot = Join-Path $runRoot "synthetic-project"
New-Item -ItemType Directory -Path $syntheticProjectRoot -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $resolvedProjectRoot "KnowledgeBase") -Destination (Join-Path $syntheticProjectRoot "KnowledgeBase") -Recurse -Force
$syntheticSetting = Join-Path $syntheticProjectRoot "KnowledgeBase\Setting.yaml"
$settingContent = Get-Content -LiteralPath $syntheticSetting -Raw
if ($settingContent -notmatch "sourceId:\s*project:nexus-agents") {
  throw "Could not prepare isolated peer sourceId from Setting.yaml."
}
$settingContent = $settingContent -replace "sourceId:\s*project:nexus-agents", "sourceId: project:nexus-agents-shadow-peer"
Set-Content -LiteralPath $syntheticSetting -Value $settingContent -Encoding UTF8
Set-Content -LiteralPath (Join-Path $syntheticProjectRoot "README.md") -Value "# Nexus Shadow Peer`n" -Encoding UTF8
& git -C $syntheticProjectRoot init | Out-Null
if ($LASTEXITCODE -ne 0) { throw "git init failed for isolated peer project" }
& git -C $syntheticProjectRoot config user.email "nexus-shadow@example.test"
& git -C $syntheticProjectRoot config user.name "Nexus Shadow Test"
& git -C $syntheticProjectRoot add .
& git -C $syntheticProjectRoot commit -m "isolated shadow fixture" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "git commit failed for isolated peer project" }

$port = Get-FreeLoopbackPort
$baseURL = "http://127.0.0.1:$port"
$process = [System.Diagnostics.Process]::new()
$process.StartInfo = [System.Diagnostics.ProcessStartInfo]::new()
$process.StartInfo.FileName = $resolvedExecutable
$process.StartInfo.WorkingDirectory = $resolvedProjectRoot
$process.StartInfo.UseShellExecute = $false
$process.StartInfo.CreateNoWindow = $true
$environment = @{
  "HOME" = $isolatedHome
  "USERPROFILE" = $isolatedHome
  "CODEX_HOME" = (Join-Path $isolatedHome ".codex")
  "NEXUS_ADDR" = "127.0.0.1:$port"
  "NEXUS_LOG_DIR" = $logs
  "NEXUS_KNOWLEDGE_GRAPH_ENABLED" = "true"
  "NEXUS_GBRAIN_EXECUTABLE" = $resolvedGBrainExecutable
  "NEXUS_GBRAIN_HOME" = $gbrainHome
  "NEXUS_KNOWLEDGE_GRAPH_DIR" = $graphState
  "NEXUS_GBRAIN_SOURCE_DIR" = $graphSources
  "NEXUS_KNOWLEDGE_SYNC_DIR" = $syncState
  "NEXUS_KNOWLEDGE_EXPORT_DIR" = $knowledgeExports
  "NEXUS_KB_QUERY_REWRITE_ENABLED" = "false"
}
foreach ($entry in $environment.GetEnumerator()) {
  $process.StartInfo.Environment[$entry.Key] = $entry.Value
}

$report = $null
try {
  if (-not $process.Start()) {
    throw "Failed to start isolated Nexus."
  }
  Wait-HTTPReady -BaseURL $baseURL -Process $process
  Wait-KnowledgeGraphReady -LogRoot $logs -Process $process

  $imported = Invoke-JSON -Uri "$baseURL/api/projects/import" -Method POST -Body @{
    name = "nexus-agents-shadow-test"
    path = $resolvedProjectRoot
  }
  if ([string]::IsNullOrWhiteSpace($imported.id)) {
    throw "Project import did not return an id."
  }
  $projectID = $imported.id
  $projectBase = "$baseURL/api/projects/$([Uri]::EscapeDataString($projectID))/knowledge"
  $peer = Invoke-JSON -Uri "$baseURL/api/projects/import" -Method POST -Body @{
    name = "nexus-agents-shadow-peer"
    path = $syntheticProjectRoot
  }
  if ([string]::IsNullOrWhiteSpace($peer.id)) {
    throw "Peer project import did not return an id."
  }
  $peerBase = "$baseURL/api/projects/$([Uri]::EscapeDataString($peer.id))/knowledge"

  $rebuild = Invoke-JSON -Uri "$projectBase/graph/rebuild" -Method POST -Body @{} -TimeoutSec 180
  if ($rebuild.result.documents -ne 21 -or $rebuild.result.created -ne 21) {
    throw "Expected isolated rebuild to create 21 documents, got $($rebuild.result | ConvertTo-Json -Compress)."
  }
  $idempotent = Invoke-JSON -Uri "$projectBase/graph/sync" -Method POST -Body @{} -TimeoutSec 180
  if ($idempotent.result.documents -ne 21 -or $idempotent.result.unchanged -ne 21) {
    throw "Expected idempotent sync to keep 21 unchanged documents, got $($idempotent.result | ConvertTo-Json -Compress)."
  }
  $peerRebuild = Invoke-JSON -Uri "$peerBase/graph/rebuild" -Method POST -Body @{} -TimeoutSec 180
  if ($peerRebuild.result.documents -ne 21 -or $peerRebuild.result.created -ne 21) {
    throw "Expected isolated peer rebuild to create 21 documents, got $($peerRebuild.result | ConvertTo-Json -Compress)."
  }
  $group = Invoke-JSON -Uri "$baseURL/api/project-groups" -Method POST -Body @{
    name = "Shadow Project Group"
    projectIds = @($projectID, $peer.id)
  }
  if ([string]::IsNullOrWhiteSpace($group.id) -or $group.projectIds.Count -ne 2) {
    throw "Project Group creation failed: $($group | ConvertTo-Json -Compress)."
  }

  $cases = @(
    @{
      query = Decode-UTF8Base64 "5L+u5pS55qih5Z6L6Lev55Sx5Lya5b2x5ZON5ZOq5Lqb5Yqf6IO95ZKM5YWl5Y+j77yf"
      expectedPaths = @(
        "KnowledgeBase/project/domains/model-routing/index.md",
        "KnowledgeBase/project/domains/model-routing/change-guidance.md"
      )
    },
    @{
      query = Decode-UTF8Base64 "55+l6K+G5bqT5aaC5L2V5omr5o+P5LuT5bqT44CB5qOA57Si5LiK5LiL5paH5bm25Yqg6L2957uZIEFJ77yf"
      expectedPaths = @(
        "KnowledgeBase/project/domains/knowledgebase/repository-scanning-and-indexing.md",
        "KnowledgeBase/project/domains/knowledgebase/domain-routing-and-context-retrieval.md",
        "KnowledgeBase/project/domains/knowledgebase/ai-workflow-context-loading.md"
      )
    },
    @{
      query = Decode-UTF8Base64 "5qih5p2/5Yid5aeL5YyW5ZKM5ZCM5q2l5pyJ5LuA5LmI5Yy65Yir77yf"
      expectedPaths = @(
        "KnowledgeBase/project/domains/template-management/initialization-versus-synchronization.md"
      )
    },
    @{
      query = Decode-UTF8Base64 "V29ya2Zsb3cgUnVuIOWmguS9lei/m+WFpeivhOS8sO+8nw=="
      expectedPaths = @(
        "KnowledgeBase/project/domains/workflow-evaluation/workflow-run-to-evaluation-lifecycle.md"
      )
    },
    @{
      query = Decode-UTF8Base64 "5ZOq5Lqb5Yqf6IO95L6d6LWWIFNlc3Npb24tSWTvvJ8="
      expectedPaths = @(
        "KnowledgeBase/project/domains/model-routing/session-bound-telemetry.md",
        "KnowledgeBase/project/domains/workflow-evaluation/workflow-run-to-evaluation-lifecycle.md"
      )
    }
  )

  $queryResults = @()
  foreach ($case in $cases) {
    $response = Invoke-JSON -Uri "$projectBase/graph/shadow-search" -Method POST -TimeoutSec 90 -Body @{
      query = $case.query
      mode = "routing"
      maxTokens = 6000
      expectedPaths = $case.expectedPaths
    }
    $shadow = $response.shadow
    if ($shadow.status -ne "ready") {
      throw "Shadow query degraded: $($shadow | ConvertTo-Json -Depth 10 -Compress)"
    }
    if ($shadow.gbrain.timedOut) {
      throw "Shadow query timed out: $($case.query)"
    }
    if ($shadow.comparison.duplicateDocuments.Count -ne 0) {
      throw "Shadow query returned duplicate GBrain pages: $($case.query)"
    }
    $queryResults += [ordered]@{
      query = $case.query
      normalizedQuery = $shadow.normalizedQuery
      status = $shadow.status
      fts5Domain = $shadow.fts5.matchedDomain
      gbrainDomain = $shadow.gbrain.matchedDomain
      domainMatched = $shadow.comparison.domainMatched
      fts5Documents = $shadow.fts5.paths.Count
      gbrainDocuments = $shadow.gbrain.paths.Count
      overlap = $shadow.comparison.pathOverlap.Count
      requiredPrecision = $shadow.comparison.requiredDocumentPrecision
      expectedCoverage = $shadow.comparison.expectedDocumentCoverage
      fts5LatencyMs = $shadow.fts5.latencyMs
      gbrainLatencyMs = $shadow.gbrain.latencyMs
      timedOut = $shadow.gbrain.timedOut
      duplicates = $shadow.comparison.duplicateDocuments.Count
      missingExpectedPaths = $shadow.comparison.missingExpectedPaths
    }
  }

  $groupResponse = Invoke-JSON -Uri "$projectBase/graph/shadow-search" -Method POST -TimeoutSec 120 -Body @{
    query = $cases[0].query
    mode = "routing"
    maxTokens = 6000
    scope = "group"
    groupId = $group.id
    expectedPaths = $cases[0].expectedPaths
  }
  $groupShadow = $groupResponse.shadow
  if ($groupShadow.status -ne "ready" -or $groupShadow.scope -ne "group" -or
      $groupShadow.groupId -ne $group.id -or $groupShadow.sourceIds.Count -ne 2 -or
      $groupShadow.comparison.duplicateDocuments.Count -ne 0) {
    throw "Project Group shadow verification failed: $($groupShadow | ConvertTo-Json -Depth 10 -Compress)."
  }

  $sequentialStarted = Get-Date
  for ($index = 0; $index -lt $SequentialQueries; $index++) {
    $case = $cases[$index % $cases.Count]
    $response = Invoke-JSON -Uri "$projectBase/graph/shadow-search" -Method POST -TimeoutSec 90 -Body @{
      query = $case.query
      mode = "routing"
      maxTokens = 6000
      expectedPaths = $case.expectedPaths
    }
    $shadow = $response.shadow
    if ($shadow.status -ne "ready" -or $shadow.gbrain.timedOut) {
      throw "Sequential shadow query $index failed: $($shadow | ConvertTo-Json -Depth 10 -Compress)"
    }
  }
  $sequentialDurationMS = [int64]((Get-Date) - $sequentialStarted).TotalMilliseconds

  $summary = Invoke-JSON -Uri "$projectBase/graph/shadow-summary"
  $health = Invoke-JSON -Uri "$baseURL/api/health"
  if ($health.status -ne "ok") {
    throw "Isolated Nexus health failed after sequential queries."
  }
  $report = [ordered]@{
    isolated = $true
    projectID = $projectID
    rebuild = $rebuild.result
    idempotentSync = $idempotent.result
    peerRebuild = $peerRebuild.result
    projectGroup = $group
    groupShadow = [ordered]@{
      scope = $groupShadow.scope
      groupId = $groupShadow.groupId
      sourceIds = $groupShadow.sourceIds
      fts5Documents = $groupShadow.fts5.scopedPaths.Count
      gbrainDocuments = $groupShadow.gbrain.scopedPaths.Count
      overlap = $groupShadow.comparison.pathOverlap.Count
      duplicates = $groupShadow.comparison.duplicateDocuments.Count
      expectedCoverage = $groupShadow.comparison.expectedDocumentCoverage
    }
    queries = $queryResults
    sequential = [ordered]@{
      queries = $SequentialQueries
      durationMs = $sequentialDurationMS
      processHealthy = $true
    }
    summary = $summary
  }

  if ([string]::IsNullOrWhiteSpace($ReportPath)) {
    $ReportPath = Join-Path $resolvedProjectRoot ".cache\p3-shadow-isolated-report.json"
  }
  $resolvedReportPath = [System.IO.Path]::GetFullPath($ReportPath)
  New-Item -ItemType Directory -Path (Split-Path -Parent $resolvedReportPath) -Force | Out-Null
  $report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $resolvedReportPath -Encoding UTF8
  Write-Output "OK: isolated P3 Shadow Search verified"
  Write-Output "REPORT=$resolvedReportPath"
  Write-Output ($report | ConvertTo-Json -Depth 12 -Compress)
}
finally {
  if (-not $process.HasExited) {
    try {
      $process.Kill($true)
    }
    catch {
      try {
        $process.Kill()
      }
      catch {
      }
    }
    [void]$process.WaitForExit(15000)
  }
  $process.Dispose()

  if (-not $KeepArtifacts -and (Test-Path -LiteralPath $runRoot)) {
    $resolvedRunRoot = [System.IO.Path]::GetFullPath($runRoot)
    $runLeaf = Split-Path -Leaf $resolvedRunRoot
    if (-not $resolvedRunRoot.StartsWith($tempBase, [System.StringComparison]::OrdinalIgnoreCase) -or
        -not $runLeaf.StartsWith("nexus-p3-isolated-", [System.StringComparison]::OrdinalIgnoreCase)) {
      throw "Refusing to remove unexpected isolated run path: $resolvedRunRoot"
    }
    Remove-Item -LiteralPath $resolvedRunRoot -Recurse -Force
  }
}
