---
type: Guide
title: Template Management - Initialization versus synchronization
description: PreviewTemplateInitialization and ApplyTemplateInitialization support general, go, and unity profiles. The preview marks each file as create, unchanged, conflict, or protected; app...
resource: KnowledgeBase/project/domains/template-management/initialization-versus-synchronization.md
tags: [template-management, feature]
sourcePaths: [KnowledgeBase/project/, templates/]
timestamp: 2026-07-19T19:55:19+08:00
managedBy: openwiki
sourceRevision: f56b64553d0eca0da65e02e120c14784e9facfe0
generatedBy: nexus-domain-knowledge-v2
---
# Initialization versus synchronization

`PreviewTemplateInitialization` and `ApplyTemplateInitialization` support `general`, `go`, and `unity` profiles. The preview marks each file as `create`, `unchanged`, `conflict`, or `protected`; apply only creates missing files, leaves conflicts unchanged, and merges a bounded Nexus block into `.gitignore`. It always protects `KnowledgeBase/project/`.

The full `SyncProjectTemplates` path is different: it resolves the template root from `NEXUS_TEMPLATES_ROOT` or by walking upward for `templates/`, then overwrites every regular template target except `KnowledgeBase/project/**`. It does not remove project-only files and has no conflict/confirmation step. Use initialization for a non-destructive first install; treat full sync as an overwrite operation.

Project scanning recognizes root `AGENTS.md`, paired agent directories, rules, both skill roots, workflows, and workflow graphs. It hashes declared template content and reports project copies as synchronized, modified, or detached.

## Domain

- [Template Management](index.md)
