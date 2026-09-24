# Zhongshu Critic fan-in result preservation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve all valid parallel Zhongshu Critic findings during fan-in and prevent an empty, unvalidated aggregate from approving the plan.

**Architecture:** Normalize each worker result at the `CriticConflictResolver` boundary. The normalizer accepts the in-memory Critic payload (`revision_id`, optional `reviewed_plan_hash`, `findings`) and the persisted stage wrapper (`payload`), then validates the normalized revision and any supplied hash before aggregation. If completed results exist but none are accepted, return a non-freeze decision with an explicit reason.

**Tech Stack:** Python 3, dataclasses, existing `CriticFinding`/`CriticAggregate` models, existing parallel runtime and JSON stage artifacts.

---

### Task 1: Add result normalization and fail-closed aggregation

**Files:**
- Modify: `D:\workspace\src\nexus-agents\cmd\orchestrator\zhongshu_parallel.py:411-435`
- Reference: `D:\workspace\src\nexus-agents\cmd\orchestrator\parallel_runtime.py:114-153,203-215`

- [ ] **Step 1: Normalize the supported result shapes**

Add a private helper that:

```python
@staticmethod
def _normalize_result(
    result: dict[str, Any],
) -> tuple[str, str, str, list[dict[str, Any]]] | None:
    payload = result.get("payload")
    body = payload if isinstance(payload, dict) else result
    revision = str(
        body.get("plan_revision_id")
        or body.get("revision_id")
        or result.get("plan_revision_id")
        or result.get("revision_id")
        or result.get("revision")
        or ""
    )
    result_hash = str(
        body.get("plan_hash")
        or body.get("reviewed_plan_hash")
        or result.get("plan_hash")
        or result.get("reviewed_plan_hash")
        or ""
    )
    worker_id = str(result.get("worker_id") or body.get("worker_id") or "")
    findings = body.get("findings")
    if not isinstance(findings, list):
        return None
    return revision, result_hash, worker_id, [item for item in findings if isinstance(item, dict)]
```

The body must be the nested `payload` only when it is a mapping; otherwise the
raw in-memory response remains the body. The outer wrapper remains authoritative
for `worker_id` when present.

- [ ] **Step 2: Aggregate normalized findings without dropping valid workers**

Replace the direct top-level field reads in `aggregate()` with:

```python
accepted_results = 0
for result in worker_results:
    normalized = self._normalize_result(result)
    if normalized is None:
        continue
    result_revision, result_hash, worker_id, raw_findings = normalized
    if result_revision != plan_revision_id:
        continue
    if result_hash and result_hash != plan_hash:
        continue
    if not result_hash:
        logger.warning("ZHONGSHU_CRITIC_FANIN_PLAN_HASH_MISSING worker_id=%s revision_id=%s", worker_id, result_revision)
    accepted_results += 1
    findings.extend(
        CriticFinding.from_dict(item, worker_id)
        for item in raw_findings
    )
```

This accepts the current runtime contract (`revision_id` +
`reviewed_plan_hash`) and the stage-artifact wrapper without changing the
conflict resolution rules.

- [ ] **Step 3: Fail closed when all completed results are unusable**

Before the existing action selection, add:

```python
if worker_results and accepted_results == 0:
    logger.error(
        "ZHONGSHU_CRITIC_FANIN_NO_ACCEPTED_RESULTS expected_revision=%s "
        "expected_plan_hash=%s completed=%s",
        plan_revision_id,
        plan_hash,
        len(worker_results),
    )
    return CriticAggregate(
        plan_revision_id=plan_revision_id,
        plan_hash=plan_hash,
        findings=(),
        conflicts=(),
        resolved_conflicts=(),
        unresolved_conflicts=(),
        action="REQUEST_SOLVER_REVISION",
        decision_basis=("No completed Critic result matched the current plan",),
    )
```

This prevents the old empty-findings default from producing `APPROVE_FREEZE`.

- [ ] **Step 4: Perform read-only static verification**

Inspect the edited function and compare its accepted field names with the
captured worker payload and persisted result wrapper. Do not run automated tests
in this turn, per the user's instruction to perform manual testing later.

- [ ] **Step 5: Commit only the plan/spec/code files**

```powershell
git add docs/superpowers/specs/2026-09-03-zhongshu-critic-fanin-design.md docs/superpowers/plans/2026-09-03-zhongshu-critic-fanin.md cmd/orchestrator/zhongshu_parallel.py
git commit -m "fix: preserve Zhongshu Critic fan-in findings"
```

Do not stage or modify unrelated existing worktree changes.
