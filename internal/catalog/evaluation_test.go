package catalog

import (
	"path/filepath"
	"testing"
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
