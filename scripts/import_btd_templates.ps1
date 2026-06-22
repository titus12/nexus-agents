$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$source = "D:\workspace\src\btd-game-server"
$templates = Join-Path $root "templates"

foreach ($folder in @("agents", "rules", "skills", "workflows")) {
  New-Item -ItemType Directory -Path (Join-Path $templates $folder) -Force | Out-Null
}
foreach ($folder in @("agents\claude", "agents\codex")) {
  New-Item -ItemType Directory -Path (Join-Path $templates $folder) -Force | Out-Null
}

$agentNames = @(
  "sisyphus",
  "prometheus",
  "hephaestus",
  "quick",
  "oracle",
  "debugger",
  "librarian",
  "worker",
  "reviewer-logic",
  "reviewer-perf",
  "reviewer-security",
  "gatekeeper"
)

foreach ($name in $agentNames) {
  Copy-Item -LiteralPath (Join-Path $source ".claude\agents\$name.md") -Destination (Join-Path $templates "agents\claude\go-$name.md") -Force
  Copy-Item -LiteralPath (Join-Path $source ".codex\agents\$name.toml") -Destination (Join-Path $templates "agents\codex\go-$name.toml") -Force
}

$ruleCopies = @{
  "00-routing.md" = "go-00-routing.md"
  "01-communication.md" = "01-communication.md"
  "02-safety.md" = "go-02-safety.md"
  "03-project-model.md" = "go-03-project-model.md"
}

foreach ($entry in $ruleCopies.GetEnumerator()) {
  Copy-Item -LiteralPath (Join-Path $source ".claude\rules\$($entry.Key)") -Destination (Join-Path $templates "rules\$($entry.Value)") -Force
}

$skillCopies = @{
  "dev-workflow.md" = "go-dev-workflow.md"
  "skill-standard.md" = "skill-standard.md"
  "review-feedback.md" = "review-feedback.md"
  "coding-rules.md" = "go-coding-rules.md"
  "testing.md" = "go-testing.md"
  "pmconf-pattern.md" = "go-pmconf-pattern.md"
  "high-risk-api.md" = "high-risk-api.md"
  "quest-system.md" = "go-quest-system.md"
  "cross-config.md" = "go-cross-config.md"
  "cross-client.md" = "cross-client.md"
  "cross-gate.md" = "cross-gate.md"
  "cross-social.md" = "cross-social.md"
}

foreach ($entry in $skillCopies.GetEnumerator()) {
  Copy-Item -LiteralPath (Join-Path $source ".claude\skills\$($entry.Key)") -Destination (Join-Path $templates "skills\$($entry.Value)") -Force
}

$workflowFiles = @{
  "go-feature-development.md" = @"
# go-feature-development

Source: ``templates/rules/go-00-routing.md``
Stack: ``go``

Entry skill: ``$wf-go-feat``

Use for new features or substantial modules. Load ``go-dev-workflow.md``, move through the nine-step development flow, and report after each step for user review before continuing.

Workflow:

1. Before step 1, ask 2-3 clarification questions to confirm scope.
2. Load ``go-dev-workflow.md``.
3. During implementation, load ``go-coding-rules.md``.
4. When writing tests, write the failing test first, watch it fail, implement, then watch it pass.
5. Verify with the Go project commands from ``templates/go-btd-game-server.md``.
"@
  "go-modify-existing.md" = @"
# go-modify-existing

Source: ``templates/rules/go-00-routing.md``
Stack: ``go``

Entry skill: ``$wf-go-mod``

Use for changes to existing Go features.

Workflow:

1. Locate the impact area with search or code graph.
2. Read the matching module skill when one exists.
3. Load ``go-coding-rules.md``.
4. Use ``go-hephaestus`` for multi-file work and ``go-quick`` for narrow single-file work.
5. Check high-risk changes with ``high-risk-api.md``.
6. Verify with build and relevant tests.
"@
  "go-bugfix.md" = @"
# go-bugfix

Source: ``templates/rules/go-00-routing.md``
Stack: ``go``

Entry skill: ``$wf-go-bugfix``

Use for Go runtime errors, crashes, and behavioral bugs.

Workflow:

1. Start with ``go-debugger`` and identify the root cause before editing.
2. Escalate to ``go-oracle`` for cross-module or ambiguous failures.
3. Add or describe a reproduction path.
4. Load ``go-coding-rules.md`` before changing code.
5. Verify the reproduction path plus build or targeted tests.
"@
  "go-code-review.md" = @"
# go-code-review

Source: ``templates/rules/go-00-routing.md``
Stack: ``go``

Entry skill: ``$wf-go-review``

Use for review of the current change set.

Workflow:

1. Dispatch reviewers in parallel:
   - ``go-reviewer-logic``
   - ``go-reviewer-perf``
   - ``go-reviewer-security``
2. Merge findings.
3. Deduplicate repeated issues.
4. Sort by severity.
5. Report concrete file and line references when available.
"@
  "design.md" = @"
# design

Source: ``templates/rules/go-00-routing.md``

Entry skill: ``$wf-design``

Use for architecture or implementation design without code changes.

Workflow:

1. Ask 2-3 clarifying questions about constraints and priorities.
2. Use ``go-prometheus`` when the target project is Go-related.
3. Compare 2-3 options by complexity, compatibility, risk, and verification.
4. Recommend one option and capture the plan.
"@
  "research.md" = @"
# research

Source: ``templates/rules/go-00-routing.md``

Entry skill: ``$wf-research``

Use for read-only understanding, investigation, or external documentation research.

Workflow:

1. Use ``go-oracle`` for code understanding when the target project is Go-related.
2. Use ``go-librarian`` for external documentation or API research.
3. Return conclusions, key paths, and references.
"@
  "commit-gate.md" = @"
# commit-gate

Source: ``templates/rules/go-00-routing.md``

Entry skill: ``$wf-commit``

Use as a pre-commit gate.

Workflow:

1. Use ``go-gatekeeper`` to inspect the current diff.
2. Produce a risk list.
3. Ask for confirmation on high-risk items.
4. Commit only after verification and explicit approval.
"@
  "go-refactor.md" = @"
# go-refactor

Source: ``templates/rules/go-00-routing.md``
Stack: ``go``

Entry skill: ``$wf-go-refactor``

Use for broad Go structural changes where external behavior should remain unchanged.

Workflow:

1. Analyze impact before editing.
2. Have ``go-prometheus`` design staged steps that remain buildable.
3. Implement with ``go-hephaestus``.
4. Run logic, performance, and security review in parallel.
5. Verify existing behavior still passes.
"@
  "lark-integration.md" = @"
# lark-integration

Source: ``templates/rules/go-00-routing.md``

Entry skill: ``$wf-lark``

Use for Feishu/Lark documents, messages, sheets, Base, and related operations.

Workflow:

1. Route to the matching Feishu/Lark skill.
2. Use ``go-librarian`` when repository context is needed.
3. Return links, extracted content, or operation results.
"@
}

foreach ($entry in $workflowFiles.GetEnumerator()) {
  Set-Content -LiteralPath (Join-Path $templates "workflows\$($entry.Key)") -Value $entry.Value -Encoding utf8
  $stem = [System.IO.Path]::GetFileNameWithoutExtension($entry.Key)
  $sourceGraph = Join-Path $source ".claude\workflows\$stem.graph.json"
  if (Test-Path -LiteralPath $sourceGraph -PathType Leaf) {
    Copy-Item -LiteralPath $sourceGraph -Destination (Join-Path $templates "workflows\$stem.graph.json") -Force
  }
}

Write-Host "OK: copied btd templates"
