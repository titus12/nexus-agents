param(
  [string]$ProjectRoot = "D:\workspace\src\btd-game-server",
  [switch]$Apply
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$templateRoot = Join-Path $repoRoot "templates"

if (-not (Test-Path -LiteralPath $templateRoot)) {
  throw "templates directory not found: $templateRoot"
}
if (-not (Test-Path -LiteralPath $ProjectRoot -PathType Container)) {
  throw "project root not found: $ProjectRoot"
}

$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).ProviderPath

$copyJobs = @()

function Write-Utf8NoBom {
  param(
    [string]$Path,
    [string]$Content
  )

  $encoding = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

function ConvertTo-Slug {
  param([string]$Value)

  $slug = $Value.ToLowerInvariant() -replace "[^a-z0-9]+", "-"
  return $slug.Trim("-")
}

function Normalize-RepoKey {
  param([string]$Remote)

  $value = $Remote.Trim()
  if ($value.EndsWith(".git")) {
    $value = $value.Substring(0, $value.Length - 4)
  }
  $value = $value -replace "^git@", ""
  $value = $value -replace ":", "/"
  $value = $value -replace "^https://", ""
  $value = $value -replace "^http://", ""
  return $value.ToLowerInvariant()
}

function Get-RepoKey {
  param([string]$Root)

  $config = Join-Path $Root ".git\config"
  if (Test-Path -LiteralPath $config -PathType Leaf) {
    $inOrigin = $false
    foreach ($line in Get-Content -LiteralPath $config) {
      $trimmed = $line.Trim()
      if ($trimmed.StartsWith("[")) {
        $inOrigin = $trimmed -ieq "[remote `"origin`"]"
        continue
      }
      if ($inOrigin -and $trimmed.StartsWith("url")) {
        $parts = $trimmed -split "=", 2
        if ($parts.Count -eq 2 -and $parts[1].Trim()) {
          return Normalize-RepoKey -Remote $parts[1]
        }
      }
    }
  }

  return "local:" + (ConvertTo-Slug -Value (Split-Path -Leaf $Root))
}

function Ensure-GitIgnoreRule {
  param(
    [string]$Root,
    [string]$Rule
  )

  $gitignore = Join-Path $Root ".gitignore"
  $content = ""
  if (Test-Path -LiteralPath $gitignore -PathType Leaf) {
    $content = Get-Content -LiteralPath $gitignore -Raw
    foreach ($line in ($content -split "`n")) {
      if ($line.Trim() -eq $Rule) {
        return
      }
    }
  }
  if ($content -and -not $content.EndsWith("`n")) {
    $content += "`n"
  }
  $content += "$Rule`n"
  Write-Utf8NoBom -Path $gitignore -Content $content
}

function Get-LegacyNexusGraphMoves {
  param([string]$Root)

  $nexusPath = Join-Path $Root ".nexus"
  if (-not (Test-Path -LiteralPath $nexusPath -PathType Container)) {
    return @()
  }

  $entries = @(Get-ChildItem -LiteralPath $nexusPath -Force)
  foreach ($entry in $entries) {
    if ($entry.Name -ne "workflows" -or -not $entry.PSIsContainer) {
      throw "legacy .nexus directory contains unknown entry $($entry.Name); move it before syncing"
    }
  }

  $workflowDir = Join-Path $nexusPath "workflows"
  if (-not (Test-Path -LiteralPath $workflowDir -PathType Container)) {
    return @()
  }

  $moves = @()
  Get-ChildItem -LiteralPath $workflowDir -File | Sort-Object Name | ForEach-Object {
    if ($_.Extension.ToLowerInvariant() -ne ".json") {
      throw "legacy .nexus\workflows contains unknown entry $($_.Name); move it before syncing"
    }
    $stem = [System.IO.Path]::GetFileNameWithoutExtension($_.Name)
    $moves += [pscustomobject]@{
      Source = $_.FullName
      Destination = Join-Path (Join-Path $Root ".claude\workflows") "$stem.graph.json"
    }
  }
  return $moves
}

function Move-LegacyNexusDirectory {
  param(
    [string]$Root,
    [object[]]$Moves
  )

  $nexusPath = Join-Path $Root ".nexus"
  if (-not (Test-Path -LiteralPath $nexusPath -PathType Container)) {
    return
  }

  foreach ($move in $Moves) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $move.Destination) -Force | Out-Null
    Move-Item -LiteralPath $move.Source -Destination $move.Destination -Force
  }

  $workflowDir = Join-Path $nexusPath "workflows"
  if (Test-Path -LiteralPath $workflowDir -PathType Container) {
    Remove-Item -LiteralPath $workflowDir -Force
  }
  Remove-Item -LiteralPath $nexusPath -Force
}

function Write-ProjectLocalMetadata {
  param([string]$Root)

  $projectName = Split-Path -Leaf $Root
  $metadataPath = Join-Path $Root ".nexus"
  $importedAt = $null
  if (Test-Path -LiteralPath $metadataPath -PathType Leaf) {
    try {
      $existing = Get-Content -LiteralPath $metadataPath -Raw | ConvertFrom-Json
      $importedAt = $existing.importedAt
    }
    catch {
      $importedAt = $null
    }
  }
  $now = (Get-Date).ToString("o")
  if (-not $importedAt) {
    $importedAt = $now
  }

  $metadata = [ordered]@{
    schemaVersion = 1
    projectId = ConvertTo-Slug -Value $projectName
    projectName = $projectName
    repoKey = Get-RepoKey -Root $Root
    localPath = $Root
    importedAt = $importedAt
    lastScannedAt = $now
  }
  Write-Utf8NoBom -Path $metadataPath -Content (($metadata | ConvertTo-Json -Depth 4) + "`n")
}

function Add-CopyJobs {
  param(
    [string]$SourceDir,
    [string]$Filter,
    [string]$DestinationDir
  )

  if (-not (Test-Path -LiteralPath $SourceDir)) {
    return
  }

  Get-ChildItem -LiteralPath $SourceDir -File -Filter $Filter | Sort-Object Name | ForEach-Object {
    $script:copyJobs += [pscustomobject]@{
      Source = $_.FullName
      Destination = Join-Path $DestinationDir $_.Name
    }
  }
}

Add-CopyJobs -SourceDir (Join-Path $templateRoot "agents\claude") -Filter "*.md" -DestinationDir (Join-Path $ProjectRoot ".claude\agents")
Add-CopyJobs -SourceDir (Join-Path $templateRoot "agents\codex") -Filter "*.toml" -DestinationDir (Join-Path $ProjectRoot ".codex\agents")
Add-CopyJobs -SourceDir (Join-Path $templateRoot "rules") -Filter "*.md" -DestinationDir (Join-Path $ProjectRoot ".claude\rules")
Add-CopyJobs -SourceDir (Join-Path $templateRoot "skills") -Filter "*.md" -DestinationDir (Join-Path $ProjectRoot ".claude\skills")
Add-CopyJobs -SourceDir (Join-Path $templateRoot "workflows") -Filter "*.md" -DestinationDir (Join-Path $ProjectRoot ".claude\workflows")

function New-Node {
  param(
    [string]$Id,
    [string]$Type,
    [string]$Category,
    [string]$Label,
    [string]$Agent,
    [string]$Detail,
    [int]$X,
    [int]$Y
  )
  [ordered]@{
    id = $Id
    type = $Type
    category = $Category
    label = $Label
    agent = $Agent
    detail = $Detail
    x = $X
    y = $Y
  }
}

function New-Edge {
  param([string]$From, [string]$To, [string]$Label)
  [ordered]@{ from = $From; to = $To; label = $Label }
}

function Get-WorkflowSpec {
  param([string]$Stem)

  $specs = @{
    "go-feature-development" = @{ Trigger = "$wf-go-feat"; Owner = "sisyphus"; Summary = "New Go feature development with clarification, implementation, testing, and verification." }
    "go-modify-existing" = @{ Trigger = "$wf-go-mod"; Owner = "hephaestus"; Summary = "Modify an existing Go feature after impact analysis and scoped verification." }
    "go-bugfix" = @{ Trigger = "$wf-go-bugfix"; Owner = "debugger"; Summary = "Investigate and fix a Go bug with root cause evidence and reproduction." }
    "go-code-review" = @{ Trigger = "$wf-go-review"; Owner = "sisyphus"; Summary = "Run parallel logic, performance, and security reviewers, then merge findings." }
    "design" = @{ Trigger = "$wf-design"; Owner = "prometheus"; Summary = "Produce architecture or implementation design without changing code." }
    "research" = @{ Trigger = "$wf-research"; Owner = "oracle"; Summary = "Read-only code understanding, investigation, or documentation research." }
    "commit-gate" = @{ Trigger = "$wf-commit"; Owner = "gatekeeper"; Summary = "Inspect diff and risks before committing." }
    "go-refactor" = @{ Trigger = "$wf-go-refactor"; Owner = "prometheus"; Summary = "Stage a Go refactor while keeping external behavior unchanged." }
    "lark-integration" = @{ Trigger = "$wf-lark"; Owner = "librarian"; Summary = "Route Feishu or Lark operations to the matching skill." }
  }

  if ($specs.ContainsKey($Stem)) {
    return $specs[$Stem]
  }
  return @{ Trigger = "manual"; Owner = "worker"; Summary = "Project workflow copied from Nexus template." }
}

function New-WorkflowGraphJson {
  param(
    [string]$Stem,
    [string]$Name
  )

  $spec = Get-WorkflowSpec -Stem $Stem
  $nodes = @(
    (New-Node -Id "trigger" -Type "input" -Category "event" -Label $spec.Trigger -Agent "-" -Detail "User input starts this workflow." -X 48 -Y 210),
    (New-Node -Id "route" -Type "condition" -Category "condition" -Label "routing match" -Agent "sisyphus" -Detail "Match the command section in .claude/rules/go-00-routing.md." -X 300 -Y 210),
    (New-Node -Id "owner" -Type "agent" -Category "action" -Label $spec.Owner -Agent $spec.Owner -Detail $spec.Summary -X 560 -Y 210),
    (New-Node -Id "skills" -Type "transform" -Category "data" -Label "load skills" -Agent "skill resolver" -Detail "Load required rules and skills for this workflow." -X 820 -Y 210),
    (New-Node -Id "verify" -Type "human_approval" -Category "human" -Label "checkpoint" -Agent "owner" -Detail "Report progress and wait when scope or risk is unclear." -X 1080 -Y 210),
    (New-Node -Id "output" -Type "output" -Category "data" -Label "deliver" -Agent $spec.Owner -Detail "Return result, evidence, and next action." -X 1340 -Y 210)
  )
  $edges = @(
    (New-Edge -From "trigger" -To "route" -Label "command"),
    (New-Edge -From "route" -To "owner" -Label "matched"),
    (New-Edge -From "owner" -To "skills" -Label "context"),
    (New-Edge -From "skills" -To "verify" -Label "steps"),
    (New-Edge -From "verify" -To "output" -Label "approved")
  )

  if ($Stem -eq "go-code-review" -or $Stem -eq "go-refactor") {
    $nodes += @(
      (New-Node -Id "logic" -Type "agent" -Category "action" -Label "reviewer-logic" -Agent "reviewer-logic" -Detail "Check control flow, state, transactions, nil handling, and boundaries." -X 820 -Y 70),
      (New-Node -Id "perf" -Type "agent" -Category "action" -Label "reviewer-perf" -Agent "reviewer-perf" -Detail "Check hot paths, allocations, locks, and goroutines." -X 820 -Y 350),
      (New-Node -Id "security" -Type "agent" -Category "action" -Label "reviewer-security" -Agent "reviewer-security" -Detail "Check permissions, validation, leakage, replay, and economy risks." -X 1080 -Y 350),
      (New-Node -Id "join" -Type "join" -Category "data" -Label "merge findings" -Agent "sisyphus" -Detail "Deduplicate findings and sort by severity." -X 1080 -Y 70)
    )
    $edges += @(
      (New-Edge -From "owner" -To "logic" -Label "diff"),
      (New-Edge -From "owner" -To "perf" -Label "diff"),
      (New-Edge -From "owner" -To "security" -Label "diff"),
      (New-Edge -From "logic" -To "join" -Label "findings"),
      (New-Edge -From "perf" -To "join" -Label "findings"),
      (New-Edge -From "security" -To "join" -Label "findings"),
      (New-Edge -From "join" -To "verify" -Label "report")
    )
  }

  $graph = [ordered]@{
    id = $Stem
    name = $Name
    nodes = $nodes
    edges = $edges
  }
  return ($graph | ConvertTo-Json -Depth 8)
}

$graphJobs = @()
$workflowTemplateDir = Join-Path $templateRoot "workflows"
if (Test-Path -LiteralPath $workflowTemplateDir) {
  Get-ChildItem -LiteralPath $workflowTemplateDir -File -Filter "*.md" | Sort-Object Name | ForEach-Object {
    $stem = [System.IO.Path]::GetFileNameWithoutExtension($_.Name)
    $firstLine = Get-Content -LiteralPath $_.FullName -TotalCount 1
    $name = if ($firstLine -and $firstLine.StartsWith("# ")) { $firstLine.Substring(2).Trim() } else { $stem }
    $templateGraph = Join-Path $workflowTemplateDir "$stem.graph.json"
    $content = if (Test-Path -LiteralPath $templateGraph -PathType Leaf) {
      Get-Content -LiteralPath $templateGraph -Raw
    }
    else {
      New-WorkflowGraphJson -Stem $stem -Name $name
    }
    $graphJobs += [pscustomobject]@{
      Destination = Join-Path (Join-Path $ProjectRoot ".claude\workflows") "$stem.graph.json"
      Content = $content.TrimEnd()
    }
  }
}

$legacyGraphMoves = @(Get-LegacyNexusGraphMoves -Root $ProjectRoot)
$legacyNexusPath = Join-Path $ProjectRoot ".nexus"
$metadataPath = Join-Path $ProjectRoot ".nexus"

Write-Host "Nexus Agents btd template sync plan"
Write-Host "ProjectRoot: $ProjectRoot"
Write-Host ("Mode: " + ($(if ($Apply) { "APPLY" } else { "DRY RUN" })))
Write-Host ""

if (Test-Path -LiteralPath $legacyNexusPath -PathType Container) {
  foreach ($move in $legacyGraphMoves) {
    Write-Host "MOVE  $($move.Source) -> $($move.Destination)"
  }
  Write-Host "REMOVE $legacyNexusPath"
}
foreach ($job in $copyJobs) {
  Write-Host "COPY  $($job.Source) -> $($job.Destination)"
}
foreach ($job in $graphJobs) {
  Write-Host "WRITE $($job.Destination)"
}
Write-Host "WRITE $metadataPath"
Write-Host "ENSURE .gitignore contains .nexus"

if (-not $Apply) {
  Write-Host ""
  Write-Host "Dry run only. Re-run with -Apply to write files."
  exit 0
}

Move-LegacyNexusDirectory -Root $ProjectRoot -Moves $legacyGraphMoves
foreach ($job in $copyJobs) {
  New-Item -ItemType Directory -Path (Split-Path -Parent $job.Destination) -Force | Out-Null
  Copy-Item -LiteralPath $job.Source -Destination $job.Destination -Force
}
foreach ($job in $graphJobs) {
  New-Item -ItemType Directory -Path (Split-Path -Parent $job.Destination) -Force | Out-Null
  Write-Utf8NoBom -Path $job.Destination -Content ($job.Content + "`n")
}
Write-ProjectLocalMetadata -Root $ProjectRoot
Ensure-GitIgnoreRule -Root $ProjectRoot -Rule ".nexus"

Write-Host ""
Write-Host "OK: synced Nexus templates into btd-game-server without deleting existing files."
