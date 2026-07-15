---
type: Rules
title: KnowledgeBase Source of Truth
description: Defines how KnowledgeBase, CodeGraph, source code, generated artifacts, configuration, assets, and task evidence divide ownership.
tags: [knowledge-base, framework, source-of-truth, codegraph]
timestamp: 2026-07-10T00:00:00+08:00
---

# KnowledgeBase Source of Truth

## Ownership

| Information | Primary source | KnowledgeBase role |
|---|---|---|
| Current symbols, implementations, callers, and callees | CodeGraph and source code | Provide stable query entry points; do not copy full call graphs. |
| Generated protocol and configuration contracts | Generated source and runtime data | Record lookup paths and ownership boundaries; do not hand-edit or duplicate values. |
| Asset, scene, generated, or external-tool wiring | Owning project files and source tools/editors | Record verification routes; validate exact wiring in the owning tool. |
| Stable architecture, domain boundaries, patterns, pitfalls, and verification paths | KnowledgeBase | Maintain concise, reusable guidance. |
| Detailed rationale and design history | Source design documents and ADRs | Link to the source document rather than copying it. |
| One feature, incident, log, or temporary workaround | Feature records, issues, and task evidence | Exclude from stable KnowledgeBase content. |

## Conflict rule

When KnowledgeBase conflicts with current source, generated contracts, configuration, or assets, treat the current
authoritative source as correct. Correct or deprecate the stale KnowledgeBase guidance after evidence is confirmed.

## CodeGraph boundary

Use KnowledgeBase to choose the domain, source area, and verification route. Use CodeGraph to inspect current code facts.
Do not make KnowledgeBase a second code graph.
