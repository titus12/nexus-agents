package workflowrunner

import (
	"fmt"
	"strings"
	"sync"
	"time"

	"nexus-agents/internal/catalog"
	"nexus-agents/internal/taskrunsubmit"
)

type RunStatus string

const (
	RunStatusRunning   RunStatus = "running"
	RunStatusCompleted RunStatus = "completed"
	RunStatusFailed    RunStatus = "failed"
)

type StartInput struct {
	ProjectID          string         `json:"projectId"`
	WorkflowTemplateID string         `json:"workflowTemplateId"`
	WorkflowCopyID     string         `json:"workflowCopyId"`
	WorkflowType       string         `json:"workflowType"`
	TaskTitle          string         `json:"taskTitle"`
	Context            map[string]any `json:"context"`
	Metrics            map[string]any `json:"metrics"`
	Evidence           map[string]any `json:"evidence"`
}

type FinishInput struct {
	SubmittedStatus string         `json:"submittedStatus"`
	Context         map[string]any `json:"context"`
	Metrics         map[string]any `json:"metrics"`
	Evidence        map[string]any `json:"evidence"`
}

type RunRecord struct {
	ID                 string         `json:"id"`
	ProjectID          string         `json:"projectId"`
	WorkflowTemplateID string         `json:"workflowTemplateId"`
	WorkflowCopyID     string         `json:"workflowCopyId,omitempty"`
	WorkflowType       string         `json:"workflowType"`
	TaskTitle          string         `json:"taskTitle,omitempty"`
	Status             RunStatus      `json:"status"`
	StartedAt          string         `json:"startedAt"`
	EndedAt            string         `json:"endedAt,omitempty"`
	DurationMS         int64          `json:"durationMs,omitempty"`
	Context            map[string]any `json:"context,omitempty"`
	Metrics            map[string]any `json:"metrics,omitempty"`
	Evidence           map[string]any `json:"evidence,omitempty"`
	TaskRunID          string         `json:"taskRunId,omitempty"`
}

type Runner struct {
	mu        sync.Mutex
	runs      map[string]RunRecord
	submitter taskrunsubmit.Submitter
}

func New(submitter taskrunsubmit.Submitter) *Runner {
	return &Runner{
		runs:      map[string]RunRecord{},
		submitter: submitter,
	}
}

func (r *Runner) StartRun(input StartInput) (RunRecord, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if strings.TrimSpace(input.ProjectID) == "" {
		return RunRecord{}, fmt.Errorf("projectId is required")
	}
	if strings.TrimSpace(input.WorkflowType) == "" {
		return RunRecord{}, fmt.Errorf("workflowType is required")
	}
	now := time.Now().UTC().Format(time.RFC3339Nano)
	record := RunRecord{
		ID:                 newRunID(),
		ProjectID:          strings.TrimSpace(input.ProjectID),
		WorkflowTemplateID: strings.TrimSpace(input.WorkflowTemplateID),
		WorkflowCopyID:     strings.TrimSpace(input.WorkflowCopyID),
		WorkflowType:       strings.TrimSpace(input.WorkflowType),
		TaskTitle:          strings.TrimSpace(input.TaskTitle),
		Status:             RunStatusRunning,
		StartedAt:          now,
		Context:            cloneMap(input.Context),
		Metrics:            cloneMap(input.Metrics),
		Evidence:           cloneMap(input.Evidence),
	}
	r.runs[record.ID] = record
	return record, nil
}

func (r *Runner) CompleteRun(id string, input FinishInput) (RunRecord, catalog.TaskRun, error) {
	return r.finishRun(id, input, RunStatusCompleted)
}

func (r *Runner) FailRun(id string, input FinishInput) (RunRecord, catalog.TaskRun, error) {
	if strings.TrimSpace(input.SubmittedStatus) == "" {
		input.SubmittedStatus = "failed"
	}
	return r.finishRun(id, input, RunStatusFailed)
}

func (r *Runner) Run(id string) (RunRecord, bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	record, ok := r.runs[id]
	return record, ok
}

func (r *Runner) finishRun(id string, input FinishInput, status RunStatus) (RunRecord, catalog.TaskRun, error) {
	r.mu.Lock()
	record, ok := r.runs[id]
	if !ok {
		r.mu.Unlock()
		return RunRecord{}, catalog.TaskRun{}, fmt.Errorf("workflow run %s not found", id)
	}
	if record.Status != RunStatusRunning {
		r.mu.Unlock()
		return RunRecord{}, catalog.TaskRun{}, fmt.Errorf("workflow run %s is already %s", id, record.Status)
	}
	record.Status = status
	record.EndedAt = time.Now().UTC().Format(time.RFC3339Nano)
	record.DurationMS = durationMS(record.StartedAt, record.EndedAt)
	record.Context = mergeMaps(record.Context, input.Context)
	record.Metrics = mergeMaps(record.Metrics, input.Metrics)
	record.Evidence = mergeMaps(record.Evidence, input.Evidence)
	r.runs[id] = record
	r.mu.Unlock()

	submittedStatus := strings.TrimSpace(input.SubmittedStatus)
	if submittedStatus == "" {
		if status == RunStatusFailed {
			submittedStatus = "failed"
		} else {
			submittedStatus = "success"
		}
	}
	metrics := cloneMap(record.Metrics)
	if r.submitter.TokenLookup != nil {
		if usage, ok, err := r.submitter.TokenLookup.TokenUsageForWorkflowRun(record.ID); err != nil {
			return record, catalog.TaskRun{}, err
		} else if ok {
			metrics["tokenUsage"] = usage
		}
		if routeMetrics, ok, err := r.submitter.TokenLookup.RouteMetricsForWorkflowRun(record.ID); err != nil {
			return record, catalog.TaskRun{}, err
		} else if ok {
			metrics["routeMetrics"] = routeMetrics
		}
	}
	taskRun, err := r.submitter.SubmitWorkflowResult(catalog.TaskRunInput{
		ProjectID:          record.ProjectID,
		WorkflowTemplateID: record.WorkflowTemplateID,
		WorkflowCopyID:     record.WorkflowCopyID,
		WorkflowType:       record.WorkflowType,
		TaskTitle:          record.TaskTitle,
		SubmittedStatus:    submittedStatus,
		StartedAt:          record.StartedAt,
		EndedAt:            record.EndedAt,
		DurationMS:         record.DurationMS,
		Context:            cloneMap(record.Context),
		Metrics:            metrics,
		Evidence:           cloneMap(record.Evidence),
	})
	if err != nil {
		return record, catalog.TaskRun{}, err
	}

	r.mu.Lock()
	record.TaskRunID = taskRun.ID
	r.runs[id] = record
	r.mu.Unlock()
	return record, taskRun, nil
}

func durationMS(startedAt, endedAt string) int64 {
	start, err1 := time.Parse(time.RFC3339Nano, startedAt)
	end, err2 := time.Parse(time.RFC3339Nano, endedAt)
	if err1 != nil || err2 != nil {
		return 0
	}
	return max(0, end.Sub(start).Milliseconds())
}

func cloneMap(input map[string]any) map[string]any {
	if input == nil {
		return map[string]any{}
	}
	output := make(map[string]any, len(input))
	for key, value := range input {
		output[key] = value
	}
	return output
}

func mergeMaps(base, delta map[string]any) map[string]any {
	out := cloneMap(base)
	for key, value := range delta {
		out[key] = value
	}
	return out
}

func newRunID() string {
	return fmt.Sprintf("wf_run_%d", time.Now().UTC().UnixNano())
}
