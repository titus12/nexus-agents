# Agent Result Envelope Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every result-file Prompt Bundle expose the canonical role schema and safely recover legacy Agent results that contain the correct request binding but omit immutable transport envelope fields.

**Architecture:** The Prompt Bundle will persist the exact `StructuredOutputSpec.to_dict()` as a required `structured-output.json` artifact and expose its path in the prompt reference. Result-file recovery will remain strict for request identity, path, hash conflicts, action, and business fields, while optionally backfilling only absent immutable transport fields in memory for the exact request-scoped file.

**Tech Stack:** Python 3, JSON Schema-shaped dictionaries, UTF-8 atomic file writes, existing Orchestrator adapters and structured-output validators. Static AST/diff verification only; no service, business test, or notification run.

---

## Files and responsibilities

- Modify `cmd/orchestrator/prompt_bundle.py`: write and manifest the request's `structured-output.json`; expose its path and mention it in the generated instruction.
- Modify `cmd/orchestrator/agent_result_file.py`: add request-scoped compatibility backfill for missing immutable transport fields while rejecting present mismatches.
- Modify `cmd/orchestrator/adapters.py`: enable the compatibility mode only for exact result-file reads and include the schema artifact in the Agent-facing transport instruction.
- Do not modify Multica's online Skill: the online `SKILL.md`, `references/io-contract-v2.md`, and `REQUIRES.md` already specify the correct envelope.

### Task 1: Persist the exact structured-output artifact

**Files:**

- Modify: `cmd/orchestrator/prompt_bundle.py:45-100, 117-315`

- [ ] **Step 1: Add the artifact reference to `PromptBundle`.**

Add an optional `structured_output_path: Path | None` field after `context_path`. In `reference()`, return its resolved string as `structured_output_path` when present, otherwise an empty string. Keep `result_path` request-scoped under the existing bundle root.

```python
structured_output_path: Path | None = None

# inside reference()
"structured_output_path": (
    str(self.structured_output_path.resolve())
    if self.structured_output_path is not None
    else ""
),
```

- [ ] **Step 2: Write `structured-output.json` from the current request spec.**

Immediately after `context_value` is established and before the manifest is assembled, require a mapping when `structured_output.mode == "result_file"`. Serialize the full `structured_output` mapping (including `schema`, `schema_hash`, `protocol`, role, state, role mode, and stable fields) with `_atomic_write_json`. Add it to `files` as required UTF-8 JSON and retain its bytes for `total_bytes`.

```python
files = [prompt_file, context_file]
structured_output_path: Path | None = None
structured_output_file: PromptFile | None = None
if isinstance(structured_output, Mapping) and structured_output.get("mode") == "result_file":
    if not isinstance(context_value.get("structured_output"), Mapping):
        raise PromptBundleError("result-file bundle is missing structured output spec")
    structured_output_path = root / "structured-output.json"
    _atomic_write_json(
        structured_output_path,
        dict(context_value["structured_output"]),
    )
    structured_output_bytes = structured_output_path.read_bytes()
    structured_output_file = PromptFile(
        name="structured-output.json",
        purpose="canonical role schema and transport contract; read as UTF-8",
        required=True,
        sha256=_sha256_bytes(structured_output_bytes),
        bytes=len(structured_output_bytes),
    )
    files.append(structured_output_file)
```

The implementation must add `structured_output_file.bytes` to `total_bytes` through the sum of all manifest files, without double-counting the manifest itself. Keep the file UTF-8 without BOM and preserve the existing atomic write behavior.

- [ ] **Step 3: Make the generated instruction and return object point to the artifact.**

When result-file mode is active, append the resolved `structured-output.json` path to the transport instruction and state that it is the canonical schema for every mode. Return the path in `PromptBundle` and include it in the bundle reference consumed by `adapters.py`. Remove the existing later `files = [prompt_file, context_file]` assignment so the artifact is not discarded.

```python
f"Read the canonical structured output schema from {structured_output_path} before writing. "
"Do not select a legacy mode-specific envelope; preserve all required transport and role fields. "
```

- [ ] **Step 4: Preserve manifest verification.**

Do not create a separate verification rule that can disagree with the manifest. The existing `verify()` loop must validate the new required file's existence, UTF-8 bytes, no BOM, byte count, and SHA-256 through the same `manifest["files"]` entries.

### Task 2: Safely recover legacy result files

**Files:**

- Modify: `cmd/orchestrator/agent_result_file.py:167-310`

- [ ] **Step 1: Add an explicit compatibility parameter.**

Extend `read_agent_result_file()` with `allow_transport_backfill: bool = False`. Keep the default strict so callers outside the exact adapter recovery path do not change behavior.

```python
allow_transport_backfill: bool = False,
```

- [ ] **Step 2: Validate request identity before backfilling.**

After JSON object parsing and before normal transport checks, require `payload["request_id"]` to be present and exactly equal to the expected `request_id`. If it is absent or different, raise `AgentResultFileError("result request_id mismatch")`. Verify the path is still inside `allowed_root`; the adapter supplies the exact request-derived path.

- [ ] **Step 3: Backfill only absent immutable fields.**

Use this exact expected mapping:

```python
expected_transport = {
    "task_id": task_id,
    "request_id": request_id,
    "phase": phase,
    "state": expected_state,
    "role": role,
    "mode": expected_role_mode,
    "structured_output_protocol": STRUCTURED_OUTPUT_PROTOCOL,
    "structured_output_schema_hash": expected_schema_hash,
}
```

For each field other than `request_id`, reject a present non-empty value that differs from the expected value. If the value is absent/empty and `allow_transport_backfill` is true and the expected value is non-empty, add the expected value to the in-memory payload and record the field name. If compatibility mode is false, retain strict rejection for missing required fields.

- [ ] **Step 4: Keep business validation strict and auditable.**

Do not backfill `action`, `summary`, `requirements`, `evidence_updates`, or any other role field. Do not rewrite the source file. When fields are backfilled, set:

```python
payload["result_transport_backfilled_fields"] = sorted(backfilled_fields)
```

and emit one warning:

```python
logger.warning(
    "AGENT_REPLY_FILE_TRANSPORT_BACKFILLED task_id=%s request_id=%s "
    "phase=%s role=%s path=%s fields=%s",
    task_id, request_id, phase, role, path, sorted(backfilled_fields),
)
```

Then run the existing protocol, schema-hash, action, and role/business validation against the normalized in-memory payload.

### Task 3: Enable compatibility only at result-file adapter boundaries

**Files:**

- Modify: `cmd/orchestrator/adapters.py:505-518, 742-760, 1007-1025`

- [ ] **Step 1: Pass the canonical schema path in the Agent instruction.**

Read `structured_output_path` from `bundle.reference()` and include it in the result-file instruction. The instruction must say that the Agent writes the complete role result to `result_path`, reads the canonical schema file first, and returns only the compact pointer.

- [ ] **Step 2: Enable compatibility for exact file recovery.**

Pass `allow_transport_backfill=True` to the `read_agent_result_file()` call in `_recover_result_file()` and to the pointer-based result-file read after `result_path` binding has already been checked. Do not pass it to inline result persistence or any caller that accepts arbitrary paths.

```python
allow_transport_backfill=True,
```

- [ ] **Step 3: Preserve pointer/path and mismatch protections.**

Keep the existing `pointer_task_id`, `pointer_request_id`, and expected `result_path` checks before file reads. A pointer to another request, a mismatched present field, an invalid hash, a missing `request_id`, or an invalid business payload must still be rejected.

### Task 4: Static verification and isolated commit

**Files:**

- Verify: `cmd/orchestrator/prompt_bundle.py`
- Verify: `cmd/orchestrator/agent_result_file.py`
- Verify: `cmd/orchestrator/adapters.py`

- [ ] **Step 1: Inspect only the intended diff.**

Run:

```powershell
rtk git diff -- cmd/orchestrator/prompt_bundle.py cmd/orchestrator/agent_result_file.py cmd/orchestrator/adapters.py
rtk git diff --check -- cmd/orchestrator/prompt_bundle.py cmd/orchestrator/agent_result_file.py cmd/orchestrator/adapters.py
```

Expected: only the three intended source files are changed by this fix, and `git diff --check` reports no whitespace errors. Existing unrelated working-tree changes remain untouched.

- [ ] **Step 2: Parse the modified Python files without running the service or tests.**

Run:

```powershell
rtk powershell -NoProfile -Command "$paths = @('cmd/orchestrator/prompt_bundle.py','cmd/orchestrator/agent_result_file.py','cmd/orchestrator/adapters.py'); foreach($path in $paths) { python -c \"import ast, pathlib; ast.parse(pathlib.Path(r'$path').read_text(encoding='utf-8'))\" }"
```

Expected: all three files parse successfully and no process/service/test is started.

- [ ] **Step 3: Commit only the implementation files.**

Run:

```powershell
rtk git add -- cmd/orchestrator/prompt_bundle.py cmd/orchestrator/agent_result_file.py cmd/orchestrator/adapters.py
rtk git commit --only -m "fix: stabilize result file transport envelope" -- cmd/orchestrator/prompt_bundle.py cmd/orchestrator/agent_result_file.py cmd/orchestrator/adapters.py
```

Expected: the commit contains only the three implementation files; the existing dirty worktree is not reset, cleaned, or otherwise overwritten.
