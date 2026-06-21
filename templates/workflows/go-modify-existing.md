# go-modify-existing

Source: `.claude/rules/go-00-routing.md`
Stack: `go`

Trigger: `--mod`

Use for changes to existing Go features.


Codex execution notes:

- Do not spawn subagents unless the user explicitly asks for subagent / parallel / delegation; otherwise execute the named role strategy in the main thread.
- Before acting, load the referenced current rule/skill files by exact path and report which ones were loaded.
- Prefer precise search/codegraph before reading large files.
- End with concrete evidence: changed files, commands run, and verification result.

Workflow:

1. Locate the impact area with search or code graph.
2. Read the matching module skill when one exists.
3. Load `go-coding-rules.md`.
4. Use `go-hephaestus` for multi-file work and `go-quick` for narrow single-file work.
5. Check high-risk changes with `high-risk-api.md`.
6. Verify with build and relevant tests.
