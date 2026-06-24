package taskrunsubmit

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"

	"nexus-agents/internal/catalog"
)

const DefaultEndpoint = "http://127.0.0.1:8766/api/task-runs"

type StoreSubmitter interface {
	SubmitTaskRun(input catalog.TaskRunInput) (catalog.TaskRun, error)
}

type TokenUsageLookup interface {
	TokenUsageForWorkflowRun(workflowRunID string) (catalog.WorkflowTokenUsage, bool, error)
	RouteMetricsForWorkflowRun(workflowRunID string) (catalog.WorkflowRouteMetrics, bool, error)
}

type Submitter struct {
	Endpoint    string
	HTTPClient  *http.Client
	Store       StoreSubmitter
	TokenLookup TokenUsageLookup
}

func (s Submitter) SubmitWorkflowResult(input catalog.TaskRunInput) (catalog.TaskRun, error) {
	if s.Store != nil {
		return s.Store.SubmitTaskRun(input)
	}
	endpoint := strings.TrimSpace(s.Endpoint)
	if endpoint == "" {
		endpoint = DefaultEndpoint
	}
	client := s.HTTPClient
	if client == nil {
		client = http.DefaultClient
	}
	payload, err := json.Marshal(input)
	if err != nil {
		return catalog.TaskRun{}, fmt.Errorf("marshal task run input: %w", err)
	}
	request, err := http.NewRequest(http.MethodPost, endpoint, bytes.NewReader(payload))
	if err != nil {
		return catalog.TaskRun{}, fmt.Errorf("create task run request: %w", err)
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "application/json")

	response, err := client.Do(request)
	if err != nil {
		return catalog.TaskRun{}, fmt.Errorf("submit task run to %s: %w", endpoint, err)
	}
	defer response.Body.Close()
	body, _ := io.ReadAll(response.Body)
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		message := strings.TrimSpace(string(body))
		if message == "" {
			message = response.Status
		}
		return catalog.TaskRun{}, fmt.Errorf("submit task run to %s failed: %s", endpoint, message)
	}
	var run catalog.TaskRun
	if err := json.Unmarshal(body, &run); err != nil {
		return catalog.TaskRun{}, fmt.Errorf("decode task run response: %w", err)
	}
	return run, nil
}
