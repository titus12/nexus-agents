# .NET Project Model

Before changing a .NET project, inspect the nearest applicable:

```text
global.json
*.sln / *.slnx
*.csproj
Directory.Build.props / Directory.Build.targets
Directory.Packages.props
packages.lock.json
existing analyzer and test-project configuration
documented CI commands
```

Respect existing SDK, `TargetFramework`/`TargetFrameworks`, nullable, implicit
using, analyzer, package-management, and test conventions. Do not create or
overwrite `global.json`, project files, or package-management files merely to
apply this AI configuration.

## Public API Documentation

For externally callable .NET APIs, add concise Chinese XML documentation:

- Document all `public` and `protected` types and members intended for callers.
- Use `<summary>` for the purpose; add `<param>`, `<returns>`, and
  `<exception>` when they clarify caller behavior.
- Use `<remarks>` for important constraints such as synchronous blocking,
  latency risk, thread affinity, lifecycle requirements, or side effects.
- Keep comments accurate and concise; do not restate an obvious identifier or
  expose implementation-only detail.
