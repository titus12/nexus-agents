---
type: Domain
title: KnowledgeBase
description: Project knowledge scanning, Domain routing, retrieval, validation, rendering, export, and AI context loading.
resource: KnowledgeBase/project/domains/knowledgebase/index.md
tags: [knowledgebase, retrieval, domains, context, domain]
timestamp: 2026-07-19T19:55:19+08:00
managedBy: openwiki
sourceRevision: f56b64553d0eca0da65e02e120c14784e9facfe0
generatedBy: nexus-domain-knowledge-v2
routing:
  aliases:
    zh: [知识库, 知识同步, 知识检索]
    en: [knowledge base, knowledge sync, knowledge retrieval]
  keywords:
    zh: [仓库扫描, Proposal, 上下文包, 增量维护]
    en: [repository scan, proposal, context pack, incremental maintenance]
---
# KnowledgeBase

## Capabilities

- [Repository scanning and indexing](repository-scanning-and-indexing.md) - The implementation uses <projectRoot>/KnowledgeBase (internal/knowledgebase/types.go), not a design/KnowledgeBase root described in an earlier plan. It scans Markdown and lightweig...
- [Domain routing and context retrieval](domain-routing-and-context-retrieval.md) - Retrieval resolves a domain through routing aliases or textual scoring, follows routing targets, ranks heading sections with FTS/BM25 and metadata boosts, and partitions them into ...
- [AI workflow context loading](ai-workflow-context-loading.md) - When a workflow starts, the HTTP server attempts routing-mode retrieval using the task title. It records a compact retrieval summary—including selected document paths, token use, c...
- [Knowledge APIs validation and maintenance](knowledge-apis-validation-and-maintenance.md) - Knowledge APIs beneath /api/projects/{id}/knowledge provide retrieval, validation, route preview, render tree/document, maintenance, and export/refresh. Exports are cached artifact...

## Verification

- Run `go test ./internal/knowledgebase` for scan, retrieval, validation, render, and export changes.

## Related Domains

- [Runtime Platform](../runtime-platform/index.md)
- [Template Management](../template-management/index.md)
- [Workflow Evaluation](../workflow-evaluation/index.md)
