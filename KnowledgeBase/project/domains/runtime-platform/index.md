---
type: Domain
title: Runtime Platform
description: Nexus service startup, embedded console, runtime composition, state boundaries, and project control-plane operations.
resource: KnowledgeBase/project/domains/runtime-platform/index.md
tags: [runtime, platform, architecture, control-plane, domain]
timestamp: 2026-07-19T19:55:19+08:00
managedBy: openwiki
sourceRevision: f56b64553d0eca0da65e02e120c14784e9facfe0
generatedBy: nexus-domain-knowledge-v2
routing:
  aliases:
    zh: [运行平台, 控制台, 项目管理]
    en: [runtime platform, control plane, nexus platform]
  keywords:
    zh: [启动, 服务装配, 运行时, 状态存储]
    en: [startup, composition, runtime, state storage]
---
# Runtime Platform

## Capabilities

- [Runtime architecture](runtime-architecture.md) - cmd/nexus-agents/main.go has two modes: the default starts the HTTP service, while submit-task-run invokes the local/HTTP task-run submission command. The service creates a codexro...
- [Project overview](project-overview.md) - Nexus Agents is an AI development-configuration control plane. The shipped application is a Go HTTP service with an embedded Vue 3/Vite console. It manages file-backed Agent, Rule,...

## Related Domains

- [Model Routing](../model-routing/index.md)
- [Template Management](../template-management/index.md)
- [Workflow Evaluation](../workflow-evaluation/index.md)
- [KnowledgeBase](../knowledgebase/index.md)
