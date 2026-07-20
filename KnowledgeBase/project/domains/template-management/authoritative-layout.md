---
type: Guide
title: Template Management - Authoritative layout
description: templates/AGENTS.md is the short project-level entry point. It directs readers to:
resource: KnowledgeBase/project/domains/template-management/authoritative-layout.md
tags: [template-management, feature]
sourcePaths: [templates/AGENTS.md, KnowledgeBase/, KnowledgeBase/project/]
timestamp: 2026-07-19T19:55:19+08:00
managedBy: openwiki
sourceRevision: f56b64553d0eca0da65e02e120c14784e9facfe0
generatedBy: nexus-domain-knowledge-v2
---
# Authoritative layout

`templates/AGENTS.md` is the short project-level entry point. It directs readers to:

- `.claude/agents/*.md` for Claude roles;
- `.codex/agents/*.toml` for Codex role projections;
- `.claude/rules/**/*.md` for shared, Go, Unity, and UIArchitect policy;
- `.claude/skills/*/SKILL.md` and `.agents/skills/*/` for procedures and Codex workflow entries;
- `.claude/commands/wf-*.md` for Claude commands;
- `.claude/workflows/*.md` plus matching `.graph.json` for canonical workflow bodies and graphs; and
- `KnowledgeBase/` for portable framework material, with `KnowledgeBase/project/` reserved for the target project.

The catalog hydrates paired Claude/Codex role files from this tree: Markdown is the displayed role content and TOML is the Codex projection. It does not enforce semantic parity between the paired files, so make paired role changes deliberately.

A workflow installation includes its body and graph, mapped `wf-*` Codex skill, configured support skills, and matching Claude command where present. Workflow skills are deliberately absent from the ordinary public Skill list because they are represented operationally through workflows.

## Domain

- [Template Management](index.md)
