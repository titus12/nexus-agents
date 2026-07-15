# Communication and File Encoding Rules

## Language

- Always reply in Chinese unless the user explicitly asks otherwise.
- Code comments should follow the existing project style; prefer English comments for source code unless surrounding code uses Chinese.
- Commit messages should be in English when commits are requested.

## When to Ask

- Ask first when the requirement has multiple reasonable interpretations and a wrong assumption would be costly.
- Ask first before high-risk or irreversible operations, including deletion, reset, permission changes, or broad asset rewrites.
- **Never autonomously operate Git beyond read-only inspection.** `git status`, `git diff`, `git log`, and `git show` are allowed; every state-changing or remote action requires explicit user approval for that exact action. This includes `git add`, `commit`, `push`, `pull`, `fetch`, `merge`, `rebase`, `reset`, `restore`, `checkout` / `switch`, `stash`, tag or branch creation/deletion, and force options. Do not infer approval for staging, committing, or pushing from a request to edit code, run tests, or review changes.
- **Workflows may only be invoked when explicitly requested by the user.** Do not infer, select, or run any `wf-*` workflow from task content, keywords, or default routing; when a workflow may help, suggest it rather than invoking it.
- Ask first for architecture decisions or technology choices that cannot be inferred from local context.

## When to Act

- Act directly when the requirement is clear, the change is small, and the result can be verified.
- Prefer reading the relevant rule, workflow, or skill source before editing.
- Use read-only CodeGraph directly when available; no user permission is needed for read-only code lookups.

## Output Style

- Keep progress and final reports concise.
- Prefer bullets or tables over long prose when summarizing findings.
- Use absolute file paths when referencing workspace files in user-facing messages.
- Do not claim fixed, verified, submitted, or uploaded without evidence from the current turn.

## File Encoding Rules

- Preserve the existing file encoding when editing files whenever possible.
- For new or rewritten text files, use UTF-8 without BOM by default.
- This applies to all project text assets, including `.cs`, `.go`, `.md`, `.json`, `.xml`, `.yaml`, `.ps1`, `.txt`, config files, workflow files, and skill files.
- Do not rely on Windows PowerShell defaults that may write UTF-8 with BOM or otherwise change encoding unexpectedly.
- When writing files from PowerShell, prefer explicit UTF-8 without BOM APIs, for example: ``[System.IO.File]::WriteAllText($path, $content, [System.Text.UTF8Encoding]::new($false))``.
- When writing JSON or request bodies from PowerShell, prefer explicit UTF-8 without BOM bytes, for example: ``[System.Text.UTF8Encoding]::new($false).GetBytes($json)``.
- If a file unexpectedly causes `invalid json body`, parser failures, Unity import issues, or unreadable mojibake, check BOM and encoding before changing business logic.
