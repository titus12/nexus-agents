# go-bugfix

Source: `.claude/rules/go-00-routing.md`
Stack: `go`

Trigger: `--bug`

Use for Go runtime errors, crashes, and behavioral bugs.


Codex execution notes:

- Do not spawn subagents unless the user explicitly asks for subagent / parallel / delegation; otherwise execute the named role strategy in the main thread.
- Before acting, load the referenced current rule/skill files by exact path and report which ones were loaded.
- Prefer precise search/codegraph before reading large files.
- End with concrete evidence: changed files, commands run, and verification result.

Workflow:

1. Start with `go-debugger` and identify the root cause before editing.
2. Escalate to `go-oracle` for cross-module or ambiguous failures.
3. Add or describe a reproduction path.
4. Load `go-coding-rules.md` before changing code.
5. Verify the reproduction path plus build or targeted tests.
