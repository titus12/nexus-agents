---
type: Guide
title: KnowledgeBase - Knowledge APIs validation and maintenance
description: Knowledge APIs beneath /api/projects/{id}/knowledge provide retrieval, validation, route preview, render tree/document, maintenance, and export/refresh. Exports are cached artifact...
resource: KnowledgeBase/project/domains/knowledgebase/knowledge-apis-validation-and-maintenance.md
tags: [knowledgebase, feature]
timestamp: 2026-07-19T19:55:19+08:00
managedBy: openwiki
sourceRevision: f56b64553d0eca0da65e02e120c14784e9facfe0
generatedBy: nexus-domain-knowledge-v2
---
# Knowledge APIs validation and maintenance

Knowledge APIs beneath `/api/projects/{id}/knowledge` provide retrieval, validation, route preview, render tree/document, maintenance, and export/refresh. Exports are cached artifacts outside the source KnowledgeBase and refresh can delete/rebuild its export directory. Validation checks front matter, links, routing coverage, placeholders, aliases/rules, orphan documents, stale material, and oversized documents.

## Domain

- [KnowledgeBase](index.md)
