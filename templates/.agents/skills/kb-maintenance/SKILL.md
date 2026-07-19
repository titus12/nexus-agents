---
name: kb-maintenance
description: Maintain an OKF-compatible project knowledge base by validating metadata, links, routing, staleness, duplicate hard rules, and workflow evidence.
---

# KB Maintenance

Use this skill when the user asks to review, tidy, validate, refresh, or maintain `KnowledgeBase`.

1. Prefer Nexus Agents Knowledge Base reports when available:
   - validation report
   - maintenance report
   - route preview
   - knowledge sync status and committed Git revision
   - pending OpenWiki/Nexus Proposal with evidence and validation
2. Validate the smallest relevant KnowledgeBase scope first: indexes and routing before leaf documents.
3. Check OKF metadata, links, stale or oversized documents, and duplicated hard rules.
4. Default to report/proposal mode. Do not rewrite hard rules without user approval.
5. Keep `AGENTS.md` as a concise entrypoint and `KnowledgeBase` as domain knowledge.
6. If HEAD is unchanged, do not invoke OpenWiki or a model.
7. Treat Feishu as initialization-only auxiliary evidence and code/CodeGraph as current implementation truth.
8. Apply only selected `KnowledgeBase/project/**` Proposal files after the target HEAD is rechecked.
