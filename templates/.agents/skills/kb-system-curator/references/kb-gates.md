# KB Curator Gates

Read this reference when the task reaches planning or editing.

## Retrieval Gate

Project KnowledgeBase access must start with Nexus Knowledge Retrieval:

```text
GET /api/projects/{projectRootName}/knowledge/retrieve?q={query}&mode=context&maxTokens=6000
```

If no callable retrieval tool exists in the current environment, record that limitation and fallback to local routing. Do not pretend retrieval succeeded.

## Local Fallback Read Order

1. `KnowledgeBase/index.md`
2. `KnowledgeBase/project/domains/routing.md`
3. matched domain `index.md` or `routing.md`
4. only the selected target page
5. framework rule files only when editing

## Edit Gate

Before editing KnowledgeBase:

- user has confirmed the entry plan;
- framework rule files are read;
- target directory is scanned for mojibake;
- target files are read;
- write is limited to confirmed files.

## Blocking Mojibake Markers

Stop on:

- Unicode replacement characters;
- common broken smart-quote sequences;
- obvious broken Chinese text in the target page.

Either fix it as part of the confirmed edit or report it as blocking.
