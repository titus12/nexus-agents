---
type: Domain Guide
title: Catalog and template management
description: Ownership, lifecycle, and safety rules for Nexus template inventory, imported projects, configuration scanning, template initialization, and project-wide template synchronization.
resource: /internal/catalog/catalog.go
tags: [catalog, templates, projects, workflows, configuration]
---
# Catalog and template management

`internal/catalog` is the management-domain core. It represents templates, projects and copied project configuration, groups, workflow graphs, infrastructure state, evaluations, and local scanning. The HTTP/UI boundary exposes this domain; the [runtime architecture](../architecture/overview.md) shows where it is composed.

## Authoritative content and project copies

The repository-owned library is `templates/`, organized into `.claude`, `.codex`, `.agents`, and `KnowledgeBase` content. It includes cross-client agent definitions, rules, skills, workflow Markdown/graph files, and the portable KnowledgeBase framework. The catalog loads that library and scans imported local project roots to understand their configuration.

An imported project is not merely a pointer: catalog records project metadata and tracks whether individual project files still originate from or diverge from a template. That provenance enables rescan, sync, and detach operations exposed through `internal/httpapi/server.go`.

## Two distinct write paths

Do not confuse initialization with synchronization.

### Initialize: conservative bootstrap

`internal/catalog/template_initializer.go` supports `general`, `go`, and `unity` profiles. It previews before application, creates only missing template files, reports existing files as conflicts, and adds a managed `.gitignore` block. It explicitly protects `KnowledgeBase/project/**`: initializing a project must not overwrite the team-owned knowledge corpus.

Use this path for a new or partially configured project. Tests in `template_initializer_test.go` exercise profile selection, conflicts, ignores, and protected knowledge paths.

### Template sync: update the managed library copy

`internal/catalog/project_template_sync.go` performs a broader project-template copy that overwrites ordinary template files. It still excludes `KnowledgeBase/Setting.yaml` and `KnowledgeBase/project/**`. This enables centrally maintained agent/workflow templates to evolve while retaining project policy and approved knowledge.

Because sync is intentionally more destructive than initialization, inspect UI/API preview/confirmation behavior and tests before changing it. A change that broadens its copy set can overwrite project-local modifications.

## Infrastructure management

`internal/catalog/infrastructure.go` checks and manages tooling such as RTK, CodeGraph, OpenWiki, and GBrain. OpenWiki is pinned to `0.2.0` and is treated as a candidate-document compiler, not a direct writer of project knowledge. That boundary is enforced by the [knowledge platform](knowledge-platform.md).

## Relationship to workflows and UI

The catalog supplies workflow definitions and project metadata used by the [request and run lifecycle](../workflows/request-and-run-lifecycle.md). The Vue console provides the management experience, but business rules must remain in catalog/API behavior rather than only the client.

## Change guide

- For a new template family, update the template tree, catalog discovery/classification, initialization profile logic, and template-catalog verification together.
- For scanner changes, use `project_scan_test.go` as the behavioral contract; it is unusually broad because routing/provenance depends on accurate detection.
- For write safety changes, cover both initializer and project-sync behavior, especially KnowledgeBase exclusions.
- Run `python scripts/verify_template_catalog.py`, relevant `go test ./internal/catalog/...`, and the broader [testing gate](../operations/testing.md) when template behavior changes.
