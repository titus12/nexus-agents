---
type: Workflow Guide
title: Requests, model routing, and workflow-run lifecycle
description: How Nexus connects its Vue/API control plane to workflow records, knowledge context, task-run submission, telemetry, and Codex-compatible model protocol bridges.
resource: /internal/httpapi/server.go
tags: [workflow, api, codex, telemetry, evaluation]
---
# Requests, model routing, and workflow-run lifecycle

`internal/httpapi/server.go` is the integration boundary for the management console, project/template resources, knowledge operations, workflow/evaluation APIs, and the Codex proxy. The Vue client (`web/src/App.vue`, `api.ts`, `types.ts`) mirrors this resource model; it is composed into the process described by [runtime architecture](../architecture/overview.md).

## Management control plane

The Vue application is a single large composition surface for projects, templates, workflow canvas editing, infrastructure, model routes/proxy testing, evaluations, and knowledge sync/retrieval. `web/src/workflow-graph.ts` contains client-side immutable graph edits/presets; persistence and authorization-relevant decisions remain server-side.

Catalog-backed API flows—project import/rescan/grouping, template initialization/sync, and graph persistence—are explained in [catalog and templates](../domains/catalog-and-templates.md).

## Workflow record to evaluation

The workflow runner records lifecycle rather than acting as a graph-node executor:

1. `POST /api/workflow-runs/start` requires a `Session-Id`, creates an in-memory run, binds the session, and retrieves knowledge context from task/project data.
2. Completion or failure merges run context, metrics, and evidence; calculates duration; then submits a durable task-run result through `internal/taskrunsubmit`.
3. Direct task-run submission also enriches route/token metrics from server-side session/run telemetry rather than trusting those caller fields.

This connects the [knowledge platform](../domains/knowledge-platform.md) to evaluation artifacts: workflow records carry context derived from approved knowledge, while proxy observations contribute runtime telemetry.

**Caveat:** repository evidence shows lifecycle recording/submission, not internal execution of every workflow graph node or agent. Treat actual execution as an external client/template responsibility unless a later contract establishes an executor.

## Codex-compatible proxy

The model gateway accepts `POST /proxy/codex/v1/responses` and dispatches based on the configured model route:

- GPT/Codex routes pass the Responses protocol and subscription bearer authentication through to the ChatGPT Codex backend.
- DeepSeek/GLM-style routes transform Responses requests into OpenAI-compatible Chat Completions and transform responses back into the complete Responses SSE event stream expected by Codex.
- Claude routes use an Anthropic Messages conversion path.

`internal/codexrouter/convert.go` carries instructions/history/tools/usage conversion; `tools.go` maps function and custom tools (including apply-patch/tool search handling); `anthropic.go` is the provider-specific bridge. Model routes and their developer setup are documented in `README.md`.

Router telemetry is attributed by workflow run/role from headers/session lookup and is incorporated during task-run evaluation. That makes protocol changes an evaluation-data compatibility concern, not merely a proxy change.

## Change guide

- For a new API resource, align handler, server types, `web/src/api.ts`, `web/src/types.ts`, and UI state; add HTTP tests in `internal/httpapi/server_test.go`.
- For workflow record changes, test session binding, lifecycle transitions, evidence/metrics merge, and task submission in `internal/workflowrunner/runner_test.go` and `internal/taskrunsubmit/submit_test.go`.
- For proxy changes, preserve full Responses SSE lifecycle events, tool mappings, history limits, and telemetry attribution; use `internal/codexrouter/router_test.go`.
- Run the relevant focused tests and then the [verification guide](../operations/testing.md).
