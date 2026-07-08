# Chinese KnowledgeBase LLM Query Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve Chinese KnowledgeBase search by rewriting Chinese queries into English technical search keywords with a cheap LLM, then merging those keywords into the existing retrieval pipeline.

**Architecture:** Add a best-effort query rewrite layer inside `internal/knowledgebase`. Retrieval keeps the original Chinese terms, optionally calls a configurable OpenAI-compatible chat-completions model such as `deepseek-v4-flash`, merges returned English query/keywords, and falls back to the original search on timeout, missing API key, invalid JSON, or upstream errors.

**Tech Stack:** Go, net/http, encoding/json, SQLite FTS5 existing retrieval, environment-variable configuration.

---

## File Structure

- Modify `internal/knowledgebase/types.go`: add `QueryRewriteResult`, add `QueryRewrite` field to `RetrievalResult`, add rewrite settings to `RetrieveOptions`.
- Create `internal/knowledgebase/query_rewrite.go`: CJK detection, query rewrite client interface, default OpenAI-compatible chat client, strict JSON extraction/parsing, term merge helpers, env-backed defaults.
- Modify `internal/knowledgebase/retrieve.go`: call the rewrite layer after scanning the bundle and before routing/FTS; use merged terms everywhere.
- Create `internal/knowledgebase/query_rewrite_test.go`: unit tests for rewrite success, fallback, disabled behavior, CJK detection, and term merging.
- Modify `internal/knowledgebase/retrieve_test.go`: add an integration-style test proving Chinese query can retrieve English-only KB text through mocked rewrite.

## Tasks

### Task 1: Add data types and query rewrite helpers

**Files:**
- Modify: `D:\workspace\src\nexus-agents\internal\knowledgebase\types.go`
- Create: `D:\workspace\src\nexus-agents\internal\knowledgebase\query_rewrite.go`
- Test: `D:\workspace\src\nexus-agents\internal\knowledgebase\query_rewrite_test.go`

- [ ] Add result/options/client types.
- [ ] Implement `containsCJK`, env defaults, JSON prompt call, strict parse, and fallback result construction.
- [ ] Add focused helper tests.

### Task 2: Integrate rewrite into retrieval

**Files:**
- Modify: `D:\workspace\src\nexus-agents\internal\knowledgebase\retrieve.go`
- Modify: `D:\workspace\src\nexus-agents\internal\knowledgebase\retrieve_test.go`

- [ ] Replace direct `expandQueryTerms(query)` use with `rewriteKnowledgeQuery` + merged terms.
- [ ] Return rewrite metadata in `RetrievalResult`.
- [ ] Add mocked retrieval test where Chinese query maps to English-only `character movement` content.

### Task 3: Verify formatting and tests

**Files:**
- All changed Go files.

- [ ] Run `gofmt -w internal\knowledgebase`.
- [ ] Run `go test ./internal/knowledgebase`.
- [ ] Run `go test ./...` if package tests remain fast enough.

## Self-Review

- The plan covers the agreed feature: CJK detection, cheap model query rewrite, merged terms, fallback, and testability.
- No global Codex config files are modified.
- The feature is best-effort and disabled automatically when no model endpoint/API key is configured.
