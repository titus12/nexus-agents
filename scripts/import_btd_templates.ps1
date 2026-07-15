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

Set-Content -LiteralPath (Join-Path $templates "rules\go-04-task-decomposition.md") -Encoding utf8 -Value @'
# Go 任务拆分与派发规则

进入实施前，每个任务必须定义主要业务目标、依赖、允许修改范围、必须完成项、验证方式、风险和未确认假设。

方案阶段必须评估业务目标数、模块/package、非机械性生产文件、独立验证路径、高风险项和未确认假设。出现多个独立目标、多个模块或验证路径、公共契约/迁移改动、未确认业务假设或高风险项时，必须拆分 Task Capsule，或先进入设计/等待用户确认。

Task Capsule 必须独立可验收，并在计划中写明执行顺序、并行条件、集成点和父任务负责的总体目标门、质量门及最终验证。

选择 ``$wf-go-feat`` 或 ``$wf-go-bugfix`` 即授权 Owner 按角色编排 subagent。Owner 必须先拆分任务；只能派发边界不重叠、完成标准和验证方式明确的单一目标 Capsule，不能把整个需求交给单个 subagent。
'@

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
  "go-feature-development.md" = @'
# go-feature-development

Source: `templates/rules/go-00-routing.md`
Stack: `go`

Entry skill: `$wf-go-feat`

适用于 Go 新功能、既有行为修改和跨文件业务调整。

## 强约束

1. 先探索，后实施：加载适用 Rules、知识库 routing 和真实代码证据后，才生成方案和修改代码。
2. 目标和质检必须可审核：需求不清晰时补齐目标、完成标准、假设和证据；权威需求来源不可读取时必须如实阻塞或取得用户同意后降级。
3. 重大假设必须确认：业务语义、公共契约、迁移和高风险改动必须等待用户确认。
4. 任务必须有边界：每项任务定义目标、允许修改范围、完成标准、验证和风险。
5. 目标门和质量门不得省略：代码写完或子任务完成不等于工作流完成。
6. Loop 受证据和上限约束：父工作流最多 3 个完整 Loop；每个子任务最多 2 次实施-质检尝试。

## 工作流

1. 先读取用户提供的权威需求来源，再加载知识库和探索真实代码、调用方、测试、配置。
2. 生成待审核目标契约：目标、必须完成项、非目标、范围、验证、假设/证据和风险；事实未确认时先输出 diagnosis-first 诊断方案。
3. 重大假设确认后，制定计划；复杂任务仅在边界清晰时拆成 Task Capsule。
4. 执行 Task Capsule Loop：最小探索 -> 最小实现 -> 子目标检查 -> 子任务质检 -> 集成。
5. 通过目标门确认必须完成项与影响范围。
6. 通过质量门确认 Rules、最小 diff、测试、构建、兼容性和真实验证证据；无新证据、超限或阻塞时退出为 partial_success 或 blocked。

## 待审核目标契约

```text
任务类型：feature | modification
目标：
必须完成：
非目标：
范围：
验证与质检：
关键假设及证据：
风险：
是否需要用户确认：
```

## 需求来源与事实冲突

权威需求来源无法读取时，必须请求授权或经用户明确同意后降级；不得用本地候选代码猜测替代。需求与代码、配置、协议或知识库冲突时，记录冲突来源、影响、选项、推荐方案和是否需要用户决定。

## Diagnosis-First 方案

原因或真实行为未确认时，先记录证据缺口、最小诊断步骤、预期观察结果和进入实现的判断规则；不得把推测性改动当作实现计划。

## 探索上下文包

只保留需求摘要、权威来源状态、关键路径、已确认事实、冲突/风险和任务拆分决策；不得默认传递完整对话历史或无关大日志。

## Task Capsule

```text
目标：
依赖：
允许修改范围：
完成标准：
验证方式：
当前尝试次数：
已有证据与风险：
```
'@
  "go-bugfix.md" = @"
# go-bugfix

Source: ``templates/rules/go-00-routing.md``
Stack: ``go``

Entry skill: ``$wf-go-bugfix``

Use for Go runtime errors, crashes, and behavioral bugs.

Workflow:

1. Load Rules, KB routing, authoritative requirement sources, logs, failing tests, code and callers; do not replace an unreadable authoritative source with local guesses.
2. Reproduce the failure and record exact command, input, environment and error evidence.
3. Report conflicts among requirements, logs, code, config, protocol and tests before choosing a fix.
4. When root cause is uncertain, use a diagnosis-first plan with minimal diagnostic logs or probes; do not apply speculative behavior changes.
5. After root cause evidence is sufficient, load ``go-04-task-decomposition.md`` and record whether the repair must be split.
6. Implement the smallest confirmed root-cause repair, then verify reproduction, regression, affected package tests or build, and diff safety.
7. Keep the loop bounded by new evidence and retry limits; report partial or blocked status when evidence is insufficient.
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
