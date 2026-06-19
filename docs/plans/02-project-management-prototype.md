# Project Management Prototype Plan

## Goal
Prototype project import, scan, project-local configuration, and manual template sync flows.

## Files
- `design/pages/projects.html`
- `design/pages/project-detail.html`
- `design/overlays/modal-import-project.html`
- `design/overlays/drawer-sync-preview.html`

## Implementation
- Projects page shows an overview dashboard and project table/cards.
- `btd-game-server` is the primary project and displays discovered config sources:
  `.claude/agents`, `.claude/rules`, `.claude/skills`, `.codex/agents`, `.mcp.json`, `.proxy`, and LiteLLM patch status.
- Project detail page introduces the Project Config Set: project-local Agents, Rules, Skills, and Workflows.
- Project config entries show whether they are template copies or project-only entries.
- Template copies show `origin.templateId`, `origin.baseVersion`, `origin.baseHash`, `localVersion`, `syncMode`, and sync status.
- Sync preview drawer shows template sync diffs, not only raw file diffs.
- Supported sync statuses: `Synced`, `Project Modified`, `Template Updated`, `Diverged`, and `Detached`.
- Project item actions include sync template to project, keep project version, and detach from template.
- V1 does not show or implement a promote-to-template action. Users can manually create a new template from the Template Library pages instead.
- Import modal simulates selecting a local path and scanning.

## Acceptance Criteria
- Import Project opens a modal and can simulate import completion.
- Project row/card opens the detail page.
- Project detail shows Project Config Set counts for Agents, Rules, Skills, and Workflows.
- Sync Preview opens a drawer with template origin, version/hash metadata, status, and diff snippets.
- Manual sync actions provide static feedback and never imply automatic overwrite.
- No V1 screen exposes one-click promotion from project changes to a global template.
