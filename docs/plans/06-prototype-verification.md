# Design Verification Plan

## Status
- Status: Done.
- Completed on: 2026-06-20.
- Implementation: `scripts/verify_design.py`, `scripts/verify_app_scaffold.py`, `scripts/smoke_app.ps1`, and `scripts/verify_all.ps1`.
- Verification: `rtk powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify_all.ps1`.
- Note: manual browser checks are represented by design acceptance plus smoke/build checks; future visual regression can add Playwright screenshots.

## Design Baseline
`design/` is the accepted V1 design document. Verification protects that baseline before backend or frontend implementation starts.

## Automated Checks
Run:

```powershell
rtk python scripts\verify_design.py
```

The verifier should cover:

- Required design files exist.
- `demo.html` references `shared.css` and required JavaScript modules.
- `fragment-cache.js` loads before `nav.js`.
- Page and overlay mappings exist in `design/js/nav.js`.
- Page fragments contain expected root ids.
- Overlay fragments contain drawer or modal roots.
- Required interaction functions exist.
- `Runs` is not registered as a first-level navigation item.
- Agents, Rules, Skills, and Workflows exist as Template Library pages.
- Projects tree exposes Overview, Agents, Rules, Skills, and Workflows for each project.
- Global template pages do not include project filters.
- Project resource pages expose Project Config Set copy views.
- Project Overview contains sync status, resource entry, sync focus, and Project Config Set mounts.
- Project Overview does not contain legacy empty `project-sources` or `project-health` panels.
- Project Workflows use the workflow shell and canvas layout.
- Resource cards use aligned horizontal proportions.
- Workflow node cards use fixed dimensions, truncation, and category styling.

Run JavaScript syntax checks:

```powershell
rtk node --check design\js\mock-data.js
rtk node --check design\js\pages.js
rtk node --check design\js\common.js
rtk node --check design\js\workflow-canvas.js
rtk node --check design\js\nav.js
rtk node --check design\js\fragment-cache.js
```

Regenerate fragment cache after page or overlay changes:

```powershell
rtk node scripts\build_fragment_cache.mjs
```

## Manual Browser Checks
- Start the static server with `design/serve.ps1`.
- Open `http://127.0.0.1:8765/demo.html`.
- Check desktop width around 1366px.
- Check narrow viewport around 390px.
- Confirm no blank page, text overlap, clipped status tag, tiny drawer body, or horizontal overflow.
- Confirm primary navigation works.
- Confirm global Agents, Rules, Skills, Workflows have no project dropdown filters.
- Confirm Projects tree can open project Overview, Agents, Rules, Skills, and Workflows.
- Confirm Project Overview shows populated sync status, resource entry, sync focus, and Project Config Set sections.
- Confirm Project Overview does not show `配置来源` or `健康检查`.
- Confirm Project copy drawer shows origin lineage and only the accepted V1 sync action.
- Confirm Workflows Node Palette appears only after new/edit.
- Confirm Project Workflows use workflow canvas layout rather than a generic drawer page.
- Confirm import project modal, sync preview drawer, node config drawer, proxy test drawer, and simulated workflow run respond.

## Documentation Checks
- Every plan treats `design/` as the accepted design baseline.
- Plans consistently use Template Library, Project Config Set, origin lineage, and manual sync terminology.
- Plans describe global template pages and project-scoped pages separately.
- Plans state that file names are display/legacy import helpers, not synchronization identity.
- Plans state that V1 allows manual template creation but excludes one-click project-to-template promotion.
- Prototype acceptance covers Projects, Agents, Rules, Skills, Workflows, and Model Proxy.
