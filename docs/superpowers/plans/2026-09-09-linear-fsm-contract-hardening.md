# Linear FSM Contract Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复新线性 FSM 基础层的 DTO、失败归因、锁适配和提交后锁释放契约。

**Architecture:** 保持旧入口和旧 `TaskLock` API 不变。新 runtime 通过 adapter 连接旧锁；持久化和 node/effect 只补充必要的执行上下文，不把 transport 或业务逻辑重新塞回 runtime。

**Tech Stack:** Python dataclasses、Protocol、unittest、现有 JSON journal/snapshot。

---

### Task 1: Make optional context DTOs fail closed

**Files:**
- Modify: `cmd/orchestrator/domain/context.py:152-170`
- Test: `cmd/test_linear_fsm_persistence.py`

- [ ] Add tests proving non-null `review` and `human_gate` values must be mappings.
- [ ] Update `context_from_dto()` to accept only `None` or mappings for optional aggregates.
- [ ] Run `rtk python -m unittest test_linear_fsm_persistence` from `cmd`.

### Task 2: Preserve execution location in failures

**Files:**
- Modify: `cmd/orchestrator/runtime/repository.py`
- Modify: `cmd/orchestrator/domain/errors.py`
- Modify: `cmd/orchestrator/runtime/nodes.py`
- Modify: `cmd/orchestrator/runtime/effects.py`
- Test: `cmd/test_linear_fsm_effects.py`
- Test: `cmd/test_linear_fsm_nodes.py`

- [ ] Add source `state` and `sequence` fields to `EffectRecord` with backward-compatible defaults.
- [ ] Populate effect records from the pre-transition snapshot.
- [ ] Serialize and deserialize the new fields without rejecting old journal records.
- [ ] Add `state` and `sequence` to `NodeContext` with defaults and use them in worker/join failure records.
- [ ] Add tests that assert effect and node failures carry the source state and sequence.
- [ ] Run the effect and node test modules.

### Task 3: Add a task-bound adapter for the legacy lock

**Files:**
- Create: `cmd/orchestrator/runtime/lock_adapter.py`
- Modify: `cmd/orchestrator/runtime/__init__.py`
- Test: `cmd/test_linear_fsm_locking.py`

- [ ] Implement `TaskLockAdapter` with task-id validation and `LockPort` compatible methods.
- [ ] Forward acquire/refresh/release to the existing no-argument `TaskLock` methods.
- [ ] Test successful forwarding and rejection of a foreign task id.
- [ ] Run the locking tests.

### Task 4: Make post-commit lock-release failure recoverable

**Files:**
- Modify: `cmd/orchestrator/domain/errors.py`
- Modify: `cmd/orchestrator/runtime/engine.py`
- Test: `cmd/test_linear_fsm_engine.py`

- [ ] Add `PostCommitLeaseReleaseError` carrying the committed transition identity and snapshot position.
- [ ] Retain the existing `dispatch()` return type for successful calls.
- [ ] Capture `CommitResult` and raise the new typed error only when release fails after commit.
- [ ] Test that the repository contains the committed transition and the raised error reports `committed=True`.
- [ ] Run the engine tests.

### Task 5: Preserve explicit human-gate metadata when supplied

**Files:**
- Modify: `cmd/orchestrator/domain/context.py`
- Modify: `cmd/orchestrator/domain/states.py`
- Modify: `cmd/orchestrator/runtime/reducer.py`
- Test: `cmd/test_linear_fsm_reducer.py`

- [ ] Add optional `reason_code` to `HumanGateUpdate`.
- [ ] When `OPEN_HUMAN_GATE` carries a non-empty `decision_id`, emit a typed human-gate update; never invent an id.
- [ ] Make the reducer preserve/update the supplied reason code.
- [ ] Test both metadata propagation and the existing no-metadata transition behavior.

### Task 6: Full verification

**Files:**
- No source changes.

- [ ] Run the 63 linear FSM tests.
- [ ] Run the selected existing transport and parallel correlation tests.
- [ ] Run `git diff --check`.
- [ ] Review the diff for accidental changes outside the scoped files.
