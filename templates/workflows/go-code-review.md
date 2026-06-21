# go-code-review

Source: `.claude/rules/go-00-routing.md`
Stack: `go`

Trigger: `--rev`

Use for review of the current change set.


Codex execution notes:

- Do not spawn subagents unless the user explicitly asks for subagent / parallel / delegation; otherwise execute the named role strategy in the main thread.
- Before acting, load the referenced current rule/skill files by exact path and report which ones were loaded.
- Prefer precise search/codegraph before reading large files.
- End with concrete evidence: changed files, commands run, and verification result.

Workflow:

1. Dispatch reviewers in parallel:
   - `go-reviewer-logic`
   - `go-reviewer-perf`
   - `go-reviewer-security`
2. Merge findings.
3. Deduplicate repeated issues.
4. Sort by severity.
5. Report concrete file and line references when available.
