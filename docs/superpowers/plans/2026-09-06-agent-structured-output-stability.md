# Agent Structured Output Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Agent structured replies a hard transport contract so malformed JSON cannot enter the Orchestrator and routes that drop structured-output controls cannot silently degrade to free text.

**Architecture:** The Orchestrator defines one phase-specific structured-output contract and a deterministic schema hash. The model gateway must preserve the requested structured-output parameter; otherwise dispatch is rejected before the Agent runs. Reply parsing remains strict and records schema/transport errors separately from existing business-contract validation.

**Tech Stack:** Python 3 standard library, Go `net/http`/`encoding/json`, existing Orchestrator adapters and Codex router.

---

## Files and Responsibilities

- Create `cmd/orchestrator/structured_output.py`: build the transport metadata and phase-level JSON Schema, calculate the stable schema hash, and identify the requested structured-output mode.
- Modify `cmd/orchestrator/models.py:99-114`: carry structured-output metadata on an Agent request without changing existing positional fields.
- Modify `cmd/orchestrator/states.py:524-596`: attach the canonical structured-output metadata to every Zhongshu/Menxia Agent request.
- Modify `cmd/orchestrator/adapters.py:466-522, 792-1080, 1427-1673`: send the metadata, make the result source explicit, and keep malformed comment JSON out of the business-result path.
- Modify `cmd/orchestrator/prompt_bundle.py`: put the result-file transport contract in the authoritative prompt bundle and keep the result path request-scoped.
- Modify `cmd/orchestrator/app.py`: propagate the contract through parallel workers and generate mode-specific contracts for requirement/evidence sub-workers.
- Modify `cmd/orchestrator/agent_result_file.py:27-230`: validate protocol version/schema hash when a result file is used.
- Modify `internal/codexrouter/router.go:77-90, 475-507`: expose route capability and reject structured requests when the route drops the requested parameter.
- Keep `internal/codexrouter/convert.go:120-122` behavior unchanged, with an explicit comment: the conversion path is now reachable only after router preflight has confirmed that the parameter is supported.
- No new test execution is part of this task; static source inspection is used because the user explicitly requested no tests.

## Task 1: Add the canonical structured-output contract

**Files:**
- Create: `cmd/orchestrator/structured_output.py`
- Modify: `cmd/orchestrator/models.py:99-114`

- [x] **Step 1: Define the transport metadata type and stable schema hash.**

Use a frozen dataclass with `mode`, `schema`, and `schema_hash`. Hash the compact UTF-8 JSON representation with sorted keys so the same phase contract always produces the same fingerprint.

```python
@dataclass(frozen=True)
class StructuredOutputSpec:
    mode: str
    schema: dict[str, Any]
    schema_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "schema": copy.deepcopy(self.schema),
            "schema_hash": self.schema_hash,
        }
```

- [x] **Step 2: Build phase-specific schemas from the existing response contract.**

Keep business fields permissive enough for the current phase contracts, but make the root an object, require `action`, enumerate the phase action values, and set `additionalProperties` to true for compatibility with existing validated payload fields. Include a recursive string-safe JSON schema for free-text fields through normal provider serialization; do not add a custom quote-repair rule.

- [x] **Step 3: Add `structured_output` to `AgentRequest` after existing fields.**

The new field defaults to `None` so all existing adapters and positional constructors remain compatible:

```python
structured_output: dict[str, Any] | None = None
```

## Task 2: Attach the contract to dispatches and make the result source explicit

**Files:**
- Modify: `cmd/orchestrator/states.py:524-596`
- Modify: `cmd/orchestrator/adapters.py:466-522`

- [x] **Step 1: Build one `StructuredOutputSpec` for each Agent request.**

Use the phase and role already present on `AgentRequest`; do not derive the schema from the current LLM reply. Store the spec in `AgentRequest.structured_output` and in the request context for logging.

- [x] **Step 2: Include the spec in the outer dispatch envelope.**

The envelope must include `structured_output_mode`, `structured_output_schema_hash`, and the schema object. The existing business `response_contract` remains explanatory only; it is not the authoritative transport schema.

- [x] **Step 3: Replace the contradictory dispatch instruction.**

Remove the instruction that says the Agent must return a large JSON document in the issue comment while the Orchestrator later writes `result.json`. State that the result must arrive through the selected structured transport, and that the comment is not a fallback business result.

- [x] **Step 4: Log requested mode and schema hash.**

Extend `AGENT_DISPATCH_TRANSPORT` and `AGENT_DISPATCH_PAYLOAD_FINGERPRINT` with the mode and hash, without logging the schema contents or business data.

## Task 3: Enforce strict route capability in the Go gateway

**Files:**
- Modify: `internal/codexrouter/router.go:77-90, 475-507`
- Modify: `internal/codexrouter/convert.go:120-122`

- [x] **Step 1: Add route capability metadata.**

Add an optional route field:

```go
StructuredOutputModes []string `json:"structuredOutputModes,omitempty"`
```

Keep the existing `DropParams` field for ordinary compatibility filtering. A route supports JSON Schema only when its capability list contains `json_schema` and it does not drop `response_format`.

- [x] **Step 2: Add a structured-request detector.**

Detect a requested `response_format` object whose `type` is `json_schema` or `json_object`. Do not classify ordinary requests as structured merely because they contain a business prompt mentioning JSON.

- [x] **Step 3: Reject unsupported structured requests before proxying upstream.**

In `handleResponses`, after selecting the route and before selecting the proxy method, return HTTP 400 with a stable error code `structured_output_unavailable` when the route cannot preserve the requested mode. Log route ID, requested mode, and the `DropParams` reason.

- [x] **Step 4: Mark only verified routes as schema-capable.**

Do not mark DeepSeek/GLM routes as `json_schema` while their definitions explicitly drop `response_format`. A caller must receive a deterministic rejection or select another route; it must never silently receive a free-text request.

- [x] **Step 5: Preserve the parameter only after preflight.**

Leave `responsesToChatRequest` responsible for conversion, but add a comment and a structured log that the parameter was preserved. Do not add a second silent fallback branch.

## Task 4: Harden Python reply classification without content repair

**Files:**
- Modify: `cmd/orchestrator/adapters.py:56-83, 792-1080, 1850-1894`
- Modify: `cmd/orchestrator/agent_result_file.py:129-230`

- [x] **Step 1: Keep `_parse_json_candidate` strict for syntax.**

Retain only the existing narrowly scoped extra-closing-brace recovery if it is already part of the accepted compatibility contract. Do not repair unescaped quotes, missing commas, truncated strings, or arbitrary trailing text.

- [x] **Step 2: Classify parse failure as `AGENT_REPLY_SCHEMA_INVALID`.**

Include `schema_hash`, parse error, position, first/last character, and response source in the diagnostic payload. Do not label a malformed comment as a business-contract rejection.

- [x] **Step 3: Require the expected schema hash for a result file.**

Read `structured_output_schema_hash` from the request and reject a file whose hash is missing or different. Continue verifying task ID, request ID, phase, role, UTF-8, BOM policy, and SHA-256.

> 2026-09-27 修订：echo 校验降级为诊断信息（`*_SCHEMA_HASH_ECHO_MISMATCH` 告警后 stamp 期望值，不再拒收）。模型手抄 64 位哈希的转写错误曾整包丢弃合法结果（task-20260927-de54aa CRITIC:6 group-01）；结构契约校验才是真闸门。上文的 "reject" 描述为当时实现记录。

- [x] **Step 4: Remove business-result fallback from arbitrary comment JSON.**

Accept only a result that came through the structured result source or the explicitly bound result-file pointer. Keep the raw comment as bounded diagnostics and audit data.

## Task 5: Perform code-level verification without executing tests

**Files:**
- Modified source files listed above; no test files were added or executed.

- [x] **Step 1: Inspect deterministic schema metadata and propagation paths.**

Cover identical specs producing identical hashes, phase changes producing different hashes, and a payload containing `ParallelCoordinatorDriver` quotes remaining valid when serialized by the local JSON serializer.

- [x] **Step 2: Inspect the exact malformed-reply path and confirm it is not repaired.**

Cover the exact malformed `verification` value from the observed run and assert it remains rejected with a parse position rather than being silently repaired.

- [x] **Step 3: Inspect route preflight, drop-param handling, and conversion ordering.**

Cover a route that drops `response_format`, a route that preserves it without declaring `json_schema`, and a declared schema-capable route. Assert the first two are rejected and the last preserves the request parameter.

- [x] **Step 4: Do not run the tests.**

The user explicitly requested no test execution. Perform only static review, diff inspection, and syntax-aware source inspection in this implementation turn.

## Final Self-Review Checklist

- [x] No code path silently turns a requested structured result into free-text generation.
- [x] No code path uses regex or heuristic quote insertion to change business content.
- [x] Schema/transport validation happens before business validation and FSM transition.
- [x] `AGENT_REPLY_SCHEMA_INVALID` and `AGENT_REPLY_CONTRACT_REJECTED` remain distinct.
- [x] Existing user changes outside these files are untouched.
- [x] No test command, live workflow, or Feishu notification is executed.
