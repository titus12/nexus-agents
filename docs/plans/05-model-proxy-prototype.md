# Model Proxy Prototype Plan

## Goal
Prototype global model routing and proxy health management.

## Files
- `design/pages/model-routes.html`
- `design/overlays/drawer-route.html`
- `design/overlays/drawer-proxy-test.html`

## Implementation
- Model Proxy is a global service configuration area, not part of the project template sync model.
- Projects, Agents, and Workflows can reference global provider/model routes, but do not copy or sync route definitions as template items in V1.
- Show Codex and Claude provider route tables.
- Include fixed V1 routes:
  - Codex `gpt-5.5` passthrough to official Codex backend.
  - Codex `gpt-5.4` to Winky `deepseek-v4-pro`.
  - Codex `gpt-5.4-mini` to Winky `deepseek-v4-flash`.
  - Claude `/v1/messages` to Winky Claude or Winky DeepSeek by model prefix.
- Show LiteLLM sidecar status, config path, port, and Responses tools patch check.
- Test drawer simulates a route test and stream test.

## Acceptance Criteria
- Route rows open a detail drawer.
- Proxy Test opens a drawer and can simulate success/failure states.
- The route table clearly shows provider, source model, target model, endpoint, and auth mode.
- The page states that provider routes are global references outside Template Library sync.
