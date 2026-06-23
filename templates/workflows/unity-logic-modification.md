# logic-modification

Source: `.claude/rules/unity-00-routing.md`  
Stack: Unity / C# / existing logic

## Trigger

Use for:

- Modifying existing C# logic behavior.
- Adjusting existing rules, state flow, calculations, validation, parsing, or process logic.
- Scoped behavior changes that do not involve UI, Prefab, Scene, or visual presentation.

Do not use for UI page, Prefab, View/ViewModel/Presenter presentation, UIArchitect, UGUI, TMP, or Resolver changes; use `ui-feature-development` instead.

## Roles

1. `unity-logic-developer` - locates the impact area and implements the smallest safe behavior change.
2. `unity-logic-tester` - verifies new behavior, key old behavior, Unity compile, Console, and targeted tests.
3. `unity-logic-reviewer` - reviews scope, compatibility, temporary data, boundary conditions, and verification evidence.

## Required Rules

- `.claude/rules/01-communication.md`
- `.claude/rules/unity-00-routing.md`
- `.claude/rules/unity-01-project-model.md`
- `.claude/rules/unity-logic-mod-safety.md`

## Required Skills

- `.agents/skills/wf-logic-mod/SKILL.md`
- `.claude/skills/unity-logic-developer/SKILL.md`
- `.claude/skills/unity-testing/SKILL.md`
- `.claude/skills/unity-logic-review/SKILL.md`

## Reusable Project Skills

- `.claude/skills/unity-mcp-skill/` - Unity Editor automation, Console, tests.
- `.claude/skills/unity-asset-safety/` - use if diff unexpectedly touches Unity assets.
- `.codex/skills/behaviour-tree/` - use only for Battle AI, BonsaiBT, monster, boss, or behavior tree logic.
- `.claude/skills/vm-logic/` - use only for ViewModel state logic that does not require UI presentation changes.

## Workflow

1. Locate the impact area with search, references, or code graph.
2. Read matching module docs, nearby code, tests, and relevant skills when they exist.
3. Summarize current behavior and target behavior before editing.
4. Load Unity/C# logic coding guidance from `unity-logic-developer`.
5. Implement the smallest scoped behavior change.
6. Add or update targeted tests when practical; otherwise record a concrete manual verification path.
7. Unity Logic Tester verifies:
   - Unity compile and Console errors when available.
   - Targeted EditMode / PlayMode tests when applicable.
   - New behavior.
   - Key old behavior that could regress.
8. Unity Logic Reviewer checks:
   - Diff stays within target logic.
   - No accidental UI, Prefab, Scene, generated file, or asset changes.
   - Compatibility with existing callers.
   - Null, boundary, exception, cancel, timeout, and destroyed-object risks.
   - Temporary data, Debug logs, mock/fake/test data, local paths, and temporary switches.
   - Hot-path performance risks.
9. Final report includes changed files, verification evidence, skipped checks with reasons, and remaining risks.

