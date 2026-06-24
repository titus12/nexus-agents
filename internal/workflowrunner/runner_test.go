package workflowrunner

import (
	"path/filepath"
	"testing"

	"nexus-agents/internal/catalog"
	"nexus-agents/internal/codexrouter"
	"nexus-agents/internal/taskrunsubmit"
)

func TestRunnerStartAndCompleteSubmitsTaskRun(t *testing.T) {
	store, err := catalog.NewEvaluationStore(filepath.Join(t.TempDir(), "evaluation.json"))
	if err != nil {
		t.Fatalf("new evaluation store: %v", err)
	}
	defer store.Close()

	runner := New(taskrunsubmit.Submitter{Store: store})
	run, err := runner.StartRun(StartInput{
		ProjectID:          "btd-client",
		WorkflowTemplateID: "ui-feature-development",
		WorkflowType:       "ui-feature-development",
		TaskTitle:          "Implement shop popup",
		Context:            map[string]any{"agent": "unity-ui-developer"},
	})
	if err != nil {
		t.Fatalf("start run: %v", err)
	}
	if run.Status != RunStatusRunning || run.ID == "" {
		t.Fatalf("unexpected start run: %#v", run)
	}

	completed, taskRun, err := runner.CompleteRun(run.ID, FinishInput{
		SubmittedStatus: "success",
		Evidence:        map[string]any{"summary": "done"},
	})
	if err != nil {
		t.Fatalf("complete run: %v", err)
	}
	if completed.Status != RunStatusCompleted || taskRun.ID == "" || taskRun.WorkflowType != "ui-feature-development" {
		t.Fatalf("unexpected completion: run=%#v taskRun=%#v", completed, taskRun)
	}
}

func TestRunnerFailSubmitsFailedTaskRun(t *testing.T) {
	store, err := catalog.NewEvaluationStore(filepath.Join(t.TempDir(), "evaluation.json"))
	if err != nil {
		t.Fatalf("new evaluation store: %v", err)
	}
	defer store.Close()

	runner := New(taskrunsubmit.Submitter{Store: store})
	run, err := runner.StartRun(StartInput{
		ProjectID:    "btd-client",
		WorkflowType: "bugfix",
		TaskTitle:    "Fix crash",
	})
	if err != nil {
		t.Fatalf("start run: %v", err)
	}

	failed, taskRun, err := runner.FailRun(run.ID, FinishInput{
		Evidence: map[string]any{"summary": "failed verification"},
	})
	if err != nil {
		t.Fatalf("fail run: %v", err)
	}
	if failed.Status != RunStatusFailed || taskRun.SubmittedStatus != "failed" {
		t.Fatalf("unexpected fail completion: run=%#v taskRun=%#v", failed, taskRun)
	}
}

func TestRunnerCompleteAttachesWorkflowTokenUsage(t *testing.T) {
	store, err := catalog.NewEvaluationStore(filepath.Join(t.TempDir(), "evaluation.json"))
	if err != nil {
		t.Fatalf("new evaluation store: %v", err)
	}
	defer store.Close()

	runner := New(taskrunsubmit.Submitter{Store: store, TokenLookup: store})
	run, err := runner.StartRun(StartInput{
		ProjectID:          "btd-client",
		WorkflowTemplateID: "bugfix",
		WorkflowType:       "bugfix",
		TaskTitle:          "Fix token regression",
	})
	if err != nil {
		t.Fatalf("start run: %v", err)
	}
	if err := store.RecordTokenUsage(codexrouter.TokenUsageEvent{
		WorkflowRunID:     run.ID,
		Role:              "worker",
		Model:             "gpt-5.5",
		InputTokens:       1000,
		CachedInputTokens: 500,
		OutputTokens:      120,
		TotalTokens:       1120,
	}); err != nil {
		t.Fatalf("record token usage: %v", err)
	}
	if err := store.RecordWorkflowRouteEvent(codexrouter.WorkflowRouteEvent{
		WorkflowRunID:  run.ID,
		Role:           "worker",
		Model:          "gpt-5.5",
		StatusCode:     200,
		DurationMS:     1500,
		RequestBytes:   2048,
		ToolCount:      4,
		InputItemCount: 2,
		ToolCallCount:  1,
	}); err != nil {
		t.Fatalf("record route event: %v", err)
	}
	_, taskRun, err := runner.CompleteRun(run.ID, FinishInput{SubmittedStatus: "success"})
	if err != nil {
		t.Fatalf("complete run: %v", err)
	}
	usage, ok := taskRun.Metrics["tokenUsage"].(map[string]any)
	if !ok {
		t.Fatalf("expected tokenUsage metric, got %#v", taskRun.Metrics)
	}
	if usage["workflowRunId"] != run.ID || numberValueForTest(usage["requestCount"]) != 1 || numberValueForTest(usage["totalTokens"]) != 1120 {
		t.Fatalf("unexpected tokenUsage metric: %#v", usage)
	}
	routeMetrics, ok := taskRun.Metrics["routeMetrics"].(map[string]any)
	if !ok {
		t.Fatalf("expected routeMetrics metric, got %#v", taskRun.Metrics)
	}
	if routeMetrics["workflowRunId"] != run.ID || numberValueForTest(routeMetrics["requestCount"]) != 1 || numberValueForTest(routeMetrics["toolCallCount"]) != 1 {
		t.Fatalf("unexpected routeMetrics metric: %#v", routeMetrics)
	}
}

func numberValueForTest(value any) float64 {
	switch typed := value.(type) {
	case int:
		return float64(typed)
	case float64:
		return typed
	default:
		return 0
	}
}
