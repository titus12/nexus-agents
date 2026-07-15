# Unity 逻辑修改

Source: `.claude/rules/unity-00-routing.md`  
Stack: Unity / C# / existing gameplay-client logic

## Start Gate

Before Knowledge Retrieval, CodeGraph, search, planning, or editing, initialize the local Nexus TaskRun payload. Use the cross-platform Node helper by default so the same workflow works on Windows and macOS:

```text
node .agents/skills/nexus-taskrun-submit/taskrun.mjs start --projectId btd-client --workflowType logic-modification --taskTitle "<task title>" --payloadFile .nexus/task-run-logic-modification.json --contextFile .nexus/workflow-context-logic-modification.json
```

Continue only after confirming `.nexus/task-run-logic-modification.json` exists and contains `sessionId` and `startedAt`. If start fails, stop the workflow and report the exact error; do not continue investigation or implementation.

## Knowledge Loading

Follow `.claude/rules/knowledge-retrieval.md`. Record `knowledgeRetrieval` and include the returned `Loaded Knowledge` section in the final summary.

## 适用场景

Use for:

- Modifying existing C# logic behavior.
- Adjusting existing rules, state flow, calculations, validation, parsing, or process logic.
- Scoped behavior changes that do not involve UI presentation, Prefab authoring, Scene authoring, or visual layout.
- ViewModel state logic only when the change does not require UI presentation, prefab binding, or UIArchitect updates.

Do not use for:

- UI page, Prefab, View/ViewModel presentation, Presenter presentation, UIArchitect, UGUI, TMP, Resolver, or serialized UI binding changes; use `$wf-unity-ui-feature`, `$wf-unity-ui-quick`, or `$wf-unity-bugfix` as appropriate.
- Runtime failures, Console errors, regressions, missing references, broken prefab/scene references, or behavior that first requires root-cause investigation; use `$wf-unity-bugfix`.
- Battle AI / BonsaiBT / monster behavior-tree work unless the `behaviour-tree` skill is also loaded.

## 角色分工

1. `unity-logic-developer` - locates the impact area, summarizes current/target behavior, and implements the smallest safe behavior change.
2. `unity-logic-tester` - verifies new behavior, key old behavior, Unity compile, Console, and targeted tests/manual path.
3. `unity-logic-reviewer` - reviews scope, compatibility, temporary residue, boundary conditions, performance risk, and verification evidence.

## 必需规则

- `.claude/rules/01-communication.md`
- `.claude/rules/knowledge-retrieval.md`
- `.claude/rules/unity-00-routing.md`
- `.claude/rules/unity-01-project-model.md`

## 阶段进度输出

输出简洁的阶段进度：

```text
[阶段: unity-logic-developer] ...
[阶段: unity-logic-tester] ...
[阶段: unity-logic-reviewer] ...
```

## 计划门禁

在编辑前，先输出简短计划并等待用户明确批准；如果用户在同一轮已经明确要求执行该计划，则可以直接继续。

计划必须简短包含：

- 当前代码事实 / 需求事实；
- 当前行为；
- 目标行为；
- 最小修改方案；
- 预计修改点；
- 验证方式；
- 何时升级范围或切换到其他工作流。

如果需求本质是 bug、运行时异常、Console 错误、Prefab/Scene 引用问题、UI 绑定问题，计划中必须说明切换到 `$wf-unity-bugfix` 或相应 UI workflow，而不是继续用 logic-mod 猜测修复。

## 代码事实与范围规则

如果可用，编辑前先使用只读 CodeGraph / search / references 确认代码事实；只读查询不需要再向用户请求许可。

必须在编辑前明确：

- 入口调用路径；
- 主要调用方 / 影响面；
- 当前行为与目标行为差异；
- 是否存在配置、热更数据、生成代码、Prefab/Scene 序列化引用参与；
- 是否有现成测试或可执行的手动验证路径。

不要因为“看起来可以顺手改”就扩大范围。未确认影响面前，只允许做只读调查；不得进行猜测式业务改动。

## 必需技能

- `.agents/skills/wf-unity-logic-mod/SKILL.md`
- `.claude/skills/unity-logic-developer/SKILL.md`
- `.claude/skills/unity-testing/SKILL.md`
- `.claude/skills/unity-logic-review/SKILL.md`

## 可复用项目技能

- `.claude/skills/unity-mcp-skill/` - Unity Editor automation, Console, compile, tests.
- `.claude/skills/unity-asset-safety/` - use if diff unexpectedly touches Unity assets, Prefabs, Scenes, ProjectSettings, or `.meta` files.
- `.codex/skills/behaviour-tree/` - use only for Battle AI, BonsaiBT, monster, boss, or behavior tree logic.
- `.claude/skills/vm-logic/` - use only for ViewModel state logic that does not require UI presentation, prefab binding, or UIArchitect changes.

## 工作流

1. Confirm the request is a scoped logic modification. If it is actually a bug investigation, UI/prefab/scene issue, or missing-reference problem, switch workflows before editing.
2. Locate the impact area with CodeGraph, search, references, nearby tests, and module docs.
3. Summarize current behavior and target behavior before editing.
4. Pass the Plan Gate:
   - present the concise plan;
   - wait for approval unless the same user turn already explicitly asked to execute;
   - record the verification path and escalation/switching conditions.
5. Load Unity/C# logic coding guidance from `unity-logic-developer` when needed.
6. Implement the smallest scoped behavior change.
7. Add or update targeted tests when practical; otherwise record a concrete manual verification path.
8. Unity Logic Tester verifies:
   - Unity compile and Console errors when available.
   - Targeted EditMode / PlayMode tests when applicable.
   - New target behavior.
   - Key old behavior that could regress.
   - If the change unexpectedly touches assets, Prefabs, Scenes, ProjectSettings, `.meta`, or serialized references, verify those references and invoke asset-safety review.
9. Unity Logic Reviewer checks:
   - Diff stays within target logic.
   - No accidental UI, Prefab, Scene, generated file, ProjectSettings, `.meta`, or asset changes.
   - Compatibility with existing callers and data/config assumptions.
   - Null, boundary, exception, cancellation, timeout, async, destroyed-object, and lifecycle risks.
   - Temporary data, Debug logs, mock/fake/test data, local paths, debug bypasses, and temporary switches.
   - Hot-path performance and allocation risks.
   - Verification evidence is sufficient for the requested behavior change.
10. Final report includes current behavior, target behavior, changed files, verification evidence, skipped checks with reasons, and remaining risks.

## Progress Output Discipline

Do not prefix every routine message with role/stage labels. Emit at most one concise checkpoint per actual role or stage transition when it helps the user understand progress. Skip checkpoints for ordinary tool calls, small corrections, repeated verification attempts, and routine main-agent messages.

Use this compact checkpoint shape only when a real transition occurs:

```text
[checkpoint]
role: <role>
goal: <what this role must decide or produce>
```


## Token and Context Hygiene

Keep workflow context small and evidence-focused:

- Prefer precise CodeGraph/file/symbol reads over broad exploration; ask for only the symbols or files needed for the next decision.
- Do not paste full source files, full diffs, large JSON/YAML assets, or repeated console stacks into the conversation unless the exact content is needed for diagnosis or review.
- Prefer `git diff --stat`, `git diff --check`, targeted hunks, and file lists before full diffs.
- For Unity Console checks, start with a small count and no stacktrace; request stacktrace only for errors related to the touched area.
- For subagents, pass the smallest task context package; do not fork or paste the whole conversation unless the subagent truly needs it.
- Use ASCII task titles for Nexus TaskRun payloads when invoking shell scripts to avoid encoding-related JSON corruption and retries.


## Nexus TaskRun Protocol

This workflow uses a **local start stage + submit-only HTTP** protocol.

Start stage rules:

1. Do not call `POST /api/workflow-runs/start`.
2. Auto-detect the current Codex session from `CODEX_THREAD_ID` as `sessionId`; ask the user for `/status` only if auto-detection is unavailable.
3. Record current UTC time as `startedAt`.
4. Initialize a local TaskRun payload/context with `projectId`, `workflowType`, `taskTitle`, `sessionId`, and `startedAt`.
5. Use `start-workflow-run.ps1` only as this local initializer; despite the historical name, it must not send HTTP.

Submit stage rules:

1. Complete the same payload with `endedAt`, `submittedStatus`, `context`, `metrics`, and `evidence`.
2. Submit exactly once to `POST http://127.0.0.1:8766/api/task-runs`.
3. The submit body must include `sessionId`, `startedAt`, and `endedAt`; do not rely on a `Session-Id` header.
4. The backend aggregates token / route metrics by `sessionId + startedAt/endedAt` and injects them into TaskRun metrics.
5. Do not include `workflowId`, `workflowRunId`, `workflowTemplateId`, `workflowCopyId`, or `X-Nexus-Workflow-Run-Id`.

## Task Run Evidence 协议

At the end of this workflow, submit a Task Run Evidence payload to Nexus instead of scoring the task inline. If the local API is unavailable, include the same JSON payload in the final response so the user can submit it later.

Preferred automation path via `$nexus-taskrun-submit`:

1. Start stage creates or updates `.nexus/task-run-logic-modification.json` with `sessionId` and `startedAt`.
2. End stage completes the same payload with `endedAt`, `submittedStatus`, `context`, `metrics`, and `evidence`.
3. Submit with:

```text
powershell -ExecutionPolicy Bypass -File .agents\skills\nexus-taskrun-submit\submit-workflow-result.ps1 -PayloadFile .nexus\task-run-logic-modification.json
```

The payload must include `projectId`, `workflowType`, `taskTitle`, `submittedStatus`, `sessionId`, `startedAt`, `endedAt`, `context`, `metrics`, and `evidence`. Do not use `go run`, local store fallback, workflow IDs, or `Session-Id` header routing from this business repository.

## Compact Nexus Evidence Rules

Submit concise TaskRun evidence:

- Keep `summary` to one short sentence.
- Keep `changedFiles` as project-relative paths.
- Use short command labels in `verification`; do not embed long command output, full diffs, source snippets, stack traces, or repeated logs.
- Use short strings for `skippedChecks` and `remainingRisks`; include only information that changes review or follow-up decisions.
- Record local payload/snapshot paths when required, but do not duplicate the full JSON in the final user response after a successful submit.


## KnowledgeBase Recommendation Gate

At workflow end, run the KnowledgeBase Recommendation Checklist. Report recommendation only; do not update KB automatically. Use one final line:

```text
KB Recommendation: none — <reason>.
KB Recommendation: consider <target> — <reason>.
```
