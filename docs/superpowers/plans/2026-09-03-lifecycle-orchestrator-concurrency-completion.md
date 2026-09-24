# Lifecycle, Orchestrator Write, and Concurrency Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete lifecycle-oriented file organization, Orchestrator-owned result persistence, production-safe Zhongshu/Menxia concurrency, prompt-budget fallback, conflict aggregation, and end-to-end observability.

**Architecture:** Keep `state.json` as the mutable task control plane and `events.jsonl` as the append-only audit stream. Store each request under one lifecycle bundle containing `manifest.json`, `prompt.txt`, `context.json`, and `result.json`; Agents return JSON only, while Orchestrator validates and writes results atomically. Integrate bounded coordinators through explicit admission leases and preserve the existing FSM as the final state authority.

**Tech Stack:** Python 3.14, `concurrent.futures`, JSON/JSONL, atomic local filesystem writes, existing Multica CLI adapter, `unittest`.

---

### Task 1: Lifecycle request bundle

**Files:**
- Modify: `cmd/orchestrator/prompt_bundle.py`
- Modify: `cmd/orchestrator/adapters.py`
- Test: `cmd/test_prompt_bundle.py`
- Test: `cmd/test_prompt_bundle_dispatch.py`

- [ ] Add `context.json` to each request bundle and include its byte/hash metadata in `manifest.json`.
- [ ] Keep `state.json` and `events.jsonl` at task root; do not duplicate mutable state into request files.
- [ ] Preserve reads from existing `runs/transport/prompt-bundles` paths.
- [ ] Verify UTF-8 without BOM, atomic writes, and manifest hash coverage.

### Task 2: Prompt-budget fallback

**Files:**
- Modify: `cmd/orchestrator/states.py`
- Modify: `cmd/orchestrator/adapters.py`
- Test: `cmd/test_menxia_item_prompt.py`
- Test: `cmd/test_prompt_bundle_dispatch.py`

- [ ] Replace the hard `ValueError` at the 24 KB Menxia item Solver boundary with a compact capsule plus file reference.
- [ ] Log original/compact byte sizes and the selected fallback mode.
- [ ] Convert unexpected prompt-build errors into explicit recoverable state events instead of process crashes.

### Task 3: Admission and bounded dispatch

**Files:**
- Modify: `cmd/orchestrator/app.py`
- Modify: `cmd/orchestrator/states.py`
- Modify: `cmd/orchestrator/concurrency.py`
- Test: `cmd/test_zhongshu_parallel.py`
- Test: `cmd/test_menxia_parallel.py`

- [ ] Instantiate one admission service per Orchestrator process.
- [ ] Acquire leases before every external Agent dispatch and release them on reply, timeout, or dispatch failure.
- [ ] Enforce global, per-task, Analyst, Critic, and per-role limits.
- [ ] Emit admission acquire/release/reject logs with request IDs.

### Task 4: Production Zhongshu fan-out/fan-in

**Files:**
- Modify: `cmd/orchestrator/app.py`
- Modify: `cmd/orchestrator/zhongshu_parallel.py`
- Modify: `cmd/orchestrator/states.py`
- Test: `cmd/test_zhongshu_parallel.py`
- Test: `cmd/test_full_workflow_v31.py`

- [ ] Add a non-blocking coordinator lifecycle (`start`, `tick`, `is_complete`) to the production path.
- [ ] Start at most three Analyst/Critic workers and persist each worker result separately.
- [ ] Fan-in only after all required workers complete; preserve deterministic ordering.
- [ ] Route worker failures through the existing retry and FSM semantics.

### Task 5: Production Menxia parallelism

**Files:**
- Modify: `cmd/orchestrator/app.py`
- Modify: `cmd/orchestrator/menxia_parallel.py`
- Modify: `cmd/orchestrator/states.py`
- Test: `cmd/test_menxia_parallel.py`
- Test: `cmd/test_full_workflow_v31.py`

- [ ] Enable bounded item parallelism only when configured.
- [ ] Keep `max_concurrent_items` at three or below by default and reject unsafe values.
- [ ] Isolate item request/result bundles and never let one item write another item’s files.
- [ ] Enforce per-item retry limit of three and preserve Feishu rendering.

### Task 6: Critic conflict aggregation

**Files:**
- Modify: `cmd/orchestrator/zhongshu_parallel.py`
- Modify: `cmd/orchestrator/menxia_parallel.py`
- Modify: `cmd/orchestrator/states.py`
- Test: `cmd/test_zhongshu_parallel.py`
- Test: `cmd/test_menxia_parallel.py`

- [ ] Aggregate findings by stable finding ID/category/claim.
- [ ] Resolve duplicate agreement deterministically.
- [ ] Route contradictory actions to `REQUEST_SOLVER_REVISION` or `HUMAN_GATE` according to severity.
- [ ] Persist the aggregate verdict and source worker IDs.

### Task 7: State/error observability

**Files:**
- Modify: `cmd/orchestrator/states.py`
- Modify: `cmd/orchestrator/state_machine.py`
- Modify: `cmd/orchestrator/persistence.py`
- Test: `cmd/test_timeout_loop_v31.py`
- Test: `cmd/test_runtime_diagnostics_v31.py`

- [ ] Always populate top-level `blocked_reason` when entering `BLOCKED`.
- [ ] Preserve structured error code/message/source request.
- [ ] Clear stale `last_error` only after a validated reply is accepted.
- [ ] Emit recovery, retry, and terminal-state events with exact request IDs.

### Task 8: End-to-end baseline and verification

**Files:**
- Modify: `cmd/orchestrator/logging_setup.py`
- Modify: `cmd/orchestrator/app.py`
- Create: `cmd/test_end_to_end_baseline.py`
- Modify: `cmd/review_orchestrator_README.md`

- [ ] Record task wall time, per-phase wall time, active worker count, admission waits, retry counts, and result byte sizes.
- [ ] Add a deterministic fake-adapter baseline covering serial and bounded-parallel modes.
- [ ] Run targeted tests, full unit tests, and Python compilation.
- [ ] Document the exact formal test command and acceptance log markers.
