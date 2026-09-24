# Review Orchestrator Reply Handling Design

## Goal
Prevent review_orchestrator_v2 from stopping on runtime system comments, truncated JSON, malformed replies, or resume metadata loss.

## Scope
The change is limited to `cmd/review_orchestrator_v2.py` and regression tests. It does not change Proposal generation, OpenWiki behavior, or workflow state definitions.

## Design
1. Fetch complete comments by default; retain an opt-in summary parameter only for non-parser callers.
2. Filter comments by system type, known runtime notification markers, and `content_truncated`.
3. Iterate candidate comments until a complete JSON object with a state-valid action is found. Invalid candidates are logged and ignored.
4. Preserve a structured wait failure reason such as `timeout_no_reply`, `timeout_malformed_reply`, `timeout_system_events_only`, or `timeout_poll_errors`; escalation messages use that reason instead of calling every failure a generic timeout.
5. On resume, only explicit `--project-type` and `--task-type` flags override saved metadata; omitted flags preserve the existing state.

## Verification
Pure helper tests cover system filtering, truncation handling, valid action extraction, and metadata-preservation semantics. The module is syntax-checked with `py_compile`.
