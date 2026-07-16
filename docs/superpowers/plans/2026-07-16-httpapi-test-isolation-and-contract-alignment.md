# HTTP API Test Isolation and Contract Alignment Implementation Plan

> **For agentic workers:** Execute each task in order. Keep production changes
> limited to the one confirmed template-display defect; other changes align
> tests with current API and model contracts.

**Goal:** Make `internal/httpapi` tests deterministic against local user state,
align stale endpoint/model assertions with current contracts, and fix template
agent filename display.

**Architecture:** Test helpers inject an empty catalog store instead of calling
`NewServer()` against the user's persisted Nexus index. Route and model tests
assert the current public endpoints and split GPT-5.6 aliases. A catalog unit
test protects the `.claude` filename-display path before the production matcher
is corrected.

**Tech Stack:** Go standard library, `net/http/httptest`, catalog store,
codex-router default configuration.

---

## File structure

- `internal/httpapi/server_test.go`: Add an isolated-server helper; use it for
  tests that require empty project state; update stale template CRUD and model
  expectations.
- `internal/catalog/project_scan_test.go`: Extend agent template hydration
  coverage to require a `go-` filename display name and slug.
- `internal/catalog/project_scan.go`: Correct the `.claude` path-segment check
  used to derive template display names.

### Task 1: Isolate HTTP API tests from the user Nexus index

**Files:**
- Modify: `internal/httpapi/server_test.go`

- [x] Add a test helper that creates `catalog.NewStoreFromData` with
  `catalog.TemplateBootstrapData()` and injects it through
  `NewServerWithStore`, so tests do not call `NewStore()` and restore
  `C:\Users\Administrator\.nexus`.
- [x] Add package-level `TestMain` isolation for `HOME` and `USERPROFILE`, so
  tests that intentionally exercise default active-session or project-index
  persistence write only to a disposable temporary home.
- [x] Replace `NewServer()` with the helper in `TestBootstrapEndpoint`,
  `TestProjectEndpoints`, and `TestTemplateCrudEndpoints`.
- [x] Run:

```powershell
rtk go test ./internal/httpapi -run 'TestBootstrapEndpoint|TestProjectEndpoints' -count=1
```

Expected: PASS with an empty project list and without writing the user index.

### Task 2: Align Template CRUD test routing

**Files:**
- Modify: `internal/httpapi/server_test.go`

- [x] Change update/delete URLs from
  `/api/templates/.claude/rules/{id}` to `/api/templates/rules/{id}`, matching
  the server's `{kind}/{templateID}` route contract.
- [x] Run:

```powershell
rtk go test ./internal/httpapi -run TestTemplateCrudEndpoints -count=1
```

Expected: PASS.

### Task 3: Align model-route and model-catalog tests

**Files:**
- Modify: `internal/httpapi/server_test.go`

- [x] Replace obsolete generic `gpt-5.6` route expectations with the current
  subscription aliases: `gpt-5.6-sol`, `gpt-5.6-terra`, and
  `gpt-5.6-luna`.
- [x] In resolution subtests, remove the obsolete generic alias and add
  assertions for the three current aliases, all targeting themselves through
  `ChatGPT Subscription`.
- [x] In catalog/list assertions, require the current GPT aliases instead of
  `gpt-5.6`, while retaining DeepSeek, GLM, and Claude coverage. Include
  `kb-maintenance` in the template-inventory skill expectation, which was
  already present in the current catalog but absent from the stale test list.
  Keep skill `Name` and `Slug` aligned to their stable skill IDs; unlike agent
  templates, they intentionally do not inherit the `go-` filename prefix.
  Accept both `.claude/skills/` and `.agents/skills/` source roots because
  Codex support skills are intentionally published under `.agents`.
- [x] Run:

```powershell
rtk go test ./internal/httpapi -run 'TestModelRouteEndpoint|TestModelRouteResolveEndpoint|TestCodexRouterCatalogAndModelsEndpoints' -count=1
```

Expected: PASS.

### Task 4: Fix agent template display-name derivation

**Files:**
- Modify: `internal/catalog/project_scan_test.go`
- Modify: `internal/catalog/project_scan.go`

- [x] Extend `TestHydrateTemplateItemFromFilesPrefersCodexTomlModel` to assert
  `Name == "go-worker"` and `Slug == "go-worker"` for a source file under
  `templates/.claude/agents/go-worker.md`.
- [x] Run:

```powershell
rtk go test ./internal/catalog -run TestHydrateTemplateItemFromFilesPrefersCodexTomlModel -count=1
```

Expected: FAIL because the current filter searches for `/claude/` rather than
the `.claude` path segment.

- [x] Normalize an absolute or relative source path to its `templates/`
  segment, then change the agent-only filter in `templateFilenameStem` from
  `"/claude/"` to `"/.claude/"`.
- [x] Re-run the focused catalog test and:

```powershell
rtk go test ./internal/httpapi -run TestBtdGameServerTemplateInventory -count=1
```

Expected: PASS.

### Task 5: Final verification

**Files:**
- Review: `internal/httpapi/server_test.go`
- Review: `internal/catalog/project_scan.go`

- [x] Run:

```powershell
rtk go test ./internal/catalog ./internal/httpapi -count=1
rtk go test ./...
rtk git diff --check
```

Expected: targeted packages and repository test suite pass with no whitespace
errors. If unrelated packages still fail, report exact failures and do not
attribute them to this fix without evidence.
