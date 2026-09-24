# Zhongshu Solver Contract Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent parallel Critic output from corrupting the Solver revision prompt and make valid Solver revisions pass the contract validator, while auditing and hardening equivalent Menxia paths.

**Architecture:** Normalize and deduplicate Zhongshu Critic findings at the fan-in boundary. Keep Analyst requirements authoritative, but explicitly permit Critic-requested additions. Make all Solver compact examples structurally valid. For Menxia, preserve the existing per-item contract and add only the missing prompt-budget and finding-correlation safeguards discovered during the audit.

**Tech Stack:** Python 3.10+, dataclasses, JSON protocol payloads, existing orchestrator FSM and parallel runtime.

---

### Task 1: Deduplicate Zhongshu Critic findings

**Files:**
- Modify: `cmd/orchestrator/zhongshu_parallel.py:449-582`

- [x] Add a deterministic semantic key for normalized findings using finding ID, category, target, claim, decision, severity, evidence IDs, and required content.
- [x] Keep the strongest equivalent finding as the canonical record and merge distinct worker IDs into provenance without emitting duplicate finding entries.
- [x] Log the raw and deduplicated finding counts in the existing fan-in log.
- [x] Preserve fail-closed behavior when no completed result is accepted.

### Task 2: Repair Zhongshu Solver prompt and requirement validation

**Files:**
- Modify: `cmd/orchestrator/states.py:1279-1311,1491-1668`

- [x] Allow typed requirement additions only during an active Critic revision response, while keeping Analyst requirements mandatory.
- [x] Validate that every Analyst requirement remains present, additions are typed objects with unique IDs, and malformed or empty IDs are rejected.
- [x] Replace the compact prompt example's empty requirements list with a typed non-empty example.
- [x] State in both full and compact prompts that Analyst requirements are mandatory and Critic-approved additions may be appended.
- [x] Keep the response contract requiring a complete `plan` for `READY_FOR_CRITIC`.

### Task 3: Audit and harden Menxia prompt/response contracts

**Files:**
- Modify: `cmd/orchestrator/states.py:814-950,1229-1260`
- Modify: `cmd/orchestrator/app.py:227-390,741-810` only if the audit identifies a concrete contract loss
- Modify: `cmd/orchestrator/menxia_parallel.py:140-180` only if the audit identifies a concrete fan-in loss

- [x] Ensure the Menxia item Solver budget fallback retains current item identity, response contract, and all active Critic finding IDs.
- [x] Ensure Menxia Solver validation compares responses against deduplicated active finding IDs and rejects unknown or missing responses deterministically.
- [x] Ensure parallel Menxia fan-in maps every completed result to its unique `(group_id, item_id, state)` key and fails closed on missing/duplicate item identities.
- [x] Do not change Menxia scheduling semantics unless the audit demonstrates data loss or an invalid transition.

### Task 4: Static verification and review

**Files:**
- Verify: `cmd/orchestrator/zhongshu_parallel.py`
- Verify: `cmd/orchestrator/states.py`
- Verify: `cmd/orchestrator/app.py`
- Verify: `cmd/orchestrator/menxia_parallel.py`

- [x] Run Python AST parsing for all modified Python files.
- [x] Run focused pure-function checks for finding deduplication and Solver requirement validation without starting the service.
- [x] Inspect the final diff and confirm no protected Codex configuration files were modified.
- [x] Do not run an end-to-end service test; the user will perform manual testing on the active instance.
