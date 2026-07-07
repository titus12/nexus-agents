# Session Time Evaluation Attribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let final evaluation submit enrich tokenUsage and routeMetrics using `sessionId + startedAt/endedAt` without requiring frontend start calls.

**Architecture:** Router events persist the Codex `Session-Id` alongside token and route telemetry. Task-run submission keeps existing workflowRun session binding enrichment, then falls back to session/time-window aggregation when no active workflow binding exists.

**Tech Stack:** Go HTTP server, JSON-backed evaluation store, existing Vue/TypeScript payload builder.

---

### Task 1: Persist Session-Id on telemetry events

**Files:**
- Modify: `internal/codexrouter/router.go`

- [ ] Add `SessionID string json:"sessionId,omitempty"` to `TokenUsageEvent` and `WorkflowRouteEvent`.
- [ ] In `recordTokenUsage` and `recordRouteEvent`, copy trimmed request `Session-Id` into the event.

### Task 2: Aggregate telemetry by session/time window

**Files:**
- Modify: `internal/catalog/evaluation.go`

- [ ] Add public store methods `TokenUsageForSessionWindow` and `RouteMetricsForSessionWindow`.
- [ ] Filter events by `SessionID` and `CreatedAt` within `[startedAt, endedAt]`.
- [ ] Reuse existing aggregate rollup shape, with workflow/run id set to a session-window identifier.

### Task 3: Fallback task-run enrichment

**Files:**
- Modify: `internal/httpapi/server.go`

- [ ] Keep deleting client-owned `tokenUsage` and `routeMetrics`.
- [ ] Try existing `sessionId -> workflowRunId` enrichment first.
- [ ] If unavailable, use `sessionId + input.StartedAt/Input.EndedAt` to enrich metrics.
- [ ] Log exact reason when fallback cannot run or finds no telemetry.

### Task 4: Frontend payload context support

**Files:**
- Modify: `web/src/workflow-task-run.ts`

- [ ] Allow `context.sessionId` in input type.
- [ ] Include it in payload context when provided.

### Task 5: Tests

**Files:**
- Modify: `internal/httpapi/server_test.go`
- Modify if needed: `internal/catalog/evaluation_test.go`

- [ ] Add coverage for task-run submit with no active workflow binding but matching session/time telemetry.
- [ ] Run targeted Go tests for catalog/httpapi.
