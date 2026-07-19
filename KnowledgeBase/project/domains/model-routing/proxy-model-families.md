---
type: Guide
title: Model Routing - Proxy model families
description: internal/codexrouter/router.go configures an ordered route table:
resource: KnowledgeBase/project/domains/model-routing/proxy-model-families.md
tags: [model-routing, feature]
sourcePaths: [internal/codexrouter/router.go]
timestamp: 2026-07-19T19:55:19+08:00
managedBy: openwiki
sourceRevision: f56b64553d0eca0da65e02e120c14784e9facfe0
generatedBy: nexus-domain-knowledge-v2
---
# Proxy model families

`internal/codexrouter/router.go` configures an ordered route table:

| Family | Wire protocol | Authentication behavior |
|---|---|---|
| GPT subscription routes | OpenAI Responses | Forwards Codex/OpenAI authorization to ChatGPT’s Codex backend. |
| DeepSeek and GLM routes | OpenAI Chat Completions | Converts from Responses and reads the service-side `DEEPSEEK_API_KEY`. |
| Claude Sonnet route | Anthropic Messages | Uses the native Anthropic Messages adapter and the service-side API-key environment variable. |

The subscription models use the ChatGPT Codex backend directly. Other route metadata such as upstream URLs, provider labels, and descriptions can be adjusted with non-secret `NEXUS_DEEPSEEK_*`, `NEXUS_GLM_*`, and `NEXUS_CLAUDE_*` environment variables. Do not document or store key values.

For Chat Completions routes, `convert.go` converts instructions and Responses input to chat messages, retains a bounded in-memory response-history map for `previous_response_id`, and maps supported tools. The router also converts upstream replies back to the Responses shape/SSE expected by Codex clients. The Claude route is intentionally not sent to Chat Completions because that upstream model uses the Anthropic Messages protocol.

## Domain

- [Model Routing](index.md)
