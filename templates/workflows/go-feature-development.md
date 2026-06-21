# go-feature-development

Source: `.claude/rules/go-00-routing.md`
Stack: `go`

Trigger: `--feat`

Use for new features or substantial modules. Load `go-dev-workflow.md`, move through the nine-step development flow, and report after each step for user review before continuing.


Codex execution notes:

- Do not spawn subagents unless the user explicitly asks for subagent / parallel / delegation; otherwise execute the named role strategy in the main thread.
- Before acting, load the referenced current rule/skill files by exact path and report which ones were loaded.
- Prefer precise search/codegraph before reading large files.
- End with concrete evidence: changed files, commands run, and verification result.

Workflow:

1. Before step 1, ask 2-3 clarification questions to confirm scope.
2. Load `go-dev-workflow.md`.
3. During implementation, load `go-coding-rules.md`.
4. When writing tests, write the failing test first, watch it fail, implement, then watch it pass.
5. Verify with the Go project commands in AGENTS.md / .codex/AGENTS.md (go build -tags actor_id_uint64 ./cmd/server/ plus targeted tests when applicable).
