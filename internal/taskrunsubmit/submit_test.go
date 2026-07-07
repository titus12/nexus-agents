package taskrunsubmit

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"

	"nexus-agents/internal/catalog"
)

func TestSubmitWorkflowResultUsesStoreWhenProvided(t *testing.T) {
	store, err := catalog.NewEvaluationStore(filepath.Join(t.TempDir(), "evaluation.json"))
	if err != nil {
		t.Fatalf("new evaluation store: %v", err)
	}
	defer store.Close()

	submitter := Submitter{Store: store}
	run, err := submitter.SubmitWorkflowResult(catalog.TaskRunInput{
		ProjectID:       "btd-client",
		WorkflowType:    "ui-feature-development",
		SubmittedStatus: "success",
	})
	if err != nil {
		t.Fatalf("submit workflow result via store: %v", err)
	}
	if run.ProjectID != "btd-client" || run.WorkflowType != "ui-feature-development" || run.ID == "" {
		t.Fatalf("unexpected run: %#v", run)
	}
}

func TestSubmitWorkflowResultPostsToHTTPAPI(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			t.Fatalf("expected POST, got %s", r.Method)
		}
		var input catalog.TaskRunInput
		if err := json.NewDecoder(r.Body).Decode(&input); err != nil {
			t.Fatalf("decode request: %v", err)
		}
		if input.ProjectID != "btd-client" || input.WorkflowType != "bugfix" {
			t.Fatalf("unexpected input: %#v", input)
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(catalog.TaskRun{
			ID:               "run_http_123",
			ProjectID:        input.ProjectID,
			WorkflowType:     input.WorkflowType,
			SubmittedStatus:  input.SubmittedStatus,
			EvaluationStatus: "pending",
		})
	}))
	defer server.Close()

	submitter := Submitter{Endpoint: server.URL}
	run, err := submitter.SubmitWorkflowResult(catalog.TaskRunInput{
		ProjectID:       "btd-client",
		WorkflowType:    "bugfix",
		SubmittedStatus: "success",
	})
	if err != nil {
		t.Fatalf("submit workflow result via http: %v", err)
	}
	if run.ID != "run_http_123" || run.WorkflowType != "bugfix" {
		t.Fatalf("unexpected run: %#v", run)
	}
}
