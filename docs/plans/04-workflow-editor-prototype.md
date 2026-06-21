# Workflow Editor Plan

## Status
- Status: Done.
- Completed on: 2026-06-20.
- Implementation: global Workflow Library, Project Workflows, fixed-size categorized nodes, Node Palette create/edit state, node inspector, simulated run drawer, create/edit/duplicate/delete APIs.
- Backend evidence: `GET/POST /api/workflows`, `GET/PUT/DELETE /api/workflows/{workflowId}`, `POST /api/workflows/{workflowId}/duplicate`.
- Frontend evidence: `web/src/App.vue`, `web/src/api.ts`, `web/src/styles.css`.
- Verification: `rtk powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify_all.ps1`.
- Out of scope by this plan: real workflow runtime execution and project-workflow promotion back into templates.

## Design Baseline
Use these accepted design fragments as the source of truth:

- `design/pages/workflows.html`
- `design/pages/project-workflows.html`
- `design/js/workflow-canvas.js`
- `design/overlays/drawer-node-config.html`
- `design/overlays/drawer-workflow-run.html`

## Product Role
Workflows are reusable agent processes. The global Workflows page is the Workflow Template Library. Project Workflows are Project Config Set copies that can diverge locally and sync manually from templates.

## Global Workflow Template Library
- Shows workflow library/list plus node canvas preview.
- Cards support create, query, edit, duplicate, and delete mock interactions.
- Workflow cards stay compact and do not show non-essential run details.
- Node Palette is hidden by default.
- Node Palette appears only after `新建工作流` or `编辑`.
- Simulated run remains inside Workflows and opens the run drawer; it is not a first-level `Runs` page.

## Project Workflows
- Project Workflows use the same workflow shell and canvas style as global Workflows.
- Project workflow entries are scoped to Project Config Set copies.
- Project workflow cards show local copy status, local version, origin template, node count, and edge count.
- Project workflow detail/inspector shows sync metadata and path information.
- Project Workflows do not use the generic Project Copy drawer as the primary workflow experience.

## Node Categories
AI development workflows need visually distinct node categories:

- `action`: agent execution, tool action, review action.
- `control`: sequence, parallel, join.
- `condition`: routing gate, risk gate, if/else decision.
- `data`: transform, merge, map fields, summarize.
- `human`: approval, confirmation, manual checkpoint.
- `event`: trigger, incoming request, PR event, schedule.
- `decorator`: retry, timeout, context guard, policy wrapper.

Nodes use fixed card dimensions. Long labels and descriptions truncate; selected node details appear in the inspector/drawer.

## Workflow Template Metadata
Workflow templates are paired files under `templates/workflows/`: a markdown description plus a matching `.graph.json` node graph. The initial files are expanded from `templates/rules/go-00-routing.md`; Go-related workflow filenames use the `go-` prefix:

```yaml
id: tpl_workflow_btd_review
kind: workflow
slug: code-review
version: 2
entry: templates/workflows/go-code-review.md
files:
  - templates/workflows/go-code-review.md
  - templates/workflows/go-code-review.graph.json
```

The V1 API/UI should use the graph JSON as the editable workflow graph asset. It should not create YAML manifests under `templates/`.

Project workflow copies use the same origin model:

```yaml
origin:
  templateId: tpl_workflow_btd_review
  baseVersion: 2
  baseHash: sha256:xxxx
localVersion: 3
syncMode: manual
status: diverged
```

## Acceptance Criteria
- Global Workflows and Project Workflows match the accepted workflow shell.
- Node cards have consistent size and category styling.
- Node Palette appears only during create/edit mode.
- Selecting nodes highlights them and shows details.
- Simulated run updates state and opens the run drawer from within Workflows.
- Project workflow copies show origin lineage and manual sync status.
- V1 does not expose project-workflow promotion back into templates.
