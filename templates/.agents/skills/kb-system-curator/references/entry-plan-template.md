# KnowledgeBase Entry Plan Template

Use this template before editing `KnowledgeBase`.

````md
# <System/Feature> KnowledgeBase Entry Plan

## 1. Goal

- System/function:
- User clues:
- Desired result: create / update / decide after exploration

## 2. Existing Knowledge Context

```yaml
knowledgeRetrieval:
  query:
  matchedDomain:
  requiredPaths:
  usedTokens:
  fallbackUsed:
  error:
```

Loaded or fallback KB files:

- ...

## 3. Evidence Explored

| Type | Path / Symbol / Doc | What it proves |
|---|---|---|
| code | | |
| config | | |
| asset | | |
| protocol | | |
| doc | | |

Exploration scope:

```yaml
exploration:
  maxLevelReached: L0/L1/L2/L3
  stopReason:
  subagentsUsed: none/explorer/challenger/validation/multiple
  subagentReason:
```

## 4. Proposed KB Actions

| Action | Target file | Reason | Search-scope impact |
|---|---|---|---|
| add/update/no-change | | | |

## 5. Boundary and Decomposition

Evidence-based classification: single subsystem / system cluster / cross-domain concern / unclear

Recommended KB structure: single page / existing page update / two-level / multi-level / cross-domain routing only

Recommended current-pass scope:

- root routing only / one subsystem: `<name>`

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

## 6. Retrieval Usefulness

```yaml
retrievalUsefulness:
  findability:
  scopeReduction:
  boundaryClarity:
  actionability:
  stability:
  tokenEfficiency:
  total:
  hardFail:
  decision:
  notes:
    - ...
```

## 7. OKF Compatibility

```yaml
okfCompatibility:
  frontmatter: pass|fail|not-applicable
  resourcePath: pass|fail|not-applicable
  tags: pass|fail|not-applicable
  timestamp: pass|fail|not-applicable
  aiLoadingComment: pass|fail|not-applicable
  searchScopeGate: pass|fail
  notes:
    - ...
```

## 8. Stable Knowledge To Persist

- Entry points:
- Routing/source map:
- Stable flow:
- Rules/patterns:
- Pitfalls:
- Verification/debugging path:

## 9. Findings Not To Persist

| Finding | Reason |
|---|---|
| | one-off / volatile / concrete value / pure code fact |

## 10. Open Questions

1. ...

## 11. Edit Gates

- User confirmation required before editing.
- Read schema maintenance/sync/encoding/template files.
- Read `references/okf-checklist.md`.
- Scan target directory for mojibake.
- Write UTF-8 without BOM.
- Do not modify generated files, `.meta` files, or protected global Codex files.

## 12. Confirmation Request

Please confirm whether to apply this KnowledgeBase update to the listed target files.
````
