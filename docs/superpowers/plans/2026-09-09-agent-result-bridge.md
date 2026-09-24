# Agent Result Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make structured Agent results reliable when Multica runs Reasonix in an isolated C: task workspace while keeping the Orchestrator-owned D: result file canonical.

**Architecture:** Structured-output prompts will require the complete business result in `result.json` in the Agent's current workspace and will return a compact `nexus-agent-result-ref-v1` pointer. The Multica adapter will validate the complete role contract before persisting any result to the canonical D: path, bridge constrained remote files, and retain inline parsing only as a compatibility path.

**Tech Stack:** Python 3, `unittest`, `pathlib`, JSON result contracts, existing `PromptBundleBuilder`, `MulticaCliAdapter`, and atomic result-file writer.

---

### Task 1: Add failing transport-contract tests

**Files:**
- Modify: `cmd/test_prompt_bundle.py`
- Modify: `cmd/test_prompt_bundle_dispatch.py`
- Modify: `cmd/test_agent_result_file_poll.py`

- [x] **Step 1: Add the prompt contract assertion**

Extend the existing result-file prompt test so it asserts the generated prompt says `result.json` is relative to the Agent workspace, identifies the canonical path as Orchestrator-owned, and does not instruct the Agent to write the D: canonical path.

```python
prompt_text = (manifest_path.parent / "prompt.txt").read_text(encoding="utf-8")
self.assertIn("relative result.json", prompt_text)
self.assertIn("Orchestrator-owned canonical result", prompt_text)
self.assertNotIn("write the one complete business JSON result to " + str(manifest_path.parent / "result.json"), prompt_text)
```

- [x] **Step 2: Add the structured-output inline acceptance test**

In `cmd/test_agent_result_file_poll.py`, build a request with `build_structured_output_spec(...)`, submit a comment containing a complete valid role result with the expected `action`, `task_id`, `request_id`, phase, role, state, mode, protocol, and schema hash, then assert:

```python
self.assertEqual(len(messages), 1)
self.assertEqual(messages[0].payload["result_source"], "orchestrator_inline")
self.assertTrue(result_path.is_file())
```

- [x] **Step 3: Add remote-pointer bridge tests**

Add one test using a temporary `multica_workspaces` root with a path shaped as `workspace/task/workdir/result.json`; assert the adapter reads the file and writes the canonical result under the adapter transport root with `result_source == "orchestrator_bridge"`.

Add two rejection tests:

```python
self.assertEqual(messages, [])  # pointer outside the configured root
self.assertEqual(messages, [])  # pointer inside the root but not workdir/result.json
```

Patch `MULTICA_WORKSPACES_ROOT` for the adapter construction so the test never reads a real user directory.

- [x] **Step 4: Run the focused tests and verify they fail for the missing behavior**

Run:

```powershell
rtk python -m unittest cmd.test_prompt_bundle cmd.test_prompt_bundle_dispatch cmd.test_agent_result_file_poll -v
```

Expected: the new prompt assertion fails because the current prompt names the D: path as the Agent write target, the structured inline test fails because result-file inline payloads are rejected, and the bridge test fails because non-canonical paths are rejected.

### Task 2: Change the result-file prompt contract

**Files:**
- Modify: `cmd/orchestrator/prompt_bundle.py:136-175`
- Modify: `cmd/orchestrator/adapters.py:510-532`

- [x] **Step 1: Replace the Agent write instruction in `PromptBundleBuilder.build`**

Keep `PromptBundle.reference()["result_path"]` as the canonical Orchestrator path for audit and recovery, but change the generated result-file instruction to the following semantics:

```python
prompt = (
    f"{prompt.rstrip()}\n\n"
    "TRANSPORT CONTRACT (authoritative): the Orchestrator-owned canonical result "
    f"is {result_path}; the remote Agent must not write that path. "
    "If a local file is needed, write the complete business JSON to relative "
    "result.json in the current Agent workspace as UTF-8 without BOM. "
    "For result_file mode, do not return the business JSON or a transport "
    "envelope inline. After the file is durably written, return exactly one "
    "compact nexus-agent-result-ref-v1 pointer containing the exact task_id, "
    "request_id, and actual local result.json path. "
    ...
)
```

Retain the exact contract fields, schema hash, stable role fields, and schema-file read requirement. Remove the instruction that forbids putting the result in the issue comment.

- [x] **Step 2: Align the dispatch-level instruction**

Change the structured output instruction assembled in `MulticaCliAdapter.dispatch` to repeat the same boundary: D: is Orchestrator-owned, `result.json` is local staging, and the Agent must return a compact `nexus-agent-result-ref-v1` pointer after writing the file. Keep the prompt bundle as the source of the detailed schema contract.

- [x] **Step 3: Run prompt tests**

Run:

```powershell
rtk python -m unittest cmd.test_prompt_bundle cmd.test_prompt_bundle_dispatch -v
```

Expected: all prompt tests pass, including the new assertion that the Agent is not instructed to write the canonical D: result path.

### Task 3: Accept and persist structured inline results

**Files:**
- Modify: `cmd/orchestrator/adapters.py:1000-1090`
- Modify: `cmd/test_agent_result_file_poll.py`

- [x] **Step 1: Relax only the result-file inline rejection**

In `parse_comments`, keep the existing author, thread, task, request, and payload binding checks. Change the result-file branch so a complete JSON payload with an `action` is marked `inline_result_pending` instead of being rejected solely because `structured_result_required` is true.

Do not accept a pointer as inline JSON. Pointer handling must remain in the dedicated `nexus-agent-result-ref-v1` branch.

- [x] **Step 2: Reuse Orchestrator-owned persistence**

Keep the existing call to `_persist_inline_result(request, payload)` for the pending inline result. This preserves the byte limit, schema-hash validation, role/state validation, and atomic canonical write through `write_agent_result_file`.

- [x] **Step 3: Add explicit inline logging**

Retain the existing `AGENT_INLINE_RESULT_ACCEPTED` event and include the result byte count from the comment fingerprint without logging business content.

- [x] **Step 4: Run the focused inline tests**

Run:

```powershell
rtk python -m unittest cmd.test_agent_result_file_poll.AgentResultFilePollTests.test_poll_accepts_structured_inline_result cmd.test_agent_result_file_poll.AgentResultFilePollTests.test_poll_persists_inline_result_under_orchestrator_ownership -v
```

Expected: both tests pass and the canonical result file is created under the adapter's transport root.

### Task 4: Add the constrained same-host remote pointer bridge

**Files:**
- Modify: `cmd/orchestrator/adapters.py:198-225, 990-1045`
- Modify: `cmd/orchestrator/agent_result_file.py` only if a small path-validation helper is needed
- Modify: `cmd/test_agent_result_file_poll.py`

- [x] **Step 1: Define the configured remote root**

In `MulticaCliAdapter.__init__`, add:

```python
self.multica_workspaces_root = Path(
    os.environ.get("MULTICA_WORKSPACES_ROOT", str(Path.home() / "multica_workspaces"))
).expanduser().resolve()
```

Log the root in `PROMPT_TRANSPORT_CONFIG` without logging any result contents.

- [x] **Step 2: Add strict remote pointer classification**

Add a private helper that accepts only a path contained by `self.multica_workspaces_root`, whose filename is `result.json` and whose parent directory name is `workdir`.

Use `Path.relative_to` for containment; do not use string-prefix comparisons. A path outside this root or with any other shape must be rejected before opening it.

- [x] **Step 3: Read, validate, and canonicalize a remote result**

When a pointer is not the expected canonical D: path but passes remote classification:

1. Read it with `read_agent_result_file(..., allowed_root=self.multica_workspaces_root, ...)` using the current task/request/schema expectations.
2. Pass the validated payload to `_persist_inline_result(request, file_result.payload)`.
3. Set the returned payload's `result_source` to `orchestrator_bridge`.
4. Continue the normal result delivery path.

If the file is missing, log `REMOTE_RESULT_FILE_MISSING` and reject the pointer. If validation fails, log `REMOTE_RESULT_FILE_REJECTED` and reject it.

- [x] **Step 4: Preserve canonical pointer behavior**

Canonical D: pointers must continue through the current `read_agent_result_file` path. Cross-worker request binding and arbitrary-path rejection tests must remain green.

- [x] **Step 5: Run bridge tests**

Run:

```powershell
rtk python -m unittest cmd.test_agent_result_file_poll -v
```

Expected: inline, canonical pointer, valid remote pointer, cross-worker rejection, outside-root rejection, and wrong-shape rejection all pass.

### Task 5: Recover a terminal remote file without a pointer

**Files:**
- Modify: `cmd/orchestrator/adapters.py:850-920, 1040-1120`
- Modify: `cmd/test_agent_result_file_poll.py`

- [x] **Step 1: Add the no-pointer recovery test**

Create one structured request with `MULTICA_WORKSPACES_ROOT` set to a temporary
directory. Write one valid `workspace/project/workdir/result.json` containing
the current task and request bindings, return no comments, and make
`get_run_status` return `completed`. Assert that `poll()` returns one message,
marks it `orchestrator_bridge`, and writes the D-side canonical result.

Also create two matching files under different `workdir` directories and
assert that `poll()` returns an empty list rather than guessing between them.

- [x] **Step 2: Implement bounded terminal discovery**

Add a helper that scans only `self.multica_workspaces_root` recursively for
files named `result.json` whose parent directory is `workdir`. For each file,
read JSON metadata and retain only exact `task_id` and `request_id` matches.
Return no candidate for zero matches and reject the recovery as ambiguous for
more than one match. Do not scan before the existing structured remote-run
terminal gate.

- [x] **Step 3: Reuse the existing bridge validator**

For exactly one candidate, call `_bridge_remote_result_file(request, {"result_path": str(path)})` so the existing allowed-root, request, phase, role, state, protocol, schema, and atomic canonical-write checks remain authoritative. Return the resulting `ExternalMessage` with an external id beginning with `remote-file:`.

- [x] **Step 4: Add recovery diagnostics**

Log candidate count, missing, ambiguous, rejected, and recovered events with
task/request/path metadata only. Never log result contents. Recover an existing
canonical D file only after complete role-contract validation; an incomplete
transport envelope must not mask remote discovery.

- [x] **Step 5: Run the focused recovery tests**

Run:

```powershell
rtk python -m unittest cmd.test_agent_result_file_poll -v
```

Expected: the no-pointer recovery, ambiguous-candidate rejection, existing
inline path, canonical pointer path, constrained remote pointer path, and all
path-binding rejection tests pass.

### Task 6: Run the repository verification gate

**Files:**
- No additional source files.

- [ ] **Step 1: Run all orchestrator-focused tests**

Run:

```powershell
rtk python -m unittest discover -s cmd -p "test_*.py" -v
```

Expected: PASS. If unrelated pre-existing tests fail, record the exact test names and keep the result-bridge tests isolated and passing.

Current verification: the full discovery run executes 289 tests and currently reports 27 failures and 14 errors in the broader pre-existing FSM, prompt-contract, notification, repair, and fixture-dependent suites. The focused transport/prompt suite is green with 23/23 tests passing, and the result-file/role-contract suite is green with 13/13 tests passing after the envelope and terminal-failure hardening.

- [x] **Step 2: Inspect the final diff and transport logs**

Run:

```powershell
rtk powershell -NoProfile -Command "git diff --check; git diff --stat; git status --short"
```

Verify that only the intended transport implementation, focused tests, and plan/spec documents are part of this work; do not stage or remove the user's unrelated dirty files.

- [x] **Step 3: Run a local dry-run transport smoke check**

Run the focused transport test suite once more with a temporary root and verify that the canonical result file is written atomically and contains the request-bound fields. Do not start, stop, or rebind the active Nexus service.

### Task 7: Reject incomplete envelopes and terminal remote failures

**Files:**
- Modify: `cmd/orchestrator/agent_result_file.py`
- Modify: `cmd/orchestrator/adapters.py`
- Modify: `cmd/orchestrator/states.py`
- Modify: `cmd/test_agent_result_file_poll.py`

- [x] **Step 1: Enforce the complete role contract at the file boundary**

Validate the structured role shape before reading or writing a result file. A
transport-only `nexus-agent-result-file-v2` envelope may not overwrite the D:
canonical result or block remote-file discovery.

- [x] **Step 2: Make the new file contract explicit**

Require the Agent to write local `result.json` and return one
`nexus-agent-result-ref-v1` pointer. Keep validated inline results as a
compatibility path only.

- [x] **Step 3: Treat remote `failed` as terminal**

Emit a synthetic terminal failure message from the adapter and route it through
the existing rejection/retry state handling instead of returning an empty poll
result and waiting forever.

- [x] **Step 4: Add regression coverage and run focused suites**

Cover transport-envelope rejection with complete remote recovery and failed-run
termination. The focused prompt/transport suite passes 23/23 tests and the
result-file/role-contract suite passes 13/13 tests.
