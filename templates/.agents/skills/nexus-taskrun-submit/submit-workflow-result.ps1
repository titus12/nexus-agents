param(
    [Parameter(Mandatory = $true)]
    [string]$PayloadFile,
    [string]$Endpoint = "http://127.0.0.1:8766/api/task-runs",
    [string]$SessionId,
    [string]$StartedAt,
    [string]$EndedAt,
    [string]$ContextFile
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $PayloadFile)) { throw "Payload file not found: $PayloadFile" }
$payload = (Get-Content -Raw -LiteralPath $PayloadFile) | ConvertFrom-Json

if (-not [string]::IsNullOrWhiteSpace($ContextFile)) {
    if (-not (Test-Path -LiteralPath $ContextFile)) { throw "Context file not found: $ContextFile" }
    $context = Get-Content -Raw -LiteralPath $ContextFile | ConvertFrom-Json
    if ([string]::IsNullOrWhiteSpace($SessionId) -and $context.PSObject.Properties.Name -contains "sessionId") { $SessionId = [string]$context.sessionId }
    if ([string]::IsNullOrWhiteSpace($StartedAt) -and $context.PSObject.Properties.Name -contains "startedAt") { $StartedAt = [string]$context.startedAt }
}

function Set-Or-Add-Property([object]$Object, [string]$Name, [object]$Value) {
    if ($Object.PSObject.Properties.Name -contains $Name) { $Object.$Name = $Value }
    else { $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value }
}

if (-not [string]::IsNullOrWhiteSpace($SessionId)) { Set-Or-Add-Property $payload "sessionId" $SessionId.Trim() }
if (-not [string]::IsNullOrWhiteSpace($StartedAt)) { Set-Or-Add-Property $payload "startedAt" $StartedAt.Trim() }
if ([string]::IsNullOrWhiteSpace($EndedAt)) { $EndedAt = [DateTimeOffset]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ") }
Set-Or-Add-Property $payload "endedAt" $EndedAt.Trim()

foreach ($field in @("projectId","workflowType","taskTitle","submittedStatus","sessionId","startedAt","endedAt","metrics","evidence")) {
    if (-not ($payload.PSObject.Properties.Name -contains $field)) { throw "Payload must contain '$field'." }
    $value = $payload.$field
    if ($null -eq $value -or ($value -is [string] -and [string]::IsNullOrWhiteSpace($value))) { throw "Payload field '$field' must not be empty." }
}
foreach ($field in @("workflowId","workflowRunId","workflowTemplateId","workflowCopyId")) {
    if ($payload.PSObject.Properties.Name -contains $field) { throw "Payload must not contain '$field'." }
}

$body = $payload | ConvertTo-Json -Depth 32
[System.IO.File]::WriteAllText($PayloadFile, $body, [System.Text.UTF8Encoding]::new($false))
$headers = @{ "Content-Type" = "application/json; charset=utf-8"; "Accept" = "application/json" }
$bodyBytes = [System.Text.UTF8Encoding]::new($false).GetBytes($body)
$response = Invoke-RestMethod -Method Post -Uri $Endpoint -Headers $headers -Body $bodyBytes -TimeoutSec 10
$response | ConvertTo-Json -Depth 8
