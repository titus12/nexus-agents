# .NET Project Template Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a first-class `.NET` quick-configuration profile for class libraries and hosted/background services, including cataloged AI roles, rules, skills, and workflows.

**Architecture:** Keep the existing explicit Go/Unity template-family design. Add `dotnet` as a fourth validated project type, filter a dedicated `dotnet-*` asset family during project initialization, and register unique `.NET` workflow IDs through the catalog and Task Run inference paths. Do not scaffold projects or mutate SDK/framework files; templates only provide project-local AI configuration and guidance. Keep the implementation uncommitted until the final AI-configuration audit requested by the user has completed.

**Tech Stack:** Go, Vue 3, TypeScript, Node test runner, Vite, Markdown/TOML/JSON template assets.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `internal/catalog/template_initializer.go` | Validate `dotnet` and select only shared plus `.NET` template paths. |
| `internal/catalog/template_initializer_test.go` | Verify `.NET` initialization, profile isolation, and shared test-policy installation. |
| `internal/catalog/catalog.go` | Register `.NET` agents/rules/skills/workflows and resolve their asset, command, graph, and routing paths. |
| `internal/catalog/*_test.go` | Extend existing catalog/inventory tests with `.NET` resource and path assertions. |
| `internal/httpapi/server_test.go` | Exercise the initialization HTTP endpoint with `projectType: "dotnet"`. |
| `web/src/types.ts` | Extend `TemplateProjectType` with `"dotnet"`. |
| `web/src/App.vue` | Render `.NET` in the quick-configuration project-type selector. |
| `web/src/workflow-task-run.ts` | Infer `.NET` workflow type, roles, rules, skills, tools, and evidence tags. |
| `web/tests/workflow-task-run.test.mjs` | Cover the new `.NET` Task Run draft inference. |
| `templates/.claude/agents/dotnet-*.md` | Claude definitions for developer, debugger, and reviewer roles. |
| `templates/.codex/agents/dotnet-*.toml` | Matching Codex agent projections. |
| `templates/.claude/rules/dotnet-*.md` | Routing, project-model, runtime-safety, and library-compatibility guidance. |
| `templates/.claude/skills/dotnet-*/SKILL.md` | Development, testing, and dependency-safety instructions. |
| `templates/.agents/skills/wf-dotnet-*/SKILL.md` | Codex workflow entry skills. |
| `templates/.claude/commands/wf-dotnet-*.md` | Claude command entries for the workflows. |
| `templates/.claude/workflows/dotnet-*.md` | Feature, bugfix, and review workflow definitions. |
| `templates/.claude/workflows/dotnet-*.graph.json` | Graph descriptors matching the workflow IDs and owners. |

### Task 1: Add the `.NET` project-type contract and prove initialization isolation

**Files:**
- Modify: `internal/catalog/template_initializer.go:13-16,449-516`
- Modify: `internal/catalog/template_initializer_test.go:35-84,102-132`
- Modify: `internal/httpapi/server_test.go:609-652`
- Modify: `web/src/types.ts:285-291`
- Modify: `web/src/App.vue:372-375,4041-4046`

- [ ] **Step 1: Write backend tests that define the `.NET` profile**

Add this focused initialization test to
`internal/catalog/template_initializer_test.go`:

```go
func TestApplyTemplateInitializationCreatesDotNetProfile(t *testing.T) {
	target := t.TempDir()

	result, err := ApplyTemplateInitialization(TemplateInitializationInput{
		TargetPath:  target,
		ProjectType: TemplateProjectTypeDotNet,
	})
	if err != nil {
		t.Fatalf("apply .NET template initialization: %v", err)
	}
	if result.ProjectType != TemplateProjectTypeDotNet {
		t.Fatalf("expected .NET project type, got %#v", result)
	}
	for _, relative := range []string{
		".claude/agents/dotnet-developer.md",
		".claude/rules/dotnet-00-routing.md",
		".claude/skills/dotnet-development/SKILL.md",
		".agents/skills/wf-dotnet-feature/SKILL.md",
		".claude/commands/wf-dotnet-feature.md",
		".claude/workflows/dotnet-feature-development.md",
		".codex/agents/dotnet-developer.toml",
	} {
		if _, err := os.Stat(filepath.Join(target, filepath.FromSlash(relative))); err != nil {
			t.Fatalf("expected .NET template output %s: %v", relative, err)
		}
	}
	for _, relative := range []string{
		".claude/agents/go-debugger.md",
		".claude/agents/unity-debugger.md",
		".claude/workflows/go-feature-development.md",
		".claude/workflows/wf-unity-bugfix.md",
	} {
		if _, err := os.Stat(filepath.Join(target, filepath.FromSlash(relative))); !os.IsNotExist(err) {
			t.Fatalf("unexpected non-.NET template output %s, err=%v", relative, err)
		}
	}
}
```

Also add `TemplateProjectTypeDotNet` to the table in
`TestInitializeProjectTemplatesIncludesTestDrivenChangePolicy`, so all four
profiles prove they retain shared TDD guidance.

Change `TestTemplateInitializationPreviewAndApplyEndpoints` to post
`"projectType":"dotnet"` and assert:

```go
if preview.ProjectType != catalog.TemplateProjectTypeDotNet {
	t.Fatalf("expected .NET preview, got %#v", preview)
}
```

- [ ] **Step 2: Run the new test before implementation**

Run:

```powershell
go test ./internal/catalog -run 'TestApplyTemplateInitializationCreatesDotNetProfile|TestInitializeProjectTemplatesIncludesTestDrivenChangePolicy' -count=1
```

Expected: FAIL because `TemplateProjectTypeDotNet` and the `.NET` template
assets do not exist.

- [ ] **Step 3: Add the validated `dotnet` profile**

In `internal/catalog/template_initializer.go`, add:

```go
const (
	TemplateProjectTypeGeneral = "general"
	TemplateProjectTypeGo      = "go"
	TemplateProjectTypeUnity   = "unity"
	TemplateProjectTypeDotNet  = "dotnet"
)
```

Extend validation:

```go
case TemplateProjectTypeGeneral, TemplateProjectTypeGo, TemplateProjectTypeUnity, TemplateProjectTypeDotNet:
	return absolutePath, projectType, nil
```

Add the `.NET` case to `includeTemplateInitializationPath`:

```go
case TemplateProjectTypeDotNet:
	return strings.HasPrefix(relativePath, ".claude/agents/dotnet-") ||
		strings.HasPrefix(relativePath, ".codex/agents/dotnet-") ||
		strings.HasPrefix(relativePath, ".claude/rules/dotnet-") ||
		strings.HasPrefix(relativePath, ".claude/skills/dotnet-") ||
		strings.HasPrefix(relativePath, ".claude/skills/review-feedback/") ||
		strings.HasPrefix(relativePath, ".agents/skills/wf-dotnet-") ||
		strings.HasPrefix(relativePath, ".claude/commands/wf-dotnet-") ||
		strings.HasPrefix(relativePath, ".claude/workflows/dotnet-")
```

Do not add a `.NET` special case to protected-path, conflict, or Codex MCP
configuration behavior; those remain common to every project type.

In `web/src/types.ts`, use:

```ts
export type TemplateProjectType = "general" | "go" | "unity" | "dotnet";
```

In the existing selector in `web/src/App.vue`, insert:

```vue
<option value="dotnet">.NET</option>
```

after the Unity option.

- [ ] **Step 4: Run focused backend and HTTP tests**

Run:

```powershell
go test ./internal/catalog -run 'TestApplyTemplateInitializationCreatesDotNetProfile|TestInitializeProjectTemplatesIncludesTestDrivenChangePolicy' -count=1
go test ./internal/httpapi -run TestTemplateInitializationPreviewAndApplyEndpoints -count=1
```

Expected: PASS once the assets from Task 2 exist; the HTTP response preserves
the exact `dotnet` project type.

### Task 2: Create the `.NET` roles, rules, and domain skills

**Files:**
- Create: `templates/.claude/agents/dotnet-developer.md`
- Create: `templates/.claude/agents/dotnet-debugger.md`
- Create: `templates/.claude/agents/dotnet-reviewer.md`
- Create: `templates/.codex/agents/dotnet-developer.toml`
- Create: `templates/.codex/agents/dotnet-debugger.toml`
- Create: `templates/.codex/agents/dotnet-reviewer.toml`
- Create: `templates/.claude/rules/dotnet-00-routing.md`
- Create: `templates/.claude/rules/dotnet-01-project-model.md`
- Create: `templates/.claude/rules/dotnet-02-runtime-safety.md`
- Create: `templates/.claude/rules/dotnet-03-library-compatibility.md`
- Create: `templates/.claude/skills/dotnet-development/SKILL.md`
- Create: `templates/.claude/skills/dotnet-testing/SKILL.md`
- Create: `templates/.claude/skills/dotnet-dependency-safety/SKILL.md`

- [ ] **Step 1: Write the template inventory assertion**

Add a catalog test in the existing template-inventory test file that reads the
library and requires the exact `.NET` IDs:

```go
for _, id := range []string{
	"dotnet-developer", "dotnet-debugger", "dotnet-reviewer",
	"dotnet-00-routing", "dotnet-01-project-model",
	"dotnet-02-runtime-safety", "dotnet-03-library-compatibility",
	"dotnet-development", "dotnet-testing", "dotnet-dependency-safety",
} {
	if !containsTemplateID(items, id) {
		t.Fatalf("expected .NET template %q in %#v", id, items)
	}
}
```

Use the existing test helper in that file instead of introducing a duplicate
helper. For every agent, assert both:

```text
templates/.claude/agents/dotnet-<id>.md
templates/.codex/agents/dotnet-<id>.toml
```

appear in `SourcePaths`.

- [ ] **Step 2: Run the inventory assertion before registration**

Run:

```powershell
go test ./internal/catalog -run DotNet -count=1
```

Expected: FAIL because the catalog does not yet contain the required IDs.

- [ ] **Step 3: Write the agent and rule assets**

Give each Claude agent YAML front matter with the exact IDs:

```yaml
---
name: dotnet-developer
description: "Implement focused .NET class-library and hosted-service changes with build and test evidence."
model: gpt-5.4
effort: high
maxTurns: 30
---
```

Use equivalent front matter for `dotnet-debugger` and `dotnet-reviewer`; their
descriptions must respectively require reproduction/root-cause evidence and
read-only compatibility/runtime/dependency review.

For every matching TOML file, use the same `name`, a role-specific
`description`, `model = "gpt-5.4"`, and
`model_reasoning_effort = "high"`. The `developer_instructions` must direct
the role to:

```text
1. Read AGENTS.md and the applicable dotnet-* rules and skills.
2. Inspect global.json, solution files, project files, Directory.Build.*,
   Directory.Packages.*, and existing tests before changing conventions.
3. Use the narrowest relevant dotnet restore/build/test command.
4. Report exact files, commands/results, and remaining risks in Chinese.
```

Write these rule requirements:

- `dotnet-00-routing.md`: `$wf-dotnet-feature`,
  `$wf-dotnet-bugfix`, and `$wf-dotnet-review` are the three entrypoints.
- `dotnet-01-project-model.md`: inspect existing SDK/TFM, build properties,
  package management, analyzers, test projects, and CI before adding
  conventions; do not create `global.json`.
- `dotnet-02-runtime-safety.md`: preserve `CancellationToken` propagation,
  `IHostedService`/`BackgroundService` lifecycle, `IDisposable`/
  `IAsyncDisposable`, timeout/retry/idempotency, structured logs, and
  secret-safe configuration.
- `dotnet-03-library-compatibility.md`: identify public API, nullable,
  exception, package, and versioning impact before breaking compatibility.

- [ ] **Step 4: Write the domain skills**

`dotnet-development/SKILL.md` must contain this SDK precedence, without
requiring a fixed version:

```text
1. Respect global.json when it exists.
2. Respect existing TargetFramework or TargetFrameworks values.
3. For new-project guidance only, prefer net10.0; allow net9.0 when the
   repository, dependency, or deployment constraint requires it.
4. Do not change global.json, target frameworks, or package versions unless
   the user task explicitly requires that change.
```

`dotnet-testing/SKILL.md` must require discovery before commands:

```text
Locate the documented CI command, then select the narrowest valid target:
dotnet restore <solution-or-project>
dotnet build <solution-or-project> --no-restore
dotnet test <solution-or-project> --no-build
```

It must prohibit claiming a command passed when it was not run.

`dotnet-dependency-safety/SKILL.md` must require checking
`Directory.Packages.props`, package references, lock-file conventions, package
compatibility, and existing vulnerability/licensing policy before adding or
updating a NuGet dependency.

- [ ] **Step 5: Run the inventory test**

Run:

```powershell
go test ./internal/catalog -run DotNet -count=1
```

Expected: still FAIL until Task 3 registers the assets; retain this red result
as evidence that file presence alone does not make the AI configuration
usable through the catalog.

### Task 3: Add `.NET` workflow assets and Codex entry skills

**Files:**
- Create: `templates/.agents/skills/wf-dotnet-feature/SKILL.md`
- Create: `templates/.agents/skills/wf-dotnet-feature/agents/openai.yaml`
- Create: `templates/.agents/skills/wf-dotnet-bugfix/SKILL.md`
- Create: `templates/.agents/skills/wf-dotnet-bugfix/agents/openai.yaml`
- Create: `templates/.agents/skills/wf-dotnet-review/SKILL.md`
- Create: `templates/.agents/skills/wf-dotnet-review/agents/openai.yaml`
- Create: `templates/.claude/commands/wf-dotnet-feature.md`
- Create: `templates/.claude/commands/wf-dotnet-bugfix.md`
- Create: `templates/.claude/commands/wf-dotnet-review.md`
- Create: `templates/.claude/workflows/dotnet-feature-development.md`
- Create: `templates/.claude/workflows/dotnet-feature-development.graph.json`
- Create: `templates/.claude/workflows/dotnet-bugfix.md`
- Create: `templates/.claude/workflows/dotnet-bugfix.graph.json`
- Create: `templates/.claude/workflows/dotnet-code-review.md`
- Create: `templates/.claude/workflows/dotnet-code-review.graph.json`

- [ ] **Step 1: Define the expected workflow asset set in a test**

Add a test equivalent to the existing Unity workflow template test. It must
require the six workflow Markdown/graph files and these three skill entry
files:

```go
workflowFiles := []string{
	"templates/.claude/workflows/dotnet-feature-development.md",
	"templates/.claude/workflows/dotnet-feature-development.graph.json",
	"templates/.claude/workflows/dotnet-bugfix.md",
	"templates/.claude/workflows/dotnet-bugfix.graph.json",
	"templates/.claude/workflows/dotnet-code-review.md",
	"templates/.claude/workflows/dotnet-code-review.graph.json",
}
skillFiles := []string{
	"templates/.agents/skills/wf-dotnet-feature/SKILL.md",
	"templates/.agents/skills/wf-dotnet-bugfix/SKILL.md",
	"templates/.agents/skills/wf-dotnet-review/SKILL.md",
}
```

Assert each workflow contains `dotnet-00-routing.md`, a `.NET` Task Run
workflow type, and `nexus-taskrun-submit`; assert every graph has the matching
workflow ID and owner role.

- [ ] **Step 2: Run the workflow asset test before creation**

Run:

```powershell
go test ./internal/catalog -run DotNetWorkflowTemplates -count=1
```

Expected: FAIL because none of the `.NET` workflow assets exist.

- [ ] **Step 3: Write the workflow entry skills and command files**

Each `wf-dotnet-*/SKILL.md` uses this exact shape:

```markdown
---
name: wf-dotnet-feature
description: .NET class-library and hosted-service feature workflow entry.
---

# wf-dotnet-feature

## Invocation

- Codex skill trigger: `$wf-dotnet-feature`

## Workflow

Read `.claude/workflows/dotnet-feature-development.md` from the repository
root and follow it as the source of truth. Treat the user's remaining prompt
as the workflow input.
```

Use the corresponding bugfix/review names and Markdown workflow paths for the
other two skills. Add an `agents/openai.yaml` to each skill, matching the
existing repository skill-package layout.

Each command file must direct Claude to invoke the corresponding `$wf-dotnet-*`
skill and load its matching workflow file.

- [ ] **Step 4: Write bounded `.NET` workflows and graphs**

All three workflow Markdown files must:

1. identify `templates/.claude/rules/dotnet-00-routing.md` as the routing
   source;
2. require loading `AGENTS.md`, applicable `dotnet-*` rules, and relevant
   skills before edits;
3. require a target contract, a narrow change, actual build/test evidence, and
   a final risk report;
4. use the Task Run helper with one of these exact types:

```text
dotnet-feature-development
dotnet-bugfix
dotnet-code-review
```

5. submit evidence through `.agents/skills/nexus-taskrun-submit/`.

The feature workflow owner is `dotnet-developer` and includes the
project-model/runtime-safety/compatibility gates. The bugfix workflow owner is
`dotnet-debugger` and requires reproduction, root cause, minimal fix, and
regression evidence. The review workflow owner is `dotnet-reviewer` and is
read-only unless the user explicitly asks for changes.

Every graph must use the workflow ID as `id`, a `.NET` title as `name`, and a
first agent node with the exact owner:

```json
{
  "id": "dotnet-feature-development",
  "name": ".NET Feature Development",
  "nodes": [
    {
      "id": "dotnet-developer",
      "type": "agent",
      "category": "execution",
      "label": "Implement .NET change",
      "agent": "dotnet-developer"
    }
  ],
  "edges": []
}
```

Use `dotnet-debugger` / `dotnet-reviewer` and matching names for the other
two graph files.

- [ ] **Step 5: Run the workflow asset test**

Run:

```powershell
go test ./internal/catalog -run DotNetWorkflowTemplates -count=1
```

Expected: PASS once every required Markdown, graph, and skill asset exists and
references the correct routing/evidence protocol.

### Task 4: Register `.NET` resources and resolve catalog workflow paths

**Files:**
- Modify: `internal/catalog/catalog.go:644-672`
- Modify: `internal/catalog/catalog.go:1740-1850`
- Modify: `internal/catalog/catalog.go:1960-2020`
- Modify: `internal/catalog/catalog.go:2135-2240`
- Modify: `internal/catalog/catalog.go:2280-2375`
- Modify: `internal/catalog/catalog.go:2470-2530`
- Modify: `internal/catalog/unity_templates_test.go` or create `internal/catalog/dotnet_templates_test.go`
- Modify: `internal/httpapi/server_test.go:650-780`

- [ ] **Step 1: Write catalog registration and source-path tests**

Create `internal/catalog/dotnet_templates_test.go` with:

```go
func TestDotNetTemplateCatalogPaths(t *testing.T) {
	library := NewStore().Snapshot().TemplateLibrary
	assertTemplateIDs(t, library.Agents, []string{
		"dotnet-developer", "dotnet-debugger", "dotnet-reviewer",
	})
	assertTemplateIDs(t, library.Rules, []string{
		"dotnet-00-routing", "dotnet-01-project-model",
		"dotnet-02-runtime-safety", "dotnet-03-library-compatibility",
	})
	assertTemplateIDs(t, library.Skills, []string{
		"dotnet-development", "dotnet-testing", "dotnet-dependency-safety",
		"wf-dotnet-feature", "wf-dotnet-bugfix", "wf-dotnet-review",
	})
	assertTemplateIDs(t, library.Workflows, []string{
		"dotnet-feature-development", "dotnet-bugfix", "dotnet-code-review",
	})
}
```

Add direct assertions:

```go
if path := btdWorkflowTemplatePath("dotnet-feature-development"); path != "templates/.claude/workflows/dotnet-feature-development.md" {
	t.Fatalf("unexpected .NET workflow path: %q", path)
}
if skill, ok := workflowSkillTemplateID("dotnet-bugfix"); !ok || skill != "wf-dotnet-bugfix" {
	t.Fatalf("unexpected .NET workflow skill: %q, %v", skill, ok)
}
```

For each workflow, assert `SourcePaths` contains its workflow Markdown, graph,
`templates/.claude/rules/dotnet-00-routing.md`, and its matching `.agents`
workflow skill.

- [ ] **Step 2: Run the catalog test before registration**

Run:

```powershell
go test ./internal/catalog -run TestDotNetTemplateCatalogPaths -count=1
```

Expected: FAIL because the static catalog does not yet return `.NET` items.

- [ ] **Step 3: Add agents, rules, and skills to the catalog**

Follow the existing `btdAgentTemplates`, `btdRuleTemplates`, and
`btdSkillTemplates` item shape. Register:

```text
Agents:
dotnet-developer
dotnet-debugger
dotnet-reviewer

Rules:
dotnet-00-routing
dotnet-01-project-model
dotnet-02-runtime-safety
dotnet-03-library-compatibility

Skills:
dotnet-development
dotnet-testing
dotnet-dependency-safety
wf-dotnet-feature
wf-dotnet-bugfix
wf-dotnet-review
```

Give `.NET` items `tools: []string{"shell", "rg", "git"}`. Do not assign
`unity-mcp` or `unityMCP`. Make the workflow-skill entries applicable to their
corresponding owner agents.

Extend `btdSkillTemplatePath` so only the three `.NET` workflow skill IDs map
to `.agents/skills/`; domain skill IDs continue to use `.claude/skills/`:

```go
case "wf-dotnet-feature", "wf-dotnet-bugfix", "wf-dotnet-review":
	return "templates/.agents/skills/" + id + "/SKILL.md"
```

- [ ] **Step 4: Add workflow IDs and path mappings**

Extend `workflowSkillTemplateID`:

```go
case "dotnet-feature-development":
	return "wf-dotnet-feature", true
case "dotnet-bugfix":
	return "wf-dotnet-bugfix", true
case "dotnet-code-review":
	return "wf-dotnet-review", true
```

Add:

```go
func isDotNetWorkflowID(id string) bool {
	switch id {
	case "dotnet-feature-development", "dotnet-bugfix", "dotnet-code-review":
		return true
	default:
		return false
	}
}
```

Use it in `workflowTemplateSourcePaths`:

```go
routing := "templates/.claude/rules/go-00-routing.md"
if isUnityWorkflowID(id) {
	routing = "templates/.claude/rules/unity-00-routing.md"
} else if isDotNetWorkflowID(id) {
	routing = "templates/.claude/rules/dotnet-00-routing.md"
}
```

Map `btdWorkflowTemplatePath` explicitly:

```go
case "dotnet-feature-development":
	return "templates/.claude/workflows/dotnet-feature-development.md"
case "dotnet-bugfix":
	return "templates/.claude/workflows/dotnet-bugfix.md"
case "dotnet-code-review":
	return "templates/.claude/workflows/dotnet-code-review.md"
```

Add three `btdWorkflowSpec` entries with the exact IDs, triggers, owners,
descriptions, `.NET` tags, and `ready` status. Do not reuse Go workflow IDs:
the existing IDs already identify Go assets.

- [ ] **Step 5: Extend the HTTP template-inventory test**

In `TestBtdGameServerTemplateInventory`, add the `.NET` agent, rule, skill,
and workflow IDs to the expected inventory lists. For every `.NET` workflow,
assert:

```go
if !containsString(workflow.SourcePaths, "templates/.claude/rules/dotnet-00-routing.md") {
	t.Fatalf("expected .NET routing rule for %#v", workflow)
}
if !strings.HasPrefix(workflow.Entry, "templates/.claude/workflows/dotnet-") {
	t.Fatalf("expected .NET workflow markdown entry, got %#v", workflow)
}
```

- [ ] **Step 6: Run catalog and HTTP tests**

Run:

```powershell
go test ./internal/catalog -run 'TestDotNetTemplateCatalogPaths|TestDotNetWorkflowTemplates' -count=1
go test ./internal/httpapi -run 'TestBtdGameServerTemplateInventory|TestTemplateInitializationPreviewAndApplyEndpoints' -count=1
```

Expected: PASS; all `.NET` catalog entries resolve to files that exist and
their workflow source paths use `.NET` routing.

### Task 5: Teach Task Run inference about `.NET` workflows

**Files:**
- Modify: `web/src/workflow-task-run.ts:62-204`
- Modify: `web/tests/workflow-task-run.test.mjs`

- [ ] **Step 1: Write the failing `.NET` Task Run test**

Add this test:

```js
test("buildWorkflowRunDraft infers .NET feature payload", () => {
  const draft = buildWorkflowRunDraft({
    projectId: "dotnet-worker",
    workflowTemplateId: "dotnet-feature-development",
    workflowName: ".NET Feature Development",
  });

  assert.equal(draft.workflowType, "dotnet-feature-development");
  assert.equal(draft.payload.context.agent, "dotnet-developer");
  assert.deepEqual(draft.payload.context.rules, [
    "dotnet-00-routing",
    "dotnet-01-project-model",
    "dotnet-02-runtime-safety",
    "dotnet-03-library-compatibility",
  ]);
  assert.deepEqual(draft.payload.context.skills, [
    "wf-dotnet-feature",
    "dotnet-development",
    "dotnet-testing",
  ]);
  assert.deepEqual(draft.payload.context.tools, ["workflow-graph"]);
  assert.deepEqual(draft.payload.evidence.tags, [
    "dotnet",
    "feature",
    "workflow-runner",
  ]);
});
```

Add short equivalent assertions for `dotnet-bugfix` and `dotnet-code-review`
to prove their different agents and entry skills.

- [ ] **Step 2: Run the unit test before implementation**

Run:

```powershell
Set-Location web
npm run test:unit
```

Expected: FAIL because `.NET` workflow IDs currently fall through to the
generic `workflow-run` defaults.

- [ ] **Step 3: Implement distinct `.NET` inference**

Place `.NET` checks before generic Go candidates in `inferWorkflowType`:

```ts
if (candidate.includes("dotnet-feature-development")) return "dotnet-feature-development";
if (candidate.includes("dotnet-bugfix")) return "dotnet-bugfix";
if (candidate.includes("dotnet-code-review")) return "dotnet-code-review";
```

Add the owner mapping:

```ts
if (workflowType === "dotnet-feature-development") return "dotnet-developer";
if (workflowType === "dotnet-bugfix") return "dotnet-debugger";
if (workflowType === "dotnet-code-review") return "dotnet-reviewer";
```

Return these exact rules:

```ts
case "dotnet-feature-development":
  return [
    "dotnet-00-routing",
    "dotnet-01-project-model",
    "dotnet-02-runtime-safety",
    "dotnet-03-library-compatibility",
  ];
case "dotnet-bugfix":
  return ["dotnet-00-routing", "dotnet-01-project-model", "dotnet-02-runtime-safety"];
case "dotnet-code-review":
  return ["dotnet-00-routing", "dotnet-03-library-compatibility"];
```

Return these exact skills:

```ts
case "dotnet-feature-development":
  return ["wf-dotnet-feature", "dotnet-development", "dotnet-testing"];
case "dotnet-bugfix":
  return ["wf-dotnet-bugfix", "dotnet-development", "dotnet-testing"];
case "dotnet-code-review":
  return ["wf-dotnet-review", "dotnet-dependency-safety"];
```

`.NET` uses only `["workflow-graph"]`. Do not add a Unity MCP tool.

Extend `buildEvidence` with one branch for each `.NET` workflow. The feature
branch adds `build`, `tests`, `sdkPolicy`, and tags
`["dotnet", "feature", "workflow-runner"]`; the bugfix branch adds
`reproduction`, `rootCause`, `build`, `tests`, and
`["dotnet", "bugfix", "workflow-runner"]`; the review branch adds
`apiCompatibility`, `runtimeSafety`, `dependencyImpact`, and
`["dotnet", "review", "workflow-runner"]`.

- [ ] **Step 4: Run frontend unit tests and production build**

Run:

```powershell
Set-Location web
npm run test:unit
npm run build
```

Expected: PASS; test output includes the existing Unity/Go cases and the three
new `.NET` workflow cases, and Vite writes an updated `web/dist`.

### Task 6: Run full verification and perform the requested uncommitted-diff AI configuration review

**Files:**
- Review: every changed file from Tasks 1-5
- Verify: `internal/catalog`, `internal/httpapi`, `web/`, generated `web/dist`

- [ ] **Step 1: Run focused and broad verification**

Run:

```powershell
go test ./internal/catalog -count=1
go test ./internal/httpapi -count=1
Set-Location web
npm run test:unit
npm run build
Set-Location ..
.\scripts\verify_all.ps1
git diff --check
```

Expected: every command exits `0`; `git diff --check` prints no whitespace
errors. If the broad script is blocked by an unavailable local dependency,
record the exact failed command and keep the focused results.

- [ ] **Step 2: Inspect actual `.NET` initialization output**

Use a fresh temporary target and run the existing initialization API or focused
catalog test. Verify the created file set contains:

```text
.claude/agents/dotnet-developer.md
.claude/rules/dotnet-00-routing.md
.claude/skills/dotnet-development/SKILL.md
.agents/skills/wf-dotnet-feature/SKILL.md
.claude/workflows/dotnet-feature-development.md
.codex/agents/dotnet-developer.toml
```

Verify the same target does not contain:

```text
.claude/agents/go-debugger.md
.claude/agents/unity-debugger.md
.claude/workflows/go-feature-development.md
.claude/workflows/wf-unity-bugfix.md
```

Also inspect the generated `.codex/config.toml`: it may contain the existing
managed CodeGraph MCP block, but must contain no model-provider, model-catalog,
or SDK pinning configuration.

- [ ] **Step 3: Review all uncommitted changes as an AI-configuration audit**

Run:

```powershell
git status --short
git diff -- templates internal/catalog internal/httpapi web/src web/tests web/dist
git diff --check
```

Use this review checklist:

1. Every catalog ID maps to an existing template file and every workflow
   Markdown/graph/command/skill uses the same ID family.
2. `.NET` profile selection copies all required shared and `dotnet-*` files,
   but zero Go/Unity-only assets.
3. Claude and Codex agent projections agree on role intent, do not reference
   `unity-mcp`, and tell agents to load the correct `.NET` rules/skills.
4. Templates respect existing `global.json`, TFM, package-management, and CI
   conventions; none creates or alters those files automatically.
5. Task Run inference selects the `.NET` workflow type, owner, rules, skills,
   non-Unity tool set, and evidence tags.
6. No change touches the protected global Codex files
   `C:\Users\Administrator\.codex\config.toml` or
   `C:\Users\Administrator\.codex\nexus-model-catalog.json`.
7. `web/dist` matches the source build after the `.NET` selector and Task Run
   changes.

Report review findings first, ordered by severity. If no issue is found, report
`No findings` and list the verification evidence plus any unavailable broad
check.

- [ ] **Step 4: Commit only after the user separately authorizes committing**

Do not run this step without explicit authorization:

```powershell
git add internal/catalog internal/httpapi templates web/src web/tests web/dist docs/superpowers/specs docs/superpowers/plans
git commit -m "feat: add dotnet project templates"
```
