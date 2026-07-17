# KnowledgeBase Structure and Migration Gates

Read this reference before proposing a new project concept page, creating a project domain, or moving KnowledgeBase pages.

## Required preflight

Run the bundled validator before the entry plan and before every KnowledgeBase edit:

```text
python .agents/skills/kb-system-curator/scripts/validate_kb_structure.py --root KnowledgeBase --project-dir project --flat-threshold 8 --json
```

Do not suppress validator errors. Report them in the entry plan.

## Domain placement gate

- Keep `KnowledgeBase/project/index.md` as a concise project entry.
- Keep `KnowledgeBase/project/routing.md` for cross-domain task decisions only.
- When `KnowledgeBase/project/domains/` exists, create project concept pages under one selected `domains/<domain>/` directory, never directly under `project/`.
- Update the selected domain `index.md` for every add, move, or removal.
- Update `domains/index.md` when a domain is added, removed, or renamed.
- If a proposed topic has no owning domain, propose the smallest new domain/index structure and wait for user confirmation.

## Flat-structure gate

If the validator reports `flat_project_requires_domains`:

1. Do not add another top-level project concept page.
2. Produce a structure-migration plan instead of a content-entry plan.
3. Include the current direct concept count, candidate domain grouping, files to move, index chain, and validation plan.
4. Wait for explicit user confirmation before creating `domains/` or moving pages.

The default threshold is eight direct project concept pages. Treat it as an early warning, not permission to ignore progressive disclosure below the threshold.

## Move gate

Before moving a page, prepare a mapping table:

| Old path | New path | Owning domain | Parent index | Cross-domain links to review |
| --- | --- | --- | --- | --- |
| | | | | |

For every moved concept page:

- update frontmatter `resource`;
- update Parent/project/domain navigation;
- update Markdown links from indexes, routing pages, and sibling pages;
- rebase relative links to source documents, `README`, `Tools`, assets, or other local files;
- remove the old page only after the new page is written and validated;
- do not retain duplicated concept pages at both paths.

## Required post-edit evidence

Run the validator again and require:

- zero broken local links;
- no orphan project document reachable from `KnowledgeBase/index.md`;
- every knowledge directory has `index.md`;
- no frontmatter/resource/H1/AI-comment/encoding failures.

For restructures, replay the existing keyword corpus when one exists. If none exists, add a small, task-representative corpus to the task evidence and report its expected page targets.
