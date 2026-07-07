# Knowledge Base Maintenance Workflow

## Goal

Keep `design/KnowledgeBase` small, accurate, routable, and useful for AI workflows.

## Inputs

- `AGENTS.md`
- `CLAUDE.md` when present
- `.claude/rules/`
- `.claude/workflows/`
- `.agents/skills/`
- `design/KnowledgeBase/`
- Nexus Knowledge Base Validation and Maintenance reports when available

## Steps

1. Inventory knowledge files.
2. Validate OKF metadata.
3. Validate links.
4. Detect stale or oversized docs.
5. Detect duplicated hard rules.
6. Propose updates.
7. Wait for human review before applying hard-rule changes.

## Output

Return a report with:

- loaded knowledge files;
- validation errors and warnings;
- stale documents;
- large documents;
- duplicate hard rules;
- suggested actions;
- changes that require human approval.

Do not automatically rewrite hard project rules, workflow logic, or business knowledge.
