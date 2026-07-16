# Project Template Synchronization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a project Overview action that copies all eligible Nexus template files into one imported project, overwriting existing files and creating missing ones, then rescans the project.

**Architecture:** The catalog store owns the filesystem synchronization and returns a rescan-shaped result with copy counters. The HTTP server exposes it as a project-scoped POST route. The Vue client calls that route, replaces its project state using the returned rescan data, and reports the counters.

**Tech Stack:** Go standard library, existing `internal/catalog` store and scan logic, Go `net/http`, Vue 3, TypeScript, Vite.

---

## File structure

- `internal/catalog/catalog.go`: Define the template-sync result and store operation.
- `internal/catalog/project_template_sync.go`: Walk the template tree, reject protected and unsafe paths, copy regular files, and count outcomes.
- `internal/catalog/project_scan_test.go`: Verify catalog behavior using temporary template and project roots.
- `internal/httpapi/server.go`: Route `POST /api/projects/{id}/template-sync`.
- `internal/httpapi/server_test.go`: Verify successful and unknown-project API behavior.
- `web/src/types.ts`: Model the synchronization summary/result.
- `web/src/api.ts`: Add the typed API call.
- `web/src/App.vue`: Add the action, in-flight state, state refresh, and result toast.

### Task 1: Catalog synchronization

**Files:**
- Modify: `internal/catalog/catalog.go`
- Create: `internal/catalog/project_template_sync.go`
- Test: `internal/catalog/project_scan_test.go`

- [ ] **Step 1: Write failing catalog tests**

Create a temporary template root and project root. Verify the catalog operation:

```go
writeTestFile(t, templateRoot, ".claude/rules/01.md", "template rule")
writeTestFile(t, projectRoot, ".claude/rules/01.md", "project rule")
writeTestFile(t, templateRoot, ".agents/skills/demo/SKILL.md", "template skill")
writeTestFile(t, templateRoot, "KnowledgeBase/project/keep.md", "protected")
writeTestFile(t, projectRoot, "KnowledgeBase/project/keep.md", "project knowledge")

result, ok, err := store.SyncProjectTemplates("sample")
if err != nil || !ok { t.Fatalf("sync: ok=%v err=%v", ok, err) }
if result.Overwritten != 1 || result.Created != 1 || result.Skipped != 1 {
    t.Fatalf("unexpected counts: %#v", result)
}
```

Assert that the rule now equals `template rule`, the skill exists with template
content, the knowledge file remains `project knowledge`, and an unrelated project
file remains untouched. Assert that `result.Project` and `result.Copies` contain
the post-copy rescan state.

- [ ] **Step 2: Run the catalog test to verify it fails**

Run:

```powershell
rtk go test ./internal/catalog -run TestStoreSyncProjectTemplates -count=1
```

Expected: FAIL because `SyncProjectTemplates` does not exist.

- [ ] **Step 3: Define the result and implement safe file copy**

Add to `internal/catalog/catalog.go`:

```go
type ProjectTemplateSyncResult struct {
    Project     Project       `json:"project"`
    Copies      []ProjectCopy `json:"copies"`
    Overwritten int           `json:"overwritten"`
    Created     int           `json:"created"`
    Skipped     int           `json:"skipped"`
}
```

In `internal/catalog/project_template_sync.go`, resolve the project root with the
same `LocalPath` then `Path` precedence used by `RescanProject`; locate the Nexus
template directory from the process working directory; walk it with
`filepath.Walk`; skip non-regular files and any relative path equal to or below
`KnowledgeBase/project`; ensure `filepath.Rel(projectRoot, destination)` does not
start with `..`; create parent directories with `os.MkdirAll`; copy bytes with
`os.ReadFile` and `os.WriteFile`; count pre-existing destinations as overwritten
and missing destinations as created. Then call `RescanProject` and return its
project/copies plus counts.

- [ ] **Step 4: Run catalog tests**

Run:

```powershell
rtk go test ./internal/catalog -count=1
```

Expected: PASS.

### Task 2: HTTP API

**Files:**
- Modify: `internal/httpapi/server.go`
- Test: `internal/httpapi/server_test.go`

- [ ] **Step 1: Write failing endpoint tests**

Add a test that creates a store with an imported temporary project and invokes:

```go
response := requestJSON(t, NewServer(store), http.MethodPost, "/api/projects/sample/template-sync", nil)
if response.Code != http.StatusOK {
    t.Fatalf("expected template sync status 200, got %d: %s", response.Code, response.Body.String())
}
```

Decode `catalog.ProjectTemplateSyncResult` and assert the counters and copied
content. Add a missing-project request and assert `http.StatusNotFound`.

- [ ] **Step 2: Run the endpoint test to verify it fails**

Run:

```powershell
rtk go test ./internal/httpapi -run TestProjectTemplateSyncEndpoint -count=1
```

Expected: FAIL with 404 because the route is not registered.

- [ ] **Step 3: Route the new request**

In `handleProjectPath`, before the `rescan` branch, handle:

```go
if len(parts) == 2 && parts[1] == "template-sync" {
    if !allowMethods(w, r, http.MethodPost) {
        return
    }
    result, ok, err := s.store.SyncProjectTemplates(projectID)
    if err != nil {
        http.Error(w, err.Error(), http.StatusInternalServerError)
        return
    }
    if !ok {
        http.NotFound(w, r)
        return
    }
    writeJSON(w, http.StatusOK, result)
    return
}
```

- [ ] **Step 4: Run API tests**

Run:

```powershell
rtk go test ./internal/httpapi -count=1
```

Expected: PASS.

### Task 3: Frontend action

**Files:**
- Modify: `web/src/types.ts`
- Modify: `web/src/api.ts`
- Modify: `web/src/App.vue`

- [ ] **Step 1: Add frontend result types and API function**

Add:

```ts
export type ProjectTemplateSyncResult = ProjectRescanResult & {
  overwritten: number;
  created: number;
  skipped: number;
};
```

and:

```ts
export function syncProjectTemplates(projectId: string): Promise<ProjectTemplateSyncResult> {
  return fetchJSON<ProjectTemplateSyncResult>(`/api/projects/${encodeURIComponent(projectId)}/template-sync`, {
    method: "POST",
  });
}
```

- [ ] **Step 2: Add the current-project handler**

Import `syncProjectTemplates`, add an in-flight `ref(false)`, and implement a
handler that calls it, applies `result.project` and `result.copies` exactly as
`rescanCurrentProject` does, then displays:

```ts
showToast(`模板同步完成：覆盖 ${result.overwritten} 个，新增 ${result.created} 个，跳过 ${result.skipped} 个。`);
```

Reset the in-flight ref in `finally`.

- [ ] **Step 3: Add the button**

In the project-detail `page-actions` group, insert the following immediately
before `重新扫描`:

```vue
<button
  class="btn-secondary"
  type="button"
  :disabled="projectTemplateSyncing"
  @click="syncCurrentProjectTemplates"
>
  {{ projectTemplateSyncing ? "同步中..." : "同步 Nexus 模板" }}
</button>
```

- [ ] **Step 4: Run the frontend checks**

Run:

```powershell
rtk npm run build
```

Expected: PASS with a generated Vite production bundle.

### Task 4: Full verification and plan reconciliation

**Files:**
- Review: `docs/superpowers/specs/2026-07-16-project-template-sync-design.md`
- Review: `docs/superpowers/plans/2026-07-16-project-template-sync.md`

- [ ] **Step 1: Format changed Go source**

Run:

```powershell
rtk gofmt -w internal/catalog/catalog.go internal/catalog/project_template_sync.go internal/catalog/project_scan_test.go internal/httpapi/server.go internal/httpapi/server_test.go
```

Expected: command succeeds with no output.

- [ ] **Step 2: Run all project tests and build**

Run:

```powershell
rtk go test ./...
rtk npm run build
```

Expected: both commands PASS.

- [ ] **Step 3: Reconcile the implementation with the design**

Check each design requirement against the code and test evidence:

- Button is immediately left of `重新扫描`.
- Eligible template files are overwritten or created.
- `KnowledgeBase/project` is skipped before destination access.
- Target-only files are not deleted.
- The API returns counts and the post-sync rescan payload.
- The UI applies the payload and reports the three counts.

- [ ] **Step 4: Report the outcome without a Git commit**

Run:

```powershell
rtk git diff --check
rtk git status --short
```

Expected: no whitespace errors and a list of changed files. Do not stage or commit
because repository instructions require separate explicit authorization for Git
write operations.
