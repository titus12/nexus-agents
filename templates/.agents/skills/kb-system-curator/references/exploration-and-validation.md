# Exploration Scope and Retrieval Usefulness Validation

Use this reference to keep exploration bounded and to judge whether a proposed KnowledgeBase update helps future AI retrieval.

## Exploration Radius

Expand in levels. Do not jump to broad repository scans.

| Level | Scope | Purpose |
|---|---|---|
| L0 User clues | User-provided names, paths, symbols, prefabs, configs, docs, protocols, or tickets. | Locate the smallest likely entry. |
| L1 Direct adjacency | Direct callers/callees, referenced configs/assets, direct services/events/protocols, nearest existing KB routes. | Build the first evidence map. |
| L2 Boundary confirmation | Same lifecycle, same configuration chain, same state owner, same verification path, or same domain route. | Decide boundary and structure. |
| L3 Sibling mapping | Nearby sibling subsystems discovered by evidence. Map only names, owners, entry clues, and domain routes. | Decide root routing or cross-domain routing without deep-diving every sibling. |

L3 is for routing and decomposition only. Do not collect detailed implementation knowledge for every sibling in one pass.

## Stop Conditions

Stop exploration when one of these is true:

- the entry points and source-of-truth paths are clear;
- single page / existing page update / two-level / multi-level / cross-domain routing can be decided;
- stable knowledge and volatile code facts can be separated;
- further search would only collect implementation details better left to CodeGraph/source;
- more progress requires user business judgment;
- the next useful step is validation, not more exploration.

Record the exploration level reached and the stop reason in the entry plan.

## Subagent Use

Do not use subagents by default.

Use one challenger or validation subagent only when subagents are available and allowed by the current agent policy/user request, and at least one trigger is true:

- the structure is uncertain after L2;
- existing KB conflicts with code or source docs;
- a new root route or new subdomain is proposed;
- the update crosses domains;
- the user requests high-confidence curation.

Use two subagents only when subagents are available and allowed, and the curation is large or high-risk:

- Explorer: independently maps entries, candidate responsibilities, domains, configs, and source docs.
- Challenger: reviews for over-splitting, under-splitting, wrong domain ownership, missing existing-page reuse, and search-scope harm.

Subagents provide evidence, not final truth. The main agent must reconcile conflicts and produce one entry plan.

If subagents are not available or not allowed, do not block the workflow. Run the rubric self-check, record `subagentsUsed: none`, and explain the skipped validation.

## High-Risk Curation

Treat a KnowledgeBase update as high-risk when any of these are true:

- it creates a new root routing page, subdomain, or multi-level hierarchy;
- it changes an existing `routing.md` used by multiple task types;
- it routes across domains such as UI, Network, Behaviour Tree, UIArchitect, or Gameplay;
- it resolves a conflict between code, source docs, and existing KB;
- it will likely become a common entry point for future agents;
- the decomposition has unresolved but acceptable uncertainty.

## Fresh Validation Agent

Use a fresh validation agent when available and allowed for high-risk KB updates, especially new root routes, multi-level structures, or cross-domain routes.

Give the validation agent only:

- the proposed KB content or entry plan;
- likely user queries;
- this scoring rubric.

Do not provide the full exploration transcript unless the validation task is explicitly about evidence audit. The goal is to test whether the KB artifact itself is useful.

## Retrieval Usefulness Rubric

Score each dimension from 0 to 3.

| Dimension | Question | 0 | 1 | 2 | 3 |
|---|---|---|---|---|---|
| Findability | Can likely user wording find the KB route? | common queries miss it | requires path guessing | routing can find it | aliases/tags/routes make it easy |
| Scope Reduction | Does it reduce what the next agent reads? | expands scope | somewhat narrows | narrows to subsystem | narrows to entry/rule/verification path |
| Boundary Clarity | Are scope, out-of-scope, and cross-domain routes clear? | unclear | partial | mostly clear | explicit and hard to misuse |
| Actionability | Does it tell the next agent what to inspect or verify? | conceptual only | lacks entries | has entries/rules | has entries, rules, and verification |
| Stability | Is it stable knowledge rather than volatile facts? | mostly volatile | mixed | mostly stable | stable routes/rules only |
| Token Efficiency | Can it be loaded selectively? | long/full-read | sectioned but broad | mostly concise | index-first and minimal |

Maximum score: 18.

## Quality Gate

| Score | Decision |
|---:|---|
| 15-18 | Good to persist after user confirmation. |
| 12-14 | Persist only after small revisions are addressed or explicitly accepted. |
| 9-11 | Revise before writing. |
| 0-8 | Do not persist. |

Hard failures:

- Scope Reduction = 0;
- Boundary Clarity = 0;
- Stability = 0.

If any hard failure occurs, revise the plan before asking the user to confirm a KnowledgeBase edit.

## Validation Output

Include this compact block in the entry plan:

```yaml
retrievalUsefulness:
  findability: 0-3
  scopeReduction: 0-3
  boundaryClarity: 0-3
  actionability: 0-3
  stability: 0-3
  tokenEfficiency: 0-3
  total: 0-18
  hardFail: true|false
  decision: good|minor-revision|revise|do-not-persist
  notes:
    - ...
```
