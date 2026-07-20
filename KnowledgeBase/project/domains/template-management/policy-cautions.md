---
type: Guide
title: Template Management - Policy cautions
description: There is a repository-owned policy conflict. templates/AGENTS.md says workflows may only run when explicitly requested; templates/.claude/rules/go-00-routing.md permits workflow se...
resource: KnowledgeBase/project/domains/template-management/policy-cautions.md
tags: [template-management, feature]
sourcePaths: [templates/AGENTS.md, templates/.claude/rules/go-00-routing.md]
timestamp: 2026-07-19T19:55:19+08:00
managedBy: openwiki
sourceRevision: f56b64553d0eca0da65e02e120c14784e9facfe0
generatedBy: nexus-domain-knowledge-v2
---
# Policy cautions

There is a repository-owned policy conflict. `templates/AGENTS.md` says workflows may only run when explicitly requested; `templates/.claude/rules/go-00-routing.md` permits workflow selection from skills, entry instructions, rules, or user intent. Do not present either rule as globally authoritative without resolving the conflict for the target configuration.

Template synchronization guidance asks a project change to be reflected back to an equivalent Nexus template when one exists, but the implemented catalog only copies or syncs templates to projects; it has no automatic project-to-template promotion. The older PowerShell import/sync scripts and the README’s legacy layout snippet conflict with the current direct-root verifier, so prefer the actual tree, catalog code, and verifier for operational behavior.

## Domain

- [Template Management](index.md)
