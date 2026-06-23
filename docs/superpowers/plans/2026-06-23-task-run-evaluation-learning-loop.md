# Task Run Evaluation Learning Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first working slice of Nexus task-run evaluation so workflows submit evidence, Nexus persists it locally, evaluates pending runs asynchronously, and exposes summaries that can later drive workflow/agent/model/rules/skills learning.

**Architecture:** Add a focused evaluation store/service in the Go backend using a persistent user-level JSON store with three logical collections: `taskRuns`, `evaluations`, and `reviews`. The first evaluator is deterministic rule-based and records attribution JSON for workflow, agent, model, rules, skills, context, and tools; LLM/cross-model judging is represented in the schema but not called in this slice. Expose HTTP APIs and a lightweight workflow UI summary so the feature is testable end-to-end.

**Tech Stack:** Go 1.22, JSON persistence, existing `net/http` API, Vue 3 + TypeScript.

**Vector Retrieval Decision:** Use `chromem-go` for the future local learning-case vector index. Main task/evaluation data remains Nexus-owned JSON/SQLite-compatible state; chromem-go should be treated as a rebuildable local index over high-value `learningCases`.

**Current Learning Slice:** High-value successes and representative failures are archived as `learningCases`. A persistent chromem-go index is rebuildable from those cases and uses a local deterministic embedding function in v1 to avoid network cost; the evaluation record stores an accuracy/cost model policy for future LLM evaluator escalation.

---

### Task 1: Backend Evaluation Store and Rule Evaluator

**Files:**
- Create: `internal/catalog/evaluation.go`
- Create: `internal/catalog/evaluation_test.go`
- Modify: `go.mod`

- [x] **Step 1: Keep dependency-free persistence**

Decision: SQLite drivers available in this environment pulled Go 1.25-era transitive dependencies, so v1 uses a persistent JSON store under the user Nexus data directory while preserving the same compact JSON/blob boundaries for a future SQLite migration.

- [x] **Step 2: Create failing catalog tests**

Add tests that:
- create an evaluation store in `t.TempDir()`;
- submit one `TaskRunInput`;
- verify it persists as `pending`;
- run `EvaluatePending`;
- verify one evaluation is produced with attribution JSON;
- reopen the store and verify data survives process-level store recreation.

- [x] **Step 3: Implement `EvaluationStore`**

Implement:
- persistent store path helper using `~/.nexus/evaluation.json` when `~/.nexus` is a directory, otherwise `~/.nexus-evaluation/evaluation.json`;
- explicit test constructor for a custom DB path;
- logical collections for task runs, evaluations, and reviews;
- JSON marshal/unmarshal helpers.

- [x] **Step 4: Implement rule evaluator**

Implement deterministic scoring:
- `completionScore` from submitted status;
- `verificationScore` from `evidence_json.verification`;
- `efficiencyScore` from duration and tool/retry metrics;
- `workflowFitScore` from presence of workflow type and workflow IDs;
- component attribution for workflow/agent/model/rules/skills/context/tools.

- [x] **Step 5: Run catalog tests**

Run:

```powershell
rtk powershell -NoProfile -Command go test ./internal/catalog
```

Expected: PASS.

---

### Task 2: HTTP API and Background Worker Hook

**Files:**
- Modify: `internal/httpapi/server.go`
- Modify: `internal/httpapi/server_test.go`

- [x] **Step 1: Add failing API tests**

Add tests for:
- `POST /api/task-runs` returns `201` and pending task run;
- `POST /api/evaluations/run-pending` evaluates pending runs;
- `GET /api/evaluations` returns the generated evaluation;
- `GET /api/evaluations/summary` returns workflow-level aggregate;
- `POST /api/evaluations/{id}/review` records a user override.

- [x] **Step 2: Wire evaluation store into server**

Add an `evaluationStore *catalog.EvaluationStore` field. Constructors should create the default user DB store; tests should have a constructor accepting a custom evaluation store.

- [x] **Step 3: Add routes**

Add:
- `POST /api/task-runs`
- `GET /api/task-runs`
- `GET /api/evaluations`
- `GET /api/evaluations/summary`
- `POST /api/evaluations/run-pending`
- `POST /api/evaluations/{id}/review`

- [x] **Step 4: Add minimal evaluation trigger**

Expose `POST /api/evaluations/run-pending` so the UI or a later scheduler can trigger pending evaluation deterministically. A timed goroutine is intentionally deferred to avoid hidden writes during existing tests and local development sessions.

- [x] **Step 5: Run HTTP tests**

Run:

```powershell
rtk powershell -NoProfile -Command go test ./internal/httpapi
```

Expected: PASS.

---

### Task 3: Frontend Types, API Client, and Workflow Summary UI

**Files:**
- Modify: `web/src/types.ts`
- Modify: `web/src/api.ts`
- Modify: `web/src/App.vue`
- Modify: `web/src/styles.css`

- [x] **Step 1: Add TypeScript types**

Add:
- `TaskRun`
- `TaskRunInput`
- `Evaluation`
- `EvaluationReviewInput`
- `EvaluationSummary`

- [x] **Step 2: Add API client methods**

Add functions:
- `createTaskRun`
- `fetchEvaluations`
- `runPendingEvaluations`
- `fetchEvaluationSummary`
- `reviewEvaluation`

- [x] **Step 3: Add workflow summary state**

Load evaluation summary during bootstrap and after pending evaluations run. Keep failures non-fatal so the app remains usable if the evaluation DB has an issue.

- [x] **Step 4: Show lightweight workflow intelligence**

On workflow cards, show:
- sample count;
- average score;
- success rate;
- top issue count if present.

- [x] **Step 5: Add manual evaluator action**

Add a small button in the workflows page toolbar to run pending evaluations and refresh summaries.

- [x] **Step 6: Build frontend**

Run:

```powershell
rtk powershell -NoProfile -Command cd web; npm run build
```

Expected: Vite build succeeds.

---

### Task 4: Full Verification and Plan Closeout

**Files:**
- Modify: `docs/superpowers/plans/2026-06-23-task-run-evaluation-learning-loop.md`

- [x] **Step 1: Run full verification**

Run:

```powershell
rtk powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify_all.ps1
```

Expected: all design checks, scaffold checks, frontend build, Go tests, and smoke tests pass or documented skips occur for missing optional dependencies.

- [x] **Step 2: Update this plan**

Mark completed checkboxes as done.

- [x] **Step 3: Summarize implementation**

Report:
- files changed;
- APIs added;
- tests run;
- any assumptions left for future LLM/cross-model evaluation.

---

### Task 5: Learning Cases and chromem-go Retrieval

**Files:**
- Modify: `internal/catalog/evaluation.go`
- Modify: `internal/catalog/evaluation_test.go`
- Modify: `internal/httpapi/server.go`
- Modify: `internal/httpapi/server_test.go`
- Modify: `web/src/types.ts`
- Modify: `web/src/api.ts`
- Modify: `web/src/App.vue`
- Modify: `web/src/styles.css`

- [x] **Step 1: Add chromem-go dependency**

Run: `go get github.com/philippgille/chromem-go@latest`

- [x] **Step 2: Archive learning cases**

Create long-term success cases when score, verification, and confidence are high; create medium-term failure cases when representative causes exist.

- [x] **Step 3: Add vector index**

Use chromem-go as a persistent, rebuildable index under the evaluation store directory. Use a deterministic local hash embedding for v1 to keep cost zero and avoid network dependency.

- [x] **Step 4: Add learning case APIs**

Add `GET /api/learning-cases`, `GET /api/learning-cases/search`, and `POST /api/learning-cases/rebuild-index`.

- [x] **Step 5: Add UI learning panel**

Show recent learning cases in the Task Run Learning panel and expose a rebuild-index button.

- [x] **Step 6: Add logs and model policy**

Log evaluation, archiving, index rebuild, and search paths. Store a model policy with primary/secondary model choices that balance accuracy and cost.
