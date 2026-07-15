# Go Feature Workflow Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `$wf-go-feat` the only Go business-change workflow, enforce the approved bounded workflow lifecycle in its templates, and remove all active `go-modify-existing` / `$wf-go-mod` surfaces.

**Architecture:** Keep the existing file-backed template library and catalog model. Update the feature workflow Markdown, graph, Codex skill, command, and Go routing rule as one coherent template family; remove the obsolete modification family rather than redirecting it. Update catalog specs and static/API tests so projects can sync only the feature workflow and so a removed workflow cannot silently return.

**Tech Stack:** Go 1.22 standard library tests, Markdown templates, JSON workflow graphs, TOML/Codex skill projections, PowerShell synchronization scripts, Python template verifier.

---

## File Structure

| Path | Responsibility |
|---|---|
| `templates/workflows/go-feature-development.md` | Canonical `$wf-go-feat` instructions, hard constraints, six stages, bounded task loop, gates, exit states. |
| `templates/workflows/go-feature-development.graph.json` | Visual graph for the canonical lifecycle. |
| `templates/skills/codex/wf-go-feat/SKILL.md` | Codex entrypoint that loads the canonical workflow and preserves workflow-run evidence protocol. |
| `templates/commands/claude/wf-go-feat.md` | Claude command entrypoint for the canonical workflow. |
| `templates/rules/go-00-routing.md` | Go routing and global safety rule; documents the single Go change entry and user-authorized subagent rule. |
| `templates/skills/go-coding-rules.md` | Removes the deleted `$wf-go-mod` reference. |
| `templates/workflows/go-modify-existing.md` | Deleted obsolete workflow Markdown. |
| `templates/workflows/go-modify-existing.graph.json` | Deleted obsolete graph. |
| `templates/skills/codex/wf-go-mod/` | Deleted obsolete Codex skill and metadata. |
| `templates/commands/claude/wf-go-mod.md` | Deleted obsolete Claude command. |
| `internal/catalog/catalog.go` | Removes obsolete workflow/skill/path/copy-state entries and describes the unified feature workflow. |
| `internal/httpapi/server_test.go` | API inventory regression test for the removed workflow. |
| `scripts/verify_template_catalog.py` | Static template inventory and canonical feature-workflow contract checks. |
| `scripts/import_btd_templates.ps1` | Bootstrap importer no longer creates the deleted workflow and emits the unified feature workflow. |
| `scripts/sync_btd_templates.ps1` | Sync graph metadata no longer declares the deleted workflow. |

## Task 1: Lock the Public Catalog Inventory Before Removing Code

**Files:**
- Modify: `internal/httpapi/server_test.go:513-624`
- Modify: `internal/catalog/catalog.go:566-594`
- Modify: `internal/catalog/catalog.go:1908-1930`
- Modify: `internal/catalog/catalog.go:2088-2156`
- Modify: `internal/catalog/catalog.go:2174-2220`
- Modify: `internal/catalog/catalog.go:2364-2408`

- [ ] **Step 1: Update the workflow inventory API test to exclude the obsolete workflow**

In `TestBtdGameServerTemplateInventory`, replace the expected workflow ID block with:

```go
assertTemplateIDs(t, "workflows", workflows, []string{
    "feature-development", "bugfix", "code-review", "design",
    "research", "commit-gate", "refactor", "lark-integration", "subagent-driven-development",
    "bug-investigation", "logic-modification", "ui-feature-development",
})
```

This is the regression assertion: the template API must not expose `modify-existing`.

- [ ] **Step 2: Run the API inventory test and verify it fails**

Run:

```powershell
go test ./internal/httpapi -run '^TestBtdGameServerTemplateInventory$' -count=1
```

Expected: FAIL because the current catalog still returns `modify-existing`.

- [ ] **Step 3: Remove the obsolete workflow-to-skill mapping**

In `workflowSkillTemplateID`, delete:

```go
case "modify-existing":
    return "wf-go-mod", true
```

Keep the `feature-development` case returning `wf-go-feat`.

- [ ] **Step 4: Remove the obsolete Codex skill specification and update the feature description**

In the `btdSkillTemplates` spec list:

1. Delete the complete `wf-go-mod` item.
2. Replace the `wf-go-feat` fields with:

```go
summary: "Codex skill entry for the unified Go business-change workflow.",
content: "Invoke with $wf-go-feat to load templates/workflows/go-feature-development.md and follow the bounded Go business-change workflow for new features or existing behavior modifications.",
applicableAgents: []string{"sisyphus", "prometheus", "hephaestus", "quick", "worker"},
updatedAt: "2026-07-15 00:00",
```

- [ ] **Step 5: Remove obsolete skill and workflow path cases**

Make these exact catalog changes:

```go
// btdSkillTemplatePath
case "wf-go-feat", "wf-go-bugfix", "wf-go-review", "wf-go-refactor",
    "wf-design", "wf-research", "wf-commit", "wf-lark", "wf-subagents",
    "wf-unity-bugfix", "wf-unity-logic-mod", "wf-unity-ui-feature":
```

and:

```go
// btdWorkflowTemplatePath
case "feature-development", "bugfix", "code-review", "refactor":
    return "templates/workflows/go-" + id + ".md"
```

Do not include `wf-go-mod` or `modify-existing` in either switch.

- [ ] **Step 6: Remove the obsolete workflow specification and copy-state branches**

1. Delete the complete `modify-existing` item from `btdWorkflowSpecs`.
2. Update the `feature-development` item:

```go
name:      "$wf-go-feat Go 业务变更",
trigger:   "$wf-go-feat",
owner:     "sisyphus",
summary:   "统一处理 Go 新功能、既有行为修改和跨文件业务调整；先探索知识与真实代码，再经目标门和质量门交付。",
content:   "加载规则、知识库路由和真实代码后生成待审核目标契约；必要时等待用户确认；复杂任务使用有边界的 Task Capsule Loop；每轮都通过目标门和质量门，并受证据和重试上限约束。",
tags:      []string{"routing", "feature", "modification", "quality-gate", "bounded-loop"},
status:    "ready",
updatedAt: "2026-07-15 00:00",
```

3. Remove `modify-existing` from `btdCopyStatus`.
4. Delete the `modify-existing` branch from `btdCopyDiff`.

- [ ] **Step 7: Format and run the API inventory regression test**

Run:

```powershell
gofmt -w internal/catalog/catalog.go internal/httpapi/server_test.go
go test ./internal/httpapi -run '^TestBtdGameServerTemplateInventory$' -count=1
```

Expected: PASS.

- [ ] **Step 8: Commit the catalog inventory change**

```powershell
git add internal/catalog/catalog.go internal/httpapi/server_test.go
git commit -m "refactor: remove Go modification workflow catalog"
```

## Task 2: Replace the Feature Workflow With the Bounded, Evidence-Driven Template

**Files:**
- Modify: `scripts/verify_template_catalog.py`
- Modify: `templates/workflows/go-feature-development.md`
- Modify: `templates/workflows/go-feature-development.graph.json`
- Modify: `templates/skills/codex/wf-go-feat/SKILL.md`
- Modify: `templates/commands/claude/wf-go-feat.md`
- Modify: `templates/rules/go-00-routing.md`
- Modify: `templates/skills/go-coding-rules.md`

- [ ] **Step 1: Add static verification for the unified workflow contract**

In `scripts/verify_template_catalog.py`:

1. Remove `"go-modify-existing.md"` from `GO_WORKFLOW_FILES`.
2. Add:

```python
GO_FEATURE_WORKFLOW_TOKENS = [
    "## 强约束",
    "先探索，后实施",
    "## 工作流",
    "生成待审核目标契约",
    "目标门",
    "质量门",
    "父工作流最多 3 个完整 Loop",
    "每个子任务最多 2 次",
    "success | partial_success | blocked | failed | cancelled",
]
```

3. Immediately after the Go workflow loop in `main()`, add:

```python
require_tokens(
    "templates/workflows/go-feature-development.md",
    GO_FEATURE_WORKFLOW_TOKENS,
)
```

4. Add absence checks:

```python
for rel in [
    "templates/workflows/go-modify-existing.md",
    "templates/workflows/go-modify-existing.graph.json",
    "templates/commands/claude/wf-go-mod.md",
    "templates/skills/codex/wf-go-mod/SKILL.md",
]:
    expect(not (ROOT / rel).exists(), f"obsolete Go modification template remains: {rel}")
```

- [ ] **Step 2: Run the static verifier and verify it fails**

Run:

```powershell
python scripts\verify_template_catalog.py
```

Expected: FAIL because the current feature workflow lacks the required contract tokens and obsolete files still exist.

- [ ] **Step 3: Rewrite the canonical workflow Markdown**

Replace `templates/workflows/go-feature-development.md` with a concise Chinese workflow containing:

```markdown
# go-feature-development

来源：`templates/rules/go-00-routing.md`
技术栈：Go
入口 skill：`$wf-go-feat`

适用于 Go 新功能、既有行为修改和跨文件业务调整。
```

Then define:

1. `## 强约束` with the eight hard constraints from the approved design: explore before implementation; auditable target/quality; confirmation for major assumptions; bounded tasks; user-authorized subagents; mandatory gates; bounded loops; auditable final result.
2. `## 工作流` with six numbered stages: context/exploration; draft contract; confirm/plan/split; task loop/integration; goal gate; quality gate/exit.
3. A `## 待审核目标契约` fenced template containing objective, required completion criteria, non-goals, scope, validation, assumptions/evidence, risks, and confirmation requirement.
4. A `## Task Capsule` fenced template containing objective, dependencies, allowed scope, completion criteria, validation, attempt number, evidence, and risks.
5. A `## Workflow Run Header Protocol` section retaining the existing canonical workflow-run ID and both `X-Nexus-Workflow-*` headers.
6. A concise `## Task Run Evidence Protocol` that points to `$nexus-taskrun-submit`, preserves the existing endpoint and status vocabulary, and requires actual validation evidence.

Use the exact required strings from `GO_FEATURE_WORKFLOW_TOKENS`.

- [ ] **Step 4: Replace the workflow graph with the approved lifecycle**

Rewrite `templates/workflows/go-feature-development.graph.json` with these node IDs and labels:

```json
[
  {"id":"start","label":"接收任务"},
  {"id":"explore","label":"加载上下文并探索"},
  {"id":"contract","label":"生成待审核目标契约"},
  {"id":"confirmation","label":"重大假设或高风险？"},
  {"id":"plan","label":"计划并决定是否拆任务"},
  {"id":"task_loop","label":"执行任务 Loop 并集成"},
  {"id":"goal_gate","label":"目标门"},
  {"id":"quality_gate","label":"质量门"},
  {"id":"complete","label":"提交证据，用户审核"},
  {"id":"blocked","label":"partial_success / blocked"}
]
```

Create edges in this exact control flow:

```text
start -> explore -> contract -> confirmation
confirmation(yes) -> plan
confirmation(no) -> plan
plan -> task_loop -> goal_gate -> quality_gate
quality_gate(pass) -> complete
quality_gate(retry) -> plan
quality_gate(stop) -> blocked
```

Use existing graph node fields (`id`, `type`, `category`, `label`, `agent`, `detail`, `x`, `y`) and existing edge shape (`from`, `to`, `label`). Mark `confirmation`, `goal_gate`, and `quality_gate` as condition nodes.

- [ ] **Step 5: Align the entrypoints and supporting rules**

1. In `templates/skills/codex/wf-go-feat/SKILL.md`, change the description to state that it handles both new features and existing behavior modifications, and retain the workflow-run/evidence protocol. Its workflow section must still read `.claude/workflows/go-feature-development.md`.
2. In `templates/commands/claude/wf-go-feat.md`, change the description to `Run the unified Go business-change workflow`; keep its repository-relative source path unchanged.
3. In `templates/rules/go-00-routing.md`, add a short hard-routing section:

```markdown
## Go 业务变更

- 新功能、既有行为修改和跨文件业务调整统一使用 `$wf-go-feat`。
- 任务拆分默认由主线程串行执行；仅当用户明确要求并行、分工或 delegation 且写入范围不重叠时，才使用 subagent。
- 修改前必须先加载适用 Rules、知识库 routing 和真实代码证据，并生成待审核目标契约。
```

4. In the frontmatter `description` of `templates/skills/go-coding-rules.md`, remove `/$wf-go-mod` from the activated workflow list.

- [ ] **Step 6: Run template validation**

Run:

```powershell
python scripts\verify_template_catalog.py
```

Expected: still FAIL only because obsolete `go-modify-existing` files are present.

- [ ] **Step 7: Commit the new canonical feature workflow**

```powershell
git add scripts/verify_template_catalog.py templates/workflows/go-feature-development.md templates/workflows/go-feature-development.graph.json templates/skills/codex/wf-go-feat/SKILL.md templates/commands/claude/wf-go-feat.md templates/rules/go-00-routing.md templates/skills/go-coding-rules.md
git commit -m "feat: unify Go feature workflow lifecycle"
```

## Task 3: Delete Obsolete Files and Prevent Import/Sync Scripts From Recreating Them

**Files:**
- Delete: `templates/workflows/go-modify-existing.md`
- Delete: `templates/workflows/go-modify-existing.graph.json`
- Delete: `templates/skills/codex/wf-go-mod/SKILL.md`
- Delete: `templates/skills/codex/wf-go-mod/agents/openai.yaml`
- Delete: `templates/commands/claude/wf-go-mod.md`
- Modify: `scripts/import_btd_templates.ps1:64-107`
- Modify: `scripts/sync_btd_templates.ps1:245-258`

- [ ] **Step 1: Delete the obsolete templates**

Delete exactly these files:

```text
templates/workflows/go-modify-existing.md
templates/workflows/go-modify-existing.graph.json
templates/skills/codex/wf-go-mod/SKILL.md
templates/skills/codex/wf-go-mod/agents/openai.yaml
templates/commands/claude/wf-go-mod.md
```

Remove the now-empty `templates/skills/codex/wf-go-mod/agents` and `templates/skills/codex/wf-go-mod` directories.

- [ ] **Step 2: Remove the importer’s obsolete workflow entry**

In `$workflowFiles` in `scripts/import_btd_templates.ps1`:

1. Delete the entire `"go-modify-existing.md" = @" ... "@` here-string entry.
2. Replace the `"go-feature-development.md"` here-string with the same canonical lifecycle headings used by `templates/workflows/go-feature-development.md`: `强约束`, `工作流`, `待审核目标契约`, `Task Capsule`, `目标门`, `质量门`, and bounded-loop exit states.

The importer must generate the same workflow semantics as the checked-in template and must not mention `$wf-go-mod`.

- [ ] **Step 3: Remove the sync graph metadata entry**

In `Get-WorkflowSpec` in `scripts/sync_btd_templates.ps1`:

1. Delete:

```powershell
"go-modify-existing" = @{ Trigger = "$wf-go-mod"; Owner = "hephaestus"; Summary = "Modify an existing Go feature after impact analysis and scoped verification." }
```

2. Replace the `go-feature-development` value with:

```powershell
"go-feature-development" = @{ Trigger = "$wf-go-feat"; Owner = "sisyphus"; Summary = "Unified Go business-change workflow with evidence-driven planning, bounded task loops, goal gates, and quality gates." }
```

- [ ] **Step 4: Run the static verifier and ensure obsolete references are gone from active surfaces**

Run:

```powershell
python scripts\verify_template_catalog.py
rg -n "go-modify-existing|wf-go-mod|modify-existing" templates internal scripts
```

Expected:

```text
OK: template catalog verified
```

The `rg` command must return no matches. Do not search `docs/superpowers` because historical plans and the approved design retain explanatory references.

- [ ] **Step 5: Commit template deletion and generator alignment**

```powershell
git add -A templates scripts/import_btd_templates.ps1 scripts/sync_btd_templates.ps1
git commit -m "refactor: remove Go modification workflow templates"
```

## Task 4: Run Full Regression and Record the Template Contract

**Files:**
- Modify: `README.md` only if it explicitly lists `$wf-go-mod` after the active-reference scan.
- Modify: `docs/superpowers/specs/2026-07-15-go-feature-workflow-unification-design.md` only if implementation reveals a design contradiction; otherwise leave it unchanged.

- [ ] **Step 1: Verify the focused Go suites**

Run:

```powershell
go test ./internal/catalog ./internal/httpapi -count=1
```

Expected: PASS.

- [ ] **Step 2: Run the repository verification suite**

Run:

```powershell
.\scripts\verify_all.ps1
```

Expected: all template verification, Go tests, and configured frontend verification steps PASS.

- [ ] **Step 3: Inspect the final diff for contract compliance**

Run:

```powershell
git diff HEAD~3..HEAD --check
git diff HEAD~3..HEAD -- templates internal scripts
rg -n "go-modify-existing|wf-go-mod|modify-existing" templates internal scripts
```

Expected: no whitespace errors; no active obsolete references; the feature workflow contains the approved hard constraints, target contract, task loop, gates, and retry limits.

- [ ] **Step 4: Commit any verification-only correction**

Only if Step 1–3 require a correction:

```powershell
git add README.md internal/catalog/catalog.go internal/httpapi/server_test.go scripts templates
git commit -m "test: verify unified Go feature workflow"
```

If no correction is needed, do not create an empty commit.
