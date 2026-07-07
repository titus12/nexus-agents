# Workflow Token Usage Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add workflow-run and role-based token usage metrics to task-run evaluation.

**Architecture:** The Codex router reads `X-Nexus-Workflow-Run-Id` and `X-Nexus-Workflow-Role`, extracts usage from existing response tracking, and records minimal token usage events into the evaluation store. The workflow runner injects the aggregate token usage into task-run metrics on completion, and the deterministic evaluator scores token efficiency from those metrics.

**Tech Stack:** Go 1.22, existing JSON evaluation persistence, existing net/http server and tests.

---

### Task 1: Token usage data model and store aggregation

**Files:**
- Modify: `internal/codexrouter/router.go`
- Modify: `internal/catalog/evaluation.go`
- Modify: `internal/catalog/evaluation_test.go`

- [x] Add `TokenUsageEvent`, `TokenUsageRecorder`, role constants, and helper accessors in codexrouter.
- [x] Extend evaluation data with token usage events.
- [x] Add `RecordTokenUsage` and `TokenUsageForWorkflowRun` to `EvaluationStore`.
- [x] Add tests for role/model aggregation and persistence.

### Task 2: Router header capture and recording

**Files:**
- Modify: `internal/codexrouter/router.go`
- Modify: `internal/codexrouter/router_test.go`
- Modify: `internal/httpapi/server.go`

- [x] Add a recorder field and setter to `codexrouter.Service`.
- [x] Read only `X-Nexus-Workflow-Run-Id` and `X-Nexus-Workflow-Role` headers.
- [x] Record usage after GPT Responses proxy streaming finishes.
- [x] Record usage after Chat Completions conversion finishes.
- [x] Wire `EvaluationStore` as the router recorder in HTTP server construction.
- [x] Add unit tests for the usage tracker and recorder wiring.

### Task 3: Workflow completion attaches token usage to task metrics

**Files:**
- Modify: `internal/workflowrunner/runner.go`
- Modify: `internal/workflowrunner/runner_test.go`
- Modify: `internal/taskrunsubmit/submit.go`

- [x] Extend submitter store interface with optional token-usage lookup.
- [x] Merge aggregate token usage into `TaskRunInput.Metrics["tokenUsage"]` before submission.
- [x] Add workflow runner tests showing completed task runs contain token usage metrics.

### Task 4: Evaluation scoring uses token usage

**Files:**
- Modify: `internal/catalog/evaluation.go`
- Modify: `internal/catalog/evaluation_test.go`

- [x] Add `tokenEfficiencyScore` to scores.
- [x] Add token-related primary causes for overuse/request loops/low cache.
- [x] Add tests verifying the evaluator records token scoring without penalizing missing legacy data.

### Task 5: Verification

**Files:**
- Modify: this plan file

- [x] Run `go test ./internal/codexrouter ./internal/catalog ./internal/workflowrunner ./internal/taskrunsubmit ./internal/httpapi`.
- [x] Run `go test ./...` if focused tests pass.
