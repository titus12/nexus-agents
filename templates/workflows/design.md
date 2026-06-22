# design

Source: `.claude/rules/go-00-routing.md`

Entry skill: `$wf-design`

Use for architecture or implementation design without code changes.


Codex execution notes:

- Do not spawn subagents unless the user explicitly asks for subagent / parallel / delegation; otherwise execute the named role strategy in the main thread.
- Before acting, load the referenced current rule/skill files by exact path and report which ones were loaded.
- Prefer precise search/codegraph before reading large files.
- End with concrete evidence: changed files, commands run, and verification result.

Workflow:

1. Ask 2-3 clarifying questions about constraints and priorities.
2. Use `go-prometheus` when the target project is Go-related.
3. Compare 2-3 options by complexity, compatibility, risk, and verification.
4. Recommend one option and capture the plan.
