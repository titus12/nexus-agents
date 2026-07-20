---
type: Domain
title: Template Management
description: Portable Agent, Rule, Skill, Workflow, and KnowledgeBase template initialization and synchronization.
resource: KnowledgeBase/project/domains/template-management/index.md
tags: [templates, agents, skills, workflows, domain]
timestamp: 2026-07-19T19:55:19+08:00
managedBy: openwiki
sourceRevision: f56b64553d0eca0da65e02e120c14784e9facfe0
generatedBy: nexus-domain-knowledge-v2
routing:
  aliases:
    zh: [模板管理, Agent 模板, 工作流模板]
    en: [template management, agent templates, workflow templates]
  keywords:
    zh: [初始化, 同步, 模板布局, 冲突保护]
    en: [initialization, synchronization, template layout, conflict protection]
---
# Template Management

`templates/` is a direct project-root overlay, not a category-based manifest directory. A template-relative path is copied unchanged into a target project: for example, `templates/.claude/workflows/go-bugfix.md` becomes `.claude/workflows/go-bugfix.md`. `scripts/verify_template_catalog.py` rejects obsolete `templates/agents`, `rules`, `skills`, and `workflows` roots.

## Capabilities

- [Authoritative layout](authoritative-layout.md) - templates/AGENTS.md is the short project-level entry point. It directs readers to:
- [Initialization versus synchronization](initialization-versus-synchronization.md) - PreviewTemplateInitialization and ApplyTemplateInitialization support general, go, and unity profiles. The preview marks each file as create, unchanged, conflict, or protected; app...
- [Policy cautions](policy-cautions.md) - There is a repository-owned policy conflict. templates/AGENTS.md says workflows may only run when explicitly requested; templates/.claude/rules/go-00-routing.md permits workflow se...
- [Change guidance](change-guidance.md) - Detailed capability, implementation boundaries, relationships, and verification guidance.

## Related Domains

- [Runtime Platform](../runtime-platform/index.md)
- [Workflow Evaluation](../workflow-evaluation/index.md)
- [KnowledgeBase](../knowledgebase/index.md)
