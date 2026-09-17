# Multi-Agent Solution Review Documentation

> Semantic documentation only. Machine-protocol authority is the Orchestrator
> Python contract registry under `cmd/orchestrator/contracts/`.

## 1. Purpose

This directory defines a two-phase review mechanism in which the same three
runtime agents first act as Zhongshu planners and then as Menxia reviewers.

```text
review-analyst
review-solver
review-critic
```

The system produces a reviewed implementation proposal. It does not claim that
code has been changed or tests have passed unless a separate execution workflow
actually performs and records those actions.

## 2. Authority Order

When documents disagree, use this order:

1. `cmd/orchestrator/contracts/<state>.py`
2. `cmd/orchestrator/contracts/common.py`
3. the active runtime phase-role Skill document for semantic guidance
4. this directory's prose and historical design documents

The Orchestrator MUST reject an output that cannot be normalized to the
canonical enums and artifact contracts.

## 3. Runtime Documents

| Document | Purpose |
|---|---|
| `cmd/orchestrator/contracts/*.py` | Authoritative state-specific machine envelopes, fields, types, enums, hashes, and validation. |
| `cmd/orchestrator/structured_output.py` | Contract-derived prompt schema and runtime adapter. |
| `multi-agent-solution-review-protocol.md` | Historical semantic workflow reference; not a machine-protocol authority. |
| `canonical-enums-and-schemas.md` | Historical artifact reference; not a machine-protocol authority. |
| `phase-skill-bundle-matrix.md` | Historical Skill-loading reference; not a machine-protocol authority. |
| `schemas/runtime-artifacts.schema.json` | Historical artifact baseline; state responses use the Python contracts. |
| `zhongshu-analyst-skill.md` | Zhongshu evidence and candidate-group behavior. |
| `zhongshu-solver-skill.md` | Zhongshu solution design and formal grouping behavior. |
| `zhongshu-critic-skill.md` | Zhongshu challenge and freeze recommendation behavior. |
| `menxia-analyst-skill.md` | Menxia item evidence audit behavior. |
| `menxia-solver-skill.md` | Menxia item feasibility and implementation-detail behavior. |
| `menxia-critic-skill.md` | Menxia item/group gate and scoring behavior. |

## 4. Reference-Only Documents

The following documents preserve earlier design reasoning. They are useful to
humans but MUST NOT be loaded as runtime instructions:

```text
analyst-role-design.md
solver-role-design.md
critic-role-design.md
multi-agent-role-skill-plan.md
```

If a reference-only document conflicts with the protocol or an active Skill,
the reference-only text is obsolete.

## 5. Stable Runtime Shape

```text
ZHONGSHU
  Analyst evidence/candidates
    -> Solver plan/formal groups
    -> Critic challenge
    -> bounded revision loop
    -> FrozenPlan

MENXIA
  for each group in dependency order:
    for each item in item order:
      Analyst evidence audit
        -> Solver feasibility/detail
        -> Critic item gate
        -> bounded item revision loop
    Critic group gate
  -> ReviewedPlan
  -> ApprovedPlan
```

## 6. Non-Negotiable Stability Rules

- Zhongshu and Menxia use separate sessions or clean contexts.
- A task pins all Skill versions at task creation.
- Every Agent response is schema-validated before a transition.
- FrozenPlan is immutable; Menxia writes ReviewOverlay amendments.
- Structural amendments invalidate affected downstream review results.
- Only Critic emits the authoritative numeric score.
- No unresolved P0/P1 finding may pass because of a high average score.
- HUMAN_GATE replies must include the active `decision_id`.
- State, artifacts, active gates, and consumed message IDs are persisted.
- `MAX_ROUNDS=15` counts material revision rounds, not ordinary Agent calls.
