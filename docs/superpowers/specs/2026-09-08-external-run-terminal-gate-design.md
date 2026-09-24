# External Run Terminal Gate Design

## Goal

Prevent the orchestrator from advancing a structured-output worker, or concurrently dispatching another request to the same Multica target, while the corresponding remote Agent run is still active.

## Scope

- Apply to Multica structured result-file transport.
- Keep the existing role contracts and skill content unchanged.
- Preserve legacy inline/comment transport behavior for requests that do not require result files.

## Design

The adapter exposes the lifecycle state of the Multica run associated with a dispatch comment. A result-file reply is accepted only after both conditions hold:

1. The bound result file passes protocol, request, schema, and role validation.
2. The matching Multica run is in the `completed` terminal state.

`running`, missing, or unreadable run status keeps the worker pending. A failed or cancelled run is not converted into a successful reply; the existing bounded retry path handles it.

Run matching accepts the dispatch comment as the trigger, or a coalesced/delivered comment ID, because Multica may consolidate comments for one Agent run.

Before fan-out, the orchestrator detects duplicate `(issue_id, agent_id)` targets. Such workers are serialized through the same coordinator instead of being submitted concurrently. This is a hard concurrency gate: the external system is never asked to execute coalescible requests in parallel.

## Error handling

- Log a distinct `AGENT_REPLY_WAITING_REMOTE_TERMINAL` event when a valid file or pointer is observed before the remote run is complete.
- Log `PARALLEL_SHARED_EXTERNAL_TARGET_SERIALIZED` when duplicate external targets force serialization.
- Do not treat a missing remote status as completed.

## Verification

Add regression tests for:

- result file present while the remote run is `running`;
- result file present after the remote run is `completed`;
- coalesced comment IDs resolving to the matching run;
- duplicate external targets being serialized;
- existing legacy and single-worker behavior remaining unchanged.
