# Unity Bug Investigation and Fix

Source: `.claude/rules/unity-00-routing.md`  
Stack: Unity / C# / UGUI / UIArchitect / assets

## Start Gate

Before Knowledge Retrieval, CodeGraph, search, planning, subagent dispatch, or editing, initialize the local Nexus TaskRun payload:

```text
node .agents/skills/nexus-taskrun-submit/taskrun.mjs start --projectId btd-client --workflowType bug-investigation --taskTitle "<task title>" --payloadFile .nexus/task-run-bug-investigation.json --contextFile .nexus/workflow-context-bug-investigation.json
```

Continue only after `.nexus/task-run-bug-investigation.json` exists and contains `sessionId` and `startedAt`. If start fails, stop and report the exact error.

## Knowledge Loading

Follow `.claude/rules/knowledge-retrieval.md`. Record `knowledgeRetrieval` and include the loaded knowledge summary in the final report.

## Scope

Use for Unity runtime, UI, asset, integration, Console, exception, regression, compile, and test failures.

## Role Policy

`$wf-unity-bugfix` authorizes the default support subagents for independent review and verification.

Default roles:

1. `unity-debugger` - gather evidence, reproduce when possible, identify or narrow root cause.
2. `unity-bugfix-developer` - main agent implements the smallest root-cause fix.
3. `unity-regression-tester` - required subagent after implementation; verifies compile, Console, tests, reproduction path, and relevant regressions.
4. `unity-bugfix-reviewer` - required subagent after implementation; reviews diff scope, root-cause alignment, asset safety, temporary residue, and risks.
5. `workflow-evaluator` - optional subagent for compact Nexus evidence and final report preparation.

The main agent must not be the only reviewer/tester for its own implementation unless subagents are unavailable. If degraded, state why and mark review/verification as degraded in the final report.

## Subagent Dispatch Mode

Use a minimal non-fork context package for every support subagent:

```text
context_mode: capsule_non_fork
fork_context: false
requestedModel: <optional model override>
requestedReasoningEffort: <optional reasoning override>
runtimeModelConfirmed: false
```

- Include only the failing path, confirmed evidence, allowed scope, verification, and risks needed by the assigned role.
- A requested model or reasoning override requires `fork_context: false`.
- Record requested settings without claiming the runtime selection: leave `runtimeModelConfirmed: false` unless independently confirmed.
- A full-history fork is an explicitly justified, read-only exception with no override; never use it for implementation, regression testing, review, or final evidence.

## Required Rules and Skills

- `.claude/rules/01-communication.md`
- `.claude/rules/knowledge-retrieval.md`
- `.claude/rules/unity-00-routing.md`
- `.claude/rules/unity-01-project-model.md`
- `.agents/skills/wf-unity-bugfix/SKILL.md`
- `.claude/skills/unity-debugger/SKILL.md`
- `.claude/skills/unity-bugfix-developer/SKILL.md`
- `.claude/skills/unity-testing/SKILL.md`
- `.claude/skills/unity-bugfix-review/SKILL.md`

Reusable project skills:

- `.claude/skills/unity-mcp-skill/` - Unity Editor automation, Console, compile, tests, screenshots.
- `.claude/skills/unity-ui-developer/` - use when the root cause is UI behavior or UIArchitect integration.
- `.claude/skills/unity-logic-developer/` - use for existing non-UI gameplay/client logic.
- `.claude/skills/unity-asset-safety/` - use when assets, Prefabs, Scenes, or `.meta` files are involved.
- `.codex/skills/behaviour-tree/` - use for Battle AI, BonsaiBT, monster, boss, or behavior tree issues.

## Plan Gate

Before editing, present a concise plan and wait for explicit approval unless the user already approved the same plan in the current turn.

The plan must include:

- current evidence and reproduction status;
- root-cause confidence: `unconfirmed`, `candidate`, or `confirmed`;
- smallest diagnosis or fix plan;
- expected changed files and forbidden files;
- validation and user-acceptance plan;
- when the workflow will loop, ask for logs, or stop.

If root cause is not confirmed, present a diagnosis-first plan, not a speculative fix plan.

## Evidence and Diagnosis Gate

Do not edit business behavior from a guess. Classify evidence first:

- `unconfirmed`: only symptom is known; gather facts or ask for reproduction evidence.
- `candidate`: one or more likely causes exist; add the smallest diagnostic needed to distinguish them.
- `confirmed`: logs, stack trace, deterministic resource/code fact, failed assertion, or user-provided evidence uniquely identifies the failure point; implement the minimal fix.

Allowed diagnosis methods:

- reproduce or ask the user to reproduce;
- read Console, stack traces, test failures, resource/prefab facts, and serialized references;
- use read-only CodeGraph/search for impact and call paths;
- add minimal diagnostic logs/probes only at key boundaries.

## Diagnostic Logging Policy

Actively request or add diagnostic logs when evidence is insufficient, but do it progressively and only at key decision points.

Rules:

- Add the fewest logs that can distinguish the current hypotheses.
- Prefer boundary logs: entry/exit, branch decision, null/missing reference, event send/receive, critical call executed/skipped.
- Each log must have a stated decision rule: what output confirms, rejects, or narrows the hypothesis.
- Avoid frame-by-frame spam unless sampling or a short reproduction window is necessary.
- Do not add logs to generated files.
- Prefer feature-scoped prefixes; use macro-gated logs for diagnostics expected to live beyond one short loop.
- Track temporary diagnostics in the conversation or payload: prefix/id, files, purpose, expected evidence, and cleanup policy.

Before final success, remove temporary diagnostics unless the user explicitly approves keeping them behind a macro.

## Bugfix Loop

This workflow is iterative. Do not end the workflow merely because one patch was made.

Loop states:

1. `investigating` - collect facts and reproduce if possible.
2. `diagnosing` - add/read minimal diagnostics and decide the next hypothesis.
3. `implementing` - apply the smallest confirmed root-cause fix.
4. `reviewing` - independent reviewer subagent checks scope, risks, and residue.
5. `verifying` - independent tester subagent checks compile, Console, tests, and reproduction path where possible.
6. `awaiting_user_evidence` - user must provide logs/screenshots/repro output for the next diagnosis step.
7. `awaiting_user_acceptance` - internal checks passed, but the original bug cannot be fully accepted automatically.

Loop transitions:

- If diagnostics confirm root cause, move to implementation.
- If diagnostics reject the hypothesis, update the hypothesis and continue diagnosis.
- If review fails, fix review findings and review again.
- If verification fails, return to evidence intake; do not stack unrelated fixes.
- If automated acceptance is unavailable, ask the user to accept the fix and keep the workflow open.
- If the user says the bug remains, collect the new evidence and continue the loop.

Terminal conditions:

- automated acceptance proves the original bug is fixed;
- user explicitly confirms acceptance;
- user explicitly asks to stop/end;
- the task is genuinely blocked on external input after repeated attempts.

## Acceptance Gate

Internal validation is not the same as bug acceptance.

Before submitting final `success`, obtain one of:

1. automated evidence that reproduces the original path and proves the bug is fixed;
2. explicit user acceptance;
3. explicit user instruction to end despite skipped acceptance.

If none is available, report `awaiting_user_acceptance`, provide exact reproduction steps, expected result, and evidence to send if still broken. Do not submit final success.

Acceptance request must include:

- what was changed;
- how to reproduce the original bug;
- expected fixed behavior;
- what logs/screenshots/video/Console output to provide if it still fails;
- a clear statement that the workflow remains open until acceptance or stop.

## Implementation Rules

- Implement the smallest confirmed root-cause fix.
- Do not refactor, retune, or broaden behavior unless required by the confirmed root cause.
- Do not hand-edit generated files.
- Do not delete or casually rewrite `.meta` files.
- For assets/prefabs/scenes, verify serialized references and component data, not only type existence.
- Keep user/other working-tree changes intact; never use broad reset/revert to clean scope.

## Review Gate

After implementation, spawn `unity-bugfix-reviewer` with the smallest context package:

- symptom and confirmed root cause;
- approved diagnosis/fix plan;
- changed-file list and diff summary;
- forbidden files and known user changes;
- diagnostic cleanup requirements.

Review must check:

- fix matches the confirmed root cause;
- diff is minimal and avoids unrelated refactors;
- generated files, `.meta`, prefabs/scenes/assets are handled safely;
- lifecycle/null/async/destroyed-object/boundary risks;
- temporary logs, mocks, local paths, debug bypasses, and stale diagnostics;
- responsibility boundaries and extension points are respected: generic workflow nodes should not accumulate domain-specific branches when an owning controller/service/model can express the policy;
- design issues in touched code are classified as blocking, follow-up, or acceptable minimal-fix tradeoff, with the better owner named when relevant;
- evidence is sufficient, or acceptance remains pending.

Blocking review findings return to implementation.

## Verification Gate

After implementation, spawn `unity-regression-tester` with the smallest context package:

- changed-file summary;
- original reproduction path or reason it cannot be automated;
- validation targets and available tests;
- expected user-visible fixed behavior;
- diagnostic prefixes that should or should not remain.

Tester verifies what is available:

- Unity compile and Console errors;
- targeted tests or reproduction scripts;
- asset/prefab serialized data when relevant;
- original bug path when automatable;
- related regression risks.

If the original bug cannot be automatically accepted, tester must return `needs_user_acceptance` with concrete user steps and expected evidence.

## Plan Compliance

使用本地 `.nexus/plan-compliance-bug-investigation.json` 及通用 Plan Compliance loop；不得写入 TaskRun payload。批准的 diagnosis/fix plan 为诊断、修复、验证和非目标分配 `planItemIds`。Reviewer 和 tester 对其负责项返回 `met | deviated | unverified | not_started` 与简短证据；Owner 只有在所有必需项为 `met`、偏离已获批准时才可报告 `success`。

## Unity Quality Gate

`unity-bugfix-reviewer`、`unity-regression-tester` 和可用的 `workflow-evaluator` 必须读取 `.agents/skills/wf-subagents/unity-quality-rubric.md` 并返回 `QualityResult`。Owner 将汇总写入本地 ledger 的 `quality.blockingFindings`、`quality.majorFindings`、`quality.skippedRequiredChecks`、`quality.requiredChecks`、`quality.passedChecks`、`quality.manualAcceptancePending` 和 `quality.unexpectedChanges`，再运行：

```text
node .agents/skills/wf-subagents/plan-loop.mjs gate --file .nexus/plan-compliance-bug-investigation.json --stage quality
```

任何 blocker 或 major 进入 `repair`；自动验证不足但人工验收可完成时进入 `awaiting_user_acceptance`；无新诊断证据或预算耗尽时为 `blocked`。

## Workflow Steps

1. Start TaskRun payload and load required knowledge.
2. Gather evidence and classify root-cause confidence.
3. Present a plan. If confidence is not `confirmed`, plan only diagnostics.
4. Run diagnosis loop until root cause is confirmed or external evidence is needed.
5. Implement the smallest root-cause fix.
6. Run independent review and verification subagents.
7. If review/verification fails, loop back to implementation or diagnosis.
8. If internal checks pass but original acceptance is unavailable, request user acceptance and keep workflow open.
9. On user acceptance or automated acceptance, remove/settle diagnostics, do final compact report, and submit TaskRun success.

## Progress Output Discipline

Do not prefix every routine message with role/stage labels. Emit at most one concise checkpoint per real role or state transition:

```text
[checkpoint]
role: <role/state>
goal: <what must be decided or produced>
```

Skip checkpoints for ordinary tool calls, repeated checks, and small corrections.

## Token and Context Hygiene

- Prefer precise CodeGraph/file/symbol reads over broad exploration.
- Do not paste full source files, large diffs, YAML assets, or repeated logs unless needed.
- Prefer `git diff --stat`, `git diff --check`, targeted hunks, and changed-file lists.
- For Console checks, start small and request stack traces only for relevant errors.
- Give subagents the smallest task context package; do not pass the full chat by default.
- Use ASCII task titles for Nexus payload scripts.

## Nexus TaskRun Protocol

This workflow uses local start + submit-only HTTP.

Start rules:

1. Do not call `POST /api/workflow-runs/start`.
2. Auto-detect `sessionId` from `CODEX_THREAD_ID`; ask the user for `/status` only if unavailable.
3. Record current UTC `startedAt`.
4. Initialize local payload/context with `projectId`, `workflowType`, `taskTitle`, `sessionId`, and `startedAt`.
5. Use start helpers only as local initializers; they must not send HTTP.

Submit rules:

1. Submit only at a terminal condition, not while awaiting user evidence/acceptance.
2. Complete payload with `endedAt`, `submittedStatus`, `context`, `metrics`, and `evidence`.
3. Submit exactly once to `POST http://127.0.0.1:8766/api/task-runs`.
4. Body must include `sessionId`, `startedAt`, and `endedAt`; do not rely on headers.
5. Do not include workflow IDs or `X-Nexus-Workflow-Run-Id`.

Preferred submit command:

```text
powershell -ExecutionPolicy Bypass -File .agents\skills\nexus-taskrun-submit\submit-workflow-result.ps1 -PayloadFile .nexus\task-run-bug-investigation.json
```

Compact evidence:

- short `summary`;
- project-relative `changedFiles`;
- short command labels in `verification`;
- concise `skippedChecks` and `remainingRisks`;
- include `awaiting_user_acceptance` or `awaiting_user_evidence` only when not terminal.

## KnowledgeBase Recommendation Gate

At workflow end, recommend only; do not update KB automatically. Use one final line:

```text
KB Recommendation: none - <reason>.
KB Recommendation: consider <target> - <reason>.
```
