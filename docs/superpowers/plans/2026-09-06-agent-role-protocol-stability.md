# Agent Role Protocol Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every Agent role/Skill one stable response protocol while preserving different protocols between Zhongshu and Menxia, and deliver the updated Skills through the Multica prompt bundle.

**Architecture:** `structured_output.py` owns one fixed schema per phase/role, independent of mode. Prompt builders keep role-specific business instructions but require the same role fields on every response. State validators extract and validate the fields relevant to the current state. `PromptBundleBuilder` includes the locked active Skill for every structured phase, so Multica receives the exact Skill snapshot.

**Tech Stack:** Python 3 standard library, existing FSM validators, local Markdown Skills, Multica CLI prompt bundles.

---

### Task 1: Replace mode-dependent role schemas with stable role schemas

**Files:**
- Modify: `cmd/orchestrator/structured_output.py`
- Modify: `cmd/orchestrator/agent_result_file.py`

- [ ] **Step 1: Define fixed role field sets.**

`build_structured_output_spec(phase, role, context)` must select fields from only `(phase, role)`. Its required root fields must include `task_id`, `request_id`, `phase`, `state`, `role`, `mode`, `structured_output_protocol`, and `structured_output_schema_hash`. The schema must keep role-specific fields at the top level so existing validators can extract them.

- [ ] **Step 2: Make mode a discriminator, not a schema selector.**

Use the union of valid role actions in the fixed role schema. Add the supported mode values as a `mode` enum. Do not include `context` values such as `contract_mode` or `solver_revision_mode` in the schema hash.

- [ ] **Step 3: Validate state and mode binding on result files.**

When an expected structured schema is supplied, reject a result whose `state` or `mode` differs from the request context. Keep existing UTF-8, request binding, protocol, and schema-hash checks.

### Task 2: Normalize Zhongshu request prompts by Agent role

**Files:**
- Modify: `cmd/orchestrator/states.py`
- Modify: `cmd/orchestrator/app.py`
- Modify: `cmd/orchestrator/zhongshu_review.py`

- [ ] **Step 1: Use one Analyst response shape.**

Keep the three Analyst operations, but make each prompt require the same Analyst fields: `requirements`, `task_proposals`, `evidence_updates`, `confirmed_facts`, `constraints`, `conflicts`, `unknowns`, `unknown_requirement_ids`, `unknown_resolutions`, `risks`, `scope`, and `questions_for_user`. Only the active operation's fields may be non-empty.

- [ ] **Step 2: Use one Solver response shape for initial, revision, and recovery.**

Always request `plan`, `changes`, `finding_resolutions`, `unknowns`, `risks`, and `questions_for_user`. Initial mode requires `plan`; revision mode may use `changes`; recovery mode uses the same fields and may return an empty `changes` array. The state validator remains responsible for deciding which content is required.

- [ ] **Step 3: Keep one Critic response shape across all three lenses.**

The three Critic workers keep different `worker_lens` instructions, but use the same fields and action contract. Do not count the lens as a schema variation.

- [ ] **Step 4: Require the canonical mode/state fields in parallel fan-in validation.**

Validate each worker against its request's fixed role schema and expected mode/state before quorum or merge. Preserve the existing worker identity, revision, plan hash, finding, and quorum checks.

### Task 3: Normalize Menxia role Skills and state contracts

**Files:**
- Modify: `cmd/orchestrator/states.py`
- Modify: `cmd/orchestrator/app.py`
- Modify: `docs/multi/menxia-solver-skill.md`
- Modify: `docs/multi/menxia-analyst-skill.md`
- Modify: `docs/multi/menxia-critic-skill.md`

- [ ] **Step 1: Preserve separate role protocols.**

Do not make Menxia Solver, Analyst, and Critic share a schema. Make each role's Skill require its own fixed fields on every response, with empty values for unused fields.

- [ ] **Step 2: Unify Menxia Critic item/group envelope.**

Use one Critic envelope for item review and group gate. Keep item and group action allowlists in the FSM, and let the Orchestrator extract `item_review` or `group_review` from the same Critic result.

### Task 4: Deliver updated Skills to Multica

**Files:**
- Modify: `cmd/orchestrator/prompt_bundle.py`
- Modify: `cmd/orchestrator/adapters.py`
- Modify: `docs/multi/runtime/zhongshu-analyst-skill.md`
- Modify: `docs/multi/runtime/zhongshu-solver-skill.md`
- Modify: `docs/multi/runtime/zhongshu-critic-skill.md`

- [ ] **Step 1: Update active Zhongshu Skills.**

Document one fixed role protocol per Skill. Replace instructions that require a different root response per mode with instructions to keep the same fields and leave irrelevant fields empty. Require result-file output and compact pointer-only comments.

- [ ] **Step 2: Include the active Skill snapshot for both phases.**

Change the prompt-bundle condition from Zhongshu-only to all structured roles with an active Skill lock. Change legacy transport to include the same locked Skill content for Menxia as well.

- [ ] **Step 3: Keep the hash lock authoritative.**

The bundle must continue verifying the Skill bytes against the lock generated by `states.py`, and Multica must receive the bundle reference plus the Skill hash.

### Task 5: Static self-audit without tests

**Files:**
- Review all files changed in Tasks 1-4.

- [ ] **Step 1: Verify schema invariants by source inspection.**

Confirm that the same `(phase, role)` produces the same schema hash for every mode, while different roles produce different hashes.

- [ ] **Step 2: Verify extraction and validation boundaries.**

Confirm that the Orchestrator extracts role fields after transport validation and still applies state-specific action, finding, plan, item, and quorum checks.

- [ ] **Step 3: Verify Multica delivery.**

Confirm both prompt-file and legacy dispatch paths include the locked active Skill for Zhongshu and Menxia. Do not start the service, invoke the live workflow, run tests, or send Feishu notifications.
