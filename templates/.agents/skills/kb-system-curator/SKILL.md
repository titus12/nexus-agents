---
name: kb-system-curator
description: Explore a project system or feature and curate stable reusable findings into a user-approved, OKF-compatible KnowledgeBase update. Use when the user asks to document, curate, create, update, move, structure, route, validate, or persist core code, configuration, conventions, and verification knowledge into KnowledgeBase.
---

# KB System Curator

Use this skill to turn a system or feature investigation into a controlled KnowledgeBase update. The output is first an entry plan; edit `KnowledgeBase` only after the user confirms the plan. Adapt project-specific commands and paths to the current repository; do not assume a Unity, Go, or BTD project.

## Hard Gates

- Reply in Chinese unless the user explicitly asks otherwise.
- Start with Nexus Knowledge Retrieval for this repository before browsing `KnowledgeBase` locally.
- If retrieval is unavailable, record `fallbackUsed=true` and the exact error, then use local KnowledgeBase routing as fallback.
- Do not recursively load all KnowledgeBase files. Load index/routing first, then only the smallest matching page.
- Do not edit KnowledgeBase files before the user confirms the entry plan.
- Before any KnowledgeBase edit, read the applicable framework scope, document-contract, quality, and sync rules.
- Before any KnowledgeBase edit, read `references/okf-checklist.md` and ensure proposed KnowledgeBase pages are OKF-compatible.
- Before an entry plan and again before every KnowledgeBase edit, run `scripts/validate_kb_structure.py --root KnowledgeBase --project-dir project --flat-threshold 8 --json`.
- If the validator reports a flat project structure that requires domains, do not add another top-level project concept page. Propose a user-approved migration first.
- If `KnowledgeBase/project/domains/` exists, create or move project concept pages only under a selected `domains/<domain>/` directory and update its `index.md`.
- Before any KnowledgeBase edit, scan the target KnowledgeBase path for mojibake or replacement characters and stop if found unless the user explicitly accepts the risk.
- Do not copy large source excerpts, full design docs, logs, or volatile code facts into KnowledgeBase.
- Do not modify generated files, Unity `.meta` files, or protected global Codex files.
- When Nexus Knowledge Sync APIs are available, prefer discovery, isolated OpenWiki compilation, and a Nexus Proposal over directly authoring broad KnowledgeBase changes.
- Never treat a pending Nexus Proposal as stable retrieved knowledge.
- Preserve the team-owned `KnowledgeBase/Setting.yaml` and project-owned `KnowledgeBase/project/**` boundaries.

## Required Inputs

Normalize the user's request into:

| Field | Meaning |
|---|---|
| System/function | The named system, module, feature, workflow, or behavior to curate. |
| Optional clues | Paths, symbols, prefabs, assets, protocols, configs, docs, tickets, or prior findings. |
| Desired action | Create new KB, update existing KB, or propose after exploration. |
| Confirmation state | Whether the user has approved writing to KnowledgeBase. |

If the same name can refer to multiple systems or domains, ask one concise clarification question before broad exploration.

## Workflow

### 0. Prefer Nexus Knowledge Sync for broad or repository-wide curation

Use Nexus Knowledge Sync when the request involves initial KB generation, repository-wide scanning, stale-knowledge checking, or multi-file enrichment:

1. Review or generate `KnowledgeBase/Setting.yaml`.
2. Run initialization or check-and-enrich to create a Proposal.
3. Review generated paths, evidence, validation, and before/after diffs.
4. Apply selected files only after explicit approval.
5. Continue with the narrow manual curation workflow below only for unresolved, human-managed, or high-context pages.

OpenWiki is a compiler, not the source of truth. Current source and CodeGraph facts remain authoritative.

### 1. Retrieve existing knowledge first

Prefer `$nexus-knowledge-retrieval` so the helper resolves the exact Nexus Project
from the current repository path and Nexus infers its Project Group. Query with
the system/function name and clues, mode `context`, maxTokens `6000`.

Record:

```yaml
knowledgeRetrieval:
  query: "<system/function and clues>"
  matchedDomain: "<from retrieval or inferred fallback>"
  requiredPaths: []
  usedTokens: null
  fallbackUsed: false
  error: null
```

If retrieval cannot be called from the current environment, set `fallbackUsed=true`, record the reason, then read only:

1. `KnowledgeBase/index.md`
2. `KnowledgeBase/project/index.md`
3. `KnowledgeBase/project/routing.md`
4. `KnowledgeBase/project/domains/index.md` when it exists
5. the matched domain `index.md`
6. the selected target page
7. framework rules only when preparing an edit

### 2. Run structure preflight and propose an initial domain

Read `references/structure-and-migration-gates.md` when the topic belongs to project knowledge. Run the structure validator before proposing a write target.

- If the validator passes and `project/domains/` exists, select the narrowest existing domain before code exploration.
- If the validator reports `flat_project_requires_domains`, stop content planning and propose only a structure migration.
- If no existing domain owns the topic, propose the smallest new domain/index structure and wait for confirmation before creating it.
- Do not create a new concept page directly under `KnowledgeBase/project/` when `domains/` exists.

Then use existing routing only as an initial exploration guide, not as the final KnowledgeBase structure decision.

| Signal | Likely target |
|---|---|
| project/workflow/AI routing | `KnowledgeBase/project/*` or `KnowledgeBase/framework/*` |
| UI View/ViewModel/Service/DataEvents | `KnowledgeBase/project/domains/ui/*` |
| UIArchitect/PSD/import/generator/prefab extraction | `KnowledgeBase/project/domains/uiarchitect/*` or source AIConfig route |
| behaviour tree/monster/boss/hero AI | `KnowledgeBase/project/domains/behaviour_tree/*` |
| protocol/network/service message flow | `KnowledgeBase/project/domains/network/*` |
| non-UI gameplay runtime/config | `KnowledgeBase/project/domains/gameplay/*` |

At this stage, do not decide whether the final KB output is a single page, two-level structure, multi-level structure, or cross-domain routing. Make only a provisional route so exploration can begin narrowly.

### 3. Explore code/config/docs narrowly

Use `references/exploration-and-validation.md` to control exploration radius. Start from user clues, expand only as needed, and stop when the KnowledgeBase structure can be decided.

Use CodeGraph for code facts when available. Otherwise use precise search by symbol/path/clue.

Collect only evidence needed to decide stable knowledge:

- primary entry points and owner files;
- asset/config/protocol/runtime relationships;
- stable service/cache/event/data flow boundaries;
- reusable conventions, patterns, anti-patterns, and pitfalls;
- verification or debugging paths;
- source-of-truth docs and generated-contract locations.

Avoid broad directory scans. Do not load all examples, all generated files, all services, or all protocol files.

### 4. Analyze boundaries and decomposition after exploration

After collecting initial evidence, read `references/decomposition-rules.md` and decide the KnowledgeBase structure from the evidence. Do not rely on the user's wording alone.

The decomposition analysis must decide:

- whether the explored topic is one stable subsystem, a system cluster, or still unclear;
- whether the KB structure should be a single page, two-level root route plus subsystem pages, multi-level hierarchy, or cross-domain routing only;
- which stable responsibilities belong together and which should split;
- which domain owns each subsystem or concern;
- whether an existing `index.md`, `routing.md`, rule page, pattern page, or flow page should be updated instead of creating a new page;
- which one scope is safe for the current write.

If the evidence indicates multiple subsystems or multiple layers, produce a decomposition plan and ask the user to confirm the current-pass scope before deeper exploration or writing. Do not persist detailed knowledge for multiple independent subsystems in a single pass.

For project knowledge, also decide the placement:

- existing `domains/<domain>/` concept page;
- existing domain route/index update;
- a user-approved new domain with its own `index.md`;
- a user-approved structural migration before feature knowledge is written.

Read `references/structure-and-migration-gates.md` before deciding to add, move, or split project pages.

### 5. Decide whether cross-checking or validation is needed

Use `references/exploration-and-validation.md` before the final entry plan when:

- the explored topic spans multiple domains;
- evidence supports more than one valid structure;
- a new root route, new subdomain, or multi-level structure is proposed;
- existing KnowledgeBase conflicts with code or source docs;
- the user asks for high-confidence curation.

Do not use subagents by default. Use them only when the current environment and user instructions allow subagents and one of the listed trigger conditions applies. Subagents provide evidence; the main agent must reconcile results and produce the final entry plan.

When a proposed KnowledgeBase update is ready, always evaluate retrieval usefulness with the scoring rubric in `references/exploration-and-validation.md`. For high-risk updates, use a fresh validation agent when available and allowed; otherwise perform the rubric self-check and report that no subagent validation was run.

### 6. Ask on uncertainty

Pause and ask the user when:

- multiple domains or systems match;
- code and existing KB/design docs conflict;
- stability is unclear;
- old/new entry points both exist;
- an existing KB page appears stale;
- the evidence supports more than one valid decomposition;
- it is unclear whether to use a single page, two-level structure, multi-level structure, or cross-domain routing;
- the write target or update type would change existing guidance materially.

Ask one key question at a time. Present options and a recommendation when possible.

### 7. Filter stable knowledge

Use this decision table:

| Finding | KB action |
|---|---|
| domain entry point or source map | include in routing/code map |
| stable asset/config/protocol/runtime relation | include in routing/rules/flow |
| reusable pattern or anti-pattern | include in pattern/rules/example index |
| stable coding/data-flow convention | include in domain rule page |
| stable verification/debugging path | include in routing/workflow/rules |
| single feature behavior | do not include; keep in report/Feature Card |
| concrete id/value/node name | do not include unless it is itself a stable route |
| temporary log/diagnostic | do not include |
| pure call graph fact | usually do not include; rely on CodeGraph |
| large source or design excerpt | do not include; cite source path instead |

## KnowledgeBase Shape and Search-Size Rules

KnowledgeBase exists to reduce search scope. Keep pages index-first and section-addressable.

- Put simple directory navigation in `index.md`; use `routing.md` only for non-trivial task decisions.
- New concept documents should follow `KnowledgeBase/framework/document-contract.md`.
- Every new or materially updated non-reserved Concept Document should have:
  - YAML frontmatter with `type`, `title`, `description`, `resource`, `tags`, `timestamp`;
  - one `#` title;
  - an AI loading comment such as `<!-- AI:ROUTING - ... -->` or `<!-- AI:RULES - ... -->`;
  - short sections with `##` headings;
  - routing/read-when tables for selective loading;
  - source-doc tables instead of copied source content.
- Use `references/okf-checklist.md` to validate frontmatter, resource paths, tags, timestamp format, index-first comments, and search-scope shape.
- Reserved `index.md` and `log.md` files follow the OKF/document-contract rules and must not receive YAML frontmatter.
- For long topics, split by stable access pattern:
  - `routing.md` for choosing the next file;
  - `coding_rules.md` for conventions;
  - `data_flow_rules.md` or equivalent for flow boundaries;
  - `feature_patterns.md` for reusable patterns;
  - `examples/index.md` plus one example page per canonical example.
- Link to exact source paths and symbols. Do not paste large code blocks.
- If one page would require agents to read unrelated sections, split it or add a routing table.
- Examples are optional and should be loaded one at a time by an examples index.

## Entry Plan Before Editing

Before writing, present an entry plan using `references/entry-plan-template.md`. The plan must state:

- target domain and existing KB context;
- evidence explored;
- exploration radius and stop reason;
- proposed add/update/no-change actions;
- decomposition decision and structure;
- retrieval usefulness score;
- OKF compatibility result;
- exact files to edit or create;
- stable findings to include;
- findings intentionally excluded;
- open questions and user decisions;
- encoding and maintenance gates to run.
- structure-preflight result, selected domain path, and all affected parent indexes;
- for moves, the old-to-new path mapping and relative-link rebasing scope.

End with a clear confirmation request. Do not edit until the user confirms.

## Edit Procedure After Confirmation

1. Read the confirmed target files and required framework files:
   - `KnowledgeBase/framework/scope-rules.md`
   - `KnowledgeBase/framework/sync-checklist.md`
   - `KnowledgeBase/framework/quality-gates.md`
   - `KnowledgeBase/framework/document-contract.md`
   - `references/okf-checklist.md`
2. Read `references/structure-and-migration-gates.md` when adding, moving, or restructuring project knowledge.
3. Run the structure validator. Stop on validation errors unless the confirmed scope is the corresponding structure migration.
4. Run a narrow mojibake scan over the target KB directory for replacement characters and common broken smart-quote sequences.
5. If scan fails, stop or ask user to acknowledge/fix before merging new content.
6. For a move, write and validate the new page before removing the old page; update `resource`, parent/domain navigation, all affected indexes, Markdown links, and rebased source/local links.
7. Write new/updated Markdown as UTF-8 without BOM.
8. Validate OKF compatibility before finalizing.
9. Re-run the structure validator; require no broken links, orphan project documents, missing indexes, or metadata/encoding failures.
10. Re-scan edited files. Replay an existing keyword corpus after a structural migration, or record a small new corpus in task evidence.
11. Report exact changed files and current-turn evidence.

## Final Report

Use this structure:

```text
Complete:
- ...

Changed files:
- ...

Evidence:
- knowledgeRetrieval: ...
- Explored code/config/docs: ...

Not persisted:
- ..., reason: ...

Gates:
- User confirmation: yes/no
- Encoding scan: passed/failed/not run
- Structure validator: passed/failed/not run
- Domain/index placement: <selected domain and updated indexes, or not-applicable>
- Did not modify generated/.meta/protected files: yes

Next suggestions:
- ...
```
