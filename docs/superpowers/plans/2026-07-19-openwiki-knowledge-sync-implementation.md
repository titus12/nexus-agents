# OpenWiki Knowledge Synchronization Implementation Plan

**Goal:** Add AI-assisted scan-policy discovery, isolated OpenWiki generation,
existing-KB enrichment, Git-based incremental checks, and reviewable knowledge
proposals to the existing Nexus Project Knowledge Base surface.

**Architecture:** Project-owned policy and approved knowledge remain in
`KnowledgeBase/Setting.yaml` and `KnowledgeBase/project/**`. Nexus persists local
run state, executes OpenWiki in an isolated workspace, converts OpenWiki output
to the Nexus OKF profile, validates it, and applies only user-approved proposal
changes. CodeGraph remains the authoritative code-fact provider.

**Primary design:** `docs/superpowers/specs/2026-07-19-openwiki-knowledge-sync-design.md`

**Tech stack:** Go standard library and existing Nexus packages, OpenWiki CLI,
CodeGraph CLI/MCP integration, Vue 3, TypeScript, Vite.

---

## File structure

### New backend packages

- `internal/wikicompiler/provider.go`
  - Define compiler-neutral initialize/update contracts.
- `internal/wikicompiler/openwiki.go`
  - Check the pinned OpenWiki version, run the CLI, and capture output.
- `internal/wikicompiler/workspace.go`
  - Create and clean isolated Git workspaces safely.
- `internal/wikicompiler/instructions.go`
  - Generate staging `openwiki/INSTRUCTIONS.md`.
- `internal/wikicompiler/normalize.go`
  - Translate OpenWiki OKF output to the Nexus repository profile.
- `internal/knowledgesync/profile.go`
  - Parse, validate, and serialize `KnowledgeBase/Setting.yaml`.
- `internal/knowledgesync/discovery.go`
  - Build Git-tracked repository inventory and AI scan-policy proposals.
- `internal/knowledgesync/git.go`
  - Resolve branch/HEAD, committed diffs, and history-rewrite fallback.
- `internal/knowledgesync/classifier.go`
  - Classify code/docs/contracts/config/tests/generated/sensitive changes.
- `internal/knowledgesync/state.go`
  - Persist per-project sync state and run metadata.
- `internal/knowledgesync/proposal.go`
  - Model proposal changes, evidence, validation, and status.
- `internal/knowledgesync/service.go`
  - Orchestrate discovery, initialization, adoption, update checks, and apply.
- `internal/knowledgesync/scheduler.go`
  - Poll enabled projects and call the same update service used manually.

### Existing backend changes

- `internal/catalog/infrastructure.go`
  - Add OpenWiki as an installable/checkable infrastructure item.
- `internal/catalog/template_initializer.go`
  - Commit `KnowledgeBase/Setting.yaml` while preserving existing project KB
    protection.
- `internal/catalog/project_template_sync.go`
  - Protect `KnowledgeBase/Setting.yaml`.
- `internal/catalog/project_scan.go`
  - Include sync summary in project-level knowledge state when useful.
- `internal/knowledgebase/frontmatter.go`
  - Parse approved source/management extension fields.
- `internal/knowledgebase/types.go`
  - Add extension fields needed for provenance and management.
- `internal/knowledgebase/validate.go`
  - Validate approved provenance fields without treating `Setting.yaml` as a
    concept document.
- `internal/httpapi/server.go`
  - Add project knowledge sync/profile/run/proposal routes.
- `cmd/nexus-agents/main.go`
  - Start and stop the local scheduler through application lifecycle.

### Frontend changes

- `web/src/types.ts`
  - Add profile, discovery, run, status, proposal, and diff types.
- `web/src/api.ts`
  - Add typed knowledge synchronization API calls.
- `web/src/App.vue`
  - Add the Knowledge Base `同步` tab, initialization flow, policy preview, and
    proposal review.
- `web/src/styles.css`
  - Add scan-policy, status, diff, and proposal styles.

### Templates and verification

- `templates/KnowledgeBase/Setting.yaml`
  - Optional default schema/template used by initialization preview, not blindly
    copied over an existing project policy.
- `templates/KnowledgeBase/framework/source-of-truth.md`
  - Document OpenWiki, proposal, and external-reference boundaries.
- `templates/KnowledgeBase/framework/sync-checklist.md`
  - Add initialization and incremental-maintenance gates.
- `templates/.agents/skills/kb-system-curator/SKILL.md`
  - Route generation requests through Nexus proposals when available.
- `templates/.agents/skills/kb-maintenance/SKILL.md`
  - Prefer Nexus sync/proposal reports.
- `scripts/verify_template_catalog.py`
  - Validate the new template/config boundary.

---

## Task 1: Setting.yaml ownership and protection

**Files:**

- Modify: `internal/catalog/template_initializer.go`
- Modify: `internal/catalog/template_initializer_test.go`
- Modify: `internal/catalog/project_template_sync.go`
- Modify: `internal/catalog/project_scan_test.go`
- Create: `templates/KnowledgeBase/Setting.yaml`

- [ ] **Step 1: Add failing ignore-block tests**

Verify project initialization writes:

```gitignore
KnowledgeBase/*
!KnowledgeBase/Setting.yaml
!KnowledgeBase/project/
!KnowledgeBase/project/**
```

Expected: existing tests fail until the setting path is preserved.

- [ ] **Step 2: Add failing template-sync protection tests**

Verify full template synchronization never overwrites:

```text
KnowledgeBase/Setting.yaml
KnowledgeBase/project/**
```

- [ ] **Step 3: Implement the protected-path rules**

Replace the single protected knowledge-path assumption with a helper that
protects the setting file and project subtree before destination access.

- [ ] **Step 4: Add the default template**

Add a minimal disabled/default `Setting.yaml` schema. Initialization uses it as a
schema baseline, not as a forced project value.

- [ ] **Step 5: Run catalog tests**

```powershell
go test ./internal/catalog -count=1
```

Expected: PASS.

---

## Task 2: Knowledge sync profile

**Files:**

- Create: `internal/knowledgesync/profile.go`
- Create: `internal/knowledgesync/profile_test.go`

- [ ] **Step 1: Define profile types**

Model:

- knowledge root/language/update mode/default branch;
- discovery provenance;
- scan rules and limits;
- instructions;
- CodeGraph options;
- pinned OpenWiki version;
- schedule;
- owners and approval.

- [ ] **Step 2: Add parser and serializer tests**

Cover:

- missing file;
- valid profile;
- invalid version/action/pattern;
- exclusion precedence;
- absolute and escaping paths;
- normalization of path separators;
- deterministic serialization.

- [ ] **Step 3: Add hard safety policy**

Merge non-overridable secret/dependency/build exclusions after user/AI rules.

- [ ] **Step 4: Run package tests**

```powershell
go test ./internal/knowledgesync -run Profile -count=1
```

Expected: PASS.

---

## Task 3: Repository inventory and AI scan-policy discovery

**Files:**

- Create: `internal/knowledgesync/discovery.go`
- Create: `internal/knowledgesync/discovery_test.go`
- Modify: `internal/codexrouter` only if a reusable structured model client is
  required; otherwise define a narrow injected discovery client.

- [ ] **Step 1: Build deterministic inventory tests**

Use temporary Git repositories to verify:

- only tracked files are included by default;
- tree depth and counts are bounded;
- manifests and README metadata are detected;
- binary/large/sensitive paths are excluded;
- existing KB and Setting summaries are included;
- no source body is loaded during the first pass.

- [ ] **Step 2: Define structured proposal output**

The AI returns JSON only:

```text
revision
rules
requiredTopics
uncertain
warnings
```

- [ ] **Step 3: Add validation and fallback**

If AI output is malformed or unavailable:

- return the deterministic auto-discovery profile;
- report that AI refinement was skipped;
- do not block manual initialization.

- [ ] **Step 4: Add uncertain-path drill-down**

Allow one bounded follow-up using README/file-name samples and CodeGraph summary.

- [ ] **Step 5: Run tests**

```powershell
go test ./internal/knowledgesync -run Discovery -count=1
```

Expected: PASS.

---

## Task 4: OpenWiki infrastructure and provider

**Files:**

- Modify: `internal/catalog/infrastructure.go`
- Modify: infrastructure tests in `internal/catalog`
- Create: `internal/wikicompiler/provider.go`
- Create: `internal/wikicompiler/openwiki.go`
- Create: `internal/wikicompiler/openwiki_test.go`

- [ ] **Step 1: Add OpenWiki infrastructure definition**

Expose:

```text
openwiki --version
npm install --global openwiki@<tested-version>
```

Pin the tested version in Nexus. Upgrades are explicit rather than silently
following latest.

- [ ] **Step 2: Define the compiler interface**

Support:

```go
Initialize(ctx, CompileInput) (GeneratedBundle, error)
Update(ctx, CompileInput) (GeneratedBundle, error)
```

- [ ] **Step 3: Implement subprocess execution**

Run:

```text
openwiki code --update --print
```

with:

- isolated working directory;
- timeout and cancellation;
- bounded captured output;
- provider/model configuration supplied by environment;
- no persisted secrets.

- [ ] **Step 4: Add fake-runner tests**

Cover:

- missing binary;
- wrong version;
- timeout;
- non-zero exit;
- successful bundle capture;
- redacted error output.

- [ ] **Step 5: Run tests**

```powershell
go test ./internal/wikicompiler -count=1
go test ./internal/catalog -run Infrastructure -count=1
```

Expected: PASS.

---

## Task 5: Isolated workspace, instructions, and normalization

**Files:**

- Create: `internal/wikicompiler/workspace.go`
- Create: `internal/wikicompiler/workspace_test.go`
- Create: `internal/wikicompiler/instructions.go`
- Create: `internal/wikicompiler/instructions_test.go`
- Create: `internal/wikicompiler/normalize.go`
- Create: `internal/wikicompiler/normalize_test.go`

- [ ] **Step 1: Test safe workspace creation**

Verify all resolved paths remain below the Nexus knowledge-sync data root.
Tests must not use the developer's active worktree.

- [ ] **Step 2: Seed OpenWiki from an existing KB**

Translate approved `KnowledgeBase/project/**` into staging `openwiki/**`.

- [ ] **Step 3: Generate INSTRUCTIONS.md**

Compose:

- Nexus source-of-truth and quality rules;
- project setting and required topics;
- scan manifest;
- code-first evidence precedence;
- optional initialization-only Feishu references.

- [ ] **Step 4: Ignore OpenWiki side effects**

Only `openwiki/**` enters conversion. Explicitly reject:

- root `AGENTS.md`;
- root `CLAUDE.md`;
- generated CI workflow;
- other out-of-scope files.

- [ ] **Step 5: Normalize the generated bundle**

Translate:

- root and nested indexes;
- resource/link paths;
- reserved documents;
- extension fields;
- UTF-8 encoding.

- [ ] **Step 6: Run validation**

Require the normalized output to pass the existing KnowledgeBase validator.

- [ ] **Step 7: Run tests**

```powershell
go test ./internal/wikicompiler -count=1
```

Expected: PASS.

---

## Task 6: Feishu initialization-only auxiliary sources

**Files:**

- Create: `internal/knowledgesync/external_source.go`
- Create: `internal/knowledgesync/external_source_test.go`
- Add a narrow Feishu client interface and test fake; keep credentials outside
  persisted project configuration.

- [ ] **Step 1: Define run-scoped external references**

Accept Feishu URLs only in initialization/check-and-enrich requests.

- [ ] **Step 2: Add temporary projection behavior**

Fetch and convert the source to Markdown below the isolated workspace. Preserve:

- document title;
- revision/update metadata;
- conversion status and warnings;
- source link for proposal evidence.

- [ ] **Step 3: Enforce auxiliary precedence**

Instructions and validation must prevent Feishu-only plans from being represented
as current implemented behavior.

- [ ] **Step 4: Delete or expire snapshots**

The default is `retainExternalSnapshots=false`. Credentials and raw private
content are not committed or written to `Setting.yaml`.

- [ ] **Step 5: Run tests**

```powershell
go test ./internal/knowledgesync -run External -count=1
```

Expected: PASS.

---

## Task 7: Git state, change classification, and proposals

**Files:**

- Create: `internal/knowledgesync/git.go`
- Create: `internal/knowledgesync/git_test.go`
- Create: `internal/knowledgesync/classifier.go`
- Create: `internal/knowledgesync/classifier_test.go`
- Create: `internal/knowledgesync/state.go`
- Create: `internal/knowledgesync/state_test.go`
- Create: `internal/knowledgesync/proposal.go`
- Create: `internal/knowledgesync/proposal_test.go`

- [ ] **Step 1: Add temporary Git repository tests**

Verify branch/HEAD and `name-status` diff behavior, including renamed/deleted
files and missing base revisions.

- [ ] **Step 2: Implement change classification**

Distinguish:

- KB-only;
- docs/contracts;
- stable code boundary;
- tests/formatting;
- generated/dependency;
- unknown/high-risk.

- [ ] **Step 3: Add CodeGraph impact seam**

Inject a narrow interface so tests can supply callers/callees/routes/tests
without invoking a real CodeGraph process.

- [ ] **Step 4: Persist state and runs**

Store under the Nexus user data root and use atomic write/rename behavior.

- [ ] **Step 5: Build deterministic proposal IDs**

Use project, branch, base/target revisions, profile hash, and compiler version.

- [ ] **Step 6: Add proposal application guards**

Before applying:

- confirm current HEAD;
- confirm project path;
- validate every destination;
- reject stale proposals;
- write only selected project-KB paths.

- [ ] **Step 7: Run tests**

```powershell
go test ./internal/knowledgesync -run "Git|Classif|State|Proposal" -count=1
```

Expected: PASS.

---

## Task 8: Synchronization service and scheduler

**Files:**

- Create: `internal/knowledgesync/service.go`
- Create: `internal/knowledgesync/service_test.go`
- Create: `internal/knowledgesync/scheduler.go`
- Create: `internal/knowledgesync/scheduler_test.go`
- Modify: `cmd/nexus-agents/main.go`

- [ ] **Step 1: Implement service operations**

Support:

```text
DiscoverPolicy
AdoptExisting
InitializePreview
CheckAndEnrich
CheckUpdates
ApplyProposal
RejectProposal
```

- [ ] **Step 2: Reuse existing KB operations**

After apply:

- scan;
- validate;
- maintenance;
- export;
- update project summary.

- [ ] **Step 3: Add per-project locking**

Reject or coalesce concurrent runs for the same project.

- [ ] **Step 4: Implement lightweight scheduler polling**

Check branch/HEAD only. Invoke the service only when a committed revision changed.

- [ ] **Step 5: Add restart tests**

Verify persisted status and pending proposals survive service recreation.

- [ ] **Step 6: Run tests**

```powershell
go test ./internal/knowledgesync -count=1
go test ./cmd/nexus-agents -count=1
```

Expected: PASS.

---

## Task 9: HTTP API

**Files:**

- Modify: `internal/httpapi/server.go`
- Modify: `internal/httpapi/server_test.go`

- [ ] **Step 1: Add endpoint tests**

Cover:

```text
GET  /api/projects/{id}/knowledge/sync-profile
PUT  /api/projects/{id}/knowledge/sync-profile
GET  /api/projects/{id}/knowledge/sync-status
POST /api/projects/{id}/knowledge/discovery-preview
POST /api/projects/{id}/knowledge/initialize-preview
POST /api/projects/{id}/knowledge/check-updates
GET  /api/projects/{id}/knowledge/runs
GET  /api/projects/{id}/knowledge/proposals
GET  /api/projects/{id}/knowledge/proposals/{proposalId}
POST /api/projects/{id}/knowledge/proposals/{proposalId}/apply
POST /api/projects/{id}/knowledge/proposals/{proposalId}/reject
```

- [ ] **Step 2: Add method and path guards**

Require existing project lookup and local path resolution. Return conflict for
stale proposals or already-running project jobs.

- [ ] **Step 3: Add request limits**

Bound external references, path-rule counts, instructions length, and response
diff size.

- [ ] **Step 4: Run HTTP tests**

```powershell
go test ./internal/httpapi -count=1
```

Expected: PASS.

---

## Task 10: Frontend synchronization and proposal review

**Files:**

- Modify: `web/src/types.ts`
- Modify: `web/src/api.ts`
- Modify: `web/src/App.vue`
- Modify: `web/src/styles.css`
- Add focused frontend tests where the current repository test style supports
  them.

- [ ] **Step 1: Add types and API calls**

Model:

- profile;
- discovery result;
- sync status;
- run;
- proposal/change/evidence;
- apply/reject inputs.

- [ ] **Step 2: Add the Knowledge Base Sync tab**

Show:

- branch, HEAD, last processed commit;
- OpenWiki/CodeGraph status;
- discovered file categories;
- schedule;
- last run and pending proposal.

- [ ] **Step 3: Add initialization choices**

```text
No KB:
  Generate scan policy
  Review and initialize

Existing KB:
  Adopt existing
  Check and enrich
```

- [ ] **Step 4: Add policy preview**

Display included/excluded/uncertain patterns with reason and confidence.

- [ ] **Step 5: Add proposal review**

Display:

- base/target commit;
- create/update/move/deprecate changes;
- before/after diff;
- evidence and warnings;
- validation status;
- selective apply and reject.

- [ ] **Step 6: Build frontend**

```powershell
cd web
npm run build
```

Expected: PASS.

---

## Task 11: Knowledge framework and skill alignment

**Files:**

- Modify: `templates/KnowledgeBase/framework/source-of-truth.md`
- Modify: `templates/KnowledgeBase/framework/sync-checklist.md`
- Modify: `templates/KnowledgeBase/framework/quality-gates.md`
- Modify: `templates/.agents/skills/kb-system-curator/SKILL.md`
- Modify: `templates/.agents/skills/kb-maintenance/SKILL.md`
- Modify: `scripts/verify_template_catalog.py`

- [ ] **Step 1: Document source boundaries**

Record:

- CodeGraph owns current code facts;
- OpenWiki proposes generated knowledge;
- Nexus owns validation and approval;
- Feishu is initialization-only auxiliary evidence;
- pending proposals are not stable knowledge.

- [ ] **Step 2: Route skills through Nexus**

When the new APIs are available, skills request discovery/proposals instead of
directly authoring broad KB updates.

- [ ] **Step 3: Validate templates**

Ensure Setting is protected/project-owned and framework templates remain
portable.

- [ ] **Step 4: Run verification**

```powershell
python scripts/verify_template_catalog.py
```

Expected: PASS.

---

## Task 12: Full verification and rollout guard

- [ ] **Step 1: Run focused tests**

```powershell
go test ./internal/catalog -count=1
go test ./internal/knowledgebase -count=1
go test ./internal/wikicompiler -count=1
go test ./internal/knowledgesync -count=1
go test ./internal/httpapi -count=1
```

- [ ] **Step 2: Run all Go tests**

```powershell
go test ./...
```

- [ ] **Step 3: Build frontend**

```powershell
cd web
npm run build
```

- [ ] **Step 4: Run project verification**

```powershell
.\scripts\verify_all.ps1
```

- [ ] **Step 5: Run a three-project acceptance exercise**

Verify:

1. no-KB initialization;
2. existing-KB adoption;
3. existing-KB enrichment;
4. no-change scheduled check;
5. docs-only update;
6. route/schema code update;
7. stale-proposal rejection;
8. Feishu auxiliary initialization;
9. restart recovery;
10. retrieval after approved apply.

- [ ] **Step 6: Keep rollout proposal-only**

Do not enable automatic KB application or Git operations in the first release.

---

## Review decisions requested before implementation

1. Confirm `KnowledgeBase/Setting.yaml` as the committed project policy path.
2. Confirm OpenWiki output remains temporary and only normalized proposals enter
   `KnowledgeBase/project/**`.
3. Confirm the first release is local and proposal-only, with no automatic Git
   branch/commit/PR creation.
4. Confirm scheduled checks use committed changes only.
5. Confirm Feishu is initialization-only auxiliary evidence.
6. Confirm GBrain and cross-project semantic graph remain deferred until this
   project-level pipeline is stable.

