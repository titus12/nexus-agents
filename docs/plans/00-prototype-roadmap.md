# Nexus Agents V1 Design Roadmap

## Status
- Status: Done.
- Completed on: 2026-06-20.
- Implementation: Go backend in `cmd/` and `internal/`, Vue 3 app in `web/`, accepted prototype in `design/`.
- Verification: `rtk powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify_all.ps1`.
- Out of scope by this plan: real LiteLLM/Winky/Codex/Claude transport, database persistence, and writing real project files.

## Design Baseline
`design/` is the accepted design document for V1. Future backend and frontend implementation must use this prototype as the product and interaction baseline:

- Entry: `design/demo.html`
- Shared style: `design/shared.css`
- Pages: `design/pages/*.html`
- Overlays: `design/overlays/*.html`
- Mock behavior: `design/js/*.js`

The implementation phase should not redesign the information architecture. It should translate the accepted static prototype into the real application.

## Product Goal
Nexus Agents manages AI development configuration through two layers:

- `Template Library`: global reusable Agents, Rules, Skills, and Workflows.
- `Project Config Set`: each project's local copies of Agents, Rules, Skills, and Workflows.

Projects can copy templates and sync template updates manually. V1 does not support one-click promotion from project changes back into global templates. Users can still manually create new templates in the Template Library.

## V1 Navigation Model
- Left sidebar `Template Library`: Agents, Rules, Skills, Workflows.
- Left sidebar `System`: Model Proxy.
- Left sidebar `Projects`: All Projects plus expandable project tree.
- Project tree path examples:
  - `Projects / btd-game-server / Overview`
  - `Projects / btd-game-server / Agents`
  - `Projects / btd-game-server / Rules`
  - `Projects / btd-game-server / Skills`
  - `Projects / btd-game-server / Workflows`
- `Runs` is not a first-level navigation item. Workflow run feedback remains inside Workflows as a simulated run drawer.

## Core Data Model
Template item:

```yaml
id: tpl_skill_testing
kind: skill # agent | rule | skill | workflow
slug: testing
version: 5
entry: templates/skills/go-testing.md
files:
  - templates/skills/go-testing.md
```

Project copy:

```yaml
id: proj_skill_btd_testing
kind: skill
origin:
  templateId: tpl_skill_testing
  baseVersion: 5
  baseHash: sha256:xxxx
localVersion: 1
syncMode: manual
status: synced # synced | project_modified | template_updated | diverged | detached
path: .claude/skills/testing.md
```

V1 template files are copied into `templates/` first and then renamed for Nexus Agents. Go-related files use a `go-` prefix:

```text
templates/
  agents/claude/go-worker.md
  agents/codex/go-worker.toml
  rules/go-00-routing.md
  skills/go-testing.md
  workflows/go-bugfix.md
```

File names are display and legacy import helpers only. Synchronization identity comes from immutable ids, origin metadata, versions, and content hashes. V1 does not use YAML manifests or profile directories under `templates/`.

## Implementation Scope
- Preserve the accepted `design/` layout and interaction model.
- Implement Template Library pages as card-based resource libraries.
- Implement project resource pages as project-local copies with origin lineage and sync status.
- Implement Project Overview using sync status, resource entry cards, sync focus items, and Project Config Set details.
- Implement Model Proxy as global service configuration, outside Template Library sync.
- Do not connect real LiteLLM, Winky, Codex, Claude, or project file writes until the backend phase explicitly plans those integrations.

## Acceptance Criteria
- The real UI matches `design/demo.html` in navigation, layout, density, and terminology.
- Global Agents, Rules, Skills, and Workflows do not have project dropdown filters.
- Projects expose Overview, Agents, Rules, Skills, and Workflows as project-scoped pages.
- Project Overview does not show legacy empty panels such as `配置来源` or `健康检查`.
- Project copies show `origin.templateId`, `baseVersion`, `baseHash`, `localVersion`, `syncMode`, and sync status.
- Project copy cards keep actions minimal; manual sync is handled in the detail drawer.
- Workflows use the node canvas design; Node Palette appears only when creating or editing.
- `scripts/verify_design.py` passes for the design baseline before implementation work starts.
