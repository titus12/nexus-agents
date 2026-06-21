# lark-integration

Source: `.claude/rules/go-00-routing.md`

Trigger: `--lark`

Use for Feishu/Lark documents, messages, sheets, Base, and related operations.


Codex execution notes:

- Do not spawn subagents unless the user explicitly asks for subagent / parallel / delegation; otherwise execute the named role strategy in the main thread.
- Before acting, load the referenced current rule/skill files by exact path and report which ones were loaded.
- Prefer precise search/codegraph before reading large files.
- End with concrete evidence: changed files, commands run, and verification result.

Workflow:

1. Route to the matching Feishu/Lark skill.
2. Use `go-librarian` when repository context is needed.
3. Return links, extracted content, or operation results.
