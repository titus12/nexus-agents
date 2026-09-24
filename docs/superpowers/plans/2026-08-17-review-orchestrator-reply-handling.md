# Review Orchestrator Reply Handling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make review_orchestrator_v2 wait for and classify valid agent replies reliably.

**Architecture:** Keep the existing state machine and CLI contract. Harden the comment adapter and wait loop, then make resume metadata updates explicit-only.

**Tech Stack:** Python 3, unittest, Multica CLI.

---

### Task 1: Harden comment retrieval and candidate filtering

**Files:**
- Modify: `cmd/review_orchestrator_v2.py`
- Test: `cmd/test_review_orchestrator_v2.py`

- [ ] Fetch complete comments by default.
- [ ] Exclude system/runtime comments and truncated content.
- [ ] Test valid agent comments remain selectable while watchdog and truncated comments are ignored.

### Task 2: Validate replies and classify wait failures

**Files:**
- Modify: `cmd/review_orchestrator_v2.py`
- Test: `cmd/test_review_orchestrator_v2.py`

- [ ] Require parseable JSON and a valid action before returning from the wait loop.
- [ ] Record distinct failure reasons and include them in escalation logs.
- [ ] Test malformed and system-only candidate behavior.

### Task 3: Preserve resume metadata

**Files:**
- Modify: `cmd/review_orchestrator_v2.py`
- Test: `cmd/test_review_orchestrator_v2.py`

- [ ] Change project/task CLI defaults to omitted values.
- [ ] Update saved metadata only when flags are explicit.
- [ ] Test that omitted resume flags preserve existing metadata.

### Task 4: Verify

**Files:**
- No additional files.

- [ ] Run `python -m py_compile cmd/review_orchestrator_v2.py cmd/test_review_orchestrator_v2.py`.
- [ ] Run `python -m unittest cmd.test_review_orchestrator_v2 -v`.
