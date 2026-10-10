# Multica execution output recovery — 2026-10-07

Failed live task: `task-20261007-d77742`, issue
`44b82d64-c98d-4f3a-ab49-5b2c35bd0013`.

## Observed failure

The three issue reply comments contained human-readable summaries. Multica's
execution records retained a complete structured response in `result.output` on
attempts 2 and 3. The adapter read comments and result files, but never consumed
that execution output. It treated each summary as an unstructured reply.

The requirements role wrapper also compared the wrong sentinel names:
`UNSTRUCTURED_REPLY` / `CONTRACT_REJECTED` instead of the shared constants
`__UNSTRUCTURED_REPLY__` / `__CONTRACT_REJECTED__`. This revalidated diagnostic
metadata as business data, produced irrelevant unknown-field errors, and used up
both format repairs.

## Repairs

- Recover completed, correlated execution `result.output` before declaring reply
  delivery invalid. Require matching agent plus explicit task/request IDs; retain
  state, role, schema, business contract, materialization and fact validation.
- Permit one unambiguous JSON fence in execution output using the existing JSON
  parser; competing fenced objects remain unusable. Never infer task/request IDs.
- Use shared sentinel constants and preserve failure artifacts for scoped repair.
  Keep transport-owned supplemental reports outside business schema validation.
- Give summary-only failures a concrete instruction to return the complete JSON
  result and reuse existing research.
- Reject example/non-open_id decision owners before constructing transport or
  spending model tokens. This latest task persisted the literal example
  `ou_替换为实际决策人的open_id`; it cannot authorize later Feishu confirmations.

## Actual-data replay

`runs/maintenance/replay_latest_failure.py` reads the captured remote execution
records and local failure artifacts, exercises `MulticaCliAdapter`, and drives the
real `OrchestratorApp` in an isolated local directory. It reads the original RTC
source files for quote and raw-byte SHA-256 validation.

Result:

- Attempt 1 remains a correctly classified unstructured reply.
- Attempt 2 recovers execution `36048d4a-418c-4dbc-987c-0a1093d62670` and yields
  `ANALYSIS_READY`; a third attempt is unnecessary.
- All 10 facts verify. The application reaches `REQ_CLARIFY_WAIT` with 4 questions.
- One format repair, zero external retries, zero new model calls and zero external
  notifications. Report: `runs/maintenance/latest-failure-replay-report.json`.

Automated regression covers summary comments with valid execution JSON, fenced
output, contract rejection, missing/mismatched request/task IDs, wrong agents,
running/failed executions, ambiguous multiple objects, valid-comment precedence,
sentinel passthrough and actual-app repair with a persisted failed-reply artifact.

Final verification: `rtk python cmd/run_tests.py` passed all 680 tests in 60.153
seconds; focused regression passed 58 tests. `git diff --check` passed.

This validates the delivery/analysis/clarification mechanism using actual captured
responses. It does not claim a fresh live Feishu approval and implementation run
has completed. The existing failed journal was preserved; no budget or state was
manually reset, and the active Nexus service was not stopped.
