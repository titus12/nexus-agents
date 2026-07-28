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
