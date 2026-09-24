# Zhongshu Solver Quality Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align the Zhongshu Solver Skill, Multica runtime prompt, response contract, and deterministic validation so Solver remains a high-quality task-graph owner without taking on Menxia implementation design.

**Architecture:** Keep Analyst responsible for evidence and candidate tasks, make Solver responsible for producing one complete and auditable requirement-to-task graph, and keep Critic responsible for independent challenge. Put the invariant runtime rules in a small Python contract module, build only bounded task-graph context in `states.py`, and let the validator enforce identity, coverage, dependency, grouping, and implementation-boundary invariants.

**Tech Stack:** Python 3.8+, `unittest`, JSON prompt bundles, PowerShell/`rtk`, Markdown Skill documentation.

---

## Scope map

| File | Responsibility |
| --- | --- |
| `cmd/orchestrator/zhongshu_solver_contract.py` | Authoritative short runtime rules and output-field allowlist shared by prompt construction and tests. |
| `cmd/orchestrator/states.py` | Build bounded Solver input, preserve targeted Critic revision context, and validate the formal task graph. |
| `cmd/orchestrator/adapters.py` | Advertise the exact Zhongshu Solver response contract to Multica/Agent transport. |
| `docs/multi/zhongshu-solver-skill.md` | Human/Agent-facing Skill specification; remove stale architecture-option responsibilities. |
| `docs/multi/phase-skill-bundle-matrix.md` | Keep the phase matrix consistent with the new `groups[*].items` contract and Solver boundary. |
| `cmd/test_solver_prompt_v31.py` | Prompt and transport regression coverage. |
| `cmd/test_solver_contract_fix.py` | Strict validation and bounded-repair regression coverage. |
| `cmd/test_zhongshu_task_graph.py` | Existing Analyst/Solver graph integration coverage. |

`multica/start_review_orchestrator.ps1` remains an entrypoint only; it does not contain a second Solver prompt. Validation will inspect the prompt bundle produced through this entrypoint path so the Multica runtime cannot silently diverge.

### Task 1: Create the authoritative runtime Solver contract

**Files:**
- Create: `cmd/orchestrator/zhongshu_solver_contract.py`
- Test: `cmd/test_solver_prompt_v31.py`

- [ ] **Step 1: Write the failing contract tests**

Add tests that import the new constants/helpers and assert:

```python
from orchestrator.zhongshu_solver_contract import (
    ZHONGSHU_SOLVER_FORBIDDEN_FIELDS,
    ZHONGSHU_SOLVER_REQUIRED_ITEM_FIELDS,
    zhongshu_solver_runtime_rules,
)

def test_runtime_contract_has_quality_fields_and_forbids_implementation_design(self):
    rules = "\n".join(zhongshu_solver_runtime_rules())
    self.assertIn("coverage", rules)
    self.assertIn("dependency", rules)
    self.assertIn("acceptance_signals", rules)
    self.assertIn("Do not emit implementation_proposal", rules)
    self.assertEqual(
        ZHONGSHU_SOLVER_REQUIRED_ITEM_FIELDS,
        ("item_id", "title", "objective", "source_requirement_ids",
         "dependencies", "acceptance_signals", "unknowns", "risks",
         "parallelizable"),
    )
    self.assertIn("implementation_proposal", ZHONGSHU_SOLVER_FORBIDDEN_FIELDS)
    self.assertIn("file_changes", ZHONGSHU_SOLVER_FORBIDDEN_FIELDS)
```

Run: `rtk powershell -NoProfile -Command "python -m unittest cmd/test_solver_prompt_v31.py -v"`

Expected: FAIL because the contract module does not exist.

- [ ] **Step 2: Implement the minimal contract module**

Create immutable tuples and a function with no I/O or environment dependence:

```python
from __future__ import annotations

ZHONGSHU_SOLVER_REQUIRED_ITEM_FIELDS = (
    "item_id", "title", "objective", "source_requirement_ids",
    "dependencies", "acceptance_signals", "unknowns", "risks",
    "parallelizable",
)

ZHONGSHU_SOLVER_FORBIDDEN_FIELDS = frozenset({
    "implementation_proposal", "file_changes", "code_changes",
    "function_changes", "implementation_steps", "interfaces",
    "data_flow", "control_flow", "migration", "rollback",
    "affected_modules",
})

def zhongshu_solver_runtime_rules() -> tuple[str, ...]:
    return (
        "Solver owns task-graph quality: coverage, task boundaries, dependencies, grouping, parallelism, acceptance, scope, unknowns, and risks.",
        "Preserve every Analyst requirement_id and statement exactly; do not invent, rename, delete, or silently reinterpret requirements.",
        "Every task must be independently actionable, traceable to known requirements, dependency-valid, and observable through acceptance_signals.",
        "Use only Analyst-cited evidence and the current graph for targeted revalidation; do not perform a repository-wide investigation.",
        "If a graph decision cannot be supported by the supplied evidence, return NEEDS_MORE_EVIDENCE or HUMAN_GATE instead of guessing.",
        "Return one formal task graph for Critic; do not emit implementation_proposal, file_changes, code_changes, interfaces, data_flow, migration, rollback, or function-level design.",
        "Do not modify files, claim implementation, claim tests passed, or return a final approval decision.",
    )
```

- [ ] **Step 3: Run the focused tests**

Run: `rtk powershell -NoProfile -Command "python -m unittest cmd/test_solver_prompt_v31.py -v"`

Expected: PASS for the new contract assertions; existing prompt assertions may still fail until Task 2.

- [ ] **Step 4: Commit the isolated contract module**

Run:

```text
rtk powershell -NoProfile -Command "git add -- cmd/orchestrator/zhongshu_solver_contract.py cmd/test_solver_prompt_v31.py; git commit -m 'refactor: centralize Zhongshu Solver contract'"
```

### Task 2: Replace the stale Zhongshu Solver Skill and phase matrix contract

**Files:**
- Modify: `docs/multi/zhongshu-solver-skill.md`
- Modify: `docs/multi/phase-skill-bundle-matrix.md`

- [ ] **Step 1: Write the Skill contract checks**

Extend the prompt/document regression test with a UTF-8 read of the Skill source:

```python
from pathlib import Path

def test_skill_document_has_same_boundary(self):
    skill = Path(__file__).parents[1] / "docs/multi/zhongshu-solver-skill.md"
    text = skill.read_text(encoding="utf-8")
    self.assertIn("任务图质量负责人", text)
    self.assertIn("不负责实现方案", text)
    self.assertIn("NEEDS_MORE_EVIDENCE", text)
    self.assertIn("source_requirement_ids", text)
    self.assertNotIn("OPTION_DESIGN", text)
    self.assertNotIn("OPTION_COMPARISON", text)
    self.assertNotIn("recommendation", text)
```

Run: `rtk powershell -NoProfile -Command "python -m unittest cmd/test_solver_prompt_v31.py -v"`

Expected: FAIL against the current stale document.

- [ ] **Step 2: Rewrite the Skill as the concise authoritative role contract**

Retain the document’s useful protocol framing, but replace the old ten-step architecture-option workflow with these sections and exact semantics:

```markdown
## Solver 定位

Solver 是“需求到任务图的质量负责人”。Analyst 交付证据、需求和候选任务；Solver 将其校验、补全、组织为唯一正式 Task Graph；Critic 独立挑战该图；Menxia 才负责任务到实现方案。

## Solver 必须保证

1. 每条 Analyst requirement 都被至少一个任务覆盖，且保留原始 requirement_id 与 statement。
2. 每个任务有唯一 item_id、清晰 objective、来源需求、已知依赖和可观察 acceptance_signals。
3. 依赖只引用已知任务、无环，并与分组、执行顺序和 parallelizable 一致。
4. 每个任务恰好进入一个正式 group；group 不能引入新的任务语义。
5. scope、protected_paths、unknowns、risks 和证据边界不丢失、不静默扩大。

## Solver 可以调查的范围

仅在证据会改变任务拆分、依赖、范围或验收标准时，复核 Analyst 已引用的代码、文档或日志；禁止全仓扫描。无法确认时返回 NEEDS_MORE_EVIDENCE 或 HUMAN_GATE。

## Solver 明确不负责

不设计接口、模块、数据流、控制流、迁移、回滚、文件修改、代码修改或实现步骤；不输出 options、recommendation、implementation_proposal；不替 Critic 批准。

## 输出

成功只返回 `action=READY_FOR_CRITIC` 与一个完整 `plan`。修订只携带相关 Critic findings 和受影响任务；结构问题只做局部修复，不重启全量调查。
```

Delete or rewrite all older examples that require `options`, `comparison`, `recommendation`, or implementation details. Keep examples aligned to the runtime schema: `plan.requirements`, `plan.items`, `plan.groups[*].items`, `plan.dependencies`, `plan.scope`, `plan.unknowns`, and `plan.risks`.

- [ ] **Step 3: Correct the phase matrix**

Change the Zhongshu Solver row to state that the primary output is one formal Task Graph with complete `plan.items` and group membership represented by `groups[*].items`; document that Menxia owns implementation proposals. Remove the stale `groups[*].item_ids`-only statement.

- [ ] **Step 4: Run documentation checks**

Run: `rtk powershell -NoProfile -Command "python -m unittest cmd/test_solver_prompt_v31.py -v"`

Expected: Skill boundary assertions PASS.

- [ ] **Step 5: Commit the Skill/documentation update**

Run:

```text
rtk powershell -NoProfile -Command "git add -- docs/multi/zhongshu-solver-skill.md docs/multi/phase-skill-bundle-matrix.md cmd/test_solver_prompt_v31.py; git commit -m 'docs: narrow Zhongshu Solver Skill boundary'"
```

### Task 3: Rebuild the Multica Solver prompt around a bounded task graph

**Files:**
- Modify: `cmd/orchestrator/states.py:1734-2070`
- Modify: `cmd/orchestrator/adapters.py:1260-1410`
- Modify: `cmd/test_solver_prompt_v31.py`
- Modify: `cmd/test_solver_revision_prompt.py`

- [ ] **Step 1: Add failing prompt-shape assertions**

Update the tests so a normal Solver request asserts:

```python
payload = json.loads(ZhongshuSolverState().request(ctx).prompt)
self.assertEqual(payload["mode"], "TASK_GRAPH_FORMALIZATION_READ_ONLY")
self.assertEqual(
    set(payload["upstream"]),
    {"task_graph", "critic_findings", "repair_scope"},
)
self.assertNotIn("previous_plan", payload["upstream"])
self.assertNotIn("human_decision", payload["upstream"])
self.assertNotIn("options", json.dumps(payload, ensure_ascii=False))
self.assertNotIn("recommendation", json.dumps(payload, ensure_ascii=False))
self.assertIn("NEEDS_MORE_EVIDENCE", json.dumps(payload, ensure_ascii=False))
self.assertIn("acceptance_signals", json.dumps(payload, ensure_ascii=False))
```

For a Critic revision, assert that only the affected task IDs and finding fields are present, and that the full historical plan is not copied into the revision context.

Run: `rtk powershell -NoProfile -Command "python -m unittest cmd/test_solver_prompt_v31.py cmd/test_solver_revision_prompt.py -v"`

Expected: FAIL because the current builder still creates legacy fields before overwriting them and includes `previous_plan`/`human_decision` in normal upstream context.

- [ ] **Step 2: Make Analyst context task-graph-only**

Replace `_compact_analyst_plan_for_solver` with a function that selects only the canonical graph fields and preserves their values without semantic truncation:

```python
def _solver_task_graph_context(plan: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "requirements", "candidate_items", "candidate_groups", "dependencies",
        "scope", "protected_paths", "constraints", "confirmed_facts",
        "unknowns", "risks", "conflicts", "questions_for_solver",
    )
    return {key: copy.deepcopy(plan[key]) for key in keys if key in plan}
```

If the context exceeds the existing prompt budget, compact only evidence prose and retain all IDs, task fields, dependencies, scope, unknowns, risks, and conflicts. Never drop a requirement or item identity merely to meet the budget.

- [ ] **Step 3: Build one clean Solver prompt payload**

Replace the current “initialize legacy payload then overwrite keys” pattern with a single payload using this shape:

```python
value = {
    "task": ctx.raw_request,
    "role": "ZHONGSHU_SOLVER",
    "mode": "TASK_GRAPH_FORMALIZATION_READ_ONLY",
    "role_objective": "Validate and formalize one complete requirement-to-task graph for Critic; do not design implementation solutions.",
    "task_context": {
        "task_id": ctx.task_id,
        "phase": "ZHONGSHU",
        "scope": task_graph.get("scope", {}),
        "protected_paths": task_graph.get("protected_paths", []),
    },
    "upstream": {
        "task_graph": task_graph,
        "critic_findings": targeted_findings,
        "repair_scope": repair_scope,
    },
    "skill_rules": list(zhongshu_solver_runtime_rules()),
    "required_output": {
        "action": ["READY_FOR_CRITIC", "NEEDS_MORE_EVIDENCE", "HUMAN_GATE", "BLOCKED"],
        "ready_plan": [
            "requirements", "items", "groups", "dependencies", "scope",
            "unknowns", "risks",
        ],
    },
}
```

Use `NEEDS_MORE_EVIDENCE` in the prompt contract only if the state machine already accepts/routes it; otherwise keep the existing allowed action set and use the existing Analyst-evidence route. Do not introduce a new transition without a dedicated state-machine test.

- [ ] **Step 4: Align the Multica response contract**

In `_response_contract_for`, the Zhongshu Solver branch must expose only task-graph output fields:

```python
optional.extend([
    "plan", "dependencies", "scope", "unknowns", "risks",
    "finding_resolutions", "next_actions",
])
allowed_actions = ["READY_FOR_CRITIC", "HUMAN_GATE", "BLOCKED"]
instruction = (
    "You are the Zhongshu task-graph Solver. Preserve Analyst requirements and "
    "produce one auditable formal task graph. Do not emit implementation design, "
    "options, comparison, or recommendation fields."
)
```

Do not add Menxia fields such as `implementation_proposal`, `files`, `changes`, `tests`, or `rollback` to this branch.

- [ ] **Step 5: Run focused prompt and dispatch tests**

Run: `rtk powershell -NoProfile -Command "python -m unittest cmd/test_solver_prompt_v31.py cmd/test_solver_revision_prompt.py cmd/test_prompt_bundle_dispatch.py -v"`

Expected: PASS, including the assertion that the prompt bundle’s `prompt.txt` is the exact bounded Solver prompt.

- [ ] **Step 6: Commit the prompt/transport update**

Run:

```text
rtk powershell -NoProfile -Command "git add -- cmd/orchestrator/states.py cmd/orchestrator/adapters.py cmd/test_solver_prompt_v31.py cmd/test_solver_revision_prompt.py cmd/test_prompt_bundle_dispatch.py; git commit -m 'refactor: bound Zhongshu Solver prompt'"
```

### Task 4: Enforce quality invariants without masking semantic omissions

**Files:**
- Modify: `cmd/orchestrator/states.py:1547-1715`
- Modify: `cmd/test_solver_contract_fix.py`
- Modify: `cmd/test_solver_prompt_v31.py`

- [ ] **Step 1: Add failing validator tests**

Add cases for exact requirement preservation, unknown source requirements, unknown dependencies, dependency cycles, incomplete task fields, group/object divergence, and forbidden implementation fields:

```python
def test_solver_rejects_requirement_statement_rewrite(self):
    payload = self._valid_solver_payload()
    payload["plan"]["requirements"][0]["statement"] = "changed"
    analyst = {"requirements": [{"requirement_id": "REQ-1", "statement": "original"}]}
    self.assertEqual(
        _validate_solver_plan(payload, analyst),
        "SOLVER_REQUIREMENT_CONTENT_MISMATCH:REQ-1",
    )

def test_solver_rejects_unknown_dependency(self):
    payload = self._valid_solver_payload()
    payload["plan"]["items"][0]["dependencies"] = ["missing-task"]
    self.assertEqual(
        _validate_state_payload("ZHONGSHU_SOLVER", payload),
        "SOLVER_DEPENDENCY_UNKNOWN:missing-task",
    )

def test_solver_rejects_group_copy_that_changes_task(self):
    payload = self._valid_solver_payload()
    payload["plan"]["groups"][0]["items"][0]["objective"] = "different"
    self.assertEqual(
        _validate_state_payload("ZHONGSHU_SOLVER", payload),
        "SOLVER_GROUP_ITEM_MISMATCH:item-1",
    )
```

Run: `rtk powershell -NoProfile -Command "python -m unittest cmd/test_solver_contract_fix.py cmd/test_solver_prompt_v31.py -v"`

Expected: FAIL until strict checks are added.

- [ ] **Step 2: Implement strict plan validation**

Extend `_validate_solver_plan` in this order:

1. Require `action=READY_FOR_CRITIC` and a dict `plan`.
2. Recursively reject every field in `ZHONGSHU_SOLVER_FORBIDDEN_FIELDS`.
3. Require unique requirements and compare each canonical Analyst requirement’s `requirement_id`, `statement`, `priority`, and `scope` exactly; reject missing or rewritten baseline requirements.
4. Require each item’s fields from `ZHONGSHU_SOLVER_REQUIRED_ITEM_FIELDS`, unique IDs, known `source_requirement_ids`, and non-empty `acceptance_signals`.
5. Require every dependency to reference a known item and reject cycles with a deterministic depth-first traversal.
6. Require non-empty groups, unique group IDs, exactly one membership per item, and deep equality between each `groups[*].items` object and its corresponding `plan.items` object.
7. Return stable error codes naming the first offending ID/path.

The dependency check must be pure and bounded:

```python
def _first_dependency_error(items: list[dict[str, Any]]) -> str:
    item_ids = {str(item.get("item_id") or "") for item in items}
    dependencies = {
        str(item["item_id"]): [str(dep) for dep in item.get("dependencies", [])]
        for item in items
    }
    for item_id, refs in dependencies.items():
        for ref in refs:
            if ref not in item_ids:
                return f"SOLVER_DEPENDENCY_UNKNOWN:{ref}"
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(item_id: str) -> bool:
        if item_id in visiting:
            return False
        if item_id in visited:
            return True
        visiting.add(item_id)
        if not all(visit(dep) for dep in dependencies[item_id]):
            return False
        visiting.remove(item_id)
        visited.add(item_id)
        return True

    for item_id in sorted(item_ids):
        if not visit(item_id):
            return f"SOLVER_DEPENDENCY_CYCLE:{item_id}"
    return ""
```

- [ ] **Step 3: Narrow runtime normalization**

Keep `_normalize_solver_payload` only for lossless, deterministic recovery of a missing `plan.items` index when complete item objects are already present in `groups[*].items`. Remove conversion from `item_ids` or string-only group entries; those replies must be rejected so the Agent learns the current contract. Never synthesize requirements, tasks, dependencies, or acceptance signals from incomplete data.

- [ ] **Step 4: Run validator tests**

Run: `rtk powershell -NoProfile -Command "python -m unittest cmd/test_solver_contract_fix.py cmd/test_solver_prompt_v31.py cmd/test_zhongshu_task_graph.py -v"`

Expected: PASS with existing valid graph cases and all new rejection cases.

- [ ] **Step 5: Commit the validator update**

Run:

```text
rtk powershell -NoProfile -Command "git add -- cmd/orchestrator/states.py cmd/test_solver_contract_fix.py cmd/test_solver_prompt_v31.py cmd/test_zhongshu_task_graph.py; git commit -m 'fix: enforce Zhongshu Solver task graph quality'"
```

### Task 5: Verify the Multica path and hand off

**Files:**
- Modify only if a test exposes a real launcher/transport mismatch: `multica/start_review_orchestrator.ps1`
- Test: existing focused test files plus the full Python suite.

- [ ] **Step 1: Verify the prompt bundle through the Multica adapter**

Run:

```text
rtk powershell -NoProfile -Command "python -m unittest cmd/test_prompt_bundle.py cmd/test_prompt_bundle_dispatch.py cmd/test_solver_prompt_v31.py cmd/test_solver_revision_prompt.py -v"
```

Expected: PASS; the generated `prompt.txt` contains the new task-graph contract and contains none of `OPTION_DESIGN`, `OPTION_COMPARISON`, `recommendation`, or `implementation_proposal`.

- [ ] **Step 2: Run the Zhongshu regression set**

Run:

```text
rtk powershell -NoProfile -Command "python -m unittest cmd/test_zhongshu_task_graph.py cmd/test_zhongshu_parallel.py cmd/test_solver_contract_fix.py cmd/test_solver_prompt_v31.py cmd/test_solver_revision_prompt.py -v"
```

Expected: PASS; all three Analyst fan-out outputs still merge, Solver receives one canonical graph, and Critic-facing output contains every item exactly once.

- [ ] **Step 3: Run the full suite without stopping the active service**

Run the repository’s documented verification command on its separate validation port when a service is required:

```text
rtk powershell -NoProfile -Command "python -m unittest discover -s cmd -p 'test*.py' -v"
```

Expected: no new failures attributable to the Solver contract. Record any pre-existing unrelated failures separately; do not stop or rebind the active Nexus instance on port 8766.

- [ ] **Step 4: Inspect the final diff and Multica runtime artifacts**

Run:

```text
rtk powershell -NoProfile -Command "git diff --check HEAD~4..HEAD; rg -n 'TASK_GRAPH_FORMALIZATION_READ_ONLY|implementation_proposal|OPTION_DESIGN|OPTION_COMPARISON|recommendation' cmd/orchestrator docs/multi multica --glob '!multica/orchestrator-*.log'"
```

Expected: implementation-boundary strings appear only in forbidden-field checks, negative tests, or explicit documentation of what is forbidden; active Zhongshu Solver prompt construction does not require or emit architecture options or implementation proposals.

- [ ] **Step 5: Commit any final test-only adjustment**

Only if Step 3 or Step 4 finds a real regression, add the narrowly scoped test/launcher adjustment and commit it separately:

```text
rtk powershell -NoProfile -Command "git add -- <only-reviewed-files>; git commit -m 'test: verify Multica Solver contract'"
```

## Self-review checklist

- Spec coverage: Tasks 1–2 cover Skill/Prompt unification; Task 3 covers bounded context and Multica response contract; Task 4 covers requirements, dependencies, grouping, implementation boundary, and lossless repair; Task 5 covers runtime bundle and regression verification.
- No placeholder scan: every implementation step is specified with concrete files, commands, and expected outcomes.
- Type consistency: `plan.items` is the complete item-object index; `groups[*].items` contains deep-equal copies; all tests and validator errors use the same `item_id`, `requirement_id`, and dependency names.
- Scope safety: no global Codex configuration files are touched; no unrelated dirty-worktree files are staged; the existing Menxia implementation-proposal flow is unchanged.

Plan complete and saved to `docs/superpowers/plans/2026-09-03-zhongshu-solver-quality-boundary.md`.
