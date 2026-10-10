# Requirements batching implementation plan

**Goal:** Reduce repeated clarification and progress interruptions while preserving human decisions and durable workflow events.

**Architecture:** Cover proposed blocking assumptions before the first clarification publication. Keep existing stable question IDs and dependency rounds, group cards by topic without changing field mappings, and show whole-batch progress. Publish one durable interactive progress message per task and PATCH it thereafter; do not use text-message editing, which has a finite edit limit. Keep action cards and failure notices separate.

**Tech stack:** Python immutable requirements domain, SQLite gateway receipts, Feishu JSON 2.0 cards, scripted OrchestratorApp tests.

- [x] Add missing-assumption question synthesis in `domain/requirements/questions.py`, invoked after analyst question merge; protect existing questions and explicit human answers.
- [x] Add first-round checklist and later-question basis rules to requirements role contracts.
- [x] Show batch totals, answered totals, topic headings, and later-round reasons in `requirements_cards.py`; retain descriptor field indices and five-question pagination.
- [x] Reuse one persisted `progress` gate/message receipt in `requirements_port.py`. Skip duplicate/stale sequence updates, PATCH the same message for later sequences, and persist successful sequence in GatewayStore. Do not create replacement messages on ambiguous failures.
- [x] Test omission coverage, dependency preservation, duplicate answers, pagination mapping, shared progress ID, restart replay, stale update rejection, and failed PATCH without fallback POST.
- [x] Run `rtk python cmd/run_tests.py`; use scripted adapters and isolate notification sends. Final result: 723 tests passed.
- [x] Reload the active task only at an idle human/recovery boundary, preserve its snapshots and callbacks, and record acceptance scope and limitations. Reloaded at REQ_BLOCKED:22 with no pending effects; document publication requires restored Feishu user authorization.

User approved this design with “实施”. Work stays in the current checkout and preserves pre-existing changes; no new branches or commits are required.
