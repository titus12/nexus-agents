# Result Pointer Binding Hardening Design

## Goal

Prevent a concurrent Agent reply from combining one request's `request_id` with another request's `result_path`.

## Evidence and boundary

The prompt bundle already creates a request-scoped path:

`<bundle_root>/<task_id>/<request_id>/result.json`

The production adapter validates the pointer `task_id` and `request_id`, but currently passes the pointer-provided path directly to the result-file reader. A pointer can therefore name a different worker's file and is rejected only after that file is opened and its embedded request identity is checked. This is an association-layer defect, not a role Skill defect.

## Design

1. The Orchestrator-computed request-scoped result path is authoritative.
2. A result pointer is accepted only when:
   - its protocol is `nexus-agent-result-ref-v1`;
   - its `task_id` and `request_id` equal the active request;
   - its `result_path` is path-equivalent to the canonical path derived from the active request;
   - the result file's embedded identity, hash, schema, state, and role mode pass the existing checks.
3. A path mismatch is rejected before reading the referenced file. The rejection log records expected and actual IDs and paths without logging business content.
4. Recovery by directory scan continues to use the canonical path only.
5. Inline results continue to be persisted under the canonical path and are unchanged.

## Failure handling

The adapter must fail closed: it must not substitute the canonical path for a bad pointer, because that could hide a transport error or accept a result that was never written for the current request. The existing retry/re-dispatch logic handles the rejected attempt.

## Verification

Add focused tests for:

- a pointer with the current request ID but another worker's result path being rejected without reading the other file;
- a pointer with the canonical path being accepted;
- distinct request IDs producing distinct canonical result paths.

The existing task/request mismatch and result-file identity tests remain valid.
