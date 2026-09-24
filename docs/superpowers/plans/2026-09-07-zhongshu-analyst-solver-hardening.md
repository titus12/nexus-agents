# Zhongshu Analyst/Solver Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Zhongshu Analyst-to-Solver handoff lossless, bounded, routable, and deadline-aware.

**Architecture:** Keep Analyst evidence-only and Solver as the sole task-graph owner. Add deterministic validation and compact projections at the handoff boundary, and enforce the stage deadline around the existing synchronous parallel coordinator.

**Tech Stack:** Python FSM, JSON result-file protocol, `ThreadPoolExecutor`, repository Markdown runtime Skills.

---

### Task 1: Preserve parallel Analyst evidence conflicts

**Files:**
- Modify: `cmd/orchestrator/zhongshu_review.py`
- Modify: `cmd/orchestrator/zhongshu_parallel.py`
- Modify: `cmd/orchestrator/states.py`

- [ ] Update evidence merging so same local IDs from different workers retain worker provenance and conflicting variants.
- [ ] Project compact variants and fact variants into Solver context.
- [ ] Keep canonical evidence IDs stable for existing supplements and audit files.

### Task 2: Make Analyst evidence requests explicit and bounded

**Files:**
- Modify: `cmd/orchestrator/app.py`
- Modify: `cmd/orchestrator/zhongshu_parallel.py`
- Modify: `cmd/orchestrator/states.py`

- [ ] Add bounded `evidence_requests` projection for blocking evidence questions.
- [ ] Require item or requirement scope for Solver `REQUEST_ANALYST_EVIDENCE` responses.
- [ ] Enforce per-worker and merged Analyst evidence limits and required decision fields.

### Task 3: Add Zhongshu stage deadline

**Files:**
- Modify: `cmd/orchestrator/app.py`
- Modify: `cmd/orchestrator/parallel_runtime.py`

- [ ] Add a stage deadline to the Analyst contract-plus-fanout sequence.
- [ ] Route deadline exhaustion through the existing `AGENT_TIMEOUT`/parallel failure path.
- [ ] Keep per-worker 900-second timeout as the default and avoid changing Solver's role semantics.

### Task 4: Align runtime Skills and perform static self-review

**Files:**
- Modify: `docs/multi/runtime/zhongshu-analyst-skill.md`
- Modify: `docs/multi/runtime/zhongshu-solver-skill.md`

- [ ] Document worker-local evidence IDs, evidence request scope, and hard limits.
- [ ] Check all modified call sites for protocol-field consistency.
- [ ] Do not start services, run business tests, or send external notifications in this change.
