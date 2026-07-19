---
type: Guide
title: Template Management - Change guidance
description: Detailed capability, implementation boundaries, relationships, and verification guidance.
resource: KnowledgeBase/project/domains/template-management/change-guidance.md
tags: [template-management, feature]
timestamp: 2026-07-19T19:55:19+08:00
managedBy: openwiki
sourceRevision: f56b64553d0eca0da65e02e120c14784e9facfe0
generatedBy: nexus-domain-knowledge-v2
---
# Change guidance

- Change template content at its direct copied path and update paired role definitions/graph files when their contract changes.
- Run `python scripts/verify_template_catalog.py` for any template-layout or workflow/skill catalog change.
- Use `go test ./internal/catalog ./internal/httpapi` for initializer, scanner, copy, sync, and endpoint changes.
- Template deployment feeds the catalog described in [runtime architecture](../runtime-platform/index.md); installed workflows feed [workflow run tracking](../workflow-evaluation/index.md).

## Domain

- [Template Management](index.md)
