---
type: Guide
title: Runtime Platform - Runtime architecture
description: cmd/nexus-agents/main.go has two modes: the default starts the HTTP service, while submit-task-run invokes the local/HTTP task-run submission command. The service creates a codexro...
resource: KnowledgeBase/project/domains/runtime-platform/runtime-architecture.md
tags: [runtime-platform, feature]
sourcePaths: [cmd/nexus-agents/main.go, internal/httpapi/server.go, web/dist, web/src/App.vue, web/src/api.ts, web/, design/, internal/workflowrunner/runner.go, internal/httpapi/server_test.go, web/src/, ./scripts/verify_all.ps1]
timestamp: 2026-07-19T19:55:19+08:00
managedBy: openwiki
sourceRevision: f56b64553d0eca0da65e02e120c14784e9facfe0
generatedBy: nexus-domain-knowledge-v2
---
# Runtime architecture

`cmd/nexus-agents/main.go` has two modes: the default starts the HTTP service, while `submit-task-run` invokes the local/HTTP task-run submission command. The service creates a `codexrouter.Service`, writes its model catalog to the user-local Codex directory, and supplies that router to `httpapi.NewServerWithCodexRouter`.

## Composition

`internal/httpapi/server.go` is the integration boundary. It composes:

- an in-memory `catalog.Store` for template library, projects, project copies, and workflow graphs;
- an `EvaluationStore` and `ActiveWorkflowSessionStore`, with temp-file fallbacks if their default locations cannot be opened;
- a [model router](../model-routing/index.md) instrumented with a server-side recorder;
- a `workflowrunner.Runner` whose task-run submitter uses the evaluation store; and
- the `web/dist` filesystem embedded by the `web` package.

The server dispatches `/api/*` management calls and `/proxy/codex/*` proxy calls, then serves embedded static files (with an SPA fallback) for all other paths. This makes the [API and router](../model-routing/index.md) the bridge between the Vue console and the Go domains.

## UI boundary

`web/src/App.vue` is the console’s feature composition point, and `web/src/api.ts` is its HTTP client boundary. The UI covers projects, templates, workflows, infrastructure, model routes, task runs, evaluation, and project knowledge. Source changes under `web/` must be rebuilt into `web/dist` before Go tests or runtime packaging can use them.

The `design/` directory is a static prototype with fragments, mock data, and independent `verify_design.py` checks. It is an implementation reference rather than the runtime application: the HTTP server serves the bundled Vue distribution instead.

## State and durability

The catalog store is initialized from `TemplateBootstrapData()` and is process-memory state, although imported-project records and project scans use filesystem-backed project data. Workflow-run records in `internal/workflowrunner/runner.go` are also process-memory state. Evaluations, task runs, learning cases, and router telemetry use a JSON-backed evaluation store; active session bindings use a separate JSON-backed store. Treat those stores as local operational data, not transactional database services.

The [template system](../template-management/index.md) supplies the catalog’s file-backed agent, rule, skill, and workflow material. The [workflow and KnowledgeBase lifecycle](../workflow-evaluation/index.md) consumes the runtime’s runner, session, evaluation, and project-local knowledge services.

## Change guidance

- For route or endpoint changes, start in `internal/httpapi/server.go`, then follow the relevant package and `internal/httpapi/server_test.go`.
- For console changes, update `web/src/`, run the frontend build, and run the corresponding `web/tests/*.test.mjs` plus Go checks that depend on embedded assets.
- For persistence or attribution changes, inspect both evaluation and active-session behavior; token and route metrics are correlated through server-owned session bindings.
- Run `go test ./...` after cross-cutting runtime changes; `.\scripts\verify_all.ps1` is the broader Windows verification path.

## Domain

- [Runtime Platform](index.md)
