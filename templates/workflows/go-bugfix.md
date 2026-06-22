# go-bugfix

Source: .claude/rules/go-00-routing.md
Stack: go

Entry skill: $wf-go-bugfix

Use for Go runtime errors, crashes, and behavioral bugs.


Codex execution notes:

- Do not spawn subagents unless the user explicitly asks for subagent / parallel / delegation; otherwise execute the named role strategy in the main thread.
- Before acting, load the referenced current rule/skill files by exact path and report which ones were loaded.
- Prefer precise search/codegraph before reading large files.
- End with concrete evidence: changed files, commands run, and verification result.

Workflow:

1. Start with go-debugger and identify the root cause before editing.
2. Escalate to go-oracle for cross-module or ambiguous failures.
3. Add or describe a reproduction path.
4. Load go-coding-rules.md before changing code.
5. Verify the reproduction path plus build or targeted tests.

## Debugging Discipline

When fixing a bug or failing test:

1. Reproduce the failure first, or document why it cannot be reproduced locally.
2. Capture the exact command, input, error, stack trace, and relevant environment.
3. Form one hypothesis at a time and inspect evidence before editing code.
4. Prefer the smallest failing reproduction or targeted test.
5. Fix the root cause, not only the visible symptom.
6. Add or update a regression test when practical.
7. Re-run the reproduction and relevant tests after the fix.
8. Report the cause, changed files, commands run, and verification result.

Do not make speculative edits without a reproduced failure or supporting evidence.

## Regression Test Preference

After reproducing the bug and before fixing it, prefer to turn the reproduction into a focused regression test.

Use this when:

- The bug affects business logic, state transitions, rewards, deductions, validation, or public helpers.
- The failure can be reproduced with a stable input or scenario.
- The same area has regressed before.

Minimal loop:

1. Reproduce the bug.
2. Add a targeted test that fails for the observed bug.
3. Confirm the test fails for the expected reason.
4. Implement the smallest fix.
5. Re-run the regression test and relevant build/test commands.

If a regression test is impractical, document the reproduction path and the alternative verification used.
