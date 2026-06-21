# Project Management Plan

## Status
- Status: Done.
- Completed on: 2026-06-20.
- Implementation: project list, import modal, project tree, Project Overview, Project Config Set pages, sync preview drawer, project copy drawer, and manual sync/detach APIs.
- Backend evidence: `GET /api/projects`, `POST /api/projects/import`, `GET /api/projects/{projectId}/sync-preview`, `POST /api/projects/{projectId}/config/{copyId}/sync`, `POST /api/projects/{projectId}/config/{copyId}/detach`.
- Frontend evidence: `web/src/App.vue`, `web/src/api.ts`, `web/src/types.ts`.
- Verification: `rtk powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify_all.ps1`.
- Out of scope by this plan: real filesystem import and writing project config files.

## Design Baseline
Use these accepted design fragments as the source of truth:

- `design/pages/projects.html`
- `design/pages/project-detail.html`
- `design/pages/project-agents.html`
- `design/pages/project-rules.html`
- `design/pages/project-skills.html`
- `design/pages/project-workflows.html`
- `design/overlays/modal-import-project.html`
- `design/overlays/drawer-sync-preview.html`
- `design/overlays/drawer-project-copy.html`

## Product Role
Projects manage each repository's Project Config Set. A Project Config Set contains project-local Agents, Rules, Skills, and Workflows that may be copied from global templates or created as detached project-only items.

## Project List
- Shows managed projects and lightweight counts.
- Opens Project Overview.
- Import Project modal simulates selecting an existing project path and scanning configuration.
- V1 design keeps import as prototype interaction; real filesystem import belongs to backend implementation.

## Project Overview
Project Overview follows the accepted design and contains:

- Stats row for Agents, Rules, Skills, Workflows.
- `同步状态`: counts by `synced`, `template_updated`, `project_modified`, `diverged`, `detached`.
- `资源入口`: cards linking to project Agents, Rules, Skills, and Workflows.
- `同步关注项`: prioritized items needing review.
- `Project Config Set`: detailed list with origin lineage and version/hash metadata.

Project Overview must not show the old empty `配置来源` or `健康检查` panels.

## Project Resource Pages
- Project Agents, Rules, and Skills reuse the Template Library card style.
- Project cards show local name, source template, local version, sync mode, status, and path context.
- Cards do not expose multiple sync decision buttons.
- Clicking a project copy opens `drawer-project-copy`.
- The project copy drawer aligns with normal detail drawers and only adds the V1 manual sync action.
- `保留项目` is not exposed as a V1 drawer action.
- Project Workflows use the workflow editor layout, not a generic copy-card drawer page.

## Manual Sync Behavior
- Sync is explicit and manual.
- Sync preview shows template-origin metadata and diff snippets.
- Supported statuses:
  - `synced`
  - `project_modified`
  - `template_updated`
  - `diverged`
  - `detached`
- V1 does not provide one-click promotion from project changes back into templates.
- Users can manually create a new template from Template Library pages when project learning should become global practice.

## Acceptance Criteria
- Project tree navigation opens Overview, Agents, Rules, Skills, and Workflows for a selected project.
- Breadcrumb shows project context.
- Project Overview panels are populated from Project Config Set data.
- Project copies display `origin.templateId`, `baseVersion`, `baseHash`, `localVersion`, `syncMode`, and sync status.
- Sync Preview and Project Copy drawers provide static feedback without implying automatic overwrite.
- Project Workflows visually matches the global Workflows page while staying scoped to project copies.
