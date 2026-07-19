---
type: Domain
title: Workflow Evaluation
description: Workflow runs, session attribution, task-run submission, evaluation, telemetry, and learning-case lifecycle.
resource: KnowledgeBase/project/domains/workflow-evaluation/index.md
tags: [workflow, evaluation, task-runs, telemetry, domain]
timestamp: 2026-07-19T19:55:19+08:00
managedBy: openwiki
sourceRevision: f56b64553d0eca0da65e02e120c14784e9facfe0
generatedBy: nexus-domain-knowledge-v2
routing:
  aliases:
    zh: [工作流评估, 任务运行, 评估闭环]
    en: [workflow evaluation, task run, evaluation loop]
  keywords:
    zh: [会话归属, 遥测, 学习案例, 运行生命周期]
    en: [session attribution, telemetry, learning case, run lifecycle]
---
# Workflow Evaluation

The runtime combines lightweight workflow execution tracking with durable local evaluation records. It also retrieves a project-local `KnowledgeBase/` at workflow start when project context is available. These features are composed by the [runtime architecture](../runtime-platform/index.md) and surfaced through the [management API](../model-routing/index.md).

## Capabilities

- [Workflow-run to evaluation lifecycle](workflow-run-to-evaluation-lifecycle.md) - 1. POST /api/workflow-runs/start requires a Session-Id, creates an in-memory wf_run_* record, and binds the session to that workflow run in the active-session store.

## Verification

- Run `go test ./internal/workflowrunner ./internal/taskrunsubmit ./internal/catalog ./internal/codexrouter ./internal/httpapi` for workflow, attribution, and evaluation changes.

## Related Domains

- [Runtime Platform](../runtime-platform/index.md)
- [Model Routing](../model-routing/index.md)
- [Template Management](../template-management/index.md)
- [KnowledgeBase](../knowledgebase/index.md)
