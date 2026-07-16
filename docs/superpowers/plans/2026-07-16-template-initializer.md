# Template Initializer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Execute this plan inline in the current session. Do not stage, commit, push, or otherwise perform Git write or remote operations without separate explicit user authorization.

**Goal:** Let a user preview and safely initialize a Go, Unity, or general project from the public template library without overwriting existing AI configuration or anything under `KnowledgeBase/project/`.

**Architecture:** Add catalog-level planning and applying functions that derive a profile-specific allowlist from `templates/`, validate every target path, and only create missing files. Add HTTP preview/apply endpoints, then add a Templates-page wizard that requires preview before Apply. Remove legacy project-root `.nexus` workflow migration so import becomes non-destructive.

**Tech Stack:** Go HTTP API and catalog store; Vue 3 / TypeScript frontend; existing template catalog and project scanner.

---

### Task 1: Remove destructive legacy import migration

**Files:**
- Modify: `internal/catalog/local_import.go`
- Modify: `internal/catalog/project_scan_test.go`

- [ ] Remove the `migrateLegacyNexusDirectory` call from import preparation and remove the migration function.
- [ ] Replace migration tests with coverage that import leaves an existing project-root `.nexus` directory untouched.

### Task 2: Build initializer planning and safe apply logic

**Files:**
- Modify: `internal/catalog/catalog.go`
- Create: `internal/catalog/template_initializer.go`
- Create: `internal/catalog/template_initializer_test.go`

- [ ] Define general, Go, and Unity template profiles with a source-relative allowlist.
- [ ] Produce preview entries with `create`, `unchanged`, `conflict`, or `protected` actions.
- [ ] Reject paths outside the target root and always protect `KnowledgeBase/project/**`.
- [ ] Apply only `create` entries, rechecking the target state before writing.

### Task 3: Expose preview and apply endpoints

**Files:**
- Modify: `internal/httpapi/server.go`
- Modify: `internal/httpapi/server_test.go`

- [ ] Add `POST /api/templates/initialize/preview`.
- [ ] Add `POST /api/templates/initialize/apply`.
- [ ] Validate request payloads, project type, absolute target directory, and a server-side initialization plan token.
- [ ] Return detailed write results and retain the existing import endpoint behavior.

### Task 4: Add the Templates-page initializer UI

**Files:**
- Modify: `web/src/api.ts`
- Modify: `web/src/App.vue`

- [ ] Add project-type selection, target path input, preview action, and an explicit Apply action.
- [ ] Present a summary and per-file status before Apply.
- [ ] Keep conflicts and protected paths non-writable in the UI.
- [ ] Offer project import only after initialization completes; do not import automatically.

### Task 5: Validate

**Files:**
- Test: `internal/catalog/template_initializer_test.go`
- Test: `internal/httpapi/server_test.go`

- [ ] Run focused catalog and HTTP API tests.
- [ ] Run template catalog verification.
- [ ] Build the frontend when local dependencies are available.
