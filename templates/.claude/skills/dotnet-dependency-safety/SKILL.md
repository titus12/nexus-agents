---
name: dotnet-dependency-safety
description: "Assess NuGet dependency changes before they alter a .NET project."
---

# .NET Dependency Safety

Before adding or updating a package, inspect:

```text
Directory.Packages.props
PackageReference items
packages.lock.json or repository lock-file policy
target-framework compatibility
existing vulnerability and license policy
```

Prefer an existing dependency or framework capability over a new package.
Keep dependency changes minimal, explain the compatibility impact, and do not
modify package versions outside the approved task scope.
