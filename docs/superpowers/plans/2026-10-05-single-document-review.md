# Single Document Review Implementation Plan

> **For agentic workers:** Execute inline in this session; preserve the existing working tree and active service.

**Goal:** Publish the authoritative Zhongshu requirements as one Feishu document and deliver one short review notice.

**Architecture:** Add document metadata to notification requests, a persistent version publisher using official lark-cli, and retry/readback checks at the production notification boundary. The existing domain snapshot and human revision state machine remain authoritative.

**Tech Stack:** Python standard library, official enterprise Feishu CLI, scripted workflow adapters.

## Task 1: Persistent document publisher

- [x] Add `cmd/orchestrator/feishu_review_document.py` with atomic receipts under the task requirements directory, source hash validation, CLI file arguments, created-result recovery, and readback verification.
- [x] Test publication retry and restart reuse, unknown creation outcomes, missing readback, content drift, and test-mode network refusal in `cmd/test_single_document_review.py`.
- [x] Use `docs +create --api-version v2 --as user --doc-format markdown --title ... --content @./body.md`; verify via a per-version `doc-task` file. Preserve source Markdown as the handoff authority; cloud rendering is a projection.

## Task 2: One review notification

- [x] Extend `NotificationRequest` with optional document content/version/hash/decision metadata, preserving three-argument callers.
- [x] Replace PLAN_REVIEW chunk rendering with one brief notice and pass the complete snapshot as request metadata.
- [x] Make `FeishuNotificationPort` publish first, append the real URL, persist the notification receipt, and reuse it on retries. Other gates use their existing text delivery.
- [x] Configure the publisher with the task root in `OrchestratorApp`; retry delivery at human-gate polling and verify cloud content before accepting a pass. Scripted ports remain network-free.

## Task 3: Verification and delivery

- [x] Update obsolete chunk-delivery expectations to assert one notice and exact complete source metadata; keep legacy reply-thread compatibility tests.
- [x] Run the six targeted modules: 48 tests, 6.241 seconds, OK. Evidence: `runs/maintenance/20261005-single-document-review/targeted.log`.
- [x] Run the full suite through `cmd/run_tests.py`: 1270 tests, 42.877 seconds, OK. Evidence: `runs/maintenance/20261005-single-document-review/full-suite.log`.
- [x] Confirm official CLI publication/readback of task-20261005-510934 v1, including final source comparison and a second drift check. URL: https://centurygames.feishu.cn/docx/L3HudPGPPo74j9xKKEPcFvnDnSN . Receipt and readback are under that task's `requirements/feishu/` directory. No chat messages were sent and the running task was not restarted.
- [x] Run `rtk git diff --check`, document validation evidence and activation requirements. No Multica role/skill changes are needed for the notification presentation change.

## Activation and live verification notes

Newly started orchestrator processes use the single-link review notices. Existing Python processes do not hot-reload the change; the current waiting task still runs the earlier notification code. Its authoritative v1 snapshot has not been changed, and its new cloud document is available for immediate reading from the link above.

The live pilot initially lacked an explicit CLI title and was created as `Untitled`. The final implementation now passes `--title` for future creation. Native title rename returned a concrete missing-scope error, so the pilot was retained with its default cloud metadata title; its visible body includes the task and v1 title and all three groups. No extra scopes were requested and no broader permissions were granted.

The current enterprise CLI reports an available update from 1.0.63 to 1.0.97. It was not updated during this change.
