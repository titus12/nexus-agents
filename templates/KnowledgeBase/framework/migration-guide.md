---
type: Guide
title: KnowledgeBase Migration Guide
description: Explains how to migrate the portable KnowledgeBase framework to another project without carrying project-specific facts.
resource: KnowledgeBase/framework/migration-guide.md
tags: [knowledge-base, migration, framework]
timestamp: 2026-07-10T00:00:00+08:00
---

# KnowledgeBase Migration Guide

## Carry to a new project

- The complete `framework/` directory.
- The OKF `index.md` navigation model.
- The project profile defined by the document contract and quality gates.
- The `kb-system-curator` workflow, after adapting retrieval and validation tools.

## Rebuild in the destination project

- `project/index.md`, project routing, and all domain indexes.
- Source maps, framework-specific rules, generated-data routes, and validation commands.
- Domain pages that describe the destination project's own architecture and stable conventions.

## Do not copy as facts

- Project-specific paths, class names, asset locations, technology-specific verification routes, and domain guidance.
- Concrete configuration identifiers, tuning values, logs, feature history, or implementation call graphs.

## Migration order

1. Copy the framework and establish a root `index.md`.
2. Create the destination `project/index.md` and its first domain indexes.
3. Define source-of-truth and verification boundaries for the destination project.
4. Curate only stable destination knowledge into focused concept documents.
5. Run the quality gates before publishing the migrated bundle.
