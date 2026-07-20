---
type: Operations Runbook
title: Local build, startup, and operational guardrails
description: Practical runbook for building and starting Nexus Agents, configuring non-secret runtime variables, checking health, and operating optional knowledge services safely.
resource: /scripts/verify_all.ps1
tags: [operations, runbook, development, windows, deployment]
---
# Local build, startup, and operational guardrails

## Build and start

The backend embeds `web/dist`, so rebuild the frontend before running the integrated service after UI changes:

```powershell
cd web
npm install
npm run build
cd ..
```

Use the ignored local startup helper when available:

```powershell
.\restart_local.ps1
```

Or start manually with documented environment configuration:

```powershell
$env:NEXUS_ADDR = ":8766"
go run .\cmd\nexus-agents
```

The README documents model-provider setup, including credentials. Do not store or document live secret values in source, templates, or generated wiki pages. `NEXUS_ADDR` defaults to `:8766`.

For UI-only iteration, run `npm run dev` in `web/`; Vite proxies `/api` to `NEXUS_API_TARGET` or `http://127.0.0.1:8766`. Production differs because the Go service serves the embedded build; this is the frontend boundary described in [runtime architecture](../architecture/overview.md).

## Health and primary endpoints

After startup, check:

```text
GET /api/health
GET /api/bootstrap
GET /proxy/codex/health
GET /proxy/codex/model-catalog.json
```

The README also lists model-route resolution and template initialization endpoints. Use the console/API to inspect configured projects, infrastructure, knowledge sync, and graph status rather than relying on hidden local state.

## Knowledge-service operation

Knowledge sync is scheduled every minute for eligible projects. Its safety invariants are documented in the [knowledge platform](../domains/knowledge-platform.md): compilation reads committed Git snapshots, output becomes a reviewable proposal, and only an explicit apply changes project knowledge.

The knowledge graph starts as an optional service. Disable it with `NEXUS_KNOWLEDGE_GRAPH_ENABLED=false` when diagnosing an app or running isolated tests. Configure a managed provider only through non-secret runtime paths such as `NEXUS_GBRAIN_EXECUTABLE`, `NEXUS_GBRAIN_HOME`, and `NEXUS_GBRAIN_DATABASE_PATH`; the service should continue without a graph provider.

Graph export is derived from approved Markdown. A graph outage, queue failure, or rebuild must not make approved KnowledgeBase Markdown unavailable or cause proposal application to roll back.

## Logs and local state

Logs go to `NEXUS_LOG_DIR` or `<repo>/.logs`, arranged by date/hour, with seven days retained. Knowledge sync and graph packages maintain their own local state; catalog and portions of workflow lifecycle are process-local. Before using Nexus across users/processes, define ownership, backup, retention, and concurrency expectations—these are not supplied by the current in-memory catalog design.

The tooling/scripts are PowerShell/Windows-oriented (native folder picker and PowerShell smoke harness have Windows-specific behavior). Some Go code has non-Windows branches, but cross-platform operational support should be verified rather than assumed.

## Operational checks after changes

- UI change: rebuild `web/dist`, run frontend unit tests, then start/verify the integrated server.
- API/catalog change: exercise bootstrap plus relevant project/template routes; run package and HTTP tests.
- Router change: check model catalog, protocol conversion, streaming behavior, and evaluation telemetry.
- Knowledge/graph change: preserve isolated snapshots/proposals and run focused retrieval/sync/graph checks, including the shadow verifier when applicable.

See [testing guidance](testing.md) for commands and coverage mapping.
