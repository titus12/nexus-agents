---
name: nexus-knowledge-retrieval
description: Retrieve the current repository's approved Nexus KnowledgeBase context through GBrain before code exploration or implementation.
---

# Nexus Knowledge Retrieval

Use this skill before implementing, debugging, reviewing, or designing code that depends on project knowledge.

## Command

Run from the repository root:

```text
node .agents/skills/nexus-knowledge-retrieval/find-knowledge.mjs --query "<task or question>"
```

The helper:

1. resolves the current repository to the exact Nexus Project by local path;
2. lets Nexus infer the current Project Group;
3. calls the existing `/api/projects/{id}/knowledge/retrieve` contract;
4. uses GBrain by default;
5. prints the token-budgeted `loadedKnowledgeMarkdown`.

Do not pass `projectId`, `groupId`, or `scope`. Nexus owns those decisions.

## Required behavior

- Treat returned Approved KnowledgeBase content as stable business guidance.
- After retrieval, use CodeGraph or precise source inspection to verify current code facts.
- If `degraded=true`, report the FTS5 fallback reason.
- If the current repository is not imported into Nexus, stop and ask the user to import it.
- Do not browse the entire local `KnowledgeBase` before attempting Nexus retrieval.

