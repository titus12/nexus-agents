# go-code-review

Source: .claude/rules/go-00-routing.md
Stack: go

Trigger: --rev

Use for review of the current change set.


Codex execution notes:

- Do not spawn subagents unless the user explicitly asks for subagent / parallel / delegation; otherwise execute the named role strategy in the main thread.
- Before acting, load the referenced current rule/skill files by exact path and report which ones were loaded.
- Prefer precise search/codegraph before reading large files.
- End with concrete evidence: changed files, commands run, and verification result.

Workflow:

1. Decide whether a full review or self-review checklist is appropriate.
2. For full review, dispatch reviewers in parallel:
   - go-reviewer-logic
   - go-reviewer-perf
   - go-reviewer-security
3. Merge findings.
4. Deduplicate repeated issues.
5. Sort by severity.
6. Report concrete file and line references when available.

## When to Request Review

Request or perform review when:

- A feature, bugfix, or refactor changes multiple files.
- Behavior, data flow, concurrency, persistence, permissions, economy, or security changes.
- The implementation is ready for merge or commit.
- A previous bug was subtle, high-impact, or hard to reproduce.
- The agent is uncertain about design trade-offs.

For small mechanical changes, a self-review checklist may be enough.

## Review Context Template

Provide reviewers with:

- Goal / requirement
- Files changed and important diffs
- Base and head revision if available
- Commands already run and their results
- Known risks or intentional trade-offs
- Areas where feedback is specifically requested

## Review Result Handling

- Critical: fix before proceeding.
- Important: fix before merge or explicitly defer with reason.
- Minor: fix if cheap, otherwise track as follow-up.
- Incorrect: respond with concise technical evidence.
