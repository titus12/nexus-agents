---
type: Rules
title: KnowledgeBase Quality Gates
description: Defines mandatory quality checks for encoding, OKF structure, navigation, links, duplication, and final review.
resource: KnowledgeBase/framework/quality-gates.md
tags: [knowledge-base, framework, quality, okf]
timestamp: 2026-07-10T00:00:00+08:00
---

# KnowledgeBase Quality Gates

## Encoding gate

- Scan the target path for the Unicode replacement character and obvious mojibake before editing.
- Block the edit until unreadable text is repaired or the user explicitly accepts the risk.
- Write new or rewritten Markdown as UTF-8 without BOM.
- Re-scan every edited file before reporting completion.

## OKF structure gate

- `index.md` and `log.md` have no YAML frontmatter.
- Every other Markdown concept document has parseable YAML frontmatter and a non-empty `type`.
- Every knowledge directory has an `index.md` that lists its direct concepts or child directories.
- Every non-index concept is reachable from a parent index; do not leave orphan pages.

## Navigation and content gate

- Index files remain concise and support progressive disclosure.
- Keep `routing.md` only for non-trivial task decisions; do not duplicate a simple index list.
- Link to the smallest matching source document or symbol rather than copying large source excerpts.
- Route cross-domain concerns instead of duplicating their rules.
- Keep current call relationships in CodeGraph or source search, not in stable KnowledgeBase pages.

## Final review gate

- Verify Markdown links and moved resource paths.
- Check frontmatter, timestamps, and tags on edited concept documents.
- Check for duplicated routing, stale source paths, and cross-domain leakage.
- Report exact changed files and the evidence used for the change.
