# Agent Rule Skill Plan

## Status
- Status: Done.
- Completed on: 2026-06-20.
- Implementation: Template Library cards, project-scoped resource cards, detail drawers, origin lineage, package metadata, and template CRUD APIs.
- Backend evidence: `GET/POST/PUT/DELETE /api/templates/{kind}` and `GET /api/projects/{projectId}/config?kind=agent|rule|skill`.
- Frontend evidence: `web/src/App.vue`, `web/src/api.ts`, `web/src/types.ts`.
- Verification: `rtk powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify_all.ps1`.
- Out of scope by this plan: one-click project-to-template promotion.

## Design Baseline
Use these accepted design fragments as the source of truth:

- `design/pages/agents.html`
- `design/pages/rules.html`
- `design/pages/skills.html`
- `design/pages/project-agents.html`
- `design/pages/project-rules.html`
- `design/pages/project-skills.html`
- `design/overlays/drawer-agent.html`
- `design/overlays/drawer-rule.html`
- `design/overlays/drawer-skill.html`
- `design/overlays/drawer-project-copy.html`

## Template Library Pages
Global Agents, Rules, and Skills are Template Library pages.

- They do not have project dropdown filters.
- They use horizontal resource cards.
- Template cards have a single `查看` style entry action.
- Cards show only compact, useful information:
  - Agent: role name, responsibility summary, model tier, rules count, skills count, status.
  - Rule: rule name, source, summary/content preview, template metadata, status.
  - Skill: skill name, purpose/content preview, copied file source, applicable agents, status.
- Detailed fields such as tools, MCP, Codex TOML projection, Claude source paths, and package files belong in drawers.

## Project Resource Pages
Project Agents, Rules, and Skills show Project Config Set copies.

- They reuse the same card visual system as Template Library pages.
- They show origin lineage and manual sync status.
- Sync actions are not placed on cards.
- Project copy detail opens in `drawer-project-copy`.
- V1 sync action is manual template-to-project sync. Project-to-template promotion is not available.

## Drawers
- Agent drawer links to related Rules and Skills.
- Rule and Skill drawers show concrete content, metadata, and mock save feedback according to the accepted prototype.
- Project copy drawer shows:
  - kind
  - local name
  - status
  - path
  - `origin.templateId`
  - `origin.baseVersion`
  - `origin.baseHash`
  - `localVersion`
  - `syncMode`
  - diff preview

## Metadata Rules
- Template identity uses immutable `id`, not file name.
- `slug` is human-readable and may change.
- Rule is represented by a copied markdown file in `templates/rules/`.
- Skill is represented by copied markdown files in `templates/skills/` for V1.
- Go-related Rule and Skill template filenames use the `go-` prefix.
- Template metadata owns id, kind, slug, version, entry, and files; no YAML manifest or profile directory is used under `templates/`.

Template Skill file:

```yaml
id: tpl_skill_testing
kind: skill
slug: testing
name: testing
version: 5
entry: templates/skills/go-testing.md
files:
  - templates/skills/go-testing.md
```

Project Skill copy:

```yaml
id: proj_skill_btd_testing
kind: skill
slug: testing
origin:
  templateId: tpl_skill_testing
  baseVersion: 5
  baseHash: sha256:xxxx
localVersion: 1
syncMode: manual
status: project_modified
entry: .claude/skills/testing.md
files:
  - .claude/skills/testing.md
```

## Acceptance Criteria
- Global Agents, Rules, and Skills render as Template Library cards.
- Global pages do not include project filters.
- Project Agents, Rules, and Skills render as project-local copy cards.
- Cards are aligned in size and density with the accepted design.
- Drawers expose detailed content without overcrowding cards.
- File names are never treated as synchronization identity.
