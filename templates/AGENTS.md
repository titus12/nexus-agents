# Project AI Tooling Guide

This file is the project-level entry point for AI-assisted work. Keep it short, stable, and focused on where authoritative instructions live.

## Tool entry points

| Tool | Authoritative locations | Purpose |
|---|---|---|
| Claude Code | `.claude/` | Claude agents, rules, skills, commands, and workflows. |
| Codex | `.codex/` and `.agents/skills/` | Codex role definitions and explicit workflow-skill entries. |

- Do not maintain duplicate `AGENTS.md` copies under tool-specific directories.
- Do not mix role definitions between `.claude/agents/` and `.codex/agents/`.

## Source of truth

| Content | Authoritative location |
|---|---|
| Project-wide AI entry point | `AGENTS.md` |
| Codex role definitions | `.codex/agents/` |
| Codex workflow entries | `.agents/skills/wf-*/` |
| Workflow bodies | `.claude/workflows/` |
| Shared rules | `.claude/rules/` |
| Task-specific skills | `.claude/skills/` and installed Codex skills |

Read the authoritative source instead of copying its content into this file.

## Codex workflow conventions

- Use `$wf-*` when the user explicitly selects a workflow.
- **Workflows may only be invoked when explicitly requested by the user.** Do not infer, select, or run any `wf-*` workflow from task content, keywords, or default routing; when a workflow may help, suggest it rather than invoking it.
- Each `wf-*` skill must point to its workflow body in `.claude/workflows/`; the workflow body is the source of truth.
- Add a workflow skill entry and its workflow body together.
- Keep `SKILL.md` and `agents/openai.yaml` as UTF-8 without BOM.
- Do not maintain a detailed workflow routing table in this file.

## Codex role conventions

- Define Codex roles only in `.codex/agents/`.
- Do not duplicate role prompts, model choices, or long workflow instructions here.
- Use subagents, parallel execution, or delegation only when the user explicitly requests it.
- Keep model-routing and provider configuration outside project-local role documentation unless the project explicitly owns that configuration.

## Template synchronization

- When changing an AI configuration file in a project, first check whether Nexus `templates/` contains a file with the same relative path and name.
- Only when that matching template file exists and its copied project counterpart has changed must the equivalent change be synchronized back to the corresponding Nexus template file and verified for consistency.
- If no matching template file exists, treat the file as project-specific. Do not add it to public templates without first proposing it to the user and receiving approval.
- If Nexus cannot be accessed, the path mapping is unclear, or the files conflict, report the blocker explicitly; do not silently skip synchronization.
- Template synchronization does not authorize Git write or remote operations.

## Execution baseline

- Clarify ambiguous requirements and high-risk actions before changing files or external state.

## Test-Driven Change Baseline

- For every code-changing task, read and follow
  `.claude/rules/test-driven-change.md` before implementation.
- For observable behavior changes and defect fixes, decide the minimum
  behavior-based test before editing production code; run it red first when
  deterministic automation is practical.
- Do not add a test merely for coverage. Retain only tests with unique
  regression or contract value; extend, parameterize, merge, or remove
  duplicates deliberately.
- A non-contract logging, comment, formatting, or behavior-preserving rename
  change normally needs no new test, but must record no behavior change and
  run proportionate existing validation.
- Any other exception must record its reason, actual substitute validation, and
  a future test trigger when applicable.
- **Git safety constraint: do not autonomously perform Git write or remote operations.** Read-only inspection such as `git status`, `git diff`, `git log`, and `git show` is allowed. `git add`, `commit`, `push`, `pull`, `fetch`, `merge`, `rebase`, `reset`, `restore`, `checkout` / `switch`, `stash`, tag or branch creation/deletion, and any force option require the user's explicit authorization for that specific action. Approval for one action does not authorize subsequent staging, committing, or pushing.
- Do not claim a task is complete, fixed, or verified without fresh evidence from this task run.
- For bugs, reproduce and confirm the root cause before changing behavior; do not apply speculative fixes.
- When the code scope is unclear, locate the relevant symbols and call paths before reading or editing broadly.
- Before reading a large file, state why it is needed and read only the relevant sections when possible.
