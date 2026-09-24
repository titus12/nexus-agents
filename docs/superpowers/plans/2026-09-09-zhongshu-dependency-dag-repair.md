# Zhongshu Dependency DAG Repair Implementation Plan

> **For agentic workers:** Execute this plan task-by-task with focused verification.

**Goal:** Keep task dependencies available while guaranteeing that every Solver task graph is a directed acyclic graph and that semantic cycle rejections produce actionable retry feedback.

**Architecture:** The Python Solver contract remains authoritative. The Solver prompt and multica Skill state the DAG invariant, while the orchestrator validates the graph and reports the full cycle path. Retry prompt construction translates semantic dependency errors into explicit repair instructions without guessing which edge to remove.

**Tech Stack:** Python, unittest, JSON prompt contracts, Markdown Skill guidance.

---

### Task 1: Make dependency validation and repair feedback explicit

**Files:**
- Modify: `cmd/orchestrator/states.py`
- Modify: `cmd/orchestrator/zhongshu_solver_contract.py`
- Modify: `cmd/orchestrator/contracts/zhongshu_solver.py`

- [x] Add a deterministic dependency-cycle path such as `item-a->item-b->item-a` to the semantic validation error.
- [x] Teach retry feedback to recognize dependency-cycle and unknown-dependency errors and include the exact graph repair instruction.
- [x] Add explicit Solver contract rules requiring one-way dependencies and a topological preflight before `READY_FOR_CRITIC`.

### Task 2: Align multica Solver guidance

**Files:**
- Modify: `docs/multi/runtime/zhongshu-solver-skill.md`
- Modify: `docs/multi/zhongshu-solver-skill.md`

- [x] State that dependencies are allowed only as directed acyclic edges; direct and transitive cycles are forbidden.
- [x] Distinguish dependency (`A` must finish before `B`) from related design concerns.

### Task 3: Add regression coverage

**Files:**
- Modify: `cmd/test_solver_contract_fix.py`
- Create or modify focused prompt-repair tests if needed.

- [x] Keep/add a one-way dependency acceptance test.
- [x] Add a three-node cycle rejection test and assert the cycle path.
- [x] Add a retry prompt test proving `SOLVER_DEPENDENCY_CYCLE` is carried into `contract_repair`.

### Task 4: Verify

- [x] Run the dependency-focused tests from `cmd`: all 4 new/changed dependency tests pass.
- [x] Run focused role-contract and Solver revision tests: 10 tests pass.
- [ ] Full prompt/contract suites remain blocked by pre-existing dirty-worktree failures and a Windows temp-directory permission failure; no unrelated files were edited by this repair.
