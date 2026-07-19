---
type: Domain
title: Model Routing
description: HTTP API surface, Codex-compatible proxy behavior, provider selection, protocol conversion, and session telemetry.
resource: KnowledgeBase/project/domains/model-routing/index.md
tags: [model-routing, api, codex, proxy, domain]
timestamp: 2026-07-19T19:55:19+08:00
managedBy: openwiki
sourceRevision: f56b64553d0eca0da65e02e120c14784e9facfe0
generatedBy: nexus-domain-knowledge-v2
routing:
  aliases:
    zh: [模型路由, 模型代理, Codex 路由]
    en: [model routing, model proxy, Codex routing]
  keywords:
    zh: [HTTP 入口, 协议转换, 供应商, 调用链]
    en: [HTTP entrypoint, protocol conversion, provider, call chain]
---
# Model Routing

The Go server exposes an administrative API and mounts the Codex-compatible proxy at `/proxy/codex`. Its [runtime composition](../runtime-platform/index.md) wires all APIs to the catalog, evaluation, workflow, infrastructure, and knowledge services.

## Capabilities

- [Management API groups](management-api-groups.md) - internal/httpapi/server.go registers these resource families:
- [Proxy model families](proxy-model-families.md) - internal/codexrouter/router.go configures an ordered route table:
- [Session-bound telemetry](session-bound-telemetry.md) - The router records token usage and route events only through the server-owned Session-Id binding: it resolves the active session to determine a workflow run and role. A client-prov...
- [Change guidance](change-guidance.md) - Detailed capability, implementation boundaries, relationships, and verification guidance.

## Related Domains

- [Runtime Platform](../runtime-platform/index.md)
- [Workflow Evaluation](../workflow-evaluation/index.md)
