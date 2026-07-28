---
name: dotnet-testing
description: "Select and report focused .NET restore, build, and test verification."
---

# .NET Testing

Locate the documented CI command and existing test layout first. Then use the
narrowest valid target:

```text
dotnet restore <solution-or-project>
dotnet build <solution-or-project> --no-restore
dotnet test <solution-or-project> --no-build
```

Use a test filter only when it targets the changed behavior and does not hide
related failures. Report commands that actually ran, their exit status, and
any unverified path. Never claim that a command passed when it was not run.
