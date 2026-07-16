---
type: Rules
title: KnowledgeBase Scope Rules
description: Defines which stable findings belong in KnowledgeBase and which findings must remain in source, configuration, issues, or task evidence.
resource: KnowledgeBase/framework/scope-rules.md
tags: [knowledge-base, framework, scope, governance]
timestamp: 2026-07-10T00:00:00+08:00
---

# KnowledgeBase Scope Rules

## Core rule

KnowledgeBase contains low-frequency, human-reviewed knowledge that helps future tasks choose the correct source area,
understand stable ownership boundaries, and avoid repeated mistakes.

## Include

| Finding | Typical destination |
|---|---|
| Domain entry points, source maps, and stable ownership boundaries | Domain index, routing, or focused guide |
| Stable asset/config/protocol/runtime relationship | Routing or source-of-truth guide |
| Reusable pattern, anti-pattern, or pitfall | Rules, patterns, or example guide |
| Stable coding or data-flow convention | Focused rules page |
| Stable verification or debugging route | Routing, guide, or workflow knowledge |
| Reusable workflow or tool usage lesson | Framework or workflow documentation |

## Exclude

| Finding | Keep it in |
|---|---|
| One feature's behavior or one bug's evidence | Feature record, issue, or task report |
| Concrete identifiers, tuning values, timings, node values, and table rows | Source configuration and generated contracts |
| Temporary logs, diagnostics, and local workarounds | Task evidence or issue |
| Large source excerpts and complete call graphs | Source files and CodeGraph |
| Volatile implementation details | Current code or generated artifacts |

## Recommendation gate

Recommend a KnowledgeBase update only when the finding will help future similar tasks beyond the current feature or bug.
Do not write KnowledgeBase content automatically unless the user explicitly asks for a KnowledgeBase update.

## Update actions

| Action | When |
|---|---|
| Add | A reusable stable topic has no index, route, rule, pattern, or verification path. |
| Adjust | Existing knowledge misses or misroutes a stable rule, boundary, or pitfall. |
| Deprecate | Existing content is proven stale, misleading, duplicated, or contradicted by current sources. |
| No change | The finding is concrete, temporary, volatile, or implementation-specific. |
