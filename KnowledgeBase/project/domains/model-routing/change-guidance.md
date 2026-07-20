---
type: Guide
title: Model Routing - Change guidance
description: Detailed capability, implementation boundaries, relationships, and verification guidance.
resource: KnowledgeBase/project/domains/model-routing/change-guidance.md
tags: [model-routing, feature]
sourcePaths: [internal/codexrouter/router.go, internal/httpapi/server_test.go]
timestamp: 2026-07-19T19:55:19+08:00
managedBy: openwiki
sourceRevision: f56b64553d0eca0da65e02e120c14784e9facfe0
generatedBy: nexus-domain-knowledge-v2
---
# Change guidance

- Add or change routes in `internal/codexrouter/router.go`; keep `router_test.go` aligned with route selection, conversion, and telemetry expectations.
- Protocol conversions span `convert.go`, `tools.go`, and `anthropic.go`; test both request and streaming response contracts when changing them.
- API handlers should enforce their method/input contracts in `internal/httpapi/server.go` and be covered by `internal/httpapi/server_test.go`.
- A router change can affect catalog UI and evaluation telemetry, so run `go test ./internal/codexrouter ./internal/httpapi ./internal/catalog` at minimum.

## Domain

- [Model Routing](index.md)
