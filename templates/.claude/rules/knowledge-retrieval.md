# Nexus Knowledge Retrieval Rule

All project KnowledgeBase access must use Nexus Knowledge Retrieval before local `KnowledgeBase` browsing.

## Required Call

Use the repository root folder name as `{id}`, mode `context`, maxTokens `6000`, and the task title or lookup question as `q`.

```text
GET /api/projects/{id}/knowledge/retrieve?q={query}&mode=context&maxTokens=6000
```

Use the repository's supported Nexus retrieval client when one is available.

## Gate

- Do not skip retrieval because the agent already knows the project.
- Do not manually browse `KnowledgeBase` to guess relevant files before retrieval.
- Do not replace retrieval with grep, CodeGraph, or source search; use those only for code facts after retrieval.
- For implementation workflows, do not plan or implement until retrieval succeeds or fallback is recorded.

## Result

Use `loadedKnowledgeMarkdown` as the authoritative knowledge context.

For workflows, record `knowledgeRetrieval` with:

- `query`
- `matchedDomain`
- `requiredPaths`
- `usedTokens`
- `loadedKnowledgeMarkdown`
- `fallbackUsed` / `error` when applicable

Include the returned `Loaded Knowledge` section in workflow final summaries.

## Fallback

If retrieval is unavailable, record `knowledgeRetrieval.error`, set `fallbackUsed=true`, then use local KB routing as fallback and report it.
