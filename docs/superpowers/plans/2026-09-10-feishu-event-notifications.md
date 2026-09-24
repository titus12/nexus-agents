# Feishu Event Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore legacy normal-flow Feishu notifications at event nodes while preserving explicit test isolation.

**Architecture:** Keep notification delivery as a direct runtime call after a committed domain event. Use a small renderer/dispatcher boundary and the persisted audit notification keys for dedupe; retain the existing gate adapter for human-gate delivery.

**Tech Stack:** Python 3, immutable workflow context, JSON workflow repository, unittest, urllib Feishu adapter.

---

### Task 1: Add the event notification dispatcher

**Files:**
- Create: `cmd/orchestrator/notifications.py`
- Modify: `cmd/orchestrator/app.py`
- Test: `cmd/test_linear_fsm_event_notifications.py`

- [ ] Define the legacy presentation-event set and bounded renderer.
- [ ] Capture the pre-event snapshot, commit the event, then send state-entry and event notifications directly.
- [ ] Skip hidden events and keys already present in `context.audit.sent_notification_keys`.

### Task 2: Preserve test isolation and gate behavior

**Files:**
- Modify: `cmd/orchestrator/app.py`
- Modify: `cmd/orchestrator/adapters.py`
- Test: `cmd/test_linear_fsm_notification_safety.py`

- [ ] Select `NullNotificationPort` only when `NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS` is explicitly enabled.
- [ ] Keep production default enabled and keep human-gate polling unchanged.
- [ ] Verify the test flag prevents HTTP notification calls.

### Task 3: Verify

**Files:**
- Test: `cmd/test_linear_fsm_event_notifications.py`
- Test: `cmd/test_linear_fsm_notification_safety.py`

- [ ] Run focused unittest modules from `cmd`.
- [ ] Run `compileall` for `cmd/orchestrator`.
