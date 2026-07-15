# Template Project-Root Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert `templates/` into a project-root overlay that installs directly into a project while moving the canonical KnowledgeBase root to `KnowledgeBase/`.

**Architecture:** The template filesystem becomes the source of exact project-relative destinations. Catalog metadata and project synchronization derive destinations by stripping the `templates/` prefix, while the UI preserves its existing component categories. KnowledgeBase adopts a canonical root with deterministic legacy discovery for existing projects.

**Tech Stack:** Go, Vue, Markdown, TOML, YAML, PowerShell, Python, Go test.

---

### Task 1: Move the template package into project-root layout

**Files:**
- Move: `templates/project-files/AGENTS.md` to `templates/AGENTS.md`
- Move: `templates/agents/claude/**` to `templates/.claude/agents/**`
- Move: `templates/agents/codex/**` to `templates/.codex/agents/**`
- Move: `templates/commands/claude/**` to `templates/.claude/commands/**`
- Move: `templates/rules/**` to `templates/.claude/rules/**`
- Move: `templates/workflows/**` to `templates/.claude/workflows/**`
- Move: `templates/skills/codex/**` to `templates/.agents/skills/**`
- Move: `templates/knowledgebase/**` to `templates/KnowledgeBase/**`
- Delete: `templates/README.md`

- [ ] **Step 1: Create the target directories and move files without changing contents**

Preserve all filenames, graph sidecars, nested skill resources, and paired Agent files. Move only their parent layout.

- [ ] **Step 2: Convert portable Markdown skills into native skill folders**

For every former `templates/skills/<id>.md`, create `templates/.claude/skills/<id>/SKILL.md` with the same content. Place Codex-specific copies that btd-client needs under `templates/.codex/skills/<id>/` instead of relying on a runtime conversion.

- [ ] **Step 3: Create a minimal portable KnowledgeBase skeleton**

Keep framework documents, add `KnowledgeBase/index.md`, `KnowledgeBase/log.md`, and `KnowledgeBase/project/.gitkeep`. Do not copy btd-client project facts into templates.

- [ ] **Step 4: Remove the old categorized directories and README**

Delete empty legacy layout directories and `templates/README.md`; move explanatory documentation to `docs/` if needed.

### Task 2: Refactor catalog paths and direct installation

**Files:**
- Modify: `internal/catalog/catalog.go`
- Modify: `internal/catalog/project_scan.go`
- Modify: `internal/catalog/project_scan_test.go`
- Modify: `internal/catalog/unity_templates_test.go`
- Modify: `internal/httpapi/server_test.go`

- [ ] **Step 1: Replace old template path constructors**

Change Agent, Rule, Skill, Workflow, command, and project-root template path functions to use the new exact `templates/.claude/...`, `templates/.codex/...`, `templates/.agents/...`, `templates/AGENTS.md`, and `templates/KnowledgeBase/...` paths.

- [ ] **Step 2: Make write destinations direct**

Replace kind-based output path construction in `projectTemplateWrites`, `agentTemplateWrites`, `workflowTemplateWrites`, Codex skill copy helpers, and workflow command helpers. Each write target must be the source template path with the leading `templates/` removed.

- [ ] **Step 3: Update scan matching**

Scan `.claude`, `.codex`, and `.agents` project paths as before, but match templates by their new direct layout prefixes. Preserve paired Agent deduplication and workflow graph discovery.

- [ ] **Step 4: Update metadata and test fixtures**

Update `SourcePaths`, `Entry`, `Files`, expected template inventory paths, and scan fixtures to assert the direct-layout locations.

### Task 3: Migrate canonical KnowledgeBase paths

**Files:**
- Modify: `internal/knowledgebase/types.go`
- Modify: `internal/knowledgebase/*.go`
- Modify: `internal/knowledgebase/*_test.go`
- Modify: `internal/catalog/evaluation.go`
- Modify: `internal/catalog/*_test.go`
- Modify: `internal/httpapi/server_test.go`
- Modify: `web/src/App.vue`
- Modify: template rules, workflows, skills, and KnowledgeBase documents that mention `design/KnowledgeBase`

- [ ] **Step 1: Set the canonical root**

Change `knowledgebase.DefaultRoot` from `design/KnowledgeBase` to `KnowledgeBase`, and update retrieval regexes, generated messages, evaluation evidence parsing, and UI root selection.

- [ ] **Step 2: Add legacy-root detection**

Introduce a legacy root constant for `design/KnowledgeBase`. Use it only when the canonical root is absent; emit a migration warning. If both roots exist, report a conflict and do not merge documents.

- [ ] **Step 3: Update template content and structured metadata**

Replace all canonical references in template bodies, frontmatter `resource`, dependencies, skills, rules, and workflows with `KnowledgeBase/...`.

- [ ] **Step 4: Update all test fixtures**

Make canonical fixtures use `KnowledgeBase/...`; add explicit legacy-only and dual-root test cases for the required warning and conflict behavior.

### Task 4: Update tooling and frontend references

**Files:**
- Modify: `scripts/verify_template_catalog.py`
- Modify: `scripts/import_btd_templates.ps1`
- Modify: `scripts/verify_design.py`
- Modify: `web/src/App.vue`
- Modify: relevant documentation under `README.md`, `docs/`, and template skill references

- [ ] **Step 1: Update template structure assertions**

Assert the new direct layout, reject the legacy category roots, and assert that `templates/README.md` is absent.

- [ ] **Step 2: Update import and validation tools**

Make scripts use direct template-relative destinations and canonical `KnowledgeBase` paths.

- [ ] **Step 3: Update frontend path assumptions**

Use `KnowledgeBase/index.md` as the primary tree entry, while showing legacy status returned by the backend for migrated projects.

### Task 5: Verify direct-copy behavior

**Files:**
- Test: `internal/catalog/project_scan_test.go`
- Test: `internal/knowledgebase/*_test.go`
- Test: `internal/httpapi/server_test.go`

- [ ] **Step 1: Add direct-copy integration coverage**

Create a temporary project, copy each template file to the relative path obtained by removing `templates/`, then scan the project. Assert that its project config set identifies Agents, Rules, Skills, Workflows, root `AGENTS.md`, and `KnowledgeBase`.

- [ ] **Step 2: Run formatting and tests**

Run:

```powershell
gofmt -w internal/catalog internal/knowledgebase internal/httpapi
go test ./internal/catalog ./internal/knowledgebase ./internal/httpapi
python scripts/verify_template_catalog.py
```

Expected: PASS, with no old categorized template path or canonical `design/KnowledgeBase` reference except the documented legacy compatibility constant and tests.

- [ ] **Step 3: Review the final filesystem**

Run:

```powershell
rtk rg --files templates
rtk rg -n -F 'templates/agents/' .
rtk rg -n -F 'templates/rules/' .
rtk rg -n -F 'design/KnowledgeBase' templates internal web scripts
rtk git diff --check
```

Expected: the package uses only direct project-root paths, remaining `design/KnowledgeBase` references are limited to the legacy compatibility implementation and its tests, and the diff has no whitespace errors.
