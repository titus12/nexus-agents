---
type: Guide
title: KnowledgeBase - AI workflow context loading
description: When a workflow starts, the HTTP server attempts routing-mode retrieval using the task title. It records a compact retrieval summary—including selected document paths, token use, c...
resource: KnowledgeBase/project/domains/knowledgebase/ai-workflow-context-loading.md
tags: [knowledgebase, feature]
timestamp: 2026-07-19T19:55:19+08:00
managedBy: openwiki
sourceRevision: f56b64553d0eca0da65e02e120c14784e9facfe0
generatedBy: nexus-domain-knowledge-v2
---
# AI workflow context loading

When a workflow starts, the HTTP server attempts routing-mode retrieval using the task title. It records a compact retrieval summary—including selected document paths, token use, confidence, and loaded Markdown—in the workflow-start context. A retrieval error is represented in context and does not prevent the run from starting.

## Domain

- [KnowledgeBase](index.md)
