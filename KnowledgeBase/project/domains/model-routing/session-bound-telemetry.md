---
type: Guide
title: Model Routing - Session-bound telemetry
description: The router records token usage and route events only through the server-owned Session-Id binding: it resolves the active session to determine a workflow run and role. A client-prov...
resource: KnowledgeBase/project/domains/model-routing/session-bound-telemetry.md
tags: [model-routing, feature]
timestamp: 2026-07-19T19:55:19+08:00
managedBy: openwiki
sourceRevision: f56b64553d0eca0da65e02e120c14784e9facfe0
generatedBy: nexus-domain-knowledge-v2
---
# Session-bound telemetry

The router records token usage and route events only through the server-owned `Session-Id` binding: it resolves the active session to determine a workflow run and role. A client-provided workflow-run header is not trusted when it does not match the binding. This telemetry is written to the evaluation store and is later attached to workflow/task-run evaluation as described in [workflow runs and KnowledgeBase](../workflow-evaluation/index.md).

## Domain

- [Model Routing](index.md)
