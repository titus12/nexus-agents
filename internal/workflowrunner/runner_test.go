package workflowrunner

import (
	"path/filepath"
	"testing"

	"nexus-agents/internal/catalog"
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
