# bug-investigation

Source: `.claude/rules/unity-00-routing.md`  
Stack: Unity / C# / UGUI / UIArchitect / assets

## Trigger

Use for:

- Unity compile failures.
- Console errors or warnings that indicate broken behavior.
- Runtime exceptions, crashes, NullReference, MissingReference, or incorrect behavior.
- Broken UI behavior, import failures, Prefab/Scene reference issues, and regressions.
- Failing tests.

## Roles

1. `unity-debugger` - reproduces the issue, captures evidence, and identifies root cause before editing.
2. `unity-bugfix-developer` - implements the smallest root-cause fix.
3. `unity-regression-tester` - verifies the reproduction path, regression tests, Unity compile, Console, and relevant behavior.
4. `unity-bugfix-reviewer` - reviews diff scope, root-cause alignment, asset safety, temporary residue, and verification evidence.

## Required Rules

- `.claude/rules/01-communication.md`
- `.claude/rules/unity-00-routing.md`
- `.claude/rules/unity-01-project-model.md`
- `.claude/rules/unity-bugfix-safety.md`

## Required Skills

- `.agents/skills/wf-unity-bugfix/SKILL.md`
- `.claude/skills/unity-debugger/SKILL.md`
- `.claude/skills/unity-bugfix-developer/SKILL.md`
- `.claude/skills/unity-testing/SKILL.md`
- `.claude/skills/unity-bugfix-review/SKILL.md`

## Reusable Project Skills

- `.claude/skills/unity-mcp-skill/` - Unity Editor automation, Console, compile, tests, screenshots.
- `.claude/skills/unity-ui-developer/` - use if the root cause is UI behavior or UIArchitect integration.
- `.claude/skills/unity-logic-developer/` - use if the root cause is existing non-UI logic.
- `.claude/skills/unity-asset-safety/` - use if assets, Prefabs, Scenes, or `.meta` files are involved.
- `.codex/skills/behaviour-tree/` - use for Battle AI, BonsaiBT, monster, boss, or behavior tree issues.

## Workflow

1. Reproduce the failure, or document why it cannot be reproduced locally.
2. Capture exact evidence: steps, error, stack trace, Console output, test failure, asset path, scene/page path, and environment.
3. Locate the impact area with search, references, code graph, Console stack trace, or Unity MCP.
4. Identify the root cause before editing; avoid speculative fixes.
5. Add or describe a minimal reproduction path; add a regression test first when practical.
6. Implement the smallest root-cause fix.
7. Unity Regression Tester verifies:
   - Original reproduction path.
   - Regression test or targeted test when available.
   - Unity compile and Console errors when applicable.
   - Related behavior that could regress.
8. Unity Bugfix Reviewer checks:
   - Fix matches the root cause.
   - Diff does not include unrelated refactors.
   - No accidental UI/Prefab/Scene/generated/asset changes unless required.
   - Null, lifecycle, async, cancellation, destroyed-object, missing-reference, and boundary risks.
   - Temporary data, Debug logs, mock/fake/test data, local paths, and debug bypasses.
9. Final report includes bug symptom, root cause, changed files, verification evidence, skipped checks with reasons, and remaining risks.

