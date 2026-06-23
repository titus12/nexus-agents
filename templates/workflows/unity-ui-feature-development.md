# ui-feature-development

Source: `.claude/rules/unity-00-routing.md`  
Stack: Unity / C# / UGUI / TextMeshPro / UIArchitect

## Trigger

Use for:

- UI page, popup, or component development.
- UI business logic.
- PSD Import output integration.
- View / ViewModel / Presenter / Binding work.
- Resolver / UIArchitect Pipeline changes.
- Prefab, UGUI, TMP, layout, or interaction changes.

## Roles

1. `ui-developer` - implements UI and business logic using UIArchitect and existing project patterns.
2. `ui-tester` - verifies compile, console, tests, interactions, and input-lock release paths.
3. `ui-reviewer` - reviews diff, assets, layout adaptation, temporary data, and quality risks.

## Required Rules

- `.claude/rules/01-communication.md`
- `.claude/rules/unity-00-routing.md`
- `.claude/rules/unity-01-project-model.md`
- `.claude/rules/unity-ui-safety.md`

## Required Skills

- `.agents/skills/wf-ui-feature/SKILL.md`
- `.claude/skills/unity-ui-developer/SKILL.md`
- `.claude/skills/unity-testing/SKILL.md`
- `.claude/skills/unity-asset-safety/SKILL.md`

## Reusable Project Skills

- `.claude/skills/UIResolver/` - add or update PSD Component Resolver.
- `.claude/skills/unity-mcp-skill/` - Unity Editor automation, Console, tests, screenshots.
- `.claude/skills/vm-logic/` - ViewModel or UI state logic when applicable.

## Workflow

1. Clarify UI scope if the request is ambiguous.
2. Load the required rules and relevant project documents from `.claude/rules/unity-01-project-model.md`.
3. Inspect existing similar UI pages, popups, Presenters, ViewModels, Resolvers, and bindings.
4. Confirm the UIArchitect touch points: PSD Import, Resolver, ViewGenerator, generated View, Presenter, ViewModel, Prefab, or Scene.
5. Implement the smallest clear solution:
   - Reuse existing components and patterns first.
   - Do not hand-edit generated View files.
   - Keep Editor-only and Runtime code separated.
   - Follow the Unity/C# version of the useful `go-coding-rules` quality principles: readable, small, YAGNI, reuse-first, evidence-based performance work.
6. Add or update tests when practical, especially for logic, parsing, validation, binding, or state transitions.
7. UI Tester verifies:
   - Unity compile and Console errors.
   - Targeted EditMode / PlayMode tests when available.
   - Open, close, repeat open, key interaction, empty/error/loading paths.
   - Loading, modal blocker, input lock, guide blocker, and button-disable release paths.
   - Rapid click, close-during-request, request failure, cancellation, timeout, and scene/page switch risks when applicable.
8. UI Reviewer checks:
   - `.meta`, Prefab, Scene, generated file, and asset safety.
   - View / ViewModel / Presenter boundary.
   - UGUI performance and lifecycle risks.
   - Resolution adaptation, Safe Area, anchors, pivots, text overflow, and list bounds.
   - Temporary data, debug logs, mock/fake data, local paths, IPs, tokens, and test-only switches.
9. Final report includes changed files, verification evidence, skipped checks with reasons, and remaining risks.

