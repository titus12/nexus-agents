# Solver Reply Framing Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Safely accept the specific extra-closing-brace corruption seen in Solver replies while preserving strict validation for all other malformed or ambiguous output.

**Architecture:** Centralize candidate parsing in `cmd/orchestrator/adapters.py`. Strict JSON parsing remains the first path; a narrowly scoped `raw_decode` fallback repairs only a root object followed by exactly one extra closing brace. The existing transport correlation and state validators remain authoritative after parsing.

**Tech Stack:** Python 3, `json.JSONDecoder`, existing unittest-style local tests, UTF-8 prompt/result transport.

---

### Task 1: Add safe JSON framing recovery

**Files:**
- Modify: `cmd/orchestrator/adapters.py:46-66,1825-1848`
- Test: `cmd/test_json_bom_v31.py`

- [ ] **Step 1: Add a shared candidate parser**

Implement a helper that returns the decoded value, parse status, and repair kind. It must run `json.loads` first, then use `JSONDecoder.raw_decode` only to recognize a complete object followed by exactly `}`. Reject all other suffixes.

- [ ] **Step 2: Route `_json_parse_diagnostic` and `_extract_json` through the helper**

Keep BOM and existing fenced-JSON handling. Report repaired replies as `json_status="repaired"` so the log distinguishes deterministic recovery from valid untouched JSON.

- [ ] **Step 3: Add focused regression cases**

Extend the existing JSON parsing test file with assertions for `{"action":"READY_FOR_CRITIC"}}` being recovered, `{"action":"A"}{"action":"B"}` being rejected, and `{"action":"A"} trailing` being rejected.

### Task 2: Make structure-repair retries explicit

**Files:**
- Modify: `cmd/orchestrator/states.py:720-755`
- Modify: `cmd/orchestrator/adapters.py:456-463`

- [ ] **Step 1: Add a framing-specific retry instruction**

When the last error is `REPLY_BODY_NOT_STRUCTURED`, tell the Agent to emit exactly one root JSON object, with no Markdown, second object, trailing brace, or trailing prose, while preserving the original business content.

- [ ] **Step 2: Preserve strict downstream validation**

Do not alter `validate_agent_reply`, action allow-lists, request binding, or Solver plan validation. A repaired transport envelope must still pass all of them.

### Task 3: Static review without executing tests

**Files:**
- Review: `cmd/orchestrator/adapters.py`
- Review: `cmd/orchestrator/states.py`
- Review: `cmd/orchestrator/validators.py`

- [ ] **Step 1: Inspect the final diff**

Confirm the parser does not accept concatenated JSON or arbitrary trailing content and that no notification, process, or test command is introduced.

- [ ] **Step 2: Report the changed files and verification limitation**

State clearly that focused tests were added but not run, matching the user instruction.

