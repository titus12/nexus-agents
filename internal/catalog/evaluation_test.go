package catalog

import (
	"path/filepath"
	"testing"
	"time"

	"nexus-agents/internal/codexrouter"
)

func TestEvaluationStoreSubmitEvaluateAndPersist(t *testing.T) {
	dbPath := filepath.Join(t.TempDir(), "evaluation.db")
	store, err := NewEvaluationStore(dbPath)
	if err != nil {
		t.Fatalf("new evaluation store: %v", err)
	}
	defer store.Close()

	run, err := store.SubmitTaskRun(TaskRunInput{
		ProjectID:          "sample",
		WorkflowTemplateID: "bugfix",
		WorkflowCopyID:     "proj_workflow_sample_bugfix",
		WorkflowType:       "bugfix",
		TaskTitle:          "fix panic",
		SubmittedStatus:    "success",
		DurationMS:         30 * 60 * 1000,
		Context: map[string]any{
			"agent":  "debugger",
			"model":  "gpt-5.4",
			"rules":  []any{"bugfix-core", "testing-required"},
			"skills": []any{"testing"},
			"tools":  []any{"shell"},
		},
		Metrics: map[string]any{
			"toolCallCount": 8,
			"testRunCount":  2,
			"retryCount":    0,
		},
		Evidence: map[string]any{
			"verification": map[string]any{"hasVerification": true, "passed": true, "types": []any{"test"}},
			"risks":        []any{"no production load test"},
		},
	})
	if err != nil {
		t.Fatalf("submit task run: %v", err)
	}
	if run.ID == "" || run.EvaluationStatus != "pending" {
		t.Fatalf("expected pending run with id, got %#v", run)
	}

	count, err := store.EvaluatePending(10)
	if err != nil {
		t.Fatalf("evaluate pending: %v", err)
	}
	if count != 1 {
		t.Fatalf("expected one evaluated run, got %d", count)
	}
	evaluations, err := store.Evaluations()
	if err != nil {
		t.Fatalf("list evaluations: %v", err)
	}
	if len(evaluations) != 1 {
		t.Fatalf("expected one evaluation, got %#v", evaluations)
	}
	evaluation := evaluations[0]
	if evaluation.RunID != run.ID || evaluation.RubricID != "bugfix-rubric" || evaluation.OverallScore <= 0 {
		t.Fatalf("unexpected evaluation: %#v", evaluation)
	}
	attribution, ok := evaluation.Analysis["attribution"].(map[string]any)
	if !ok || attribution["workflow"] == nil || attribution["rules"] == nil || attribution["model"] == nil {
		t.Fatalf("expected attribution matrix for workflow/rules/model, got %#v", evaluation.Analysis)
	}

	if err := store.Close(); err != nil {
		t.Fatalf("close store: %v", err)
	}
	reopened, err := NewEvaluationStore(dbPath)
	if err != nil {
		t.Fatalf("reopen evaluation store: %v", err)
	}
	defer reopened.Close()
	reopenedRuns, err := reopened.TaskRuns()
	if err != nil {
		t.Fatalf("reopened task runs: %v", err)
	}
	if len(reopenedRuns) != 1 || reopenedRuns[0].EvaluationStatus != "evaluated" {
		t.Fatalf("expected persisted evaluated run, got %#v", reopenedRuns)
	}
}

func TestEvaluationStoreSummaryAndReview(t *testing.T) {
	store, err := NewEvaluationStore(filepath.Join(t.TempDir(), "evaluation.db"))
	if err != nil {
		t.Fatalf("new evaluation store: %v", err)
	}
	defer store.Close()

	if _, err := store.SubmitTaskRun(TaskRunInput{
		ProjectID:          "sample",
		WorkflowTemplateID: "research",
		WorkflowType:       "research",
		SubmittedStatus:    "partial_success",
		Evidence:           map[string]any{"contextMissing": true},
		Context:            map[string]any{"rules": []any{}},
	}); err != nil {
		t.Fatalf("submit task run: %v", err)
	}
	if _, err := store.EvaluatePending(10); err != nil {
		t.Fatalf("evaluate pending: %v", err)
	}
	evaluations, err := store.Evaluations()
	if err != nil || len(evaluations) != 1 {
		t.Fatalf("expected one evaluation, got %#v err=%v", evaluations, err)
	}
	score := 55.0
	review, err := store.ReviewEvaluation(evaluations[0].ID, EvaluationReviewInput{
		Reviewer:       "user",
		OverrideStatus: "failed",
		OverrideScore:  &score,
		Review:         map[string]any{"comment": "insufficient sources"},
	})
	if err != nil {
		t.Fatalf("review evaluation: %v", err)
	}
	if review.ID == "" || review.OverrideScore == nil || *review.OverrideScore != 55 {
		t.Fatalf("unexpected review: %#v", review)
	}
	summary, err := store.Summary()
	if err != nil {
		t.Fatalf("summary: %v", err)
	}
	if summary.TotalRuns != 1 || summary.EvaluatedRuns != 1 || len(summary.WorkflowMetrics) != 1 {
		t.Fatalf("unexpected summary: %#v", summary)
	}
	if summary.WorkflowMetrics[0].WorkflowType != "research" || len(summary.TopIssues) == 0 || summary.ComponentStats["context"].SampleCount != 1 {
		t.Fatalf("expected research workflow metrics and attribution issue stats, got %#v", summary)
	}
}

func TestEvaluationSummaryAggregatesAgentModelRulesDimensions(t *testing.T) {
	store, err := NewEvaluationStore(filepath.Join(t.TempDir(), "evaluation.json"))
	if err != nil {
		t.Fatalf("new evaluation store: %v", err)
	}
	defer store.Close()

	if _, err := store.SubmitTaskRun(TaskRunInput{
		ProjectID:          "sample",
		WorkflowTemplateID: "bugfix",
		WorkflowType:       "bugfix",
		SubmittedStatus:    "success",
		Context: map[string]any{
			"agent": "debugger",
			"model": map[string]any{"id": "gpt-5.4"},
			"rules": []any{"bugfix-core", "testing-required"},
		},
		Evidence: map[string]any{"verification": map[string]any{"passed": true}},
	}); err != nil {
		t.Fatalf("submit task run: %v", err)
	}
	if _, err := store.EvaluatePending(10); err != nil {
		t.Fatalf("evaluate pending: %v", err)
	}
	summary, err := store.Summary()
	if err != nil {
		t.Fatalf("summary: %v", err)
	}
	foundAgent := false
	foundModel := false
	foundRule := false
	for _, stat := range summary.DimensionStats {
		foundAgent = foundAgent || stat.Dimension == "agent" && stat.Name == "debugger" && stat.SampleCount == 1
		foundModel = foundModel || stat.Dimension == "model" && stat.Name == "gpt-5.4" && stat.SampleCount == 1
		foundRule = foundRule || stat.Dimension == "rules" && stat.Name == "bugfix-core" && stat.SampleCount == 1
	}
	if !foundAgent || !foundModel || !foundRule {
		t.Fatalf("expected agent/model/rules dimension stats, got %#v", summary.DimensionStats)
	}
}

func TestLearningCasesArchiveAndSearch(t *testing.T) {
	store, err := NewEvaluationStore(filepath.Join(t.TempDir(), "evaluation.json"))
	if err != nil {
		t.Fatalf("new evaluation store: %v", err)
	}
	defer store.Close()

	if _, err := store.SubmitTaskRun(TaskRunInput{
		ProjectID:          "sample",
		WorkflowTemplateID: "bugfix",
		WorkflowType:       "bugfix",
		TaskTitle:          "fix login nil pointer panic",
		SubmittedStatus:    "success",
		Context: map[string]any{
			"agent": "debugger",
			"model": "gpt-5.4",
			"rules": []any{"bugfix-core", "testing-required"},
		},
		Evidence: map[string]any{
			"summary":        "login panic fixed with nil guard",
			"verification":   map[string]any{"hasVerification": true, "passed": true},
			"successfulPath": []any{"reproduce panic", "identify nil pointer", "add regression test"},
			"tags":           []any{"login", "panic", "nil-pointer"},
		},
	}); err != nil {
		t.Fatalf("submit task run: %v", err)
	}
	if _, err := store.EvaluatePending(10); err != nil {
		t.Fatalf("evaluate pending: %v", err)
	}
	cases, err := store.LearningCases()
	if err != nil {
		t.Fatalf("learning cases: %v", err)
	}
	if len(cases) != 1 || cases[0].CaseType != "success" || cases[0].RetentionClass != "long_term" {
		t.Fatalf("expected high-value success learning case, got %#v", cases)
	}
	count, err := store.RebuildLearningCaseIndex()
	if err != nil {
		t.Fatalf("rebuild learning case index: %v", err)
	}
	if count != 1 {
		t.Fatalf("expected one indexed case, got %d", count)
	}
	hits, err := store.SearchLearningCases("login panic nil pointer", 3)
	if err != nil {
		t.Fatalf("search learning cases: %v", err)
	}
	if len(hits) != 1 || hits[0].Case.ID != cases[0].ID {
		t.Fatalf("expected search hit for archived case, got %#v", hits)
	}
}

func TestUnityWorkflowsUseHighRiskEvaluationPolicy(t *testing.T) {
	store, err := NewEvaluationStore(filepath.Join(t.TempDir(), "evaluation.json"))
	if err != nil {
		t.Fatalf("new evaluation store: %v", err)
	}
	defer store.Close()

	for _, workflow := range []string{"bug-investigation", "logic-modification", "ui-feature-development", "unity-workflow-evaluation"} {
		if _, err := store.SubmitTaskRun(TaskRunInput{
			ProjectID:          "unity-sample",
			WorkflowTemplateID: workflow,
			WorkflowType:       workflow,
			SubmittedStatus:    "success",
			Context:            map[string]any{"agent": "unity-workflow-evaluator", "model": "deepseek-v4-flash"},
			Evidence:           map[string]any{"verification": map[string]any{"passed": true}},
		}); err != nil {
			t.Fatalf("submit unity task run %s: %v", workflow, err)
		}
	}
	if _, err := store.EvaluatePending(10); err != nil {
		t.Fatalf("evaluate pending: %v", err)
	}
	evaluations, err := store.Evaluations()
	if err != nil {
		t.Fatalf("evaluations: %v", err)
	}
	if len(evaluations) != 4 {
		t.Fatalf("expected four Unity evaluations, got %#v", evaluations)
	}
	for _, evaluation := range evaluations {
		if highRisk, _ := evaluation.ModelPolicy["highRiskWorkflow"].(bool); !highRisk {
			t.Fatalf("expected Unity workflow to be high risk, got %#v", evaluation.ModelPolicy)
		}
		reasons := evaluationEscalationReasons(evaluation)
		if !containsString(reasons, "high_risk_workflow") {
			t.Fatalf("expected high_risk_workflow escalation reason, got %#v", reasons)
		}
	}
}

func TestEvaluationStoreTokenUsageAggregationByRole(t *testing.T) {
	store, err := NewEvaluationStore(filepath.Join(t.TempDir(), "evaluation.json"))
	if err != nil {
		t.Fatalf("new evaluation store: %v", err)
	}
	defer store.Close()

	events := []codexrouter.TokenUsageEvent{
		{WorkflowRunID: "wf_run_token", Role: "worker", Model: "gpt-5.5", InputTokens: 100, CachedInputTokens: 40, OutputTokens: 10, TotalTokens: 110},
		{WorkflowRunID: "wf_run_token", Role: "worker", Model: "gpt-5.5", InputTokens: 50, CachedInputTokens: 10, OutputTokens: 5, TotalTokens: 55},
		{WorkflowRunID: "wf_run_token", Role: "reviewer", Model: "deepseek-v4-pro", InputTokens: 30, OutputTokens: 7, TotalTokens: 37},
		{WorkflowRunID: "other", Role: "worker", Model: "gpt-5.5", InputTokens: 999, OutputTokens: 999},
	}
	for _, event := range events {
		if err := store.RecordTokenUsage(event); err != nil {
			t.Fatalf("record usage: %v", err)
		}
	}
	usage, ok, err := store.TokenUsageForWorkflowRun("wf_run_token")
	if err != nil {
		t.Fatalf("token usage lookup: %v", err)
	}
	if !ok || usage.RequestCount != 3 || usage.InputTokens != 180 || usage.OutputTokens != 22 || usage.TotalTokens != 202 {
		t.Fatalf("unexpected aggregate: %#v ok=%v", usage, ok)
	}
	worker := usage.Roles["worker"]
	if worker.RequestCount != 2 || worker.Models["gpt-5.5"] != 2 || worker.CacheHitRate != 33.3 {
		t.Fatalf("unexpected worker rollup: %#v", worker)
	}
	reviewer := usage.Roles["reviewer"]
	if reviewer.RequestCount != 1 || reviewer.Models["deepseek-v4-pro"] != 1 {
		t.Fatalf("unexpected reviewer rollup: %#v", reviewer)
	}

	reopened, err := NewEvaluationStore(store.path)
	if err != nil {
		t.Fatalf("reopen evaluation store: %v", err)
	}
	defer reopened.Close()
	persisted, ok, err := reopened.TokenUsageForWorkflowRun("wf_run_token")
	if err != nil || !ok || persisted.RequestCount != 3 {
		t.Fatalf("expected persisted token usage, got %#v ok=%v err=%v", persisted, ok, err)
	}
}

func TestEvaluationIncludesTokenEfficiencyScore(t *testing.T) {
	store, err := NewEvaluationStore(filepath.Join(t.TempDir(), "evaluation.json"))
	if err != nil {
		t.Fatalf("new evaluation store: %v", err)
	}
	defer store.Close()
	if _, err := store.SubmitTaskRun(TaskRunInput{
		ProjectID:          "sample",
		WorkflowTemplateID: "bugfix",
		WorkflowType:       "bugfix",
		SubmittedStatus:    "success",
		Metrics: map[string]any{"tokenUsage": map[string]any{
			"workflowRunId": "wf_run_expensive",
			"requestCount":  20,
			"inputTokens":   320000,
			"outputTokens":  20000,
			"totalTokens":   340000,
			"cacheHitRate":  5,
		}},
		Context:  map[string]any{"rules": []any{"bugfix-core"}, "agent": "debugger", "model": "gpt-5.5"},
		Evidence: map[string]any{"verification": map[string]any{"passed": true}},
	}); err != nil {
		t.Fatalf("submit task run: %v", err)
	}
	if _, err := store.EvaluatePending(10); err != nil {
		t.Fatalf("evaluate pending: %v", err)
	}
	evaluations, err := store.Evaluations()
	if err != nil || len(evaluations) != 1 {
		t.Fatalf("expected one evaluation, got %#v err=%v", evaluations, err)
	}
	if numberValue(evaluations[0].Scores["tokenEfficiencyScore"]) >= 70 {
		t.Fatalf("expected low token efficiency score, got %#v", evaluations[0].Scores)
	}
	if !containsString(anySliceToStrings(evaluations[0].Analysis["primaryCauses"]), "token_overuse") {
		t.Fatalf("expected token_overuse cause, got %#v", evaluations[0].Analysis)
	}
}

func TestEvaluationStoreRouteMetricsAggregationByRole(t *testing.T) {
	store, err := NewEvaluationStore(filepath.Join(t.TempDir(), "evaluation.json"))
	if err != nil {
		t.Fatalf("new evaluation store: %v", err)
	}
	defer store.Close()
	events := []codexrouter.WorkflowRouteEvent{
		{WorkflowRunID: "wf_run_route", Role: "worker", Model: "gpt-5.5", StatusCode: 200, DurationMS: 1000, RequestBytes: 100, ToolCount: 3, InputItemCount: 2, ToolCallCount: 1},
		{WorkflowRunID: "wf_run_route", Role: "worker", Model: "gpt-5.5", StatusCode: 502, DurationMS: 2000, RequestBytes: 150, ToolCount: 3, InputItemCount: 2, ToolCallCount: 0},
		{WorkflowRunID: "wf_run_route", Role: "reviewer", Model: "deepseek-v4-pro", StatusCode: 200, DurationMS: 3000, RequestBytes: 250, ToolCount: 1, InputItemCount: 1, ToolCallCount: 2},
	}
	for _, event := range events {
		if err := store.RecordWorkflowRouteEvent(event); err != nil {
			t.Fatalf("record route event: %v", err)
		}
	}
	metrics, ok, err := store.RouteMetricsForWorkflowRun("wf_run_route")
	if err != nil || !ok {
		t.Fatalf("route metrics lookup ok=%v err=%v metrics=%#v", ok, err, metrics)
	}
	if metrics.RequestCount != 3 || metrics.SuccessCount != 2 || metrics.ErrorCount != 1 || metrics.ErrorRate != 33.3 || metrics.DurationMS != 6000 {
		t.Fatalf("unexpected route metrics: %#v", metrics)
	}
	worker := metrics.Roles["worker"]
	if worker.RequestCount != 2 || worker.ErrorCount != 1 || worker.Models["gpt-5.5"] != 2 || worker.AverageRequestDurationMS != 1500 {
		t.Fatalf("unexpected worker route rollup: %#v", worker)
	}
}

func TestEvaluationIncludesRouteHealthScore(t *testing.T) {
	store, err := NewEvaluationStore(filepath.Join(t.TempDir(), "evaluation.json"))
	if err != nil {
		t.Fatalf("new evaluation store: %v", err)
	}
	defer store.Close()
	if _, err := store.SubmitTaskRun(TaskRunInput{
		ProjectID:          "sample",
		WorkflowTemplateID: "bugfix",
		WorkflowType:       "bugfix",
		SubmittedStatus:    "success",
		Metrics: map[string]any{"routeMetrics": map[string]any{
			"workflowRunId": "wf_run_unhealthy",
			"requestCount":  15,
			"errorCount":    3,
			"durationMs":    20 * 60 * 1000,
			"toolCallCount": 0,
		}},
		Context:  map[string]any{"rules": []any{"bugfix-core"}, "agent": "debugger", "model": "gpt-5.5"},
		Evidence: map[string]any{"verification": map[string]any{"passed": true}},
	}); err != nil {
		t.Fatalf("submit task run: %v", err)
	}
	if _, err := store.EvaluatePending(10); err != nil {
		t.Fatalf("evaluate pending: %v", err)
	}
	evaluations, err := store.Evaluations()
	if err != nil || len(evaluations) != 1 {
		t.Fatalf("expected one evaluation, got %#v err=%v", evaluations, err)
	}
	if numberValue(evaluations[0].Scores["routeHealthScore"]) >= 70 {
		t.Fatalf("expected low route health score, got %#v", evaluations[0].Scores)
	}
	if !containsString(anySliceToStrings(evaluations[0].Analysis["primaryCauses"]), "route_health_issue") {
		t.Fatalf("expected route_health_issue cause, got %#v", evaluations[0].Analysis)
	}
}

func TestEvaluationStoreTaskRunsAndStatisticsIncludeAggregatedMetrics(t *testing.T) {
	store, err := NewEvaluationStore(filepath.Join(t.TempDir(), "evaluation.json"))
	if err != nil {
		t.Fatalf("new evaluation store: %v", err)
	}
	run, err := store.SubmitTaskRun(TaskRunInput{
		ProjectID:       "sample",
		WorkflowType:    "bugfix",
		SubmittedStatus: "success",
		Context:         map[string]any{"agent": "debugger", "model": "gpt-5.5"},
		Evidence:        map[string]any{"verification": map[string]any{"hasVerification": true, "passed": true}},
	})
	if err != nil {
		t.Fatalf("submit task run: %v", err)
	}
	if err := store.RecordTokenUsage(codexrouter.TokenUsageEvent{WorkflowRunID: run.ID, Role: "worker", Model: "gpt-5.5", InputTokens: 100, OutputTokens: 20, TotalTokens: 120}); err != nil {
		t.Fatalf("record token usage: %v", err)
	}
	if err := store.RecordWorkflowRouteEvent(codexrouter.WorkflowRouteEvent{WorkflowRunID: run.ID, Role: "worker", Model: "gpt-5.5", StatusCode: 200, DurationMS: 1500}); err != nil {
		t.Fatalf("record route event: %v", err)
	}
	runs, err := store.TaskRuns()
	if err != nil || len(runs) != 1 {
		t.Fatalf("task runs = %#v err=%v", runs, err)
	}
	usage, ok := runs[0].Metrics["tokenUsage"].(WorkflowTokenUsage)
	if !ok || usage.RequestCount != 1 || usage.TotalTokens != 120 {
		t.Fatalf("expected enriched token usage, got %#v", runs[0].Metrics)
	}
	routeMetrics, ok := runs[0].Metrics["routeMetrics"].(WorkflowRouteMetrics)
	if !ok || routeMetrics.RequestCount != 1 || routeMetrics.DurationMS != 1500 {
		t.Fatalf("expected enriched route metrics, got %#v", runs[0].Metrics)
	}
	if runs[0].DurationMS != 1500 {
		t.Fatalf("expected duration from route metrics, got %d", runs[0].DurationMS)
	}
	stats, err := store.StatisticsTasks("pending", "all")
	if err != nil || len(stats.Items) != 1 {
		t.Fatalf("statistics = %#v err=%v", stats, err)
	}
	if stats.Items[0].TokenUsage.RequestCount != 1 || stats.Items[0].RouteMetrics.RequestCount != 1 || stats.Items[0].DurationMS != 1500 {
		t.Fatalf("expected statistics metrics, got %#v", stats.Items[0])
	}
}

func TestActiveWorkflowSessionStoreBindsAndExpires(t *testing.T) {
	store, err := NewActiveWorkflowSessionStore(filepath.Join(t.TempDir(), "active-workflow-sessions.json"))
	if err != nil {
		t.Fatalf("new active workflow session store: %v", err)
	}
	base := time.Date(2026, 6, 24, 0, 0, 0, 0, time.UTC)
	store.now = func() time.Time { return base }
	if err := store.Bind(ActiveWorkflowSession{SessionID: "sess-1", WorkflowRunID: "wf_run_1", ProjectID: "sample", WorkflowType: "bugfix", Status: "active"}); err != nil {
		t.Fatalf("bind active session: %v", err)
	}
	session, ok, err := store.Lookup("sess-1")
	if err != nil || !ok || session.WorkflowRunID != "wf_run_1" {
		t.Fatalf("lookup active session ok=%v err=%v session=%#v", ok, err, session)
	}
	store.now = func() time.Time { return base.Add(25 * time.Hour) }
	_, ok, err = store.Lookup("sess-1")
	if err != nil {
		t.Fatalf("lookup expired session: %v", err)
	}
	if ok {
		t.Fatalf("expected expired session to be pruned")
	}
}
