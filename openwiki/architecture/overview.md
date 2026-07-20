---
type: System Architecture
title: Nexus Agents runtime architecture
description: Architecture of the Nexus Agents single-process Go service, embedded Vue console, HTTP resource boundary, knowledge services, and operational state.
resource: /cmd/nexus-agents/main.go
tags: [architecture, go, http, vue, runtime]
---
# Runtime architecture

Nexus is a single Go process that starts an in-memory catalog, a Codex-compatible router, optional knowledge-graph services, a knowledge-sync scheduler, and an HTTP server. `cmd/nexus-agents/main.go` is the composition root: it chooses `NEXUS_ADDR` (default `:8766`), configures logging, creates services, and coordinates signal-driven shutdown.

```text
Browser / agent client
        │
        ├── Vue control plane ──────────────┐
        │                                   │
        └── HTTP API / Codex proxy ── httpapi.Server
                                            │
              ┌─────────────────────────────┼────────────────────────────┐
              │                             │                            │
          catalog                       codexrouter              knowledge services
              │                             │                            │
      templates + project copies     external model backends  KB retrieval / sync / graph
```

## Main boundaries

- **HTTP and UI.** `internal/httpapi/server.go` owns management endpoints, health checks, proxy registration, and SPA/static serving. The service embeds the built Vue distribution through `web/assets.go`; the browser source remains in `web/src/`. The [request and run lifecycle](../workflows/request-and-run-lifecycle.md) describes the high-value API flows carried through this boundary.
- **Catalog.** `internal/catalog` owns in-process project/template/workflow inventory and infrastructure actions. It provides the project list used by the scheduler and feeds UI/API resources. Its file ownership model is documented in [catalog and templates](../domains/catalog-and-templates.md).
- **Knowledge services.** Startup creates `knowledgegraph.Service`, then injects it into `knowledgesync.Service`; a one-minute scheduler evaluates imported projects. This chain connects the runtime to the [knowledge platform](../domains/knowledge-platform.md), where approved Markdown remains canonical.
- **Model gateway.** `codexrouter.Service` is mounted alongside management endpoints and has its own request protocol/telemetry behavior. It is consumed through the [request and run lifecycle](../workflows/request-and-run-lifecycle.md).

## Lifecycle and operational state

The process creates hourly logs under `NEXUS_LOG_DIR` or `<cwd>/.logs`, retaining seven date directories. On shutdown it stops the HTTP server with a 10-second timeout and the knowledge graph with a 5-second timeout.

By default the graph provider uses GBrain process options. `NEXUS_KNOWLEDGE_GRAPH_ENABLED=0|false|no|off` selects a no-op provider. `NEXUS_GBRAIN_EXECUTABLE`, `NEXUS_GBRAIN_HOME`, and `NEXUS_GBRAIN_DATABASE_PATH` alter its process/home/database locations. The graph is intentionally optional; absence or startup failure is logged rather than preventing Nexus from listening.

State has different durability characteristics:

- The catalog and active workflow-run maps are principally in process.
- Knowledge-sync state/proposals and knowledge-graph state have local data directories managed by their packages.
- The graph is a rebuildable derivative of approved Markdown, not an authority.

This mixed model is important when changing deployment or adding multi-user behavior; see [runbook cautions](../operations/runbook.md).

## Frontend boundary

Vite serves the development frontend on port 5173 and proxies `/api` to `NEXUS_API_TARGET` (default `http://127.0.0.1:8766`). Production is different: `npm run build` materializes `web/dist`, and the Go server embeds and serves it. Therefore a frontend change must include the build step before Go tests or release builds; [testing guidance](../operations/testing.md) identifies the gate that enforces this.

## Change guide

- Add a management resource by aligning route/handler behavior in `internal/httpapi/server.go`, types/API calls in `web/src/types.ts` and `web/src/api.ts`, and presentation/state in `web/src/App.vue`.
- Change boot order, environment behavior, or shutdown only in `cmd/nexus-agents/main.go` after checking the services’ lifecycle expectations and smoke coverage.
- Keep graph failure non-fatal unless the product explicitly changes its optional/derived status; that safety posture is part of the [knowledge platform](../domains/knowledge-platform.md).
