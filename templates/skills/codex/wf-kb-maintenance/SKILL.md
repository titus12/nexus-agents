---
name: wf-kb-maintenance
description: Maintain an OKF-compatible project knowledge base by validating metadata, links, routing, staleness, duplicate hard rules, and workflow evidence.
---

# Knowledge Base Maintenance

Use this workflow when the user asks to review, tidy, validate, refresh, or maintain `design/KnowledgeBase`.

1. Read `.claude/workflows/kb-maintenance.md` as the source of truth when present.
2. Prefer Nexus Agents Knowledge Base reports when available:
   - validation report
   - maintenance report
   - route preview
3. Default to report/proposal mode. Do not rewrite hard rules without user approval.
4. Keep `AGENTS.md` as a concise entrypoint and `design/KnowledgeBase` as domain knowledge.
