# KnowledgeBase System Decomposition Rules

Use this reference after initial exploration. The goal is to make KnowledgeBase boundary and hierarchy decisions from evidence, not from the user's wording alone.

## Core Principle

KnowledgeBase pages reduce future search scope. The correct structure is the smallest hierarchy that lets future agents read only the relevant stable knowledge.

Do not decide that a request is broad only because of its name. First explore narrowly, then analyze evidence. If evidence shows multiple independent responsibilities, produce a decomposition plan and ask the user to choose either:

1. create/update only the root route/index; or
2. focus on one subsystem for the current KnowledgeBase entry.

## Boundary Definition

Define a boundary by stable responsibility, not by folder shape alone.

| Boundary test | Question | Split when |
|---|---|---|
| Responsibility | What stable problem does this part solve? | Two parts solve different recurring problems. |
| Entry point | Where should a future agent start reading? | They have different manager/service/component/tree/config entry points. |
| Data ownership | Who owns state and mutation? | State lifecycles or owners differ. |
| Configuration | Which config/asset/table drives it? | Config sources differ or evolve independently. |
| Runtime lifecycle | When does it start/update/end? | Lifecycles differ materially. |
| Integration surface | How do other systems call or observe it? | APIs/events/protocols differ. |
| Verification | How is it tested/debugged? | Verification paths differ. |
| Change cadence | Will this knowledge change independently? | One part changes without the other. |
| Search scope | Would one page force unrelated reads? | Future tasks need only one part. |

If at least two tests indicate separation, split the topic or route it to another domain. If only one weak test indicates separation, keep the topic together and record the uncertainty.

## Evidence to Collect Before Deciding

Before deciding the structure, collect enough evidence to fill this table:

| Evidence type | Minimum question |
|---|---|
| Code entry points | Which managers/services/components/trees are real entry points? |
| Configuration | Which assets/tables/config files drive behavior? |
| Runtime flow | Which lifecycle stages are involved? |
| Data ownership | Which objects own state and mutation? |
| Integration | Which APIs/events/protocols connect this to other systems? |
| Existing docs/KB | Is there already a domain route or source-of-truth doc? |
| Verification | How would a future agent verify/debug this topic? |

If evidence is insufficient, ask the user for more clues or propose a read-only exploration pass. Do not invent a hierarchy.

## Structure Decision Rules

Choose the smallest structure that preserves search scope:

| Structure | Use when | Do not use when |
|---|---|---|
| Single page | Evidence shows one stable responsibility, one owner domain, and one coherent read path. | The page needs unrelated flows, multiple owners, or multiple verification paths. |
| Existing page update | Existing `routing.md`, rule, pattern, or flow page already owns this knowledge. | The update would make the page cover unrelated responsibilities. |
| Two-level structure | A root route can choose among several sibling subsystems, and each subsystem can stay small. | There is only one real subsystem, or siblings belong to different existing domains. |
| Multi-level structure | Evidence shows nested clusters where one root route would still force unrelated reads. | Two levels are enough to keep pages small. |
| Cross-domain routing only | Concerns belong to existing domains such as UI, Network, Behaviour Tree, or UIArchitect. | The current domain owns stable rules that are not represented elsewhere. |

Default to updating existing routing before creating new folders. Create a new root only when it reduces future search scope.

## Decomposition Procedure

1. Name the requested topic and the user's clues.
2. Summarize evidence collected so far.
3. Identify candidate responsibilities and subsystems from evidence, not from name alone.
4. Classify each candidate:
   - owning domain;
   - stable responsibility;
   - likely entry/source-of-truth;
   - whether it is in current scope;
   - whether it needs its own future KB entry.
5. Decide the structure:
   - single page;
   - existing page update;
   - two-level root route plus subsystem pages;
   - multi-level hierarchy;
   - cross-domain routing only.
6. Decide the root route if needed:
   - update an existing domain `routing.md` when that is enough;
   - create a root folder only when it reduces future search scope;
   - do not create a root folder just because a system name sounds broad.
7. Recommend one current-pass scope:
   - root routing/index only; or
   - one subsystem page/update.
8. Ask the user to confirm the current-pass scope before deep exploration or writing.

## Decomposition Output

Use this shape in the entry plan for broad systems:

```md
## Boundary and Decomposition

Evidence-based classification: single subsystem / system cluster / cross-domain concern / unclear

Recommended KB structure: single page / existing page update / two-level / multi-level / cross-domain routing only

Recommended current-pass scope:
- root routing only / one subsystem: <name>

| Candidate responsibility/subsystem | Owning domain | Boundary evidence | Entry/source clues | Current pass? | KB action |
|---|---|---|---|---|---|
| | | | | yes/no | route/add/update/defer |

Cross-domain routes:

| Concern | Route to | Reason |
|---|---|---|
| | | |

Out of scope this pass:

- ...

User decision needed:

- ...
```

## Root Routing Rules

Create or update a root routing page only when it helps choose the next small page. The root page should contain:

- a short purpose;
- a subsystem routing table;
- cross-domain route table;
- source-of-truth map;
- explicit "do not load all subsystem pages" instruction.

The root page should not contain:

- subsystem implementation details;
- large code excerpts;
- concrete ids, values, or one-off facts;
- multiple unrelated flow explanations.

## Subsystem Page Rules

A subsystem page is appropriate when the topic has:

- a stable responsibility;
- clear code/config/source-doc entry points;
- reusable rules or pitfalls;
- a verification or debugging path;
- future tasks that can read this page without reading sibling subsystems.

If the subsystem is still too broad, repeat decomposition inside that subsystem.

## Example: Battle System

When the user asks for "battle system", do not assume the final structure from the name alone. Explore enough evidence to decide whether the current request is about one battle subsystem, a root battle routing map, or cross-domain routing.

Possible responsibilities to look for during exploration:

| Candidate subsystem | Likely owning domain | Notes |
|---|---|---|
| Battle runtime/lifecycle | gameplay | Root runtime flow and start/end lifecycle. |
| Buff/status/effects | gameplay | Independent state/effect rules. |
| Projectile/barrage | gameplay | Independent runtime and config concerns. |
| Behavior tree/monster AI | behaviour_tree | Route to behaviour tree domain, do not duplicate in gameplay. |
| Target selection/aggro | gameplay | May become its own rule/flow page. |
| Battle config | gameplay | Usually a config routing/source map. |
| Battle HUD/result UI | UI | Route to UI domain. |
| Battle protocol/sync | network | Route to network domain. |

Possible first-pass recommendations after evidence:

1. update only an existing gameplay routing page if it can route battle concerns;
2. create/update a battle root routing map if evidence shows several gameplay-owned battle subsystems;
3. choose one subsystem such as Buff, Projectile, Behavior Tree, or Battle runtime;
4. route to existing Behaviour Tree, UI, or Network domains when they already own the concern.

Do not write detailed Buff, Projectile, Behavior Tree, UI, and Network knowledge into one battle page.
