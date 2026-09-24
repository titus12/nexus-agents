# Agent Role Protocol Stability Design

**Date:** 2026-09-06  
**Status:** Approved for implementation by the current task direction

## Goal

Keep one stable business-response protocol for each Agent role/Skill while allowing Zhongshu and Menxia to use different role protocols. The Orchestrator may extract the fields needed by the current FSM state, but the Agent must not switch its root response shape between normal, revision, recovery, or evidence modes.

## Design

Each role has one canonical result shape:

- Zhongshu Analyst: requirements, task proposals, evidence updates, facts, constraints, unknowns, risks, and questions.
- Zhongshu Solver: formal plan, changes, finding resolutions, unknowns, risks, and questions.
- Zhongshu Critic: plan identity, findings, review summary, evidence alignment, required changes, and blockers.
- Menxia Solver: implementation proposal, evidence, risks, verification, rollback, and questions.
- Menxia Analyst: assessment, requirement trace, evidence, missing evidence, conflicts, unknowns, and questions.
- Menxia Critic: item/group decision, findings, scores, remaining risks, and consistency results.

The root transport metadata is identical only within a role contract: task/request binding, phase, state, role, mode, protocol, and schema hash. Zhongshu and Menxia role schemas remain independent.

Mode is a required discriminator value, not a different JSON shape. Every role result includes the same role fields; fields not relevant to the current mode are emitted as an empty array or null object. The Orchestrator validates the allowed action and required business content for the current state after transport validation.

## Transport and Multica

The result-file protocol remains authoritative. The Agent writes one UTF-8 JSON object to the request-scoped result file and replies with only the compact pointer. The active Skill snapshot must be included in the Prompt Bundle for both Zhongshu and Menxia, so the exact updated Skill is delivered to Multica and recorded by its hash.

## Scope

- Remove mode-dependent schema hashes for one role.
- Normalize prompt contracts so Analyst, Solver, and Critic each have one role schema.
- Keep state-specific action allowlists and business validators.
- Update all active role Skills with the stable protocol and result-file transport rules.
- Do not merge the three roles into one schema.
- Do not run tests or send Feishu notifications in this task.
