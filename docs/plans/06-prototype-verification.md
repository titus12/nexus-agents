# Prototype Verification Plan

## Goal
Define automated and manual verification for the static HTML prototype and the Template Library / Project Config Set design.

## Automated Checks
- Run `rtk python scripts\verify_design.py`.
- Verify all required files exist.
- Verify `demo.html` references `shared.css` and required JavaScript modules.
- Verify page and overlay mappings exist in `design/js/nav.js`.
- Verify page fragments contain expected root ids.
- Verify overlays contain modal or drawer roots.
- Verify expected interaction functions exist.
- Verify `Runs` is not a first-level navigation item.
- Verify Agents, Rules, Skills, and Workflows remain accessible as Template Library entries.
- Verify project filters use dropdown selects rather than many project chips.
- Verify resource cards use aligned card proportions and do not overflow at desktop or narrow widths.

## Manual Checks
- Start the static server with `design/serve.ps1`.
- Open `http://127.0.0.1:8765/demo.html`.
- Check desktop width around 1366px.
- Check a narrow viewport around 390px.
- Confirm no blank page, text overlap, clipped status tag, or broken drawer/modal.
- Confirm primary navigation, import project modal, sync preview drawer, node config drawer, proxy test drawer, and simulated workflow run all respond.
- Confirm Projects can show Project Config Set entries with origin template id, base version, base hash, local version, and sync status.
- Confirm sync status examples include `Synced`, `Project Modified`, `Template Updated`, `Diverged`, and `Detached`.
- Confirm Skill multi-file package metadata is visible with a `nexus.skill.yaml` example.
- Confirm V1 documentation and UI do not expose project-change promotion into global templates.

## Documentation Checks
- Plan files consistently use Template Library, Project Config Set, origin lineage, and manual sync terminology.
- File names are documented as display and legacy import helpers only, not synchronization identity.
- V1 explicitly allows manual template creation and excludes one-click promotion from project changes.
- Prototype acceptance still covers Projects, Agents, Rules, Skills, Workflows, and Model Proxy.
