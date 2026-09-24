# Agent Structured Output Stability Design

**Date:** 2026-09-06  
**Scope:** Zhongshu and Menxia Agent reply transport  
**Status:** Approved direction; implementation pending

## Goal

Make structured Agent replies a protocol guarantee at the transport boundary, so a semantically correct Solver result cannot be rejected because a free-text field contains an unescaped quote.

## Problem Statement

The current path asks an Agent to place a large JSON object in an issue comment, then extracts JSON from the comment body. The request text and runtime Skill require JSON, but they do not constrain token generation. The latest failure contained an unescaped quote inside `finding_resolutions[].verification`, causing JSON parsing to fail before business validation.

The current routing layer can also drop `response_format` for selected model routes. This means the caller may believe that structured output is enabled while the model receives only a natural-language instruction. A file result is not currently authoritative: the Orchestrator writes `result.json` only after it has parsed the inline comment successfully.

## Evidence Boundaries

- The first failed Solver revision returned a 45,731-character body with `json_error_position=42156`; the offending text contained unescaped quotes in a JSON string.
- The same body had no replacement characters or question-mark corruption, so this failure is not explained by UTF-8 damage.
- Later Solver revisions returned valid JSON but were rejected by the deterministic candidate-provenance validator. That is a separate semantic-contract issue and is outside this design.
- Prompt size amplification and repair-loop behavior are also outside the primary transport fix, except that this design must not depend on replaying malformed JSON.

## Non-goals

- Do not redesign Zhongshu task decomposition, Critic quorum, or Menxia execution in this change.
- Do not infer or repair malformed business JSON with regular expressions, quote insertion, comma insertion, or truncation.
- Do not silently switch to a weaker free-text protocol when a selected model route lacks structured-output support.
- Do not change Feishu notification behavior.

## Chosen Architecture

Use a capability-gated structured-output transport with one authoritative result source.

1. The Orchestrator builds a phase-specific JSON Schema and a stable `schema_hash`.
2. The model gateway receives the schema through the provider's native structured-output mechanism (`response_format` JSON Schema where supported, or a strict single-result tool call where that is the supported mechanism).
3. The selected route must report that the mechanism was retained. A route that drops the requested parameter is ineligible for this dispatch.
4. The Agent returns a structured payload only. Transport metadata such as task ID, request ID, role, phase, and schema hash is owned and verified by the Orchestrator.
5. The Orchestrator validates the parsed object against the phase schema and then applies the existing business validator.
6. Only after both validations succeed may the FSM consume the event and persist the result.

The comment body may remain as an audit/diagnostic channel, but it is not a business-result fallback. If the active channel cannot deliver structured output, the dispatch fails explicitly with `STRUCTURED_OUTPUT_UNAVAILABLE` instead of asking the model to serialize JSON in prose.

## Data Flow

```text
StateContext canonical state
        |
        v
Build phase schema + schema_hash
        |
        v
Route capability check
        |-- unsupported or parameter dropped --> STRUCTURED_OUTPUT_UNAVAILABLE
        |
        v
Provider constrained output / strict result tool
        |
        v
Decode structured object
        |-- decode/schema failure --> AGENT_REPLY_SCHEMA_INVALID
        |
        v
Existing business validation
        |-- contract failure --> AGENT_REPLY_CONTRACT_REJECTED
        |
        v
Persist canonical result and emit FSM event
```

## Schema and Ownership Rules

- Each phase has one response schema owned by the Orchestrator. Prompt text and Skill may explain the schema but cannot redefine it.
- Free-text fields remain ordinary JSON string values; the provider serializer is responsible for escaping quotes and control characters.
- The Agent must not return a second JSON envelope, Markdown, a result pointer, or transport metadata that the Orchestrator can derive.
- The Orchestrator verifies `task_id`, `request_id`, `phase`, `role`, and `schema_hash` after decoding.
- Schema validation happens before `task_provenance_error`, quorum processing, or any FSM transition.
- The final canonical plan continues to be materialized and persisted by the Orchestrator.

## Route Capability Contract

The gateway must expose, for the selected route, whether it preserves the requested structured-output mechanism. The dispatch record must include:

```json
{
  "structured_output_mode": "json_schema",
  "schema_hash": "...",
  "route": "...",
  "parameter_preserved": true
}
```

If `parameter_preserved` is false, the adapter must not dispatch the Agent request. Existing route definitions that explicitly drop `response_format` must either be upgraded to support the selected schema mechanism or be excluded from structured workflow roles.

## Failure Handling

- Invalid JSON/schema is a transport failure, not a business-plan failure.
- The raw response is retained only for diagnostics with bounded logging; it is not copied into a new business Prompt.
- A retry, if permitted by the existing FSM policy, uses the same canonical state and schema, with a new request ID/idempotency key. It does not ask the model to convert or repair the previous raw response.
- After the configured transport retry limit, the state becomes `BLOCKED` with the exact code, route, schema hash, and parse position.
- A valid structured payload that fails business validation remains `AGENT_REPLY_CONTRACT_REJECTED`; it must not be relabeled as unstructured.

## Observability

Every dispatch and reply must record:

- selected route/model;
- requested and effective structured-output mode;
- schema hash;
- parameter-preserved capability result;
- response source (structured provider/tool, not inferred comment JSON);
- decode/schema status and bounded error position;
- business validation status.

The logs must make these two cases visually distinct:

```text
AGENT_REPLY_SCHEMA_INVALID
AGENT_REPLY_CONTRACT_REJECTED
```

## Compatibility and Rollout

1. Add capability reporting and schema-hash logging without changing FSM semantics.
2. Enable strict structured dispatch for Solver first, because the observed failure occurred in Solver's large revision response.
3. Enable the same boundary for Analyst and Critic after their phase schemas are wired.
4. Remove inline JSON extraction as a business-result fallback once all production workflow roles use the gated transport.

## Verification Strategy

Verification must cover the protocol boundary, not just the Prompt text:

- a string containing `"ParallelCoordinatorDriver"` is serialized and decoded without corruption;
- a route that drops `response_format` is rejected before dispatch;
- a schema-invalid response never reaches business validation or FSM transition;
- a valid structured response with a business-contract error is classified separately;
- retry input is rebuilt from canonical state and does not contain `original_reply`.

Per the current user instruction, this design phase does not run tests. Implementation verification will be performed only when explicitly requested.
