# TDD and Test Quality Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make test-first, non-redundant test design the default for every
code-changing task in generated Nexus project templates, with explicit
exceptions and review evidence.

**Architecture:** Put the normative policy in a new shared Claude rule and a
short mandatory baseline in the root project `AGENTS.md`. Update Go workflows,
their graphs, and their implementation/review roles to require concise
test-design evidence rather than coverage targets. Keep Go-specific mechanics
in the existing Go-testing skill and leave all language-neutral policy in the
shared rule.

**Tech Stack:** Markdown workflow/rule/skill/agent templates, JSON workflow
graphs, Go template-catalog tests, Go test tooling.

---

## File structure

- `templates/AGENTS.md`: Project-wide, language-neutral test-driven change
  baseline that applies even when no `wf-*` workflow is invoked.
- `templates/.claude/rules/test-driven-change.md`: New authoritative detailed
  policy for classification, minimum test design, test retention, exceptions,
  isolation, and review.
- `templates/.claude/skills/go-testing/SKILL.md`: Go-specific execution
  guidance aligned with the shared policy.
- `templates/.claude/workflows/go-feature-development.md`: Require a
  test-design decision and red-green evidence before behavior implementation.
- `templates/.claude/workflows/go-feature-development.graph.json`: Add a
  visible test-design/TDD stage before the task loop.
- `templates/.claude/workflows/go-bugfix.md`: Require a retained regression
  test or structured exception after reproducing a defect.
- `templates/.claude/workflows/go-bugfix.graph.json`: Make the TDD regression
  decision visible between reproduction and patching.
- `templates/.claude/workflows/go-code-review.md`: Add test-design evidence
  and a review gate for test quality.
- `templates/.claude/workflows/go-code-review.graph.json`: Make test-quality
  review an explicit logic-review responsibility.
- `templates/.claude/agents/go-worker.md`: Require workers to report the test
  decision and actual targeted validation.
- `templates/.claude/agents/go-reviewer-logic.md`: Add a test-quality review
  checklist and evidence format.
- `internal/catalog/template_initializer.go`: Include the new shared rule in
  initialization for all project types.
- `internal/catalog/template_initializer_test.go`: Add a red-green assertion
  that initialized Go projects contain the mandatory shared rule and baseline
  language.

### Task 1: Add the shared, all-language TDD policy

**Files:**
- Modify: `templates/AGENTS.md`
- Create: `templates/.claude/rules/test-driven-change.md`
- Modify: `internal/catalog/template_initializer.go`
- Modify: `internal/catalog/template_initializer_test.go`

- [x] **Step 1: Write the failing template-initializer test**

In `internal/catalog/template_initializer_test.go`, add a test named
`TestInitializeProjectTemplatesIncludesTestDrivenChangePolicy`. Use the
existing temporary project/template initialization helpers. Assert that an
initialized project contains `.claude/rules/test-driven-change.md`, that its
content contains `Test Design Decision`, and that `AGENTS.md` contains
`Test-Driven Change Baseline`.

```go
rule, err := os.ReadFile(filepath.Join(projectRoot, ".claude", "rules", "test-driven-change.md"))
if err != nil {
    t.Fatalf("read test-driven change rule: %v", err)
}
if !strings.Contains(string(rule), "## Test Design Decision") {
    t.Fatalf("expected test-design rule, got %q", string(rule))
}

agents, err := os.ReadFile(filepath.Join(projectRoot, "AGENTS.md"))
if err != nil {
    t.Fatalf("read AGENTS.md: %v", err)
}
if !strings.Contains(string(agents), "## Test-Driven Change Baseline") {
    t.Fatalf("expected TDD baseline in AGENTS.md, got %q", string(agents))
}
```

- [x] **Step 2: Run the focused test to verify it fails**

Run:

```powershell
rtk go test ./internal/catalog -run TestInitializeProjectTemplatesIncludesTestDrivenChangePolicy -count=1
```

Expected: FAIL because the shared rule and baseline do not yet exist.

- [x] **Step 3: Add the compact baseline to `AGENTS.md`**

Add a `## Test-Driven Change Baseline` section after `## Execution baseline`.
The section must state all of the following without copying the full rule:

```markdown
- For every code-changing task, read and follow
  `.claude/rules/test-driven-change.md` before implementation.
- For observable behavior changes and defect fixes, decide the minimum
  behavior-based test before editing production code; run it red first when
  deterministic automation is practical.
- Do not add a test merely for coverage. Retain only tests with unique
  regression or contract value; extend, parameterize, merge, or remove
  duplicates deliberately.
- A non-contract logging, comment, formatting, or behavior-preserving rename
  change normally needs no new test, but must record no behavior change and
  run proportionate existing validation.
- Any other exception must record reason, actual substitute validation, and a
  future test trigger when applicable.
```

- [x] **Step 4: Create the detailed shared rule**

Create `templates/.claude/rules/test-driven-change.md` in UTF-8 without BOM.
It must contain these exact top-level sections:

```markdown
# Test-Driven Change Rule

## Scope and precedence
## Change classification
## Test Design Decision
## Test-first execution
## Test portfolio management
## Production-code boundary
## Exceptions and non-contract logging
## Review gate
## Evidence format
```

Write the rule from the approved design at
`docs/superpowers/specs/2026-07-16-tdd-test-quality-gates-design.md`:

- Classify behavioral, defect, boundary/contract, non-behavioral, and
  exception changes.
- Use the four questions: behavior/risk, existing coverage, unique value, and
  lowest effective layer.
- Require red-green-refactor for deterministic behavioral changes.
- Retain unique regression/contract assets; parameterize same-rule cases;
  merge or remove exploratory, duplicate, and implementation-coupled tests.
- Prohibit test-only production exports, flags, and artificial abstractions.
- Define non-contract logging precisely and require a compact no-test record.
- Require exception reason, substitute validation, and later-test trigger.
- Give reviewers the nine questions from the approved design's review gate.

End with this compact reusable evidence template:

```text
Test decision: new | extend | no new test | exception.
Behavior and risk:
Existing coverage:
Unique protection:
Selected layer:
Red evidence / exception reason:
Actual validation:
Retain, merge, or delete decision:
Follow-up trigger:
```

- [x] **Step 5: Include the shared rule for every initialized project**

In `includeTemplateInitializationPath` in
`internal/catalog/template_initializer.go`, add the new shared rule to the
project-type-independent paths:

```go
strings.HasPrefix(relativePath, ".claude/rules/test-driven-change.md") ||
```

Place it next to the existing shared
`.claude/rules/01-communication.md` and
`.claude/rules/knowledge-retrieval.md` checks. Do not add it inside the Go-only
or Unity-only `switch`: the policy applies to every project type.

- [x] **Step 6: Run the focused test to verify it passes**

Run:

```powershell
rtk go test ./internal/catalog -run TestInitializeProjectTemplatesIncludesTestDrivenChangePolicy -count=1
```

Expected: PASS.

### Task 2: Align Go test guidance and implementation role

**Files:**
- Modify: `templates/.claude/skills/go-testing/SKILL.md`
- Modify: `templates/.claude/agents/go-worker.md`

- [x] **Step 1: Update the Go-testing skill**

Preserve the existing front matter. Replace the body with concise Go-specific
guidance that begins by requiring the shared rule:

```markdown
Read `.claude/rules/test-driven-change.md` first. It defines whether a test is
needed and how its long-term value is judged; this skill only defines Go
techniques and commands.
```

Then include:

- Table-driven subtests for one shared rule with several inputs.
- Separate named tests only for different business rules, failure causes, or
  diagnostic ownership.
- Fakes for clock, randomness, filesystem, network, and external services;
  avoid real network, sleeps, and unmanaged time.
- Assertions on exported behavior, returned errors, state, and boundary
  contracts instead of private helpers or incidental call order.
- The targeted-first command progression:

```text
go test ./path/to/package -run TestName -count=1
go test ./path/to/package -count=1
go test ./...
```

- A requirement to report only commands actually run and their outcomes.

- [x] **Step 2: Update the worker role**

In `templates/.claude/agents/go-worker.md`, replace the current build-only
completion sequence with:

```text
4. Before production edits, complete the Test decision evidence from
   `.claude/rules/test-driven-change.md`.
5. For an applicable deterministic behavior change, write/extend the focused
   test, run it red, implement the smallest fix, and run it green.
6. Run targeted validation first, then the package/build checks required by the
   capsule.
7. Report changed files, Test decision, commands actually run and their
   outcomes, retained/merged/deleted test decision, and remaining risks.
```

Keep the role's scope narrow: it must not independently invent a wider
workflow or weaken the rule's exception protocol.

- [x] **Step 3: Review the two files for duplicate policy**

Confirm that the Go skill contains mechanics only and the worker contains
execution/reporting responsibilities only. The full classification, exception,
and review policy must remain authoritative only in
`templates/.claude/rules/test-driven-change.md`.

### Task 3: Add the feature-development TDD gate and graph

**Files:**
- Modify: `templates/.claude/workflows/go-feature-development.md`
- Modify: `templates/.claude/workflows/go-feature-development.graph.json`

- [x] **Step 1: Add the feature workflow rule-loading requirement**

In the first workflow step, require loading
`.claude/rules/test-driven-change.md` with the other applicable rules. Before
the task-decomposition section, add `## Test Design Gate` with:

```markdown
Before creating an implementation Capsule or editing production code, classify
the change and fill the shared `Test decision` evidence. For a behavior change,
select the lowest effective test layer and create or extend the minimum
behavioral test. If deterministic automation is practical, run it and confirm
the expected red result before implementation. Reuse or extend an existing
test when it already covers the same risk; do not add coverage-only or
implementation-coupled tests.

Non-contract logging may use the compact no-test record. Any other exception
uses the complete shared exception protocol and may not enter implementation
until the substitute validation is explicit.
```

Add `Test decision` to the exploration context package and add these capsule
fields:

```text
Test decision evidence:
Focused red command and expected failure, or exception/substitute validation:
Targeted green and expanded validation commands:
Test asset action: retain / extend / parameterize / merge / delete:
```

- [x] **Step 2: Make the graph represent the gate**

In `go-feature-development.graph.json`, insert a node with:

```json
{
  "id": "test_design",
  "type": "transform",
  "category": "data",
  "label": "测试设计决策",
  "agent": "sisyphus",
  "detail": "分类变更、识别独特回归风险并选择最低有效测试层；适用时先获得定向测试的 red 证据。",
  "x": 1040,
  "y": 100
}
```

Reposition the existing `plan` and following nodes only as needed for readable
layout. Add edges from `confirmation` to `test_design`, from `test_design` to
`plan`, and preserve all original success/failure paths. Do not replace the
graph with a different schema.

- [x] **Step 3: Validate Markdown and graph JSON**

Run:

```powershell
Get-Content templates\.claude\workflows\go-feature-development.md | Select-String -Pattern '## Test Design Gate|Test decision evidence|Test asset action'
Get-Content -Raw templates\.claude\workflows\go-feature-development.graph.json | ConvertFrom-Json | Out-Null
```

Expected: the three Markdown markers are found and JSON parsing exits
successfully.

### Task 4: Strengthen bug-fix regression evidence and graph

**Files:**
- Modify: `templates/.claude/workflows/go-bugfix.md`
- Modify: `templates/.claude/workflows/go-bugfix.graph.json`

- [x] **Step 1: Add the bug-fix test-design gate**

Immediately after the existing reproduction requirement and before the minimal
patch phase, add:

```markdown
## Regression Test Gate

Record the shared `Test decision` evidence after reproducing the defect. A
reliable automated reproduction becomes the retained regression test by
default: run it red against the defective behavior, make the minimal root-cause
fix, then run it green. Do not keep a probe solely because it was useful for
diagnosis; retain it only when it protects a distinct regression risk.

If the defect cannot be automated now, use the full shared exception protocol:
state why, record the actual substitute reproduction/validation, and define
the trigger for a later automated regression test. A non-contract diagnostic
log change is not itself a regression test.
```

Extend the bug-fix loop/capsule output to report the red/green command or
exception evidence plus retain/merge/delete decision.

- [x] **Step 2: Add the graph decision node**

In `go-bugfix.graph.json`, add a condition node between `reproduce` and
`evidence_gate`:

```json
{
  "id": "regression_test_gate",
  "type": "condition",
  "category": "condition",
  "label": "回归测试或例外",
  "agent": "reproducer",
  "detail": "为可自动化缺陷获取 red 复现并作为长期回归测试；否则记录例外、替代验证和补测触发条件。",
  "x": 700,
  "y": 340
}
```

Route `reproduce` through `regression_test_gate` before the existing evidence
decision. Preserve all existing probe and patch paths.

- [x] **Step 3: Validate Markdown and graph JSON**

Run:

```powershell
Get-Content templates\.claude\workflows\go-bugfix.md | Select-String -Pattern '## Regression Test Gate|retain|exception'
Get-Content -Raw templates\.claude\workflows\go-bugfix.graph.json | ConvertFrom-Json | Out-Null
```

Expected: the workflow contains the gate and the graph parses successfully.

### Task 5: Make test quality a review gate and graph responsibility

**Files:**
- Modify: `templates/.claude/workflows/go-code-review.md`
- Modify: `templates/.claude/workflows/go-code-review.graph.json`
- Modify: `templates/.claude/agents/go-reviewer-logic.md`

- [x] **Step 1: Extend the review context package**

In `go-code-review.md`, add this block to the review context template:

```text
Test design evidence:
- Change classification:
- Behavior/risk and unique regression protection:
- Existing coverage reused, extended, merged, or deleted:
- Selected test layer and rationale:
- Red/green command evidence, or complete exception:
- Actual validation commands and outcomes:
```

In the workflow's quality-gate step, require the owner to classify missing
test-design evidence as `Insufficient evidence` and implementation-coupled,
redundant, or non-deterministic tests as review findings even when checks pass.

- [x] **Step 2: Add the reviewer checklist**

In `go-reviewer-logic.md`, add `**测试设计**` to the checklist with:

```markdown
**测试设计**: 变更分类是否正确 / 行为变化是否有定向测试或完整例外 /
是否验证外部行为而非私有实现 / 是否与已有测试重复 / 是否选择最低有效层级 /
mock、时间、网络和随机性是否可控 / 合理重构后测试是否仍应通过 /
删除测试后失去的独特保护是否被其他测试承接
```

Add to the output format:

```text
### 测试设计结论
- 证据: 充分 / 不足
- 冗余或脆弱测试: 无 / <finding>
- 例外: 无 / 接受 / 需补充
```

- [x] **Step 3: Update the graph**

Change the `logic` node detail in
`go-code-review.graph.json` to:

```text
Check control flow, state, transactions, nil handling, boundaries, and whether
test evidence is behavior-based, unique, deterministic, and proportionate.
```

If the graph has an explicit quality-gate node, add test-design evidence to its
detail. Do not add a fourth reviewer role: test quality is part of logic review
and the owner’s aggregation.

- [x] **Step 4: Validate Markdown and graph JSON**

Run:

```powershell
Get-Content templates\.claude\workflows\go-code-review.md | Select-String -Pattern 'Test design evidence|Insufficient evidence'
Get-Content templates\.claude\agents\go-reviewer-logic.md | Select-String -Pattern '测试设计|测试设计结论'
Get-Content -Raw templates\.claude\workflows\go-code-review.graph.json | ConvertFrom-Json | Out-Null
```

Expected: all markers are found and the graph parses successfully.

### Task 6: Verify template synchronization and reconcile with the design

**Files:**
- Review: `docs/superpowers/specs/2026-07-16-tdd-test-quality-gates-design.md`
- Review: `docs/superpowers/plans/2026-07-16-tdd-test-quality-gates.md`

- [x] **Step 1: Run all focused template tests**

Run:

```powershell
rtk go test ./internal/catalog -run 'TestInitializeProjectTemplatesIncludesTestDrivenChangePolicy|TestStoreSyncProjectTemplates' -count=1
```

Expected: PASS.

- [x] **Step 2: Run repository verification**

Run:

```powershell
rtk go test ./...
rtk npm run build  # run from the web/ directory
rtk git diff --check
rtk git status --short
```

Expected: Go tests and the web build pass; `git diff --check` reports no
whitespace errors; status lists only the intended specification, plan,
template, graph, role, and test files. Do not stage or commit without separate
explicit user authorization.

- [x] **Step 3: Perform plan-based acceptance**

Use the approved design and every task in this plan to verify:

- The all-language baseline is in `AGENTS.md` and the detailed policy has one
  shared source of truth.
- Non-contract logs are exempt while audit, monitoring, alerting, parsing, and
  behavior-changing logs are not.
- Feature and bug-fix workflows require test design before production
  implementation and expose the gate in both Markdown and graph JSON.
- Review context and logic review enforce unique, behavior-based,
  deterministic tests and documented exceptions.
- Go-specific material explains mechanics without duplicating or weakening the
  shared policy.
- Template initialization proves the policy is copied into generated projects;
  the existing generic synchronization regression test remains green for
  eligible template files.
- No tests, policy language, or template edits introduce coverage targets,
  test-only production APIs, or test-only runtime flags.

Report each acceptance criterion as `met`, `deviated`, or `unverified` with the
actual command/file evidence.
