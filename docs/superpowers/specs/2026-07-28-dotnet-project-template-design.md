# .NET Project Template Design

**Date:** 2026-07-28  
**Status:** Approved design; awaiting written-spec review

## Goal

Add `.NET` as a fourth project type in the quick project-configuration flow.
The type is for general-purpose class libraries and background services,
including Worker Service and hosted-service applications. It must generate a
dedicated set of AI project guidance, roles, skills, and workflows without
copying Go- or Unity-specific configuration.

The machine-readable project-type value is `dotnet`; the UI label is `.NET`.

## Context and Design Basis

The existing feature has three coordinated layers:

1. `web/src/types.ts` and `web/src/App.vue` define the project-type request
   contract and selector.
2. `internal/catalog/template_initializer.go` validates the type and filters
   `templates/` files into the initialization preview and apply result.
3. `internal/catalog/catalog.go` exposes known agents, rules, skills, and
   workflows through the template-management APIs.

Go and Unity each use a prefixed template family. Go has general feature,
bugfix, and review workflows, while Unity uses separate workflow IDs and
routes to accommodate its different project and asset safety model. `.NET`
will follow the same independent-family model rather than reusing either
family.

## Scope

### Included

- Add `dotnet` to the project-type API contract and the quick-configuration
  selector.
- Copy shared templates plus `.NET`-specific templates for `dotnet` projects.
- Add `.NET` agents, rules, skills, commands, workflows, and Codex
  projections.
- Register all `.NET` template resources in the catalog.
- Add workflow routing and Task Run context classification for the `.NET`
  workflows so the template set is usable through the existing workflow
  surface, not merely copied as files.
- Add focused backend and frontend regression coverage.

### Excluded

- Project scaffolding: do not create or edit `.sln`, `.slnx`, `*.csproj`,
  `Directory.Build.props`, `Directory.Packages.props`, or source files.
- SDK pinning: do not create, overwrite, or remove `global.json`.
- Framework-specific rules for ASP.NET MVC, Blazor, WPF, WinForms, MAUI, or
  Unity.
- New external MCP integrations or NuGet packages.
- Refactoring the existing project-type filter into a generic profile registry.
  The current three-type switch is small; a direct fourth case is the safer
  change.
- Modifying global Codex configuration or
  `C:\Users\Administrator\.codex\nexus-model-catalog.json`.

## Project Initialization Behavior

### Accepted value

Add `TemplateProjectTypeDotNet = "dotnet"` beside the existing `general`,
`go`, and `unity` constants. The template initialization input validator must
accept exactly these four values after its existing normalization.

The frontend `TemplateProjectType` union must include `"dotnet"`, and the
quick-configuration modal must show:

```text
通用
Go
Unity
.NET
```

### Included files

For `projectType: "dotnet"`, retain the current shared-file behavior:

- `AGENTS.md`;
- protected `KnowledgeBase/` handling;
- knowledge and shared-rule/skill files already selected for every project;
- shared design, research, commit, and subagent workflows.

In addition, include only these .NET-family prefixes:

```text
.claude/agents/dotnet-*
.codex/agents/dotnet-*
.claude/rules/dotnet-*
.claude/skills/dotnet-*
.agents/skills/wf-dotnet-*
.claude/commands/wf-dotnet-*
.claude/workflows/dotnet-*
```

The filter must not include any `go-*`, `unity-*`, UIArchitect, or Unity UI
quick-workflow path for a `.NET` initialization.

Existing preview, conflict, protected-path, and apply semantics remain
unchanged. In particular, `KnowledgeBase/project/` remains protected and is
never initialized or overwritten.

## .NET Template Family

The template family is intentionally small for the first release. Its
guidance is specific enough for class libraries and background services but
does not assume a web framework, persistence layer, or application
architecture.

### Agents

| ID | Responsibility | Key evidence |
| --- | --- | --- |
| `dotnet-developer` | Implement focused class-library or hosted-service changes. | Affected solution/project layout, build/test result, changed behavior. |
| `dotnet-debugger` | Reproduce and isolate build, test, runtime, configuration, concurrency, or cancellation failures. | Exact command, stack trace/log evidence, root cause, regression check. |
| `dotnet-reviewer` | Review public API compatibility, runtime safety, package impact, and verification evidence. | Diff scope, risk findings, missing tests, release or compatibility impact. |

Each agent has a Claude definition under
`templates/.claude/agents/dotnet-*.md` and a matching Codex projection under
`templates/.codex/agents/dotnet-*.toml`.

Agents use existing local tools such as shell, `rg`, Git inspection, and patch
application. They do not require a Unity MCP server or a new connector.

### Rules

| ID | Requirement |
| --- | --- |
| `dotnet-00-routing` | Route feature work, bug fixes, and reviews to the corresponding `.NET` workflows. |
| `dotnet-01-project-model` | Inspect `global.json`, solution files, project files, `Directory.Build.*`, `Directory.Packages.*`, analyzers, and existing test projects before changing conventions. |
| `dotnet-02-runtime-safety` | Preserve cancellation propagation, host lifecycle behavior, async/resource disposal, timeout/retry/idempotency behavior, structured logging, and secret-safe configuration. |
| `dotnet-03-library-compatibility` | Preserve public API, nullable, exception, package, and versioning compatibility unless the task explicitly changes the contract. |

### Skills

| ID | Responsibility |
| --- | --- |
| `dotnet-development` | C# project discovery, dependency injection, Options/configuration, logging, hosted-service implementation, and focused change discipline. |
| `dotnet-testing` | Restore/build/test workflow, solution or project selection, test filtering, and failure evidence. |
| `dotnet-dependency-safety` | NuGet dependency changes, centralized package management, lock-file conventions, and minimal dependency expansion. |
| `wf-dotnet-feature` | Codex skill entry for the `.NET` feature workflow. |
| `wf-dotnet-bugfix` | Codex skill entry for the `.NET` bugfix workflow. |
| `wf-dotnet-review` | Codex skill entry for the `.NET` review workflow. |

The three `wf-dotnet-*` skills live under `.agents/skills/`, matching the
existing Go and Unity workflow-skill pattern. Domain skills live under
`.claude/skills/`, with the normal catalog projection behavior.

### Workflows

Use unique workflow IDs to avoid colliding with the existing Go workflow IDs:

| Workflow ID | Trigger | Owner | Purpose |
| --- | --- | --- | --- |
| `dotnet-feature-development` | `$wf-dotnet-feature` | `dotnet-developer` | Focused new capability for a library or background service. |
| `dotnet-bugfix` | `$wf-dotnet-bugfix` | `dotnet-debugger` | Reproduce, diagnose, minimally fix, and regress a confirmed defect. |
| `dotnet-code-review` | `$wf-dotnet-review` | `dotnet-reviewer` | Review a .NET change for API, runtime, dependency, and verification risks. |

Each workflow has matching Markdown and graph files under
`templates/.claude/workflows/` plus a command file under
`templates/.claude/commands/`. The workflows reuse the existing design,
research, commit-gate, and subagent mechanisms instead of duplicating them.

## SDK and Target Framework Policy

The initialization process only installs AI configuration. It must not change
the repository's SDK or target-framework decision.

The templates enforce this precedence:

1. If `global.json` exists, use the SDK it declares.
2. If project files declare `TargetFramework` or `TargetFrameworks`, preserve
   those values and their compatibility constraints.
3. For a genuinely new project that asks for guidance, recommend `net10.0`
   first and permit `net9.0` when existing infrastructure, dependencies, or
   deployment requirements demand it.
4. Choose restore, build, and test commands based on the repository's
   solution/project structure and documented CI commands.

No template treats `net9.0` or `net10.0` as a hard-coded requirement for an
existing project.

## Catalog and Workflow Integration

The catalog currently has explicit mappings for Go workflow IDs and a separate
Unity workflow family. `.NET` needs equivalent explicit mapping rather than a
path-only addition.

Implementation must extend the relevant catalog helpers so that:

- workflow ID maps to its `wf-dotnet-*` skill and command;
- workflow source paths use `dotnet-00-routing.md`;
- workflow Markdown and graph source paths resolve to the three `.NET`
  workflow files;
- `.NET` agents, rules, skills, and workflows appear in the template APIs with
  their correct source paths;
- Task Run context chooses the `.NET` owner role and relevant rules/skills
  instead of defaulting to Go or Unity behavior.

The frontend Task Run helpers must recognize the three new workflow IDs and
attach .NET-specific tags and evidence defaults. This keeps the result
protocol consistent with the current workflow catalog.

## Error Handling

- The API continues to reject unsupported project types with a clear error.
- A missing required template asset must fail preview/apply rather than silently
  produce a partial `.NET` profile.
- Existing target-file conflict detection and protected-path reporting apply
  without special cases for `.NET`.
- Template catalog entries must not reference a missing Claude, Codex, skill,
  command, workflow, or graph path.

## Verification

### Backend tests

Add or extend tests to verify:

1. `dotnet` passes template initialization validation.
2. A `.NET` preview and apply include shared files and required `dotnet-*`
   assets.
3. A `.NET` initialization excludes Go and Unity assets.
4. The API accepts `projectType: "dotnet"` and returns its exact value.
5. Catalog APIs expose all `.NET` agents, rules, skills, workflows, and valid
   source paths.
6. Workflow helpers resolve the `.NET` skill, command, routing rule, Markdown,
   and graph paths.
7. Task Run context assigns the intended `.NET` role/rules/skills/tags.
8. Existing general, Go, and Unity initialization and catalog tests remain
   unchanged and pass.

### Frontend verification

1. TypeScript accepts the expanded project-type union.
2. The modal renders `.NET` and submits `"dotnet"`.
3. `npm run build` succeeds after frontend source changes so the embedded
   `web/dist` asset is current.

### Repository checks

- Run focused Go tests for `internal/catalog`, `internal/httpapi`, and the
  relevant workflow/task-run packages.
- Run the standard frontend checks appropriate to modified files.
- Run `scripts/verify_all.ps1` before final delivery when its runtime is
  practical for the change.
- Run `git diff --check`.

## Review Points

This design deliberately makes two scope decisions that should be kept during
implementation unless a review changes them:

1. `.NET` is a first-class workflow family, including Task Run classification;
   it is not a label that merely copies the generic templates.
2. The first release does not pin the SDK or modify project files. The
   `net10.0` preference is guidance only, while existing `net9.0` and
   repository-specific settings remain authoritative.
