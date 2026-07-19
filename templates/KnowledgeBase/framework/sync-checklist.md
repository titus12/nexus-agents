---
type: Checklist
title: KnowledgeBase Sync Checklist
description: Provides a reusable post-task checklist for deciding whether stable knowledge should be added or adjusted.
resource: KnowledgeBase/framework/sync-checklist.md
tags: [knowledge-base, framework, checklist, maintenance]
timestamp: 2026-07-19T00:00:00+08:00
---

# KnowledgeBase Sync Checklist

## Check before recommending an update

- Did the task reveal a reusable domain entry point, source map, or stable ownership boundary?
- Did it reveal a stable relation among assets, configuration, generated contracts, services, and runtime?
- Did it reveal a reusable pattern, anti-pattern, pitfall, or verification route?
- Is the result broader than one feature, one bug, one configuration value, or temporary diagnostics?
- Did the target KnowledgeBase path pass the encoding gate?
- Is `KnowledgeBase/Setting.yaml` present, reviewed, and limited to Git-tracked safe paths?
- Does the current Proposal target the exact current branch and HEAD revision?
- Did OpenWiki run in a Nexus isolated workspace rather than the active developer worktree?
- Were CodeGraph/source facts used to cross-check generated claims?
- Were external Feishu references treated as auxiliary initialization evidence only?
- Did the normalized result pass KnowledgeBase validation before application?
- Are pending Proposals excluded from stable AI retrieval?

## Incremental maintenance gates

- If HEAD is unchanged, skip OpenWiki and model calls.
- If only approved KnowledgeBase files changed, validate/export/reindex without OpenWiki.
- If docs, schemas, routes, public models, events, persistence, or service boundaries changed, generate a Proposal.
- If the previous base commit is unavailable, require a full comparison Proposal.
- Scheduled checks use committed changes only and never inspect or write the uncommitted worktree.

## Report format

Use one concise line:

```text
KB Recommendation: none - concrete task facts only.
KB Recommendation: none - one-off evidence only.
KB Recommendation: consider <target path> - reusable stable knowledge discovered.
KB Recommendation: consider <target path> - stability needs user review.
```
