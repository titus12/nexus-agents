---
type: Testing Guide
title: Verification strategy and focused test map
description: Layered verification for Nexus Agents covering design checks, template catalog checks, frontend build/tests, Go package tests, smoke tests, and knowledge-graph shadow verification.
resource: /scripts/verify_all.ps1
tags: [testing, verification, go, frontend, knowledgebase]
---
# Verification strategy and focused test map

The primary repository gate is:

```powershell
.\scripts\verify_all.ps1
```

It runs design and application-scaffold checks, validates the template catalog, builds the frontend when `web/node_modules` exists, runs `go test ./...`, and then runs the PowerShell smoke test when frontend dependencies are installed. If dependencies are absent but `web/dist` is present, it uses the existing embedded build; otherwise it fails before Go tests.

This gate connects all runtime areas described in [runtime architecture](../architecture/overview.md). Prefer focused checks while iterating, then run it before integration/hand-off.

## Focused checks by change area

| Change | First checks | Why |
| --- | --- | --- |
| Vue types, API client, graph/draft helpers | `cd web; npm run test:unit`; `npm run build` | `test:unit` compiles selected modules and runs `web/tests/*.test.mjs`; build validates Vue/type/Vite integration. |
| API endpoints or SPA delivery | `go test ./internal/httpapi/...`; `scripts/smoke_app.ps1` | Covers API contracts, embedded assets/SPA fallback, bootstrap, selected project/template operations. |
| Catalog, scanning, template writes | `go test ./internal/catalog/...`; `python scripts/verify_template_catalog.py` | Protects template inventory, project scanner/provenance, initializer safety, sync exclusions. |
| Workflow lifecycle/evaluations | `go test ./internal/workflowrunner/... ./internal/taskrunsubmit/... ./internal/httpapi/...` | Checks session/run lifecycle, durable submission, and server-side telemetry enrichment. |
| Codex protocol/model route | `go test ./internal/codexrouter/... ./internal/httpapi/...` | Covers conversions, SSE/tool behavior, routing, and HTTP integration. |
| Knowledge retrieval/proposal/compiler | `go test ./internal/knowledgebase/... ./internal/knowledgesync/... ./internal/wikicompiler/...` | Protects OKF parsing/validation, FTS5/routing/context budget, snapshot isolation, proposal safety. |
| Graph export/shadow behavior | `go test ./internal/knowledgegraph/...`; `scripts/verify_p3_shadow_isolated.ps1` | Checks deterministic safe export, provider sync, and isolated FTS-versus-GBrain comparisons. |

## What the smoke test deliberately does

`scripts/smoke_app.ps1` starts the server on a random port and disables the knowledge graph so it does not disturb an active developer GBrain/PGLite process. It verifies health, assets, SPA fallback, bootstrap, model resolution, template CRUD, project import/rescan/sync, and workflow duplication. Do not remove the graph-disable behavior casually: it makes the smoke test safe for local knowledge-service state.

## Test design guidance

- Extend package tests beside the behavior they protect; most core domains already have direct `*_test.go` coverage.
- Treat tests as contract evidence when changing the recent knowledge features. The current stack intentionally relies on committed snapshots, proposal review, validated approved Markdown, and a shadow-only graph; do not simplify those contracts without updating specs, code, and tests together.
- When testing frontend changes, remember a successful dev server is not sufficient—the backend ships the built and embedded distribution.
- When a change crosses catalog, API, and UI layers, include unit/package coverage plus `verify_all.ps1`.

For build/start prerequisites and runtime-specific cautions, return to the [operations runbook](runbook.md). For domain invariants, see [catalog and templates](../domains/catalog-and-templates.md), [knowledge platform](../domains/knowledge-platform.md), and [request/run lifecycle](../workflows/request-and-run-lifecycle.md).
