---
type: Rules
title: KnowledgeBase Document Contract
description: Defines the OKF v0.1 baseline and repository profile for KnowledgeBase directories, index files, routing documents, and concept documents.
tags: [knowledge-base, framework, okf, document-contract]
timestamp: 2026-07-10T00:00:00+08:00
---

# KnowledgeBase Document Contract

## OKF baseline

- `index.md` and `log.md` are reserved OKF navigation/history files and do not use YAML frontmatter.
- Every other KnowledgeBase Markdown file is a concept document. It must use parseable YAML frontmatter with a non-empty `type`.
- Concept documents should also provide `title`, `description`, `tags`, and `timestamp`.
- Use UTF-8 without BOM for new or rewritten Markdown.

## Repository profile

Every KnowledgeBase directory that contains concept documents or child knowledge directories must have an `index.md`.
The root index separates portable framework knowledge from project-specific knowledge.

| Path | Owns |
|---|---|
| `framework/` | Portable KnowledgeBase rules, contracts, quality gates, and migration guidance. |
| `project/` | Project-specific source maps, domains, stable conventions, and validation routes. |

## Index documents

`index.md` supports progressive disclosure only:

- list direct concept documents and child directories;
- give each link a short loading description;
- route to cross-domain indexes when useful;
- do not add YAML frontmatter, long implementation explanations, or a full call graph.

## Routing documents

`routing.md` is optional. Keep it only when stable task diagnosis needs decisions that cannot fit in a short directory index:

- symptom-to-guide selection;
- multi-step or multi-domain routing;
- source-of-truth or verification choices that vary by task.

If a routing document only repeats the directory file list, merge it into `index.md`.

## Concept documents

Focused documents describe stable knowledge such as ownership boundaries, source maps, reusable patterns, pitfalls, and verification paths.

- Prefer exact source paths and symbols over copied code.
- Keep one stable access pattern per page.
- Use CodeGraph or precise source search for current implementation facts and call relationships.
- Update the parent `index.md` whenever a concept document is added, moved, or removed.
