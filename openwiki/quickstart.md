---
type: Project Guide
title: Nexus Agents quickstart
description: Entry point for the Nexus Agents Go and Vue control plane, covering the catalog, templates, workflow and model-routing runtime, and proposal-gated knowledge platform.
resource: /README.md
tags: [nexus-agents, quickstart, go, vue, knowledge-management]
---
# Nexus Agents quickstart

Nexus Agents is a local AI-development configuration console. It combines a Go HTTP service with an embedded Vue 3 control plane to manage agent templates and projects, model routing, workflow/evaluation records, and repository knowledge. The top-level product and startup guidance lives in [`README.md`](../README.md).

## Start here

1. Build the UI after frontend changes: `cd web; npm run build`. The Go binary embeds `web/dist` through [`web/assets.go`](../web/assets.go).
2. Start locally with `./restart_local.ps1`, or set the documented non-secret environment variables and run `go run ./cmd/nexus-agents`. The default listener is `:8766`.
3. Run the broad verification gate with `./scripts/verify_all.ps1`; see [testing guidance](operations/testing.md) before narrowing checks.
4. Avoid placing credentials in repository files. The README describes environment-variable configuration; local restart helpers are intentionally ignored because they may contain API credentials.

## What this wiki covers

- [Runtime architecture](architecture/overview.md) explains the composed Go process, embedded SPA, HTTP boundary, scheduler, and state boundaries.
- [Catalog and template management](domains/catalog-and-templates.md) explains the authoritative `templates/` library, local projects, safe initialization, and overwrite-based template sync.
- [Knowledge platform](domains/knowledge-platform.md) explains the Git-owned KnowledgeBase, isolated OpenWiki compilation, reviewable proposals, FTS5 retrieval, and optional GBrain export/shadow search.
- [Request and run lifecycle](workflows/request-and-run-lifecycle.md) connects the UI/API, workflow/evaluation records, knowledge context, and Codex protocol bridges.
- [Runbook](operations/runbook.md) collects build/start/health and environment-specific operational notes.
- [Testing guidance](operations/testing.md) maps the verification scripts and package tests to the areas they protect.

## Repository map

| Area | Primary locations | Why it matters |
| --- | --- | --- |
| Process bootstrap | `cmd/nexus-agents/main.go` | Wires catalog, router, knowledge graph/sync scheduler, logging, signal shutdown, and HTTP server. |
| Management API + SPA | `internal/httpapi/server.go`, `web/src/` | The API resource surface and the single Vue application state/UI. |
| Project/template catalog | `internal/catalog/`, `templates/` | Defines template inventory, project import/scan, initialization, sync, groups, and infrastructure operations. |
| Model proxy | `internal/codexrouter/` | Routes Responses API traffic to ChatGPT passthrough or converted provider protocols. |
| Knowledge | `internal/knowledgebase/`, `internal/knowledgesync/`, `internal/wikicompiler/`, `internal/knowledgegraph/`, `KnowledgeBase/` | Maintains reviewable repository knowledge and derived search/graph capabilities. |
| Workflow records | `internal/workflowrunner/`, `internal/taskrunsubmit/` | Tracks run lifecycle and submits evaluations with route telemetry. |
| Verification | `scripts/`, `web/tests/`, package `*_test.go` | Cross-stack quality gates and focused regression tests. |

## Recent direction

Git history shows a deliberate progression from template/workflow hardening to knowledge governance: `601aaae` introduced OpenWiki-based proposal synchronization; `80b77e7` added the GBrain/PGLite derived graph and shadow comparison; the current merge `5121d92` integrated the knowledge-base work and expanded API/UI/tests. Treat the knowledge system as active architecture, not auxiliary documentation.

## Working-tree note

This wiki was initialized while `.github/`, `AGENTS.md`, `CLAUDE.md`, and `openwiki/` were untracked. Generated pages are confined to `openwiki/`; no source or existing control/brief file was modified.

## Backlog

- **Workflow execution semantics** — `internal/workflowrunner/runner.go`: lifecycle recording/submission is documented, but node/agent execution appears to be delegated to external clients/templates and needs a product-level contract if Nexus will execute graphs itself.
- **Production persistence model** — `internal/catalog/catalog.go`, `internal/workflowrunner/runner.go`: catalog and some active-run state are process-local; durability/concurrency requirements need confirmation before multi-user deployment.
