# Agent GPT Model Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace non-GPT default models in Agent roles and Codex skill sub-agent defaults with the approved Codex GPT tier mapping.

**Architecture:** Agent model selection is duplicated between paired template files and the catalog registry. Update the executable Codex TOML templates, paired Claude source metadata, catalog `ModelTier` entries, and skill `openai.yaml` defaults together so API and file-based consumers agree.

**Tech Stack:** Go, TOML, Markdown, YAML, PowerShell, Go test.

---

### Task 1: Update Agent template model assignments

**Files:**
- Modify: `templates/agents/codex/go-*.toml`
- Modify: `templates/agents/codex/unity-*.toml`
- Modify: `templates/agents/claude/go-*.md`

- [ ] **Step 1: Apply the approved model mapping**

Set fast gatekeeping, lookup, quick-edit, and workflow evaluator roles to `gpt-5.6-luna`; standard worker, performance review, Unity debugging/review/regression roles to `gpt-5.4`; and model arbitration, logic/security review, and asset safety roles to `gpt-5.6-terra`.

- [ ] **Step 2: Align role-facing prose**

Replace every `deepseek-v4-pro`, `deepseek-v4-flash`, `claude-sonnet-*`, and `claude-opus-*` recommendation in the affected Agent templates with its selected GPT model.

- [ ] **Step 3: Verify template defaults**

Run:

```powershell
rtk rg -n -i --glob '*.toml' --glob '*.md' 'deepseek-v4-(pro|flash)|claude-(opus|sonnet)-[0-9]' templates/agents
```

Expected: no model-assignment or recommendation matches in Agent templates.

### Task 2: Synchronize catalog and skill default metadata

**Files:**
- Modify: `internal/catalog/catalog.go`
- Modify: `templates/skills/codex/wf-unity-bugfix/agents/openai.yaml`
- Modify: `templates/skills/codex/wf-unity-logic-mod/agents/openai.yaml`
- Modify: `templates/skills/codex/wf-unity-ui-feature/agents/openai.yaml`
- Modify: `templates/skills/codex/wf-unity-ui-quick/agents/openai.yaml`

- [ ] **Step 1: Update catalog model tiers**

Use the same role mapping as Task 1 for each corresponding Agent specification in `btdAgentTemplates`, preserving the role IDs, tools, skills, and reasoning efforts.

- [ ] **Step 2: Update Unity workflow sub-agent defaults**

Set each listed `openai.yaml` `models.default` value from `deepseek-v4-pro` to `gpt-5.4`.

- [ ] **Step 3: Format Go source**

Run:

```powershell
gofmt -w internal/catalog/catalog.go
```

Expected: command exits successfully without changing unrelated files.

### Task 3: Validate the synchronized configuration

**Files:**
- Test: `internal/catalog/project_scan_test.go`
- Test: `internal/httpapi/server_test.go`

- [ ] **Step 1: Run focused tests**

Run:

```powershell
go test ./internal/catalog ./internal/httpapi
```

Expected: PASS.

- [ ] **Step 2: Scan role configuration for residual non-GPT defaults**

Run:

```powershell
rtk rg -n -i --glob '*.toml' --glob '*.md' --glob 'openai.yaml' 'deepseek-v4-(pro|flash)|claude-(opus|sonnet)-[0-9]' templates/agents templates/skills/codex
```

Expected: no default model or recommendation assignments remain; router-specific references are excluded from the scan scope.

- [ ] **Step 3: Review the diff**

Run:

```powershell
rtk git diff --check
rtk git diff -- internal/catalog/catalog.go templates/agents templates/skills/codex
```

Expected: no whitespace errors and only model/default-model/documentation changes related to this migration.
