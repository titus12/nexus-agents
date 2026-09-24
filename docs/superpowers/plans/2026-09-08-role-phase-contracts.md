# Role/Phase Machine Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every Orchestrator role/phase response contract explicit in one Python module, use it to generate the LLM schema and runtime validator, and remove conflicting machine-protocol rules from skills.

**Architecture:** Add seven state-specific contract modules behind a small registry. Each module owns the complete top-level and nested JSON schema, path-aware validation, prompt rules, and retry repair messages. `structured_output.py`, prompt construction, fan-in validation, and retry handling consume the registry; skills retain only semantic role guidance.

**Tech Stack:** Python 3.14, `unittest`, existing result-file transport, JSON Schema-shaped dictionaries, existing Orchestrator FSM.

---

## File map

Create:

- `cmd/orchestrator/contracts/__init__.py` — registry lookup by target state.
- `cmd/orchestrator/contracts/common.py` — schema primitives, recursive path-aware checks, and shared envelope fields only.
- `cmd/orchestrator/contracts/zhongshu_analyst.py` — `ZHONGSHU_ANALYST`, including requirement-contract, evidence-collection, and evidence-supplement modes.
- `cmd/orchestrator/contracts/zhongshu_solver.py` — `ZHONGSHU_SOLVER` initial and resume modes.
- `cmd/orchestrator/contracts/zhongshu_critic.py` — `ZHONGSHU_CRITIC` graph and one-task review modes.
- `cmd/orchestrator/contracts/menxia_item_solver.py` — `MENXIA_ITEM_SOLVER`.
- `cmd/orchestrator/contracts/menxia_item_analyst.py` — `MENXIA_ITEM_ANALYST`.
- `cmd/orchestrator/contracts/menxia_item_critic.py` — `MENXIA_ITEM_CRITIC`.
- `cmd/orchestrator/contracts/menxia_group_gate.py` — `MENXIA_GROUP_GATE`.
- `cmd/test_role_phase_contracts.py` — contract, schema-hash, strict-field, nested-field, and retry-error tests.

Modify:

- `cmd/orchestrator/structured_output.py` — replace `_ROLE_FIELDS`, `_ROLE_ACTIONS`, `_ROLE_MODES`, `_ROLE_STATES`, and `_STATE_ACTIONS` as authorities with the contract registry adapter.
- `cmd/orchestrator/app.py` — select the target-state contract for dispatch, pass contract-derived schemas to workers, and preserve all contract paths in rejection/retry events.
- `cmd/orchestrator/states.py` — replace hand-written `required_response_schema` examples and repair text with contract-derived schema and repair instructions.
- `cmd/orchestrator/zhongshu_parallel.py` — route evidence validation through the Analyst contract while preserving domain-specific requirement binding and merge checks.
- `cmd/orchestrator/parallel_runtime.py` — retain contract validation errors as structured retry context instead of collapsing them to a generic worker failure.
- `cmd/orchestrator/notifications.py` — render the bounded contract error paths in retry status without dumping raw worker replies.
- `docs/multi/runtime/zhongshu-analyst-skill.md` — remove machine field/schema/enum duplication and keep role semantics.
- `docs/multi/runtime/zhongshu-solver-skill.md` — remove machine field/schema/enum duplication and keep solver behavior.
- `docs/multi/runtime/zhongshu-critic-skill.md` — remove machine field/schema/enum duplication and keep critic behavior.

## Task 1: Lock the registry contract with failing tests

**Files:**

- Create: `cmd/test_role_phase_contracts.py`
- Read/modify only as needed: `cmd/orchestrator/contracts/__init__.py`

- [ ] **Step 1: Write registry and strictness tests**

Add tests that import `contract_for_state` and assert all seven states are
registered. Add a valid minimal payload fixture per state from the module's
`example_payload()`. Add these exact assertions:

```python
CONTRACT_STATES = (
    "ZHONGSHU_ANALYST",
    "ZHONGSHU_SOLVER",
    "ZHONGSHU_CRITIC",
    "MENXIA_ITEM_SOLVER",
    "MENXIA_ITEM_ANALYST",
    "MENXIA_ITEM_CRITIC",
    "MENXIA_GROUP_GATE",
)

def valid_payload(state: str) -> dict[str, Any]:
    return copy.deepcopy(contract_for_state(state).example_payload())

def test_all_states_have_distinct_contract_ids(self):
    contracts = [contract_for_state(state) for state in CONTRACT_STATES]
    self.assertEqual(len({contract.contract_id for contract in contracts}), 7)

def test_missing_nested_decision_relevance_is_rejected(self):
    payload = valid_payload("ZHONGSHU_ANALYST")
    payload["evidence_updates"] = [{
        "evidence_id": "ev-1",
        "requirement_id": "REQ-001",
        "source": "a.py:1",
        "conclusion": "verified",
    }]
    errors = contract_for_state("ZHONGSHU_ANALYST").validate(payload)
    self.assertIn("evidence_updates[0].decision_relevance: required", errors)

def test_legacy_relevance_alias_is_rejected(self):
    payload = valid_payload("ZHONGSHU_ANALYST")
    update = payload["evidence_updates"][0]
    update["relevance"] = "coverage"
    errors = contract_for_state("ZHONGSHU_ANALYST").validate(update)
    self.assertTrue(any("decision_relevance" in error for error in errors))
```

Use the actual project import path (`from orchestrator.contracts import ...`).
Do not use a test helper that silently fills missing fields after the test
payload is constructed.

- [ ] **Step 2: Run the focused test to verify it fails**

Run:

```powershell
rtk powershell -NoProfile -Command "python -m unittest cmd.test_role_phase_contracts -v"
```

Expected: import/registry failures because the contract package does not yet
exist.

## Task 2: Add the common contract API and seven state modules

**Files:**

- Create: `cmd/orchestrator/contracts/__init__.py`
- Create: `cmd/orchestrator/contracts/common.py`
- Create: the seven state modules listed in the file map.

- [ ] **Step 1: Implement the shared `PhaseContract` API**

Define one frozen dataclass with these fields:

```python
@dataclass(frozen=True)
class PhaseContract:
    contract_id: str
    state: str
    phase: str
    role: str
    modes: tuple[str, ...]
    actions: tuple[str, ...]
    schema: dict[str, Any]
    prompt_rules: tuple[str, ...]
    required_fields: tuple[str, ...]
    example_payload: Callable[[], dict[str, Any]]

    def validate(self, payload: Mapping[str, Any]) -> tuple[str, ...]: ...
    def repair_instructions(self, errors: Sequence[str]) -> tuple[str, ...]: ...
```

The shared validator must return all deterministic errors, use dotted/indexed
paths, enforce exact JSON types, enforce required fields, reject unknown legacy
aliases for contract-owned nested objects, and enforce `actions`, `modes`,
`phase`, `state`, and `role`. It must not normalize or repair the payload.

- [ ] **Step 2: Encode each state module from existing behavior**

Move the existing top-level fields and action/mode/state sets out of
`structured_output.py` into the matching module. Preserve existing valid
fields, then make nested structures explicit. At minimum, the Analyst contract
must require `decision_relevance` with enum
`boundary|coverage|dependency|acceptance|risk` in every evidence update and
must reject `relevance`.

The other modules must make the currently domain-validated nested objects
explicit rather than leaving them as unconstrained `array`/`object` values:

- Solver: plan items, groups, changes, finding resolutions, and finding batch.
- Critic: findings, requirement coverage, evidence alignment, required changes,
  and review checks.
- Menxia item solver: implementation proposal, files, changes, tests,
  verification, and rollback.
- Menxia item analyst: evidence, requirement trace, missing evidence, and
  verified dependencies.
- Menxia item critic: decision, findings, item/group review, required changes,
  and verification/rollback plans.
- Menxia group gate: group decision, item decisions, consistency checks, and
  remaining blockers.

Every module must expose `example_payload()` with all required fields present;
unused arrays are `[]`, optional objects are `None`, and required strings use
stable test values. No module may import another role's required-field table.

- [ ] **Step 3: Register all seven modules**

`contracts/__init__.py` must expose:

```python
def contract_for_state(state: str) -> PhaseContract:
    try:
        return _BY_STATE[str(state).upper()]
    except KeyError as error:
        raise KeyError(f"UNSUPPORTED_CONTRACT_STATE:{state}") from error
```

The registry must be the only state-to-contract map used by runtime code.

- [ ] **Step 4: Run contract tests**

Run:

```powershell
rtk powershell -NoProfile -Command "python -m unittest cmd.test_role_phase_contracts -v"
```

Expected: all registry, valid-payload, missing-field, nested-field, enum, and
alias tests pass.

## Task 3: Make structured output a registry adapter

**Files:**

- Modify: `cmd/orchestrator/structured_output.py`
- Modify: `cmd/test_role_phase_contracts.py`
- Modify: `cmd/test_analyst_contract_v31.py`

- [ ] **Step 1: Replace the duplicate authorities**

Keep `StructuredOutputSpec` and transport constants. Replace the five private
role/state dictionaries with calls to `contract_for_state`. `stable_role_fields`,
`role_modes`, `role_states`, and `state_actions` must read the selected
contract. `_schema_for` must return a deep copy of the selected contract schema
with the transport envelope fields and the exact required list.

- [ ] **Step 2: Make the schema hash cover the contract actually validated**

`build_structured_output_spec` must hash the same schema object that
`PhaseContract.validate` uses. `role_result_template` must use the contract's
required fields and example defaults. `validate_role_result_shape` must call
the contract validator and return all errors in one bounded string, while
retaining existing envelope mismatch error codes where callers depend on them.

- [ ] **Step 3: Add schema/validator identity tests**

Assert that the schema hash is stable, the schema lists
`evidence_updates.items.required` including `decision_relevance`, and a payload
accepted by the contract is accepted by `validate_role_result_shape` with the
same expected hash. Assert that removing that field fails both paths.

- [ ] **Step 4: Run structured-output and contract tests**

```powershell
rtk powershell -NoProfile -Command "python -m unittest cmd.test_role_phase_contracts cmd.test_prompt_bundle cmd.test_prompt_bundle_dispatch -v"
```

## Task 4: Wire dispatch, fan-in, and retry to the selected contract

**Files:**

- Modify: `cmd/orchestrator/app.py`
- Modify: `cmd/orchestrator/states.py`
- Modify: `cmd/orchestrator/zhongshu_parallel.py`
- Modify: `cmd/orchestrator/parallel_runtime.py`
- Modify: `cmd/orchestrator/notifications.py`
- Modify: `cmd/test_reply_retry_scope.py`
- Modify: `cmd/test_zhongshu_parallel.py`

- [ ] **Step 1: Generate prompt schemas from the target-state contract**

At each `build_structured_output_spec` and `role_result_template` call, select
the contract by target state. Remove hand-written nested response examples from
`states.py`; retain only runtime inputs and semantic working rules. Attach the
contract id, schema hash, and schema to the prompt bundle.

- [ ] **Step 2: Enforce before fan-in completion**

In `_validate_parallel_worker_reply` and the serial reply path, validate the
payload with the target-state contract before adding it to `completed`. Preserve
the existing Analyst requirement binding, then validate the bound payload. A
contract failure must produce a rejected worker with all path errors and must
never increment completed count.

- [ ] **Step 3: Pass targeted repair errors into the next prompt**

Store a bounded structured error list in `ctx.last_error` and the rejected
reply history. Change `_attach_repair_feedback` from a no-op to append only:

```text
The previous response was rejected by contract <contract_id>.
Repair only these paths and return the complete result object:
- <path>: <constraint>
Do not change analysis, scope, or unrelated fields.
```

Do not include the raw rejected result. Keep the existing retry counters and
transition behavior; after the configured limit, the final `BLOCKED` reason
must include the contract id and bounded paths.

- [ ] **Step 4: Keep domain validators, but make them subordinate**

`validate_zhongshu_evidence_packet` remains responsible for requirement
binding, evidence limits, and cross-object semantics. It must no longer be the
only place that knows the nested evidence field shape. The contract validator
runs first; domain validation runs second.

- [ ] **Step 5: Test repeated invalid fan-in and targeted retry**

Add assertions that three workers missing `decision_relevance` yield zero
completed workers, each rejection contains
`evidence_updates[0].decision_relevance`, retry prompt contains that path, and
the fourth invalid round transitions to `BLOCKED` without accepting a partial
packet. Keep existing transport/timeouts tests unchanged unless their expected
error text is now the more specific contract path.

- [ ] **Step 6: Run focused runtime tests**

```powershell
rtk powershell -NoProfile -Command "python -m unittest cmd.test_zhongshu_parallel cmd.test_reply_retry_scope cmd.test_unstructured_reply_repair cmd.test_solver_prompt_v31 cmd.test_menxia_item_prompt -v"
```

## Task 5: Remove conflicting machine protocol from skills

**Files:**

- Modify: `docs/multi/runtime/zhongshu-analyst-skill.md`
- Modify: `docs/multi/runtime/zhongshu-solver-skill.md`
- Modify: `docs/multi/runtime/zhongshu-critic-skill.md`
- Modify: prompt snapshot tests if they assert the old duplicated text.

- [ ] **Step 1: Inventory protocol-shaped text**

Search the three skills for `required`, `schema`, `decision_relevance`,
`relevance`, `action`, `mode`, JSON fences, and field tables. Classify each hit
as semantic guidance or machine contract text before editing.

- [ ] **Step 2: Remove duplicate authorities**

Delete required-field lists, enum lists, JSON response examples, and aliases.
Replace them with one semantic instruction: follow the injected contract for
the current target state exactly; if a semantic field is unclear, use the
definition in the skill but do not invent a different machine shape.

- [ ] **Step 3: Verify skill/runtime separation**

Assert the skill files contain no `relevance` alias and no competing
`required_response_schema`. Assert prompt bundles still contain the generated
contract schema and the semantic skill guidance.

## Task 6: Full self-review and verification

**Files:**

- Review all files changed by Tasks 1–5.
- Do not modify unrelated pre-existing worktree changes.

- [ ] **Step 1: Contract-chain self-review**

Trace one Analyst result from contract selection to prompt bundle, result-file
read, worker validation, fan-in, retry prompt, and final block. Repeat the
trace for one Solver and one Menxia Critic result. Confirm every path uses the
same contract id/schema hash and no shallow fallback remains.

- [ ] **Step 2: Skill-conflict self-review**

Run:

```powershell
rtk powershell -NoProfile -Command "rg -n 'required_response_schema|decision_relevance|relevance|JSON Schema|enum|must contain|required fields' docs/multi/runtime"
```

Every remaining hit must be semantic guidance or an explicit instruction to
follow the injected contract; no skill may define a competing machine shape.

- [ ] **Step 3: Run the complete relevant test set**

```powershell
rtk powershell -NoProfile -Command "python -m unittest cmd.test_role_phase_contracts cmd.test_zhongshu_parallel cmd.test_zhongshu_evidence_routing cmd.test_zhongshu_iterative_convergence cmd.test_zhongshu_task_review_queue cmd.test_solver_contract_fix cmd.test_solver_prompt_v31 cmd.test_solver_revision_prompt cmd.test_menxia_item_prompt cmd.test_menxia_parallel cmd.test_reply_retry_scope cmd.test_prompt_bundle cmd.test_prompt_bundle_dispatch -v"
```

If a live service is required for a smoke check, use a separate port such as
`18766`; do not stop or rebind the active Nexus service.

- [ ] **Step 4: Inspect the diff**

```powershell
rtk powershell -NoProfile -Command "git diff --check; git status --short"
```

Confirm only the contract migration, skill cleanup, tests, and design/plan
documents are included; do not stage or revert unrelated user changes.
