# Role/Phase Machine Contracts Design

## Goal

Make every machine-readable response contract authoritative, explicit, and
enforceable for each Orchestrator role and phase. A response that omits a
required field, uses the wrong type, or emits an invalid enum must be rejected
before fan-in acceptance. Retries must receive the exact missing/invalid paths,
not a generic request to repeat the work.

## First-principles decision

The Orchestrator owns the protocol. It owns dispatch, acceptance, state
transitions, fan-in, retry, and blocking decisions; therefore it is the only
component that can be the authority for a machine contract.

Skills remain semantic guidance: role objective, investigation method, scope,
and field meaning. Skills must not be a second authority for required fields,
types, enum values, or nested response shape.

## Canonical layout

Create one contract module per state/phase. Each module exports the same small
interface:

```python
CONTRACT_ID: str
STATE: str
ROLE: str
PHASE: str
REQUIRED_FIELDS: tuple[str, ...]
SCHEMA: dict[str, object]
PROMPT_RULES: tuple[str, ...]

def validate(payload: Mapping[str, object]) -> tuple[str, ...]: ...
def repair_instructions(errors: Sequence[str]) -> tuple[str, ...]: ...
```

The initial modules are:

```text
cmd/orchestrator/contracts/
  __init__.py
  common.py
  zhongshu_analyst.py
  zhongshu_solver.py
  zhongshu_critic.py
  menxia_item_solver.py
  menxia_item_analyst.py
  menxia_item_critic.py
  menxia_group_gate.py
```

`common.py` contains only reusable schema primitives and path-aware validation
helpers. It does not define role-specific required fields.

## Contract responsibilities

Each phase module is the single source for:

1. top-level required fields and their types;
2. nested item requirements, including evidence updates, findings,
   requirement coverage, changes, and verification objects;
3. allowed actions, modes, and enums for that state;
4. prompt-facing schema and concise prompt rules;
5. deterministic validation with paths such as
   `evidence_updates[0].decision_relevance`;
6. targeted repair instructions for retry;
7. contract-specific unit-test fixtures.

`structured_output.py` becomes a registry/adapter. It selects the phase module,
computes the schema hash, and preserves the existing transport envelope. It
must not maintain a second shallow `_ROLE_FIELDS` or duplicate nested schema.

The dispatch code in `app.py` and prompt builders in `states.py` consume the
registry. They may add runtime context, but may not hand-write a competing
`required_response_schema`.

## Strict gate behavior

Validation occurs in this order:

1. result file can be decoded as JSON;
2. transport envelope and request metadata match;
3. phase contract validates all required top-level and nested fields;
4. state transition validates the action against the phase contract;
5. only then can fan-in count the result as completed.

For every rejection, collect all deterministic errors in one response. Do not
stop at the first missing field. Error paths must identify the exact object and
constraint, for example:

```text
evidence_updates[0].decision_relevance: required
evidence_updates[0].decision_relevance: must be one of boundary|coverage|dependency|acceptance|risk
requirements[1].requirement_id: required
```

Retry prompts contain the original task context plus only the repair contract
and error paths. They do not ask the model to redo unrelated analysis. After the
existing retry budget is exhausted, the state becomes `BLOCKED` and the final
error retains the contract id and all validation paths.

## Skill reconciliation

Review:

```text
docs/multi/runtime/zhongshu-analyst-skill.md
```

and every role skill that describes a result shape. Remove or rewrite any
machine-contract text that can drift from the Python contract, including
duplicated required-field lists, enum lists, JSON examples, and aliases such as
`relevance` for `decision_relevance`.

Keep only:

- role purpose and boundaries;
- evidence/reasoning method;
- semantic definitions of fields;
- instruction to follow the injected phase contract exactly.

The skill may reference the contract conceptually, but the runtime-injected
contract and validator are authoritative.

## Compatibility policy

There will be no silent alias acceptance for machine fields. Existing aliases
are migration errors and must be surfaced as invalid fields. Existing valid
result envelopes remain compatible where their payload already satisfies the
new phase contract.

The transport protocol name remains unchanged unless a test demonstrates that
the envelope itself must be versioned. The contract id and schema hash are
persisted in prompt bundles and result metadata for diagnosis.

## Verification

Add tests covering every contract module and the registry:

- valid minimal payload is accepted;
- each required field missing is rejected with its exact path;
- wrong type is rejected with its exact path;
- illegal action/mode/enum is rejected;
- nested evidence update without `decision_relevance` is rejected;
- legacy `relevance` is rejected rather than silently mapped;
- prompt schema and runtime validator come from the same contract hash;
- a retry receives targeted repair paths;
- repeated invalid results reach `BLOCKED` without counting as completed;
- all seven state/phase modules are registered and reachable.

Run focused contract/orchestrator tests first, then the existing test suite on a
separate validation port if a service is required. Do not alter the active
Nexus service port.

## Self-review before implementation

- Authority is singular: the phase contract module, not skill text or a prompt
  example.
- Enforcement is before fan-in acceptance, so malformed workers cannot be
  counted as completed.
- Retry feedback is actionable and path-specific.
- The design covers all currently registered role/state combinations,
  including `MENXIA_GROUP_GATE`.
- Compatibility does not preserve the alias that caused the observed failure;
  this is intentional strict gating.
- The plan preserves the transport envelope and limits changes to protocol
  ownership, validation, prompt generation, retry diagnostics, and tests.

