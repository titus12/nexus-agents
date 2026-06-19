# Design Shell And Style Plan

## Goal
Create the static prototype shell and shared design system for a dense AI development configuration console.

## Files
- `design/demo.html`
- `design/shared.css`
- `design/serve.ps1`
- `design/README.md`
- `design/js/common.js`
- `design/js/nav.js`

## Implementation
- Use a fixed left sidebar, top breadcrumb bar, and scrollable content area.
- Treat `Projects` as the project configuration management entry.
- Treat `Agents`, `Rules`, `Skills`, and `Workflows` as Template Library entries.
- Use dark workspace tokens similar to ResCenter: panel backgrounds, compact resource cards, thin borders, purple primary accent, teal/orange/pink status accents.
- Use `fetch()` to lazy-load page and overlay fragments into `#page-container` and `#overlay-container`, with embedded fragment fallback for `file://` mode.
- Keep the UI dense and operational with resource cards, project dropdown filters, sync states, diff drawers, and compact editor drawers.
- Do not present `Runs` as a first-level sidebar item; workflow run feedback stays inside Workflows.

## Acceptance Criteria
- The app shell renders without page fragments.
- `navigate(pageId)` lazy-loads a page fragment and updates active nav and breadcrumb.
- `loadOverlay(overlayId)` lazy-loads overlay fragments.
- Sidebar entries match V1: Projects, Agents, Rules, Skills, Workflows, and Model Proxy.
- Toasts, drawers, modals, dropdown filters, copy buttons, and small mock actions share common helpers.
- Resource cards remain compact, aligned, and readable at desktop and narrow widths.
