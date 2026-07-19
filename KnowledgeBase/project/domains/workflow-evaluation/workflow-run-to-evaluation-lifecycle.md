---
type: Guide
title: Workflow Evaluation - Workflow-run to evaluation lifecycle
description: 1. POST /api/workflow-runs/start requires a Session-Id, creates an in-memory wf_run_* record, and binds the session to that workflow run in the active-session store.
resource: KnowledgeBase/project/domains/workflow-evaluation/workflow-run-to-evaluation-lifecycle.md
tags: [workflow-evaluation, feature]
timestamp: 2026-07-19T19:55:19+08:00
managedBy: openwiki
sourceRevision: f56b64553d0eca0da65e02e120c14784e9facfe0
generatedBy: nexus-domain-knowledge-v2
---
# Workflow-run to evaluation lifecycle

1. `POST /api/workflow-runs/start` requires a `Session-Id`, creates an in-memory `wf_run_*` record, and binds the session to that workflow run in the active-session store.
2. `workflowrunner.Runner` snapshots start context, metrics, and evidence. It validates `projectId` and `workflowType`; the run record is process-memory only.
3. Codex proxy calls with the bound session cause the [model router](../model-routing/index.md) to record token and route telemetry against the server-resolved workflow run and role.
4. `POST /api/workflow-runs/{id}/complete` or `/fail` merges finish data, calculates duration, gathers recorded telemetry, and submits a durable task run. A finished run cannot be finished again.
5. The evaluation store holds task runs, evaluations, reviews, learning cases, proposals, token events, and route events in one JSON document. `POST /api/evaluations/run-pending` triggers deterministic rule-based evaluation; there is no automatic evaluator worker.

The CLI mode `go run .\cmd\nexus-agents submit-task-run --file <payload.json>` submits a task-run payload over HTTP by default or directly to a local evaluation store with `--use-store`. It obtains session ID from the flag, payload, or payload context, and when using the store it enriches token/route metrics from the bound session.

Evaluation’s model-judgment mode is currently `rule_only_v1`. The local stores are whole-file JSON rewrites rather than transactional database tables; active session bindings expire after their configured lifetime. Treat both details as operational constraints when adding asynchronous or concurrent workflows.

## Domain

- [Workflow Evaluation](index.md)
