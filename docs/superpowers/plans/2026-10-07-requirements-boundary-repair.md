# Requirements result boundary repair

User authorized implementation following the diagnosed schema/domain mismatch.

Design: preserve evidence/human authority and bounded retries; report all independent shape, identity and evidence failures together. Keep pure semantic checks separate from file I/O. Do not silently change model answers or edit task snapshots.

1. Share question revision bounds with contracts; validate numeric schema bounds and explain local-key identity rules in role prompts.
2. Add pure result preflight collecting per-record decoding errors, duplicate local keys/IDs and reference errors. Inspect the whole result before file-evidence rejection. Dry-run existing domain analysis to catch authority invariants before commit.
3. Aggregate safe, indexed repair feedback and failure logs; preserve old error categories for evidence-only and contract-only failures, use a reply validation error for mixed failures.
4. Replay all three actual model results; ensure first feedback includes evidence, collision and invalid revisions. Commit sanitized deterministic fixtures for portable regression.
5. Drive the real OrchestratorApp with a script returning simultaneous errors then a repaired result; verify progression and bounded persistent invalid failures without model tokens/notifications.
6. Run focused tests and full test runner. Resume the failed task via verified runtime-code recovery only after checks pass; preserve callbacks and active Nexus service.

Acceptance is bounded: the repaired model-result boundary and saved-result replay must pass. A scripted run is not evidence that arbitrary live model content will always converge. Live recovery completion and any independent authorization block must be reported separately.

Completed: all six implementation/verification steps. Final full suite 733 passed; focused boundary/recovery/crash suite 17 passed. Live code-fingerprint recovery re-entered analysis with a fresh request and reached REQ_CLARIFY_WAIT:9 on its first model return, with four questions and no pending effects. Full proposal generation still requires the human answers and document authorization. Validation record: `docs/multi/runtime/requirements-boundary-repair-validation-20261007.md`.
