---
type: Guide
title: Runtime Platform - Project overview
description: Nexus Agents is an AI development-configuration control plane. The shipped application is a Go HTTP service with an embedded Vue 3/Vite console. It manages file-backed Agent, Rule,...
resource: KnowledgeBase/project/domains/runtime-platform/project-overview.md
tags: [runtime-platform, feature]
sourcePaths: [web/, design/, web/dist, web/src, KnowledgeBase/, powershell
./scripts/verify_all.ps1, web/node_modules]
timestamp: 2026-07-19T19:55:19+08:00
managedBy: openwiki
sourceRevision: f56b64553d0eca0da65e02e120c14784e9facfe0
generatedBy: nexus-domain-knowledge-v2
---
# Project overview

Nexus Agents is an AI development-configuration control plane. The shipped application is a Go HTTP service with an embedded Vue 3/Vite console. It manages file-backed Agent, Rule, Skill, and Workflow templates; project copies and synchronization; workflow-run evaluation; per-project KnowledgeBase retrieval; and a local Codex-compatible model proxy.

## Start here

1. Build the embedded web assets after changing `web/`:

   ```powershell
   cd web
   npm install
   npm run build
   cd ..
   ```

2. Set only the required environment variables in your local shell—do not put credentials in repository files—then run:

   ```powershell
   $env:NEXUS_ADDR = ":8766" # optional; this is the default
   go run .\cmd\nexus-agents
   ```

   API-key model routes read `DEEPSEEK_API_KEY`; base URLs and display metadata have non-secret `NEXUS_*` overrides in the router. The server also writes its generated Codex model catalog under the current user's `.codex` directory.

3. Open `http://127.0.0.1:8766/`; `GET /api/health` reports service health.

The static HTML prototype in `design/` is independently previewable and verified, but it is not the runtime UI. The running service embeds `web/dist` and uses the TypeScript source under `web/src`.

## Core concepts

- [Runtime architecture](../runtime-platform/index.md) explains the Go entrypoint, embedded UI, HTTP composition, state boundaries, and the relationship between the template catalog and runtime services.
- [API and model routing](../model-routing/index.md) explains the management API, Codex proxy protocols, model families, and route/token telemetry.
- [Template system](../template-management/index.md) explains the direct project-root layout for agents, rules, skills, and workflows; safe initialization versus full synchronization; and template-policy cautions.
- [Workflow runs and KnowledgeBase](../workflow-evaluation/index.md) explains how workflow execution becomes evaluated task-run data and how a project-local `KnowledgeBase/` is retrieved, validated, rendered, and exported.

## Verification

Run the repository-wide Windows verification path:

```powershell
.\scripts\verify_all.ps1
```

It runs design, application-scaffold, and template-catalog validators; builds the web UI when `web/node_modules` is present (otherwise it requires an existing `web/dist`); runs `go test ./...`; and runs the browser smoke script only when Node dependencies are present. For a focused change, use the relevant Go package tests alongside `python scripts/verify_template_catalog.py` for template-layout changes.

## Snapshot boundary

This documentation reflects the supplied security-isolated snapshot at committed revision `f56b64553d0eca0da65e02e120c14784e9facfe0`. The snapshot intentionally omits `.git`; this does not describe a limitation of the source repository.

## Backlog

No deferred areas: the required agents, API, architecture, design boundary, Go runtime, KnowledgeBase, templates, and workflow topics are covered in the pages above.

## Domain

- [Runtime Platform](index.md)
