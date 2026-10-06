# Zhongshu Evidence Efficiency Implementation Plan

> **For agentic workers:** Execute this accepted plan task-by-task in the current conversation; preserve existing uncommitted work.

**Goal:** Eliminate evidence loss and repeated successful work without weakening review quality.

**Architecture:** A pure cumulative packet merger handles persistence. Node join validates and salvages individual results before retry; existing group approval ratchet protects reuse. Skills retain the existing delivery contract.

**Tech Stack:** Python unittest, scripted adapters, Multica REST sync/readback.

- [x] Add regression tests in `cmd/test_zhongshu_evidence_efficiency.py` for scoped cumulative evidence, changed contracts, immutability, invalid Analyst sibling salvage, group failure salvage and bounded draft feedback; run through `rtk proxy python cmd/run_tests.py test_zhongshu_evidence_efficiency`.
- [x] Implement pure packet merge in `cmd/orchestrator/zhongshu_parallel.py`; invoke from `domain/states.py` without touching the formal plan. Keep new-round and prompt limits.
- [x] Extend `runtime/agent_effects.py` to validate Analyst results individually and preserve valid peers on contract rejection; route worker-specific rejection feedback. Preserve group verdicts only after identity and scope checks.
- [x] Extend `runtime/nodes.py` with bounded rejected draft feedback and format-only repair instructions. Keep retry count and delivery protocol.
- [x] Align three Zhongshu skills and three agent instructions; verify Menxia skills use the same delivery rules and do not inherit Zhongshu approval rules.
- [x] Run focused tests plus `test_linear_fsm_entrypoint`, `test_zhongshu_convergence_e2e`, then full `cmd/run_tests.py` safely; repair regressions.
- [x] Publish with `cmd/sync_multica_review_agents.py --apply --publish-for-next-dispatch` and a new maintenance snapshot directory. Read back all three agent/skill sets and protected configurations.
- [x] Save measured verification and activation limits in `docs/multi/runtime/zhongshu-evidence-efficiency-20261006.md`. Do not claim estimated minutes as measured runtime.
