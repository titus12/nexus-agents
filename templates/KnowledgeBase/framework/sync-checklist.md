---
type: Checklist
title: KnowledgeBase Sync Checklist
description: Provides a reusable post-task checklist for deciding whether stable knowledge should be added or adjusted.
tags: [knowledge-base, framework, checklist, maintenance]
timestamp: 2026-07-10T00:00:00+08:00
---

# KnowledgeBase Sync Checklist

## Check before recommending an update

- Did the task reveal a reusable domain entry point, source map, or stable ownership boundary?
- Did it reveal a stable relation among assets, configuration, generated contracts, services, and runtime?
- Did it reveal a reusable pattern, anti-pattern, pitfall, or verification route?
- Is the result broader than one feature, one bug, one configuration value, or temporary diagnostics?
- Did the target KnowledgeBase path pass the encoding gate?

## Report format

Use one concise line:

```text
KB Recommendation: none - concrete task facts only.
KB Recommendation: none - one-off evidence only.
KB Recommendation: consider <target path> - reusable stable knowledge discovered.
KB Recommendation: consider <target path> - stability needs user review.
```
