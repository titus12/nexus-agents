# Design Shell And Style Plan

## Status
- Status: Done.
- Completed on: 2026-06-20.
- Implementation: `design/demo.html`, `design/shared.css`, `design/js/*`, and production shell in `web/src/App.vue` plus `web/src/styles.css`.
- Verification: `rtk powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify_all.ps1`.

## Design Baseline
Use `design/demo.html` and `design/shared.css` as the accepted shell and visual baseline. The production UI should preserve the same dark operational console style, density, card proportions, drawer sizing, and sidebar hierarchy unless a later reviewed design changes it.

## Files Representing The Design
- `design/demo.html`
- `design/shared.css`
- `design/README.md`
- `design/serve.ps1`
- `design/js/common.js`
- `design/js/nav.js`
- `design/js/fragment-cache.js`

## Shell Requirements
- Fixed left sidebar.
- Top breadcrumb bar.
- Scrollable content area.
- Overlay container for drawers and modals.
- Toast container for static feedback.
- Fragment loader with embedded fragment fallback for direct `file://` preview.

## Sidebar Structure
- `Template Library`
  - Agents
  - Rules
  - Skills
  - Workflows
- `System`
  - Model Proxy
- `Projects`
  - All Projects
  - Expandable project tree with Overview, Agents, Rules, Skills, Workflows per project.

Do not add `Runs` as a first-level navigation item.

## Style Requirements
- Keep the ResCenter-inspired dark workspace feeling.
- Use compact cards, thin borders, controlled contrast, and larger readable V1 typography.
- Use semantic icon styles from the prototype:
  - Agents: working employee
  - Rules: handbook
  - Skills: operation manual
  - Workflows: process
- Cards should be horizontal management cards, not square cards.
- Avoid repeated large titles inside content panels; page context comes from breadcrumb/topbar and lightweight page descriptions.
- Drawers should use the accepted wide, content-first layout and avoid tiny inner scroll boxes.

## Interaction Requirements
- `navigate(pageId)` loads page fragments and updates active nav/breadcrumb.
- `loadOverlay(overlayId)` loads overlay fragments.
- Common helpers handle drawers, modals, toasts, card actions, and mock save/sync feedback.
- Top-right duplicate global actions are removed from content pages; page actions stay local and purposeful.

## Acceptance Criteria
- Sidebar matches the accepted design hierarchy.
- Project tree shows project-level second navigation.
- Global template pages have no project filter dropdowns.
- Resource cards align across Agents, Rules, and Skills.
- Drawers and modals match the prototype sizing and spacing.
- Desktop and narrow viewport layouts do not overlap, clip status tags, or create horizontal overflow.
