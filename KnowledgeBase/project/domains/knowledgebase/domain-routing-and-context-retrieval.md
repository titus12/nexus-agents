---
type: Guide
title: KnowledgeBase - Domain routing and context retrieval
description: Retrieval resolves a domain through routing aliases or textual scoring, follows routing targets, ranks heading sections with FTS/BM25 and metadata boosts, and partitions them into ...
resource: KnowledgeBase/project/domains/knowledgebase/domain-routing-and-context-retrieval.md
tags: [knowledgebase, feature]
timestamp: 2026-07-19T19:55:19+08:00
managedBy: openwiki
sourceRevision: f56b64553d0eca0da65e02e120c14784e9facfe0
generatedBy: nexus-domain-knowledge-v2
---
# Domain routing and context retrieval

Retrieval resolves a domain through routing aliases or textual scoring, follows routing targets, ranks heading sections with FTS/BM25 and metadata boosts, and partitions them into required, optional, and related context under token limits. Chinese/CJK queries may use configurable OpenAI-compatible query rewrite; rewrite failure retains the original terms rather than failing retrieval.

## Domain

- [KnowledgeBase](index.md)
