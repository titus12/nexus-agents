# commit-gate

Source: `.claude/rules/go-00-routing.md`

Trigger: `--commit`

Use as a pre-commit gate.


Codex execution notes:

- Do not spawn subagents unless the user explicitly asks for subagent / parallel / delegation; otherwise execute the named role strategy in the main thread.
- Before acting, load the referenced current rule/skill files by exact path and report which ones were loaded.
- Prefer precise search/codegraph before reading large files.
- End with concrete evidence: changed files, commands run, and verification result.

Workflow:

1. Use `go-gatekeeper` to inspect the current diff.
2. Produce a risk list.
3. Ask for confirmation on high-risk items.
4. Commit only after verification and explicit approval.
