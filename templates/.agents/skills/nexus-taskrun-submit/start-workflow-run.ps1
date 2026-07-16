param(
    [Parameter(Mandatory = $true)]
    [string]$WorkflowType,
    [Parameter(Mandatory = $true)]
    [string]$TaskTitle,
    [string]$SessionId,
    [string]$ProjectId,
    [string]$PayloadFile,
    [string]$ContextFile,
    [string]$StartedAt
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ProjectId)) { $ProjectId = Split-Path -Leaf (Get-Location) }
if ([string]::IsNullOrWhiteSpace($SessionId)) { $SessionId = $env:CODEX_THREAD_ID }
if ([string]::IsNullOrWhiteSpace($SessionId)) {
    throw "SessionId is required. Codex should provide CODEX_THREAD_ID automatically; if unavailable, use /status and pass -SessionId."
}
if ([string]::IsNullOrWhiteSpace($StartedAt)) { $StartedAt = [DateTimeOffset]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ") }
if ([string]::IsNullOrWhiteSpace($PayloadFile)) {
    $safeWorkflowType = ($WorkflowType -replace '[^A-Za-z0-9_.-]', '-')
    $PayloadFile = Join-Path ".nexus" "task-run-$safeWorkflowType.json"
}

$payloadDir = Split-Path -Parent $PayloadFile
if (-not [string]::IsNullOrWhiteSpace($payloadDir) -and -not (Test-Path -LiteralPath $payloadDir)) {
    New-Item -ItemType Directory -Path $payloadDir -Force | Out-Null
}

$payload = [ordered]@{
    projectId = $ProjectId
    workflowType = $WorkflowType
    taskTitle = $TaskTitle
    submittedStatus = "partial_success"
    sessionId = $SessionId.Trim()
    startedAt = $StartedAt
    metrics = [ordered]@{
        filesChangedCount = 0
        testRunCount = 0
    }
    evidence = [ordered]@{
        changedFiles = @()
        skippedChecks = @()
        remainingRisks = @()
    }
}

[System.IO.File]::WriteAllText($PayloadFile, ($payload | ConvertTo-Json -Depth 32), [System.Text.UTF8Encoding]::new($false))

if (-not [string]::IsNullOrWhiteSpace($ContextFile)) {
    $contextDir = Split-Path -Parent $ContextFile
    if (-not [string]::IsNullOrWhiteSpace($contextDir) -and -not (Test-Path -LiteralPath $contextDir)) {
        New-Item -ItemType Directory -Path $contextDir -Force | Out-Null
    }
    $context = [ordered]@{
        projectId = $ProjectId
        workflowType = $WorkflowType
        taskTitle = $TaskTitle
        sessionId = $SessionId.Trim()
        startedAt = $StartedAt
        payloadFile = $PayloadFile
        startMode = "local-payload-only"
    }
    [System.IO.File]::WriteAllText($ContextFile, ($context | ConvertTo-Json -Depth 12), [System.Text.UTF8Encoding]::new($false))
}

[ordered]@{
    projectId = $ProjectId
    workflowType = $WorkflowType
    taskTitle = $TaskTitle
    sessionId = $SessionId.Trim()
    startedAt = $StartedAt
    payloadFile = $PayloadFile
    contextFile = $ContextFile
    startMode = "local-payload-only"
} | ConvertTo-Json -Depth 8
