# go-refactor

Source: `.claude/rules/go-00-routing.md`
Stack: `go`

Entry skill: `$wf-go-refactor`

Use for broad Go structural changes where external behavior should remain unchanged.


Codex execution notes:

- Do not spawn subagents unless the user explicitly asks for subagent / parallel / delegation; otherwise execute the named role strategy in the main thread.
- Before acting, load the referenced current rule/skill files by exact path and report which ones were loaded.
- Prefer precise search/codegraph before reading large files.
- End with concrete evidence: changed files, commands run, and verification result.

Workflow:

1. Analyze impact before editing.
2. Have `go-prometheus` design staged steps that remain buildable.
3. Implement with `go-hephaestus`.
4. Run logic, performance, and security review in parallel.
5. Verify existing behavior still passes.
