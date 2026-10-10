# Preserve independent Feishu stage messages

The user explicitly clarified that startup and every progression stage must remain separate messages. This supersedes the previous single-card timeline design.

Design: derive one durable delivery gate and platform UUID per original event key. POST distinct stages, reuse receipts on restart or crash replay, and never PATCH delivered progress messages. Preserve legacy history timestamps and reject the old refresh operation. Clarification cards may still update answer status.

Verify independent identities, replay, rejection retry identity and historical timestamps with isolated-notification tests. Backfill the latest task's committed stage intents, read actual Feishu cards and replay to prove no duplicates. Reload idle waiting runners with no pending effects. No model calls, fabricated domain events or active Nexus restart. Completed; evidence: docs/multi/runtime/progress-history-validation-20261007.md.
