# Model Proxy Route Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align the ModelProxy API route matrix with the five supported model backends: three GPT subscription routes and two Winky DeepSeek routes.

**Architecture:** Keep `internal/codexrouter` as the runtime proxy config and update `internal/catalog` so the ModelProxy UI/API exposes the same route semantics. Update endpoint tests and the smoke script so old GPT-to-DeepSeek and Claude route entries cannot regress.

**Tech Stack:** Go standard library HTTP tests, PowerShell smoke script.

---

### Task 1: Update route endpoint tests first

**Files:**
- Modify: `internal/httpapi/server_test.go`

- [ ] Change `TestModelRouteEndpoint` to expect exactly five Codex route entries and no Claude entries.
- [ ] Change `TestModelRouteResolveEndpoint` to expect GPT subscription passthrough for `gpt-5.4-mini` and direct Winky DeepSeek resolution for `deepseek-v4-flash`.
- [ ] Run `go test ./internal/httpapi -run "TestModelRoute(Endpoint|ResolveEndpoint)$" -count=1` and confirm it fails against the old implementation.

### Task 2: Update catalog route implementation

**Files:**
- Modify: `internal/catalog/catalog.go`

- [ ] Replace `ModelRoutes()` with five routes: `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`, `deepseek-v4-pro`, `deepseek-v4-flash`.
- [ ] Replace `ResolveModelRoute()` so only Codex/Codex Responses resolves these five models.
- [ ] Ensure GPT routes use passthrough and Codex bearer auth; DeepSeek routes use Winky endpoint and `DEEPSEEK_API_KEY`.
- [ ] Run the targeted route tests and confirm they pass.

### Task 3: Update smoke verification

**Files:**
- Modify: `scripts/smoke_app.ps1`

- [ ] Change the route smoke assertion to expect `gpt-5.4-mini` resolving to itself.
- [ ] Run `go test ./... -count=1` and confirm all Go tests pass.
