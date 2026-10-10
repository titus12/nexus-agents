# Single user chat owner implementation plan

**Goal:** Make legacy startup commands work in the user's single-person bot group without an explicit owner parameter.

**Architecture:** Persist chat-members as the default policy for new tasks without explicit owners. Validate authenticated Gate callbacks against the configured chat and card; explicit owners remain restrictive.

**Tech Stack:** Python, official Gate SDK callbacks, durable gateway bridge, scripted tests.

- [x] Check actual integration capabilities: Gate member query is denied with 99991672; eliminate that dependency.
- [x] Wire persisted group authorization into task creation and gateway/domain callbacks; retain explicit owner checks.
- [x] Verify old command creation, restart, authenticated clarification/approval and wrong-group/message rejection with mocked HTTP.
- [x] Complete live no-owner readiness check, gateway reload and full regression suite (711 passed).
