# Workflow code self-review repair

**Goal:** Audit operational boundaries independently of the requirements document.

**Architecture:** Preserve workflow approval rules, make PID probes read-only, reject stale recovery safely, validate exact message receipts, and prevent idle cancellation from executing queued business effects.

**Tech Stack:** Python immutable FSM, ctypes Windows process query, scripted adapters.

- [x] Reproduce unsafe Windows liveness probe, stale recovery and overly permissive message verification.
- [x] Repair with focused tests including an isolated real Windows child process.
- [x] Audit cancellation queue behavior and fix any reproduced model dispatch after cancellation.
- [x] Run focused and full suppressed-notification tests, record scope and remaining live acceptance limits.
