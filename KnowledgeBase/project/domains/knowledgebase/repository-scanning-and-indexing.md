---
type: Guide
title: KnowledgeBase - Repository scanning and indexing
description: The implementation uses <projectRoot>/KnowledgeBase (internal/knowledgebase/types.go), not a design/KnowledgeBase root described in an earlier plan. It scans Markdown and lightweig...
resource: KnowledgeBase/project/domains/knowledgebase/repository-scanning-and-indexing.md
tags: [knowledgebase, feature]
sourcePaths: [internal/knowledgebase/types.go, design/KnowledgeBase]
timestamp: 2026-07-19T19:55:19+08:00
managedBy: openwiki
sourceRevision: f56b64553d0eca0da65e02e120c14784e9facfe0
generatedBy: nexus-domain-knowledge-v2
---
# Repository scanning and indexing

The implementation uses `<projectRoot>/KnowledgeBase` (`internal/knowledgebase/types.go`), not a `design/KnowledgeBase` root described in an earlier plan. It scans Markdown and lightweight front matter, recognizes reserved `index.md` and `log.md`, extracts links, and builds an in-memory SQLite FTS5 search index for each retrieval.

## Domain

- [KnowledgeBase](index.md)
