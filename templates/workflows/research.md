# research

Source: `.claude/rules/go-00-routing.md`

Trigger: `--ask`

Use for read-only understanding, investigation, or external documentation research.


Codex execution notes:

- Do not spawn subagents unless the user explicitly asks for subagent / parallel / delegation; otherwise execute the named role strategy in the main thread.
- Before acting, load the referenced current rule/skill files by exact path and report which ones were loaded.
- Prefer precise search/codegraph before reading large files.
- End with concrete evidence: changed files, commands run, and verification result.

Workflow:

1. Use `go-oracle` for code understanding when the target project is Go-related.
2. Use `go-librarian` for external documentation or API research.
3. Return conclusions, key paths, and references.
