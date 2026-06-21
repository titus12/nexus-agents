# Model Proxy Plan

## Status
- Status: Done.
- Completed on: 2026-06-20.
- Implementation: route matrix, route detail drawer, proxy test drawer, failure sample, mock save feedback, and resolver API.
- Backend evidence: `GET /api/model-routes` and `GET /api/model-routes/resolve?client=codex|claude&model=...`.
- Frontend evidence: `web/src/App.vue`, `web/src/api.ts`, `web/src/types.ts`.
- Verification: `rtk powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify_all.ps1`.
- Out of scope by this plan: live LiteLLM sidecar control and real request forwarding to Winky/Codex/Claude.

## Design Baseline
Use these accepted design fragments as the source of truth:

- `design/pages/model-routes.html`
- `design/overlays/drawer-route.html`
- `design/overlays/drawer-proxy-test.html`

## Product Role
Model Proxy is global service configuration. It is referenced by Agents and Workflows but does not participate in Template Library or Project Config Set synchronization.

## Route Requirements
V1 route table contains:

- Codex `gpt-5.5` passthrough to official Codex backend.
- Codex `gpt-5.4` to Winky `deepseek-v4-pro`.
- Codex `gpt-5.4-mini` to Winky `deepseek-v4-flash`.
- Claude `/v1/messages` to Winky Claude or Winky DeepSeek by model prefix.

## Page Requirements
- Keep the route table visually quiet and operational.
- Highlight provider, source model, target model, status, and row actions.
- Keep endpoint, auth mode, and test details in drawers.
- Do not add duplicate global top-right `代理测试` actions.
- Show LiteLLM sidecar status and Responses tools patch status as compact global service cards.
- Treat API keys as runtime environment values, never repository content.

## Drawer Requirements
- Route drawer shows complete route details and mock save feedback.
- Proxy test drawer simulates request/stream tests and failure states.
- Route test is a local page action, not a global header action.

## Acceptance Criteria
- Route rows open detail drawers.
- Proxy Test opens from the route row or drawer context.
- Page states that Model Proxy is global service config outside Template Library sync.
- The UI does not imply routes are copied into projects.
- The accepted Codex and Claude routing matrix is visible.
