# commit-gate

Source: .claude/rules/go-00-routing.md

Trigger: --commit

Use as a pre-commit gate.


Codex execution notes:

- Do not spawn subagents unless the user explicitly asks for subagent / parallel / delegation; otherwise execute the named role strategy in the main thread.
- Before acting, load the referenced current rule/skill files by exact path and report which ones were loaded.
- Prefer precise search/codegraph before reading large files.
- End with concrete evidence: changed files, commands run, and verification result.

Workflow:

1. Use go-gatekeeper to inspect the current diff.
2. Produce a risk list.
3. Ask for confirmation on high-risk items.
4. Run the verification gate below.
5. Commit only after verification and explicit approval.

## Verification Gate

Before commit / PR / handoff:

- [ ] Inspect git diff and list changed files.
- [ ] Run go build -tags actor_id_uint64 ./cmd/server/ for code changes.
- [ ] Run targeted tests for changed logic or bug fixes.
- [ ] Run go vet for broad or risky changes when practical.
- [ ] Confirm generated files are either intentionally regenerated or untouched.
- [ ] Document any skipped verification with the exact reason.
- [ ] Final response reports evidence, not assumptions.
