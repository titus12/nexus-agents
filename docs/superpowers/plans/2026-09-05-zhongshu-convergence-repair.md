# Zhongshu Convergence Repair Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans when available. Neither is available in this session; implement inline. User has approved implementation and prohibited test execution.

**Goal:** Repair the confirmed Zhongshu convergence defects without losing requirements, evidence or unresolved findings.

**Architecture:** Keep the existing FSM and worker driver. Extract pure review/coverage/feedback rules into focused Zhongshu modules, then connect the existing dispatch, validation and persistence paths. Preserve existing worktree changes.

**Tech Stack:** Python standard library; local Markdown runtime Skills.

## 1. Review aggregation

- [x] Add `cmd/orchestrator/zhongshu_review.py`: shared actions, strict review identity checks, lossless finding observations, valid-source quorum and explicit action priority.
- [x] Connect `CriticConflictResolver.aggregate` in `zhongshu_parallel.py`, retaining its public result interface. Final verdict is computed once with previous findings and required quorum.

```python
# Qualification precedes counting; fingerprints are diagnostic only.
quorum = 2
can_freeze = approvals >= quorum and not active_blockers and not pending_actions
# Omission preserves history. Closing requires two explicit, reasoned confirmations.
resolved = len(resolving_workers) >= quorum and not open_observations
```

## 2. Dispatch, recovery and progress

- [x] In `app.py`, validate saved attempt identity using `dataclasses.replace`, never compare an attempt ID directly to a logical ID. Keep valid partial results across retries.
- [x] Save/reuse the authoritative requirement contract; route HUMAN_GATE/BLOCKED before interpreting success fields.
- [x] Save full Solver evidence requests independently of Critic reviews. Propagate human decisions and the current graph to Analyst.
- [x] Add persistent semantic progress tracking at Zhongshu repair boundaries; three unchanged cycles produce HUMAN_GATE with full pending work.

```python
actual_request = replace(worker.request, request_id=payload['request_id'])
# Only after validating task, logical ID, attempt suffix and stage metadata.
actual_worker = replace(worker, request=actual_request)
```

## 3. Task graph and role contracts

- [x] In `zhongshu_parallel.py`, preserve structured task/global unknowns and evidence, preserve conflicting candidates, and permit all-unknown results to reach a gate.
- [x] In `states.py`, validate executable must coverage and explicit candidate-to-formal task provenance; retain source evidence, scope and unknowns.
- [x] Replace blanket unresolved-to-Analyst routing with explicit owner routing; retain the unresolved response for Critic when it is a Solver revision issue.
- [x] Align actions in `adapters.py`, prompts and validators, including bounded evidence supplementation.

```python
missing = executable_must_ids - covered_requirement_ids
if missing:
    return 'SOLVER_REQUIREMENT_COVERAGE_MISSING:' + ','.join(sorted(missing))
```

## 4. Skills and static handoff

- [x] Replace conflicting local Analyst/Critic legacy runtime instructions with one current contract; update Solver provenance and repair rules.
- [x] Inspect affected callers, serialization, recovery, action dispatch and final diff; do not run tests, imports, services or notification paths.
- [x] Report implementation coverage, static-only assurance, and that external Multica Skills were not published.

Static cases: unanimous approval; stale aliases; repeated worker; closed P1; conflicting observations; mixed routing; blocked contract; attempt recovery; partial quorum; candidate merge/split; uncovered must; structured unknowns; unchanged loop; human decision propagation.

## Implementation notes

- Pure rules live in `zhongshu_review.py` and `zhongshu_tasks.py`; progress state uses the existing persisted `request_payload.zhongshu_progress` dictionary, preserving old StateContext compatibility.
- Current Skills live in `docs/multi/runtime/`. Historical documents remain available but are marked inactive. Every Zhongshu prompt bundle includes a hash-checked `active-skill.md` snapshot; legacy transport includes the same content.
- A confirmed undefined variable in the adjacent Menxia stage-recovery binding was corrected while checking the shared validator, along with its logical/attempt comparison. No Menxia workflow redesign was performed.
- Invalid worker replies are retained as separate stage artifacts and exposed through rejection-path references.
- Verification performed: source AST parsing, symbol-table unresolved-global inspection and diff whitespace checks. These do not execute tests or import the application. Ruff and pyflakes were unavailable; no packages were installed.
- No tests, Agent runs, service restarts, notifications, live task migrations or external Multica Skill publication were performed. End-to-end success remains to be checked by the user's manual run.
