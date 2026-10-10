# Requirements Progress Notifications Implementation Plan

**Goal:** Restore observable lifecycle notifications and verify actual delivery.

**Architecture:** A requirements runtime state wrapper appends durable notification
effects. The existing requirements communication port sends idempotent text; tests
and local mode persist artifacts. Notification failures never emit workflow events.

**Tech Stack:** Python dataclasses, journal repository, scripted Multica adapters,
configured Feishu bot, official lark-cli for message readback.

## Tasks

- [x] Add cmd/test_requirements_progress_notifications.py covering actual app flow,
  start-before-dispatch, retry reason/artifact, terminal drain, persisted delivery,
  crash/restart UUID reuse, API errors, explicit suppression and production port wiring.
  Run `rtk python cmd/run_tests.py test_requirements_progress_notifications` and
  confirm the currently missing lifecycle notifications cause failures.
- [x] Add cmd/orchestrator/runtime/requirements_notifications.py with a state wrapper
  that projects each decision through the existing reducer and prepends
  requirements_progress effects keyed by task/sequence/destination.
- [x] Bind the wrapper in RequirementsRegistry; allow committed notification effects
  through RequirementsRuntime.guard even after terminal transitions. Register
  requirements_progress in OrchestratorApp and restore the production worker port.
- [x] Add publish_progress to both requirements ports and execute it independently
  of business effects. Return message_id/channel/delivered or a durable delivery
  failure without a domain event; use the existing test suppression and UUID hashing.
- [x] Run focused and full test suites via `rtk python cmd/run_tests.py`. Update only
  tests whose old effect counts assumed notifications were absent, preserving their
  business intent assertions. Run `rtk proxy git -c core.safecrlf=false diff --check`.
- [x] Create a bounded, explicit live acceptance script: isolated task, scripted
  adapter, start then cancel, two labelled Feishu messages, zero model calls; verify
  persisted receipts and absence of repeat sends after restart.
- [x] Read message content via official lark-cli and record results in runtime
  validation documentation. Both sending and exact body/chat/sender readback
  passed after read-only authorization. See requirements-notifications-validation-20261007.md.
