# Feishu Event Notifications Design

## Goal

Restore the legacy Feishu notifications for normal workflow events while keeping
test runs network-free through `NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS=1`.

## Behavior

The runtime sends notifications directly at the event/transition boundary for
the legacy presentation events: state entry, accepted or rejected agent
replies, local validation, human decisions, heartbeats, Zhongshu fan-out/retry/
fan-in progress, and final completion. Internal polling and dispatch events are
not sent. `AuditState.sent_notification_keys` remains the idempotency guard.

Human-gate delivery keeps the existing Feishu gate adapter and its reply
polling behavior. Production uses the HTTP adapter by default; tests use the
null adapter when the explicit test environment flag is set.

## Data flow

`OrchestratorApp` captures the pre-event snapshot, commits the domain event,
then invokes the direct notifier with the event, pre-event context, and
post-event context. The notifier renders a bounded human-facing message and
calls the configured notification port once for a new key. A duplicate key is
skipped. Notification failures are logged and do not roll back the already
committed workflow transition.

## Verification

Add focused tests for state-entry and agent-reply notifications, duplicate-key
suppression, hidden-event suppression, and the test flag preventing HTTP calls.
