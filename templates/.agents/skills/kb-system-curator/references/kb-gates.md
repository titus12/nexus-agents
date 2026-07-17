# KB Curator Gates

Read this reference when the task reaches planning or editing. For project-domain placement, flat-structure detection, or page moves, also read `structure-and-migration-gates.md`.

## Retrieval Gate

Project KnowledgeBase access must start with Nexus Knowledge Retrieval:

```text
GET /api/projects/{projectRootName}/knowledge/retrieve?q={query}&mode=context&maxTokens=6000
```

If no callable retrieval tool exists in the current environment, record that limitation and fallback to local routing. Do not pretend retrieval succeeded.

## Local Fallback Read Order

1. `KnowledgeBase/index.md`
2. `KnowledgeBase/project/index.md`
3. `KnowledgeBase/project/routing.md`
4. `KnowledgeBase/project/domains/index.md` when it exists
5. matched domain `index.md`
6. only the selected target page
7. framework rule files only when editing

## Edit Gate

Before editing KnowledgeBase:

- user has confirmed the entry plan;
- framework rule files are read;
- `scripts/validate_kb_structure.py` passes before editing, or its structure failure has been converted into a user-approved migration plan;
- target directory is scanned for mojibake;
- target files are read;
- write is limited to confirmed files.

After editing, rerun the validator and report its link, reachability, index, metadata, and encoding result.

## Blocking Mojibake Markers

Stop on:

- Unicode replacement characters;
- common broken smart-quote sequences;
- obvious broken Chinese text in the target page.

Either fix it as part of the confirmed edit or report it as blocking.
