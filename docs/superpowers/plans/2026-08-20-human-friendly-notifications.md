# Human Gate and Human-Friendly Role Notifications Implementation Plan

> **For agentic workers:** Implement task-by-task with tests and checkpoints.

**Goal:** Prevent structured HUMAN_GATE responses from crashing the orchestrator and produce concise, code-generated Feishu summaries for Analyst, Solver, and Critic without adding notification instructions to model prompts.

**Architecture:** Add safe value/question formatters near the HUMAN_GATE handler. Reuse existing payloads and elapsed-time metadata to build role-specific summaries in Python; retain full details in artifacts and cap Feishu list output. Keep Agent prompts unchanged.

**Tech Stack:** Python 3, unittest, existing TaskStore/FeishuClient/OrchestratorV2.

---

### Task 1: Fix HUMAN_GATE structured question formatting

**Files:**
- Modify: `cmd/review_orchestrator_v2.py` in `handle_human_gate`
- Test: `cmd/test_review_orchestrator_v2.py`

- [ ] Add a formatter that accepts strings, dict questions, and arbitrary values without slicing mappings.
- [ ] Render question text, context, options, and required decision safely.
- [ ] Add regression tests for dict and string question entries.

### Task 2: Add code-side role notification summaries

**Files:**
- Modify: `cmd/review_orchestrator_v2.py`
- Test: `cmd/test_review_orchestrator_v2.py`

- [ ] Add bounded helpers for list summaries and group/item summaries.
- [ ] Add Analyst completion summary using findings/evidence/questions.
- [ ] Add Solver start/completion summary using selected direction and groups/items.
- [ ] Add Critic completion summary using findings, blockers, and next actions.
- [ ] Keep full payloads in artifacts and cap displayed lists.

### Task 3: Replace terse Zhongshu notifications

**Files:**
- Modify: `cmd/review_orchestrator_v2.py`

- [ ] Replace Analyst completion messages with the new summary helper.
- [ ] Replace Solver completion messages with plan summary helper.
- [ ] Replace Critic completion messages with review summary helper.

### Task 4: Verify

- [ ] Run `python cmd/test_review_orchestrator_v2.py`.
- [ ] Run syntax compilation for `cmd/review_orchestrator_v2.py`.
- [ ] Inspect generated notification strings and confirm no Agent prompt changes.
