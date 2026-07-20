---
type: Guide
title: Model Routing - Management API groups
description: internal/httpapi/server.go registers these resource families:
resource: KnowledgeBase/project/domains/model-routing/management-api-groups.md
tags: [model-routing, feature]
sourcePaths: [internal/httpapi/server.go, web/src/api.ts]
timestamp: 2026-07-19T19:55:19+08:00
managedBy: openwiki
sourceRevision: f56b64553d0eca0da65e02e120c14784e9facfe0
generatedBy: nexus-domain-knowledge-v2
---
# Management API groups

`internal/httpapi/server.go` registers these resource families:

- bootstrap, templates, projects, project configuration copies, and workflows;
- model-route listing/resolution and a Codex model probe;
- task runs, workflow-run start/finish, evaluations, learning cases, and statistics;
- local-directory browsing/picking and infrastructure catalog/install/update; and
- project KnowledgeBase retrieval, validation, rendering, maintenance, and export.

The Vue client in `web/src/api.ts` is the primary in-repository consumer and exposes typed calls for the same resource groups. Health is `GET /api/health`; the root serves the embedded console.

## Domain

- [Model Routing](index.md)
