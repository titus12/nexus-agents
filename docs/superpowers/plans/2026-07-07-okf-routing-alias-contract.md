# OKF Routing Alias Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Agent Knowledge Routing readable in the UI and make domain matching maintainable by reading lightweight bilingual aliases from OKF frontmatter instead of relying only on hard-coded keywords.

**Architecture:** Keep SQLite FTS5 as the deterministic retrieval backbone. Add a small OKF `routing.aliases` / `routing.keywords` contract to frontmatter, validate domain routing documents for bilingual aliases, and build a per-bundle alias index that narrows search to the matched domain before FTS5 ranking. Vector search is intentionally deferred; this plan creates the metadata shape and ranking boundary needed for future domain-scoped semantic reranking.

**Tech Stack:** Go 1.26, existing `internal/knowledgebase` package, Vue 3, CSS, existing markdown frontmatter parser.

---

## File Structure

- Modify `D:\workspace\src\nexus-agents\web\src\App.vue`
  - Render routing metric cards with block-level label/value DOM so text cannot collapse into `Matched domainbehaviour_tree`.
- Modify `D:\workspace\src\nexus-agents\web\src\styles.css`
  - Style `.route-metric`, `.route-metric-label`, and `.route-metric-value`.
- Modify `D:\workspace\src\nexus-agents\web\src\types.ts`
  - Add optional matched alias metadata if exposed by retrieval.
- Modify `D:\workspace\src\nexus-agents\internal\knowledgebase\types.go`
  - Add `RoutingMetadata`, `RoutingAliases`, `RoutingAliasPair`, and `MatchedAlias` structs.
- Modify `D:\workspace\src\nexus-agents\internal\knowledgebase\frontmatter.go`
  - Parse nested `routing.aliases`, `routing.aliases.zh`, `routing.aliases.en`, `routing.aliases.pairs`, and `routing.keywords`.
- Modify `D:\workspace\src\nexus-agents\internal\knowledgebase\validate.go`
  - Add domain routing alias checks: missing aliases, missing bilingual coverage, duplicate strong aliases, broad alias warning.
- Modify `D:\workspace\src\nexus-agents\internal\knowledgebase\retrieve.go`
  - Build dynamic alias index from OKF frontmatter and prefer it over fallback aliases for domain matching.
- Modify tests under `D:\workspace\src\nexus-agents\internal\knowledgebase`
  - Cover parsing, validation, and retrieval behavior.

## Tasks

### Task 1: Fix metric DOM layout

**Files:**
- Modify: `D:\workspace\src\nexus-agents\web\src\App.vue`
- Modify: `D:\workspace\src\nexus-agents\web\src\styles.css`

- [ ] Replace inline metric cards with block-level label/value elements:

```vue
<div class="route-metric">
  <div class="route-metric-label">Matched domain</div>
  <div class="route-metric-value">{{ knowledgeRetrieval.matchedDomain || "project" }}</div>
</div>
```

- [ ] Add CSS that makes label and value separate rows:

```css
.route-metric {
  display: grid;
  gap: 8px;
}

.route-metric-label {
  display: block;
}

.route-metric-value {
  display: inline-flex;
}
```

- [ ] Run `npm run build` in `D:\workspace\src\nexus-agents\web`.

### Task 2: Add OKF routing metadata types

**Files:**
- Modify: `D:\workspace\src\nexus-agents\internal\knowledgebase\types.go`

- [ ] Add routing metadata fields to `Frontmatter`:

```go
Routing RoutingMetadata `json:"routing,omitempty"`
```

- [ ] Add supporting structs:

```go
type RoutingMetadata struct {
    Aliases  RoutingAliases `json:"aliases,omitempty"`
    Keywords RoutingKeywords `json:"keywords,omitempty"`
}
```

- [ ] Normalize nil slices in `normalizeDocument`.

### Task 3: Parse frontmatter aliases

**Files:**
- Modify: `D:\workspace\src\nexus-agents\internal\knowledgebase\frontmatter.go`
- Modify: `D:\workspace\src\nexus-agents\internal\knowledgebase\frontmatter_test.go`

- [ ] Add a test parsing:

```yaml
routing:
  aliases:
    zh: [行为树]
    en: [behaviour tree, BonsaiBT]
  keywords:
    zh: [黑板]
    en: [blackboard]
```

- [ ] Implement minimal nested YAML parsing using the existing frontmatter scanner style.

- [ ] Run `go test ./internal/knowledgebase -run TestParseFrontmatter -count=1`.

### Task 4: Validate alias contract

**Files:**
- Modify: `D:\workspace\src\nexus-agents\internal\knowledgebase\validate.go`
- Modify: `D:\workspace\src\nexus-agents\internal\knowledgebase\validate_test.go`

- [ ] Add validation for domain `routing.md` files:
  - `missing_domain_aliases` if no aliases.
  - `missing_bilingual_aliases` if aliases do not include at least one Chinese and one English token.
  - `duplicate_domain_alias` if two domains claim the same alias.
  - `broad_domain_alias` for broad words such as `状态`, `数据`, `system`, `data`, `state`.

- [ ] Run `go test ./internal/knowledgebase -run TestValidate -count=1`.

### Task 5: Use dynamic alias index in retrieval

**Files:**
- Modify: `D:\workspace\src\nexus-agents\internal\knowledgebase\retrieve.go`
- Modify: `D:\workspace\src\nexus-agents\internal\knowledgebase\retrieve_test.go`

- [ ] Build `alias -> domain` from `routing.aliases` on domain routing documents.
- [ ] Prefer dynamic alias matches before hard-coded fallback aliases.
- [ ] Keep FTS5 scoped to matched domain.
- [ ] Add a retrieval test where `行为树` only works because frontmatter declares the alias.

- [ ] Run `go test ./internal/knowledgebase -run TestRetrieve -count=1`.

### Task 6: Final validation

**Files:**
- No new files.

- [ ] Run:

```powershell
rtk powershell -NoProfile -Command "go test ./internal/knowledgebase -count=1"
```

- [ ] Run:

```powershell
rtk powershell -NoProfile -Command "npm run build"
```

from `D:\workspace\src\nexus-agents\web`.

## Self-Review

- Spec coverage: UI layout, low-maintenance aliases, routing validation, dynamic retrieval, and search scope are covered.
- Placeholder scan: no implementation placeholders are intentionally left in this plan.
- Type consistency: type names are aligned with intended Go structs and Vue fields.
