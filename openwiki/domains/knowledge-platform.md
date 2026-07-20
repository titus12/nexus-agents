---
type: Knowledge System
title: Proposal-gated knowledge platform
description: "The Nexus knowledge architecture: Git-owned approved Markdown, isolated OpenWiki compilation, reviewable proposals, FTS5 retrieval, and optional GBrain/PGLite derived graph synchronization."
resource: /internal/knowledgesync/service.go
tags: [knowledgebase, openwiki, retrieval, gbrain, governance]
---
# Proposal-gated knowledge platform

Nexus treats repository knowledge as a governed product artifact, not generated output that can overwrite a project. The committed `KnowledgeBase/Setting.yaml` declares `KnowledgeBase/project` as the knowledge root and uses `updateMode: proposal`. `KnowledgeBase/project/**` is the stable, project-owned corpus; its pages are the canonical source for retrieval and graph export.

## Ownership and source precedence

The design in `docs/superpowers/specs/2026-07-19-openwiki-knowledge-sync-design.md` assigns ownership deliberately:

1. Current source/configuration/contracts and CodeGraph facts win.
2. Repository-owned docs and approved KnowledgeBase pages follow.
3. Optional external initialization snapshots are auxiliary.
4. AI inference remains a review finding until accepted.

The [catalog/template boundary](catalog-and-templates.md) preserves this model by protecting `KnowledgeBase/Setting.yaml` and `KnowledgeBase/project/**` from template writes.

## From commit to approved knowledge

```text
Committed Git revision + scan policy
  → isolated archive workspace
  → pinned OpenWiki 0.2.0 candidate Markdown
  → Nexus normalization + validation + diff
  → persisted proposal for review
  → explicit apply at matching Git HEAD
  → approved KnowledgeBase/project Markdown
  → retrieval and asynchronous graph export
```

`internal/wikicompiler` creates a temporary workspace from `git archive`, restricts it to the scan manifest, runs OpenWiki at the fixed version, and reads constrained output. This protects uncommitted worktrees and prevents the compiler from directly owning project files.

`internal/knowledgesync` orchestrates profile discovery, Git checks, optional discovery sources, compilation, proposal formation, selection/apply, and state. Proposal application rejects stale target revisions, limits files/bytes, limits paths to knowledge output, defaults human pages to deselected, and rolls back partial write failures. It then validates/exports and queues graph work; graph queue failure does not undo an approved Markdown update.

## Serving retrieval and context

`internal/knowledgebase` scans Markdown/front matter, extracts links and heading sections, validates quality, routes domains, and retrieves context. Its current serving path is in-memory SQLite FTS5/BM25 with routing/metadata boosts and a token-budgeted context pack. `context_pack.go` represents the AI-facing package assembled from selected knowledge.

The [request and run lifecycle](../workflows/request-and-run-lifecycle.md) consumes that context when starting workflow records. Keep retrieval stable and test it when modifying the knowledge schema, aliases, links, or token-budget logic.

## Derived graph: optional and shadowed

`internal/knowledgegraph` exports only validated approved `KnowledgeBase/project/**` pages. The exporter rejects unsafe paths, oversized documents, likely secrets, and duplicate slugs, then rewrites internal links to graph-safe identifiers. `gbrain` provides a long-lived stdio/MCP-backed process over PGLite.

The GBrain graph is currently a derived optional service, not the source of truth. Per the approved implementation plan, SQLite FTS5 remains authoritative while shadow search records graph-versus-FTS agreement, coverage, precision, latency, timeouts, and restarts without merging graph results into production retrieval. The P3 isolated verifier (`scripts/verify_p3_shadow_isolated.ps1`) exists to exercise that migration safely.

## Change guide

- Change policy/schema/validation together: `KnowledgeBase/Setting.yaml`, `internal/knowledgebase/types.go`, `validate.go`, and tests.
- Preserve isolated-snapshot and proposal-only guarantees in compiler/sync work. Never convert OpenWiki output into direct project writes without an explicit product decision.
- Treat graph output as derived: exports must remain deterministic and safely rebuildable; retain the FTS baseline/shadow tests until a promoted retrieval decision is documented.
- Start with `go test ./internal/knowledgebase/... ./internal/knowledgesync/... ./internal/wikicompiler/... ./internal/knowledgegraph/...`, then use the [testing guide](../operations/testing.md).
