# OKF Compatibility Checklist

Use this checklist before proposing or writing any file under `KnowledgeBase`.

This checklist applies to KnowledgeBase documents, not to skill files, command files, or workflow wrappers outside `KnowledgeBase`.

## Reserved files

`index.md` and `log.md` are reserved OKF files:

- do not add YAML frontmatter;
- use `index.md` for concise directory navigation and progressive disclosure;
- use `log.md` only for high-level change history.

## Concept frontmatter

Every new or materially rewritten KnowledgeBase Markdown file other than `index.md` and `log.md` must start with YAML frontmatter:

```yaml
---
type: <Index|Routing|Guide|Rules|Schema|Template|Example>
title: <human-readable title>
description: OKF-compatible btd-client knowledge document for <resource path>.
tags: [btd-client, unity, knowledge-base, <domain-or-topic>]
timestamp: <ISO-8601 timestamp with timezone>
---
```

Rules:

- `resource` should match the repository-relative file path when the project profile includes it.
- `description` must explain the page purpose and mention OKF compatibility for KB documents.
- `tags` must include at least `btd-client`, `unity`, and `knowledge-base`.
- Add a domain/topic tag when possible, such as `ui`, `gameplay`, `network`, `behaviour-tree`, `uiarchitect`, `routing`, or `schema`.
- `timestamp` must include a timezone, for example `2026-07-08T00:00:00+08:00`.

## Concept body shape

Each KB page must be index-first and selectively loadable:

- exactly one `#` title;
- one AI loading comment near the title:
  - `<!-- AI:ROUTING - ... -->`
  - `<!-- AI:RULES - ... -->`
  - `<!-- AI:TEMPLATE - ... -->`
- short `##` sections;
- routing/read-when/source-doc tables where they reduce search scope;
- links to source paths instead of large copied source or design excerpts.

## Index navigation shape

Every KnowledgeBase directory that contains concept documents or child knowledge directories should have `index.md`.
Each index should list direct child documents or directories with concise descriptions, and must not duplicate a full routing guide.

## Search-Scope Gate

Before writing, answer:

| Question | Pass condition |
|---|---|
| Can a future agent decide whether to read this page from its title/tags/routing? | yes |
| Can a future agent avoid reading unrelated sibling systems? | yes |
| Does the page route cross-domain concerns instead of duplicating them? | yes |
| Does the page avoid volatile ids, one-off bug evidence, and large code excerpts? | yes |
| Does the page point to source-of-truth docs/code/configs for details? | yes |

If any answer is no, revise the structure before writing.

## Existing Page Updates

For updates to existing KnowledgeBase files:

- preserve existing frontmatter unless it is missing or clearly stale;
- fix missing `resource`, `tags`, or timestamp only when the user has approved the KB edit;
- keep the existing `type` unless the page role has truly changed;
- add routing rows or sections instead of appending long prose.

## OKF Result Block

Include this block in the entry plan:

```yaml
okfCompatibility:
  frontmatter: pass|fail|not-applicable
  resourcePath: pass|fail|not-applicable
  tags: pass|fail|not-applicable
  timestamp: pass|fail|not-applicable
  aiLoadingComment: pass|fail|not-applicable
  searchScopeGate: pass|fail
  notes:
    - ...
```
