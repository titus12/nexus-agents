# Codex Project Configuration Design

**Date:** 2026-07-16  
**Status:** Approved design; awaiting written-spec review

## Goal

Add a small, Codex-only AI configuration layer that makes iterative work on
Nexus Agents more consistent without changing its model routing, provider
configuration, or non-Codex AI assets.

## Scope

Create four project-local configuration files:

| File | Responsibility |
|---|---|
| `AGENTS.md` | Repository-wide Codex guidance, architecture summary, validation commands, and safety constraints. |
| `.codex/agents/go-backend.toml` | Narrow role for Go services, HTTP APIs, catalog, workflow, knowledge-base, and Codex-router work. |
| `.codex/agents/vue-frontend.toml` | Narrow role for Vue, TypeScript, Vite, frontend tests, and embedded web-dist work. |
| `.codex/agents/reviewer.toml` | Read-only code-review role focused on regressions, API contracts, validation, generated artifacts, and protected configuration. |

All files will be UTF-8 without a BOM.

## Architecture

`AGENTS.md` is the durable repository-level instruction surface. It gives every
Codex task the same authoritative project context and commands, while keeping
task-specific behavior out of the main file.

The TOML files in `.codex/agents/` define narrow, explicitly selectable Codex
roles. They complement the common guidance instead of duplicating it. Each role
owns a distinct concern and states its expected verification commands.

No project `.codex/config.toml` will be created. The global Codex configuration
at `C:\Users\Administrator\.codex\config.toml` is managed for the
`nexus-codex` provider and must not be changed by this work.

## Required Guidance

### Repository guide

The root guide will state:

- The backend is Go 1.26 and is primarily under `cmd/` and `internal/`.
- The web console is Vue 3, Vite, and TypeScript under `web/`.
- The Go binary embeds `web/dist`; frontend-source changes require a fresh
  frontend build before end-to-end verification.
- The complete project verification entry point is
  `scripts/verify_all.ps1`.
- Work should identify relevant call paths before broad changes, keep changes
  focused, and provide fresh validation evidence.
- Git write and remote operations require explicit user authorization.
- The managed global Codex configuration and
  `C:\Users\Administrator\.codex\nexus-model-catalog.json` are protected and
  must not be modified.

### Go backend role

The backend role will be scoped to Go files and backend-facing embedded assets.
It will call out the main packages:

- `internal/catalog/`
- `internal/codexrouter/`
- `internal/httpapi/`
- `internal/knowledgebase/`
- `internal/taskrunsubmit/`
- `internal/workflowrunner/`
- `cmd/nexus-agents/`

It will require focused package tests while iterating and `go test ./...` for
completed backend changes.

### Vue frontend role

The frontend role will be scoped to `web/src/`, `web/tests/`,
`web/package.json`, and Vite configuration. It will require:

- `npm run test:unit` for TypeScript/frontend behavior changes.
- `npm run build` when changing frontend source, so `web/dist` remains in sync
  for the embedded application.

### Reviewer role

The reviewer role will be configured as read-only. Its review checklist will
cover:

- behavioral regressions and missing tests;
- client/server API contract changes;
- incorrect or stale `web/dist` artifacts after frontend edits;
- sensitive configuration, credentials, and protected Codex routing files;
- commands and evidence needed to validate the proposed change.

## Non-Goals

- Do not create `.codex/config.toml`.
- Do not modify `C:\Users\Administrator\.codex\config.toml`.
- Do not modify
  `C:\Users\Administrator\.codex\nexus-model-catalog.json`.
- Do not create or modify `.claude/` configuration, Claude roles, or Claude
  workflows.
- Do not modify `templates/`; no matching project-to-template path currently
  exists for these project-specific Codex files.
- Do not make application-code, dependency, model-provider, or Git-history
  changes.

## Validation

Validate the new configuration by checking:

1. Each expected file exists at its exact path.
2. `AGENTS.md` references valid project commands and paths.
3. The TOML files are syntactically valid.
4. The reviewer role is explicitly read-only and none of the files contains
   model-provider or model-catalog overrides.
5. `git diff --check` reports no whitespace errors.
