# Single user chat default authorization

The user confirmed the target chat contains only themselves and the bot and explicitly requested that old commands require no decision-owner parameter.

New tasks without explicit owners use a persisted chat-members authorization policy. The authenticated Gate SDK callback must match the configured app, chat, gate token, page and message receipt. Only human open_id operators are accepted. The gateway bridge supplies a trusted chat-member authorization marker to domain answer/approval processing. Document version and cloud approval verification remain required.

Explicit --decision-owner selects the existing restrictive owner policy. Historical snapshots without this policy field retain explicit ownership behavior. Local mode remains explicit and auditable. Default startup does not query membership or require new permissions: the live Gate application lacks member-query permissions, and adding that dependency would prevent the old command from working.

Acceptance: old --new CLI reaches context creation with chat-members policy, startup performs no member lookup, policy survives restart, authenticated answer and approval callbacks advance correctly, wrong chat/message and explicit-owner mismatches are rejected. Live --check-prerequisites without owners confirms readiness from the user's multica directory without creating a task.
