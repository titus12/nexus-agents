# Agent Result Bridge Design

## Problem

The Multica project has a `local_directory` resource pointing to
`D:\workspace\src\nexus-agents`, but the remote Reasonix Agent runs with a
task workspace under `C:\Users\Administrator\multica_workspaces\...\workdir`
and `--workspace-only`. The project resource is exposed as a pointer in Agent
context; it is not a writable filesystem mount.

The current Orchestrator result-file contract embeds the Orchestrator-owned D:
absolute `result.json` path in the Agent prompt and requires the returned
pointer to use that exact physical path. Reasonix rejects that write. Previous
runs succeeded only when a shell path happened to bypass the guarded file tool.

## Goals

- Make structured results reliable when the Agent runs in an isolated C: task workspace.
- Keep D: `runs/.../result.json` as the Orchestrator-owned canonical artifact.
- Preserve strict task, request, phase, role, schema, and result-size validation.
- Preserve compatibility with existing canonical D: pointers and same-host C: pointers.
- Avoid widening Reasonix writable roots or changing global Multica/Codex configuration.
- Make cleanup of the remote task workspace safe: inline results must not depend on a file surviving cleanup.

## Non-goals

- Changing the semantics of Multica project resources or implementing a daemon-level filesystem mount.
- Granting the remote Agent write access to the D: project or the Orchestrator `runs` tree.
- Refactoring the workflow state machine, heartbeat behavior, or Agent role contracts.
- Accepting arbitrary filesystem paths from issue comments.

## Design

### 1. Agent-side output instruction

The prompt bundle remains an Orchestrator-owned read bundle. Its canonical
`result_path` remains metadata for local recovery and audit, but it must not be
presented as an Agent write target.

For result-file requests, the dispatch instruction will tell the Agent to:

1. Write the complete business JSON to relative `result.json` in its current
   workspace if a file is needed.
2. Prefer returning the complete JSON object inline in the issue comment.
3. Use a compact pointer only when necessary, using the actual local result path
   and retaining the file until the response has been emitted.
4. Never attempt to write the Orchestrator's D: canonical path.

The result JSON continues to carry the exact transport fields from the bundle:
`task_id`, `request_id`, `phase`, `state`, `role`, `mode`,
`structured_output_protocol`, and `structured_output_schema_hash`.

### 2. Inline result ingestion

The Multica adapter will accept a complete Agent JSON object for a request whose
structured output mode is `result_file`. It will apply the existing request and
thread correlation checks, then persist the validated object through the
Orchestrator-owned result writer.

The inline result path is authoritative because it survives remote workspace
cleanup. The inline byte limit remains bounded by `INLINE_RESULT_MAX_BYTES`.

### 3. Same-host remote pointer bridge

For compatibility with Agents that still emit a pointer, the adapter will
support a pointer whose file is under the configured Multica workspaces root.
The default root is the current user's `multica_workspaces` directory and can
be overridden with `MULTICA_WORKSPACES_ROOT`.

A remote pointer is eligible only when:

- its path is inside the configured workspaces root;
- its filename is exactly `result.json`;
- its parent directory is exactly `workdir`;
- the file content binds to the current task and request;
- the file passes the expected phase, role, state, protocol, schema, and hash checks.

After validation, the adapter writes the payload atomically to the canonical D:
result path. The resulting payload is marked as `orchestrator_bridge` so logs
can distinguish it from a direct canonical file and an inline response.

Canonical D: pointers remain supported exactly as before. A path outside both
the canonical result path and the constrained Multica workspaces root is
rejected.

### 4. Error and race behavior

- If a remote pointer arrives after the Agent has deleted the C: file, the
  pointer is rejected with an explicit remote-file-missing diagnostic; it is not
  treated as a valid result.
- If the Agent returns inline JSON, no remote file lookup is required.
- A malformed, oversized, cross-request, cross-task, wrong-schema, or wrong-role
  inline result is rejected and does not overwrite the canonical artifact.
- Canonical writes use the existing temporary-file-plus-`os.replace` flow.

### 5. Observability

Add log distinctions for:

- inline result accepted and persisted;
- remote result read and bridged;
- remote pointer rejected because the path is outside the constrained root;
- remote result missing after pointer emission;
- canonical result write source.

No business result contents are logged.

## Testing

Add focused regression coverage for:

- result-file prompts naming relative `result.json` as the Agent write target and
  identifying the D: path as Orchestrator-owned;
- inline result acceptance when structured output mode is `result_file`;
- inline result schema, request, size, and role validation;
- bridging a valid C: pointer into the canonical result file;
- rejecting a pointer outside the Multica workspaces root;
- rejecting a remote path that is not `workdir\result.json`;
- preserving existing canonical D: pointer behavior and cross-worker rejection.

Run the focused transport tests and the repository's Python orchestrator test
suite. Do not stop or rebind the active Nexus service; tests must remain local
and filesystem-scoped.

## Rollout

The change is backward-compatible at the Orchestrator boundary. Existing
canonical result files and legacy non-structured replies are unaffected. The
new inline path is used for new structured-output prompts, while the remote
pointer bridge provides a compatibility window for Agents that still follow the
older pointer instruction.
