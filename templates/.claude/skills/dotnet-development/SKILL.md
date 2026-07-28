---
name: dotnet-development
description: "Develop .NET class libraries and hosted/background services while respecting repository SDK, project, and runtime conventions."
---

# .NET Development

## Discovery

Before editing, inspect the existing solution/project structure, `global.json`,
target frameworks, `Directory.Build.*`, `Directory.Packages.*`, analyzers,
configuration, DI registration, logging, and tests.

## SDK policy

1. Respect `global.json` when it exists.
2. Respect existing `TargetFramework` or `TargetFrameworks`.
3. For new-project guidance only, prefer `net10.0`; allow `net9.0` when the
   repository, dependency, or deployment constraint requires it.
4. Do not change `global.json`, target frameworks, or package versions unless
   the user explicitly requests that change.

## Runtime implementation

Preserve cancellation propagation, hosted-service lifecycle, async disposal,
timeout/retry/idempotency behavior, structured logs, and secret-safe
configuration. Reuse established DI and Options patterns instead of introducing
new frameworks or abstractions without evidence.
