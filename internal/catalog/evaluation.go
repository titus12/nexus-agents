package catalog

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	chromem "github.com/philippgille/chromem-go"

	"nexus-agents/internal/codexrouter"
)

type TaskRun struct {
	ID                 string         `json:"id"`
	ProjectID          string         `json:"projectId"`
	WorkflowTemplateID string         `json:"workflowTemplateId,omitempty"`
	WorkflowCopyID     string         `json:"workflowCopyId,omitempty"`
	WorkflowType       string         `json:"workflowType"`
	TaskTitle          string         `json:"taskTitle,omitempty"`
	SubmittedStatus    string         `json:"submittedStatus"`
	StartedAt          string         `json:"startedAt,omitempty"`
	EndedAt            string         `json:"endedAt,omitempty"`
	DurationMS         int64          `json:"durationMs,omitempty"`
	EvaluationStatus   string         `json:"evaluationStatus"`
	Context            map[string]any `json:"context,omitempty"`
	Metrics            map[string]any `json:"metrics,omitempty"`
	Evidence           map[string]any `json:"evidence,omitempty"`
	CreatedAt          string         `json:"createdAt"`
	UpdatedAt          string         `json:"updatedAt"`
}

type TaskRunInput struct {
	ProjectID          string         `json:"projectId"`
	WorkflowTemplateID string         `json:"workflowTemplateId"`
	WorkflowCopyID     string         `json:"workflowCopyId"`
	WorkflowType       string         `json:"workflowType"`
	TaskTitle          string         `json:"taskTitle"`
	SubmittedStatus    string         `json:"submittedStatus"`
	StartedAt          string         `json:"startedAt"`
	EndedAt            string         `json:"endedAt"`
	DurationMS         int64          `json:"durationMs"`
	Context            map[string]any `json:"context"`
	Metrics            map[string]any `json:"metrics"`
	Evidence           map[string]any `json:"evidence"`
}

type Evaluation struct {
	ID                  string         `json:"id"`
	RunID               string         `json:"runId"`
	RubricID            string         `json:"rubricId"`
	RubricVersion       string         `json:"rubricVersion"`
	EvaluationLevel     int            `json:"evaluationLevel"`
	FinalStatus         string         `json:"finalStatus"`
	OverallScore        float64        `json:"overallScore"`
	Confidence          float64        `json:"confidence"`
	Scores              map[string]any `json:"scores"`
	Analysis            map[string]any `json:"analysis"`
	ModelJudgements     map[string]any `json:"modelJudgements,omitempty"`
	ModelPolicy         map[string]any `json:"modelPolicy,omitempty"`
	CreatedAt           string         `json:"createdAt"`
	LatestReviewStatus  string         `json:"latestReviewStatus,omitempty"`
	LatestReviewScore   *float64       `json:"latestReviewScore,omitempty"`
	LatestReviewComment string         `json:"latestReviewComment,omitempty"`
}

type EvaluationReview struct {
	ID             string         `json:"id"`
	EvaluationID   string         `json:"evaluationId"`
	Reviewer       string         `json:"reviewer,omitempty"`
	OverrideStatus string         `json:"overrideStatus,omitempty"`
	OverrideScore  *float64       `json:"overrideScore,omitempty"`
	Review         map[string]any `json:"review,omitempty"`
	CreatedAt      string         `json:"createdAt"`
}

type EvaluationReviewInput struct {
	Reviewer       string         `json:"reviewer"`
	OverrideStatus string         `json:"overrideStatus"`
	OverrideScore  *float64       `json:"overrideScore"`
	Review         map[string]any `json:"review"`
}

type EvaluationSummary struct {
	TotalRuns       int                       `json:"totalRuns"`
	EvaluatedRuns   int                       `json:"evaluatedRuns"`
	PendingRuns     int                       `json:"pendingRuns"`
	WorkflowMetrics []WorkflowEvaluationStat  `json:"workflowMetrics"`
	TopIssues       []EvaluationIssueStat     `json:"topIssues"`
	ComponentStats  map[string]ComponentStat  `json:"componentStats"`
	DimensionStats  []EvaluationDimensionStat `json:"dimensionStats"`
}

type WorkflowEvaluationStat struct {
	WorkflowTemplateID string  `json:"workflowTemplateId"`
	WorkflowType       string  `json:"workflowType"`
	SampleCount        int     `json:"sampleCount"`
	AverageScore       float64 `json:"averageScore"`
	SuccessRate        float64 `json:"successRate"`
}

type EvaluationIssueStat struct {
	Issue string `json:"issue"`
	Count int    `json:"count"`
}

type ComponentStat struct {
	SampleCount  int     `json:"sampleCount"`
	AverageScore float64 `json:"averageScore"`
}

type EvaluationDimensionStat struct {
	Dimension    string  `json:"dimension"`
	Name         string  `json:"name"`
	SampleCount  int     `json:"sampleCount"`
	AverageScore float64 `json:"averageScore"`
	SuccessRate  float64 `json:"successRate"`
}

type WorkflowTokenUsage struct {
	WorkflowRunID     string                      `json:"workflowRunId"`
	RequestCount      int                         `json:"requestCount"`
	InputTokens       int                         `json:"inputTokens"`
	CachedInputTokens int                         `json:"cachedInputTokens"`
	OutputTokens      int                         `json:"outputTokens"`
	TotalTokens       int                         `json:"totalTokens"`
	CacheHitRate      float64                     `json:"cacheHitRate"`
	OutputInputRatio  float64                     `json:"outputInputRatio"`
	Roles             map[string]TokenUsageRollup `json:"roles"`
	UpdatedAt         string                      `json:"updatedAt"`
}

type TokenUsageRollup struct {
	RequestCount      int            `json:"requestCount"`
	InputTokens       int            `json:"inputTokens"`
	CachedInputTokens int            `json:"cachedInputTokens"`
	OutputTokens      int            `json:"outputTokens"`
	TotalTokens       int            `json:"totalTokens"`
	CacheHitRate      float64        `json:"cacheHitRate"`
	OutputInputRatio  float64        `json:"outputInputRatio"`
	Models            map[string]int `json:"models"`
}

type WorkflowRouteMetrics struct {
	WorkflowRunID            string                       `json:"workflowRunId"`
	RequestCount             int                          `json:"requestCount"`
	SuccessCount             int                          `json:"successCount"`
	ErrorCount               int                          `json:"errorCount"`
	ErrorRate                float64                      `json:"errorRate"`
	DurationMS               int64                        `json:"durationMs"`
	AverageRequestDurationMS int64                        `json:"averageRequestDurationMs"`
	RequestBytes             int                          `json:"requestBytes"`
	ToolCount                int                          `json:"toolCount"`
	InputItemCount           int                          `json:"inputItemCount"`
	ToolCallCount            int                          `json:"toolCallCount"`
	Roles                    map[string]RouteMetricRollup `json:"roles"`
	UpdatedAt                string                       `json:"updatedAt"`
}

type RouteMetricRollup struct {
	RequestCount             int            `json:"requestCount"`
	SuccessCount             int            `json:"successCount"`
	ErrorCount               int            `json:"errorCount"`
	ErrorRate                float64        `json:"errorRate"`
	DurationMS               int64          `json:"durationMs"`
	AverageRequestDurationMS int64          `json:"averageRequestDurationMs"`
	RequestBytes             int            `json:"requestBytes"`
	ToolCount                int            `json:"toolCount"`
	InputItemCount           int            `json:"inputItemCount"`
	ToolCallCount            int            `json:"toolCallCount"`
	Models                   map[string]int `json:"models"`
}

type LearningCase struct {
	ID                 string         `json:"id"`
	RunID              string         `json:"runId"`
	EvaluationID       string         `json:"evaluationId"`
	CaseType           string         `json:"caseType"`
	ProjectID          string         `json:"projectId"`
	WorkflowTemplateID string         `json:"workflowTemplateId,omitempty"`
	WorkflowType       string         `json:"workflowType"`
	Title              string         `json:"title"`
	Summary            string         `json:"summary"`
	Path               []string       `json:"path"`
	Components         map[string]any `json:"components"`
	Scores             map[string]any `json:"scores"`
	Tags               []string       `json:"tags"`
	RetentionClass     string         `json:"retentionClass"`
	CreatedAt          string         `json:"createdAt"`
}

type LearningCaseHit struct {
	Case       LearningCase `json:"case"`
	Similarity float32      `json:"similarity"`
}

type EvaluationProposal struct {
	ID                 string   `json:"id"`
	ProjectID          string   `json:"projectId"`
	SourceRunID        string   `json:"sourceRunId"`
	SourceEvaluationID string   `json:"sourceEvaluationId"`
	Target             string   `json:"target"`
	Action             string   `json:"action"`
	Reason             string   `json:"reason"`
	Severity           string   `json:"severity"`
	Status             string   `json:"status"`
	ArbiterModel       string   `json:"arbiterModel,omitempty"`
	EscalationModel    string   `json:"escalationModel,omitempty"`
	NeedsEscalation    bool     `json:"needsEscalation,omitempty"`
	EscalationReasons  []string `json:"escalationReasons,omitempty"`
	HighRiskWorkflow   bool     `json:"highRiskWorkflow,omitempty"`
	FailedTask         bool     `json:"failedTask,omitempty"`
	CreatedAt          string   `json:"createdAt"`
	ReviewedAt         string   `json:"reviewedAt,omitempty"`
	ReviewNote         string   `json:"reviewNote,omitempty"`
}

type EvaluationProposalReviewInput struct {
	Status     string `json:"status"`
	ReviewNote string `json:"reviewNote"`
}

type EvaluationProjectHealth struct {
	ProjectID     string  `json:"projectId"`
	TotalRuns     int     `json:"totalRuns"`
	SuccessRate   float64 `json:"successRate"`
	AverageScore  float64 `json:"averageScore"`
	FailedCount   int     `json:"failedCount"`
	PendingCount  int     `json:"pendingCount"`
	ProposalCount int     `json:"proposalCount"`
}

type EvaluationProjectsResponse struct {
	Projects []EvaluationProjectHealth `json:"projects"`
}

type EvaluationProposalsResponse struct {
	Items []EvaluationProposal `json:"items"`
}

type StatisticsTaskItem struct {
	RunID             string               `json:"runId"`
	EvaluationID      string               `json:"evaluationId,omitempty"`
	ProjectID         string               `json:"projectId"`
	TaskTitle         string               `json:"taskTitle"`
	WorkflowType      string               `json:"workflowType"`
	Status            string               `json:"status"`
	Score             float64              `json:"score"`
	Confidence        float64              `json:"confidence"`
	Agent             string               `json:"agent,omitempty"`
	Model             string               `json:"model,omitempty"`
	Rules             []string             `json:"rules,omitempty"`
	ArbiterModel      string               `json:"arbiterModel,omitempty"`
	EscalationModel   string               `json:"escalationModel,omitempty"`
	NeedsEscalation   bool                 `json:"needsEscalation,omitempty"`
	EscalationReasons []string             `json:"escalationReasons,omitempty"`
	HighRiskWorkflow  bool                 `json:"highRiskWorkflow,omitempty"`
	FailedTask        bool                 `json:"failedTask,omitempty"`
	TokenUsage        WorkflowTokenUsage   `json:"tokenUsage,omitempty"`
	RouteMetrics      WorkflowRouteMetrics `json:"routeMetrics,omitempty"`
	DurationMS        int64                `json:"durationMs,omitempty"`
	CreatedAt         string               `json:"createdAt"`
}

type StatisticsTasksResponse struct {
	Items []StatisticsTaskItem `json:"items"`
}

type EvaluationStore struct {
	mu   sync.Mutex
	path string
	data evaluationData
}

type evaluationData struct {
	Version       int                              `json:"version"`
	TaskRuns      []TaskRun                        `json:"taskRuns"`
	Evaluations   []Evaluation                     `json:"evaluations"`
	Reviews       []EvaluationReview               `json:"reviews"`
	LearningCases []LearningCase                   `json:"learningCases"`
	Proposals     []EvaluationProposal             `json:"proposals"`
	TokenUsages   []codexrouter.TokenUsageEvent    `json:"tokenUsages"`
	RouteEvents   []codexrouter.WorkflowRouteEvent `json:"routeEvents"`
}

func NewDefaultEvaluationStore() (*EvaluationStore, error) {
	return NewEvaluationStore(defaultEvaluationDBPath())
}

func NewEvaluationStore(path string) (*EvaluationStore, error) {
	if strings.TrimSpace(path) == "" {
		return nil, fmt.Errorf("evaluation store path is empty")
	}
	store := &EvaluationStore{path: path, data: evaluationData{Version: 1}}
	if err := store.load(); err != nil {
		return nil, err
	}
	return store, nil
}

func (s *EvaluationStore) Close() error {
	return nil
}

func (s *EvaluationStore) SubmitTaskRun(input TaskRunInput) (TaskRun, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if strings.TrimSpace(input.ProjectID) == "" {
		return TaskRun{}, fmt.Errorf("projectId is required")
	}
	if strings.TrimSpace(input.WorkflowType) == "" {
		return TaskRun{}, fmt.Errorf("workflowType is required")
	}
	now := time.Now().UTC().Format(time.RFC3339Nano)
	run := TaskRun{
		ID:                 newEvaluationID("run"),
		ProjectID:          strings.TrimSpace(input.ProjectID),
		WorkflowTemplateID: strings.TrimSpace(input.WorkflowTemplateID),
		WorkflowCopyID:     strings.TrimSpace(input.WorkflowCopyID),
		WorkflowType:       strings.TrimSpace(input.WorkflowType),
		TaskTitle:          strings.TrimSpace(input.TaskTitle),
		SubmittedStatus:    normalizeTaskStatus(input.SubmittedStatus),
		StartedAt:          strings.TrimSpace(input.StartedAt),
		EndedAt:            strings.TrimSpace(input.EndedAt),
		DurationMS:         input.DurationMS,
		EvaluationStatus:   "pending",
		Context:            cloneEvaluationMap(input.Context),
		Metrics:            cloneEvaluationMap(input.Metrics),
		Evidence:           cloneEvaluationMap(input.Evidence),
		CreatedAt:          now,
		UpdatedAt:          now,
	}
	s.data.TaskRuns = append(s.data.TaskRuns, run)
	return run, s.saveLocked()
}

func (s *EvaluationStore) TaskRuns() ([]TaskRun, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	runs := make([]TaskRun, len(s.data.TaskRuns))
	for index, run := range s.data.TaskRuns {
		runs[index] = s.enrichTaskRunMetricsLocked(run)
	}
	sort.Slice(runs, func(i, j int) bool { return runs[i].CreatedAt > runs[j].CreatedAt })
	return runs, nil
}

func (s *EvaluationStore) DeleteTaskRun(id string) (bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	id = strings.TrimSpace(id)
	if id == "" {
		return false, nil
	}
	found := false
	taskRuns := s.data.TaskRuns[:0]
	for _, run := range s.data.TaskRuns {
		if run.ID == id {
			found = true
			continue
		}
		taskRuns = append(taskRuns, run)
	}
	if !found {
		return false, nil
	}
	s.data.TaskRuns = taskRuns
	evaluations := s.data.Evaluations[:0]
	deletedEvaluationIDs := map[string]bool{}
	for _, evaluation := range s.data.Evaluations {
		if evaluation.RunID == id {
			deletedEvaluationIDs[evaluation.ID] = true
			continue
		}
		evaluations = append(evaluations, evaluation)
	}
	s.data.Evaluations = evaluations
	reviews := s.data.Reviews[:0]
	for _, review := range s.data.Reviews {
		if deletedEvaluationIDs[review.EvaluationID] {
			continue
		}
		reviews = append(reviews, review)
	}
	s.data.Reviews = reviews
	learningCases := s.data.LearningCases[:0]
	for _, item := range s.data.LearningCases {
		if item.RunID == id || deletedEvaluationIDs[item.EvaluationID] {
			continue
		}
		learningCases = append(learningCases, item)
	}
	s.data.LearningCases = learningCases
	proposals := s.data.Proposals[:0]
	for _, proposal := range s.data.Proposals {
		if proposal.SourceRunID == id || deletedEvaluationIDs[proposal.SourceEvaluationID] {
			continue
		}
		proposals = append(proposals, proposal)
	}
	s.data.Proposals = proposals
	return true, s.saveLocked()
}

func (s *EvaluationStore) EvaluatePending(limit int) (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if limit <= 0 {
		limit = 20
	}
	count := 0
	for index := range s.data.TaskRuns {
		if count >= limit {
			break
		}
		if s.data.TaskRuns[index].EvaluationStatus != "pending" && s.data.TaskRuns[index].EvaluationStatus != "re_evaluation_requested" {
			continue
		}
		evaluation := evaluateTaskRun(s.data.TaskRuns[index])
		s.data.Evaluations = append(s.data.Evaluations, evaluation)
		s.data.TaskRuns[index].EvaluationStatus = "evaluated"
		s.data.TaskRuns[index].UpdatedAt = evaluation.CreatedAt
		if learningCase, ok := learningCaseForRun(s.data.TaskRuns[index], evaluation); ok {
			log.Printf("[evaluation] archive learning case run=%s evaluation=%s type=%s score=%.1f", s.data.TaskRuns[index].ID, evaluation.ID, learningCase.CaseType, evaluation.OverallScore)
			s.data.LearningCases = append(s.data.LearningCases, learningCase)
		}
		log.Printf("[evaluation] evaluated task run id=%s workflow=%s status=%s score=%.1f policy=%s", s.data.TaskRuns[index].ID, s.data.TaskRuns[index].WorkflowType, evaluation.FinalStatus, evaluation.OverallScore, evaluation.ModelPolicy["mode"])
		count++
	}
	if count == 0 {
		return 0, nil
	}
	return count, s.saveLocked()
}

func (s *EvaluationStore) Evaluations() ([]Evaluation, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	evaluations := append([]Evaluation(nil), s.data.Evaluations...)
	for index := range evaluations {
		if review, ok := s.latestReviewLocked(evaluations[index].ID); ok {
			evaluations[index].LatestReviewStatus = review.OverrideStatus
			evaluations[index].LatestReviewScore = review.OverrideScore
			if comment, ok := review.Review["comment"].(string); ok {
				evaluations[index].LatestReviewComment = comment
			}
		}
	}
	sort.Slice(evaluations, func(i, j int) bool { return evaluations[i].CreatedAt > evaluations[j].CreatedAt })
	return evaluations, nil
}

func (s *EvaluationStore) ReviewEvaluation(evaluationID string, input EvaluationReviewInput) (EvaluationReview, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, ok := s.evaluationByIDLocked(evaluationID); !ok {
		return EvaluationReview{}, sql.ErrNoRows
	}
	review := EvaluationReview{
		ID:             newEvaluationID("review"),
		EvaluationID:   evaluationID,
		Reviewer:       strings.TrimSpace(input.Reviewer),
		OverrideStatus: strings.TrimSpace(input.OverrideStatus),
		OverrideScore:  input.OverrideScore,
		Review:         cloneEvaluationMap(input.Review),
		CreatedAt:      time.Now().UTC().Format(time.RFC3339Nano),
	}
	s.data.Reviews = append(s.data.Reviews, review)
	return review, s.saveLocked()
}

func (s *EvaluationStore) Summary() (EvaluationSummary, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.summaryLocked(), nil
}

func (s *EvaluationStore) LearningCases() ([]LearningCase, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	cases := append([]LearningCase(nil), s.data.LearningCases...)
	sort.Slice(cases, func(i, j int) bool { return cases[i].CreatedAt > cases[j].CreatedAt })
	return cases, nil
}

func (s *EvaluationStore) SearchLearningCases(query string, limit int) ([]LearningCaseHit, error) {
	s.mu.Lock()
	cases := append([]LearningCase(nil), s.data.LearningCases...)
	indexPath := filepath.Join(filepath.Dir(s.path), "vector-index", "chromem-learning-cases")
	s.mu.Unlock()
	if strings.TrimSpace(query) == "" {
		return nil, fmt.Errorf("query is required")
	}
	if limit <= 0 {
		limit = 5
	}
	index, err := buildLearningCaseIndex(indexPath, cases)
	if err != nil {
		return nil, err
	}
	hits, err := index.Search(context.Background(), query, limit)
	if err != nil {
		return nil, err
	}
	log.Printf("[learning-cases] search query=%q limit=%d hits=%d", query, limit, len(hits))
	return hits, nil
}

func (s *EvaluationStore) RebuildLearningCaseIndex() (int, error) {
	s.mu.Lock()
	cases := append([]LearningCase(nil), s.data.LearningCases...)
	indexPath := filepath.Join(filepath.Dir(s.path), "vector-index", "chromem-learning-cases")
	s.mu.Unlock()
	if err := os.RemoveAll(indexPath); err != nil {
		return 0, fmt.Errorf("reset learning case vector index: %w", err)
	}
	if _, err := buildLearningCaseIndex(indexPath, cases); err != nil {
		return 0, err
	}
	log.Printf("[learning-cases] rebuilt chromem-go index path=%s cases=%d", indexPath, len(cases))
	return len(cases), nil
}

func (s *EvaluationStore) RecordTokenUsage(event codexrouter.TokenUsageEvent) error {
	event.WorkflowRunID = strings.TrimSpace(event.WorkflowRunID)
	if event.WorkflowRunID == "" {
		return nil
	}
	event.Role = normalizeTokenRole(event.Role)
	event.Model = strings.TrimSpace(event.Model)
	if event.Model == "" {
		event.Model = "unknown"
	}
	if event.TotalTokens == 0 {
		event.TotalTokens = event.InputTokens + event.OutputTokens
	}
	if strings.TrimSpace(event.CreatedAt) == "" {
		event.CreatedAt = time.Now().UTC().Format(time.RFC3339Nano)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.data.TokenUsages = append(s.data.TokenUsages, event)
	return s.saveLocked()
}

func (s *EvaluationStore) TokenUsageForWorkflowRun(workflowRunID string) (WorkflowTokenUsage, bool, error) {
	workflowRunID = strings.TrimSpace(workflowRunID)
	if workflowRunID == "" {
		return WorkflowTokenUsage{}, false, nil
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	usage := aggregateTokenUsageLocked(workflowRunID, s.data.TokenUsages)
	return usage, usage.RequestCount > 0, nil
}

func (s *EvaluationStore) RecordWorkflowRouteEvent(event codexrouter.WorkflowRouteEvent) error {
	event.WorkflowRunID = strings.TrimSpace(event.WorkflowRunID)
	if event.WorkflowRunID == "" {
		return nil
	}
	event.Role = normalizeTokenRole(event.Role)
	event.Model = strings.TrimSpace(event.Model)
	if event.Model == "" {
		event.Model = "unknown"
	}
	if strings.TrimSpace(event.CreatedAt) == "" {
		event.CreatedAt = time.Now().UTC().Format(time.RFC3339Nano)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.data.RouteEvents = append(s.data.RouteEvents, event)
	return s.saveLocked()
}

func (s *EvaluationStore) RouteMetricsForWorkflowRun(workflowRunID string) (WorkflowRouteMetrics, bool, error) {
	workflowRunID = strings.TrimSpace(workflowRunID)
	if workflowRunID == "" {
		return WorkflowRouteMetrics{}, false, nil
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	metrics := aggregateRouteMetricsLocked(workflowRunID, s.data.RouteEvents)
	return metrics, metrics.RequestCount > 0, nil
}

func (s *EvaluationStore) LookupWorkflowSession(sessionID string) (codexrouter.WorkflowSessionContext, bool, error) {
	return codexrouter.WorkflowSessionContext{}, false, nil
}

func (s *EvaluationStore) EvaluationProjects() (EvaluationProjectsResponse, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	runByID := s.runByIDLocked()
	type total struct {
		runs, evaluated, failed, pending int
		score                            float64
	}
	totals := map[string]*total{}
	for _, run := range s.data.TaskRuns {
		item := totals[run.ProjectID]
		if item == nil {
			item = &total{}
			totals[run.ProjectID] = item
		}
		item.runs++
		if run.EvaluationStatus == "pending" {
			item.pending++
		}
	}
	for _, eval := range s.data.Evaluations {
		run := s.enrichTaskRunMetricsLocked(runByID[eval.RunID])
		item := totals[run.ProjectID]
		if item == nil {
			item = &total{}
			totals[run.ProjectID] = item
		}
		item.evaluated++
		item.score += eval.OverallScore
		if eval.FinalStatus == "failed" {
			item.failed++
		}
	}
	response := EvaluationProjectsResponse{}
	for projectID, item := range totals {
		health := EvaluationProjectHealth{ProjectID: projectID, TotalRuns: item.runs, FailedCount: item.failed, PendingCount: item.pending, ProposalCount: 0}
		if item.evaluated > 0 {
			health.AverageScore = round1(item.score / float64(item.evaluated))
			health.SuccessRate = round1(float64(item.evaluated-item.failed) * 100 / float64(item.evaluated))
		}
		response.Projects = append(response.Projects, health)
	}
	sort.Slice(response.Projects, func(i, j int) bool { return response.Projects[i].AverageScore < response.Projects[j].AverageScore })
	return response, nil
}

func (s *EvaluationStore) EvaluationProposals(projectID, status string) (EvaluationProposalsResponse, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	items := []EvaluationProposal{}
	for _, proposal := range s.data.Proposals {
		if projectID != "" && proposal.ProjectID != projectID {
			continue
		}
		if status != "" && proposal.Status != status {
			continue
		}
		items = append(items, proposal)
	}
	sort.Slice(items, func(i, j int) bool { return items[i].CreatedAt > items[j].CreatedAt })
	return EvaluationProposalsResponse{Items: items}, nil
}

func (s *EvaluationStore) ReviewEvaluationProposal(id string, input EvaluationProposalReviewInput) (EvaluationProposal, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	status := normalizeProposalStatus(input.Status)
	for index := range s.data.Proposals {
		if s.data.Proposals[index].ID != id {
			continue
		}
		s.data.Proposals[index].Status = status
		s.data.Proposals[index].ReviewNote = strings.TrimSpace(input.ReviewNote)
		s.data.Proposals[index].ReviewedAt = time.Now().UTC().Format(time.RFC3339Nano)
		log.Printf("[evaluation] proposal review id=%s status=%s", id, status)
		return s.data.Proposals[index], s.saveLocked()
	}
	return EvaluationProposal{}, sql.ErrNoRows
}

func (s *EvaluationStore) StatisticsTasks(view, rangeKey string) (StatisticsTasksResponse, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	runByID := s.runByIDLocked()
	items := []StatisticsTaskItem{}
	cutoff := statisticsCutoff(rangeKey)
	for _, eval := range s.data.Evaluations {
		run := s.enrichTaskRunMetricsLocked(runByID[eval.RunID])
		if !timeInRange(eval.CreatedAt, cutoff) {
			continue
		}
		item := statisticsItemForRun(run, eval)
		items = append(items, item)
	}
	if view == "pending" {
		for _, run := range s.data.TaskRuns {
			if run.EvaluationStatus == "pending" && timeInRange(run.CreatedAt, cutoff) {
				items = append(items, statisticsItemForRun(s.enrichTaskRunMetricsLocked(run), Evaluation{}))
			}
		}
	}
	items = filterStatisticsItems(items, view)
	return StatisticsTasksResponse{Items: items}, nil
}

func (s *EvaluationStore) runByIDLocked() map[string]TaskRun {
	runs := map[string]TaskRun{}
	for _, run := range s.data.TaskRuns {
		runs[run.ID] = run
	}
	return runs
}

func (s *EvaluationStore) load() error {
	data, err := os.ReadFile(s.path)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("read evaluation store: %w", err)
	}
	if err := json.Unmarshal(stripUTF8BOM(data), &s.data); err != nil {
		return fmt.Errorf("parse evaluation store: %w", err)
	}
	if s.data.Version == 0 {
		s.data.Version = 1
	}
	return nil
}

func (s *EvaluationStore) saveLocked() error {
	if err := os.MkdirAll(filepath.Dir(s.path), 0o755); err != nil {
		return fmt.Errorf("create evaluation store directory: %w", err)
	}
	data, err := json.MarshalIndent(s.data, "", "  ")
	if err != nil {
		return fmt.Errorf("marshal evaluation store: %w", err)
	}
	data = append(data, '\n')
	if err := os.WriteFile(s.path, data, 0o644); err != nil {
		return fmt.Errorf("write evaluation store: %w", err)
	}
	return nil
}

func (s *EvaluationStore) evaluationByIDLocked(id string) (Evaluation, bool) {
	for _, evaluation := range s.data.Evaluations {
		if evaluation.ID == id {
			return evaluation, true
		}
	}
	return Evaluation{}, false
}

func (s *EvaluationStore) latestReviewLocked(evaluationID string) (EvaluationReview, bool) {
	for i := len(s.data.Reviews) - 1; i >= 0; i-- {
		if s.data.Reviews[i].EvaluationID == evaluationID {
			return s.data.Reviews[i], true
		}
	}
	return EvaluationReview{}, false
}

func (s *EvaluationStore) summaryLocked() EvaluationSummary {
	summary := EvaluationSummary{ComponentStats: map[string]ComponentStat{}}
	summary.TotalRuns = len(s.data.TaskRuns)
	for _, run := range s.data.TaskRuns {
		if run.EvaluationStatus == "pending" {
			summary.PendingRuns++
		}
		if run.EvaluationStatus == "evaluated" {
			summary.EvaluatedRuns++
		}
	}
	type workflowTotal struct {
		templateID   string
		workflowType string
		count        int
		score        float64
		success      int
	}
	workflowTotals := map[string]*workflowTotal{}
	issueCounts := map[string]int{}
	componentTotals := map[string]struct {
		count int
		score float64
	}{}
	dimensionTotals := map[string]struct {
		dimension string
		name      string
		count     int
		score     float64
		success   int
	}{}
	runByID := map[string]TaskRun{}
	for _, run := range s.data.TaskRuns {
		runByID[run.ID] = run
	}
	for _, evaluation := range s.data.Evaluations {
		run := runByID[evaluation.RunID]
		key := run.WorkflowTemplateID + "|" + run.WorkflowType
		total := workflowTotals[key]
		if total == nil {
			total = &workflowTotal{templateID: run.WorkflowTemplateID, workflowType: run.WorkflowType}
			workflowTotals[key] = total
		}
		total.count++
		total.score += evaluation.OverallScore
		if evaluation.FinalStatus == "success" {
			total.success++
		}
		if causes, ok := evaluation.Analysis["primaryCauses"].([]any); ok {
			for _, cause := range causes {
				if text, ok := cause.(string); ok && text != "" {
					issueCounts[text]++
				}
			}
		}
		if attribution, ok := evaluation.Analysis["attribution"].(map[string]any); ok {
			for component, raw := range attribution {
				item, ok := raw.(map[string]any)
				if !ok {
					continue
				}
				total := componentTotals[component]
				total.count++
				total.score += numberValue(item["score"])
				componentTotals[component] = total
			}
		}
		for _, entry := range dimensionEntries(run) {
			key := entry.dimension + "|" + entry.name
			total := dimensionTotals[key]
			total.dimension = entry.dimension
			total.name = entry.name
			total.count++
			total.score += evaluation.OverallScore
			if evaluation.FinalStatus == "success" {
				total.success++
			}
			dimensionTotals[key] = total
		}
	}
	for _, total := range workflowTotals {
		stat := WorkflowEvaluationStat{
			WorkflowTemplateID: total.templateID,
			WorkflowType:       total.workflowType,
			SampleCount:        total.count,
			AverageScore:       round1(total.score / float64(total.count)),
			SuccessRate:        round1(float64(total.success) * 100 / float64(total.count)),
		}
		summary.WorkflowMetrics = append(summary.WorkflowMetrics, stat)
	}
	sort.Slice(summary.WorkflowMetrics, func(i, j int) bool {
		return summary.WorkflowMetrics[i].SampleCount > summary.WorkflowMetrics[j].SampleCount
	})
	for issue, count := range issueCounts {
		summary.TopIssues = append(summary.TopIssues, EvaluationIssueStat{Issue: issue, Count: count})
	}
	sort.Slice(summary.TopIssues, func(i, j int) bool { return summary.TopIssues[i].Count > summary.TopIssues[j].Count })
	for component, total := range componentTotals {
		summary.ComponentStats[component] = ComponentStat{SampleCount: total.count, AverageScore: round1(total.score / float64(total.count))}
	}
	for _, total := range dimensionTotals {
		summary.DimensionStats = append(summary.DimensionStats, EvaluationDimensionStat{
			Dimension:    total.dimension,
			Name:         total.name,
			SampleCount:  total.count,
			AverageScore: round1(total.score / float64(total.count)),
			SuccessRate:  round1(float64(total.success) * 100 / float64(total.count)),
		})
	}
	sort.Slice(summary.DimensionStats, func(i, j int) bool {
		if summary.DimensionStats[i].Dimension == summary.DimensionStats[j].Dimension {
			return summary.DimensionStats[i].SampleCount > summary.DimensionStats[j].SampleCount
		}
		return summary.DimensionStats[i].Dimension < summary.DimensionStats[j].Dimension
	})
	return summary
}

func evaluateTaskRun(run TaskRun) Evaluation {
	scores, analysis := scoreTaskRun(run)
	overall := numberValue(scores["overallScore"])
	now := time.Now().UTC().Format(time.RFC3339Nano)
	return Evaluation{
		ID:              newEvaluationID("eval"),
		RunID:           run.ID,
		RubricID:        rubricForWorkflow(run.WorkflowType),
		RubricVersion:   "v1",
		EvaluationLevel: 0,
		FinalStatus:     finalStatus(run, overall),
		OverallScore:    overall,
		Confidence:      numberValue(analysis["confidence"]),
		Scores:          scores,
		Analysis:        analysis,
		ModelJudgements: map[string]any{"strategy": "rule_only_v1", "judgements": []any{}},
		ModelPolicy:     evaluationModelPolicy(run),
		CreatedAt:       now,
	}
}

func defaultEvaluationDBPath() string {
	home, err := os.UserHomeDir()
	if err != nil || strings.TrimSpace(home) == "" {
		return filepath.Join(".nexus-evaluation", "evaluation.json")
	}
	nexusPath := filepath.Join(home, ".nexus")
	if stat, err := os.Stat(nexusPath); err == nil && stat.IsDir() {
		return filepath.Join(nexusPath, "evaluation.json")
	}
	return filepath.Join(home, ".nexus-evaluation", "evaluation.json")
}

func scoreTaskRun(run TaskRun) (map[string]any, map[string]any) {
	completion := statusScore(run.SubmittedStatus)
	verification := verificationScore(run.Evidence)
	efficiency := efficiencyScore(run)
	tokenEfficiency := tokenEfficiencyScore(run)
	routeHealth := routeHealthScore(run)
	workflowFit := 70.0
	if run.WorkflowTemplateID != "" || run.WorkflowCopyID != "" {
		workflowFit = 88
	}
	risk := riskControlScore(run.Evidence)
	overall := completion*0.22 + verification*0.23 + efficiency*0.12 + tokenEfficiency*0.08 + routeHealth*0.08 + workflowFit*0.16 + risk*0.11
	scores := map[string]any{
		"completionScore":      round1(completion),
		"accuracyScore":        round1((completion + verification) / 2),
		"efficiencyScore":      round1(efficiency),
		"tokenEfficiencyScore": round1(tokenEfficiency),
		"routeHealthScore":     round1(routeHealth),
		"verificationScore":    round1(verification),
		"riskControlScore":     round1(risk),
		"workflowFitScore":     round1(workflowFit),
		"overallScore":         round1(overall),
	}
	attribution := map[string]any{
		"workflow": componentAttribution(workflowFit, workflowIssue(run)),
		"agent":    componentAttribution(componentPresenceScore(run.Context, "agent"), nil),
		"model":    componentAttribution(componentPresenceScore(run.Context, "model"), nil),
		"rules":    componentAttribution(rulesScore(run), rulesIssues(run)),
		"skills":   componentAttribution(componentPresenceScore(run.Context, "skills"), nil),
		"context":  componentAttribution(contextScore(run), contextIssues(run)),
		"tools":    componentAttribution(toolsScore(run), nil),
		"tokens":   componentAttribution(tokenEfficiency, tokenIssues(run)),
		"route":    componentAttribution(routeHealth, routeIssues(run)),
	}
	analysis := map[string]any{
		"confidence":    round1(confidenceForRun(run) / 100),
		"primaryCauses": stringSliceToAny(primaryCauses(run, scores)),
		"attribution":   attribution,
	}
	return scores, analysis
}

func normalizeTaskStatus(status string) string {
	switch strings.ToLower(strings.TrimSpace(status)) {
	case "success", "partial_success", "failed", "cancelled":
		return strings.ToLower(strings.TrimSpace(status))
	default:
		return "success"
	}
}

func newEvaluationID(prefix string) string {
	return fmt.Sprintf("%s_%d", prefix, time.Now().UnixNano())
}

func rubricForWorkflow(workflowType string) string {
	normalized := strings.TrimSpace(strings.ToLower(workflowType))
	if normalized == "" {
		return "default-rubric"
	}
	return normalized + "-rubric"
}

func statusScore(status string) float64 {
	switch normalizeTaskStatus(status) {
	case "success":
		return 95
	case "partial_success":
		return 65
	case "failed":
		return 25
	case "cancelled":
		return 10
	default:
		return 50
	}
}

func verificationScore(evidence map[string]any) float64 {
	verification, _ := evidence["verification"].(map[string]any)
	if len(verification) == 0 {
		return 45
	}
	if passed, ok := verification["passed"].(bool); ok && passed {
		return 92
	}
	if has, ok := verification["hasVerification"].(bool); ok && has {
		return 72
	}
	return 45
}

func riskControlScore(evidence map[string]any) float64 {
	if risks, ok := evidence["risks"].([]any); ok && len(risks) > 0 {
		return 72
	}
	if verificationScore(evidence) >= 80 {
		return 84
	}
	return 62
}

func efficiencyScore(run TaskRun) float64 {
	score := 86.0
	if run.DurationMS > 0 {
		minutes := float64(run.DurationMS) / 60000
		if minutes > 60 {
			score -= math.Min(25, (minutes-60)/4)
		}
	}
	score -= numberValue(run.Metrics["retryCount"]) * 4
	score -= numberValue(run.Metrics["errorCount"]) * 3
	if score < 20 {
		return 20
	}
	return score
}

func tokenEfficiencyScore(run TaskRun) float64 {
	usage, ok := tokenUsageMap(run)
	if !ok {
		return 78
	}
	total := numberValue(usage["totalTokens"])
	input := numberValue(usage["inputTokens"])
	cached := numberValue(usage["cachedInputTokens"])
	requestCount := numberValue(usage["requestCount"])
	score := 88.0
	if total > 250000 {
		score -= math.Min(30, (total-250000)/20000)
	}
	if requestCount > 12 {
		score -= math.Min(18, (requestCount-12)*2)
	}
	if input > 0 {
		cacheHitRate := cached * 100 / input
		if cacheHitRate < 20 && input > 50000 {
			score -= 10
		}
		if cacheHitRate > 60 {
			score += 4
		}
	}
	if score < 20 {
		return 20
	}
	if score > 96 {
		return 96
	}
	return score
}

func tokenUsageMap(run TaskRun) (map[string]any, bool) {
	usage, ok := run.Metrics["tokenUsage"].(map[string]any)
	if !ok || len(usage) == 0 {
		return nil, false
	}
	return usage, true
}

func tokenIssues(run TaskRun) []string {
	usage, ok := tokenUsageMap(run)
	if !ok {
		return []string{"token metrics missing from task evidence"}
	}
	issues := []string{}
	if numberValue(usage["totalTokens"]) > 250000 {
		issues = append(issues, "workflow token usage exceeds first-pass threshold")
	}
	if numberValue(usage["requestCount"]) > 12 {
		issues = append(issues, "many model requests in one workflow run")
	}
	input := numberValue(usage["inputTokens"])
	if input > 50000 && numberValue(usage["cacheHitRate"]) < 20 {
		issues = append(issues, "low prompt cache reuse for large context")
	}
	return issues
}

func routeHealthScore(run TaskRun) float64 {
	metrics, ok := routeMetricsMap(run)
	if !ok {
		return 78
	}
	score := 90.0
	requestCount := numberValue(metrics["requestCount"])
	errorCount := numberValue(metrics["errorCount"])
	duration := numberValue(metrics["durationMs"])
	toolCallCount := numberValue(metrics["toolCallCount"])
	if errorCount > 0 {
		score -= math.Min(30, errorCount*10)
	}
	if requestCount > 12 {
		score -= math.Min(18, (requestCount-12)*2)
	}
	if duration > 15*60*1000 {
		score -= math.Min(18, (duration-15*60*1000)/(2*60*1000))
	}
	if toolCallCount == 0 && requestCount >= 4 {
		score -= 8
	}
	if score < 20 {
		return 20
	}
	return score
}

func routeMetricsMap(run TaskRun) (map[string]any, bool) {
	metrics, ok := run.Metrics["routeMetrics"].(map[string]any)
	if !ok || len(metrics) == 0 {
		return nil, false
	}
	return metrics, true
}

func routeIssues(run TaskRun) []string {
	metrics, ok := routeMetricsMap(run)
	if !ok {
		return []string{"route metrics missing from task evidence"}
	}
	issues := []string{}
	if numberValue(metrics["errorCount"]) > 0 {
		issues = append(issues, "model route errors occurred during workflow")
	}
	if numberValue(metrics["requestCount"]) > 12 {
		issues = append(issues, "many model requests in one workflow run")
	}
	if numberValue(metrics["durationMs"]) > 15*60*1000 {
		issues = append(issues, "workflow route duration exceeds first-pass threshold")
	}
	if numberValue(metrics["toolCallCount"]) == 0 && numberValue(metrics["requestCount"]) >= 4 {
		issues = append(issues, "multiple model requests without tool calls")
	}
	return issues
}

func componentPresenceScore(context map[string]any, key string) float64 {
	value, ok := context[key]
	if !ok || value == nil {
		return 68
	}
	switch typed := value.(type) {
	case string:
		if strings.TrimSpace(typed) == "" {
			return 68
		}
	case []any:
		if len(typed) == 0 {
			return 68
		}
	}
	return 82
}

func rulesScore(run TaskRun) float64 {
	raw, ok := run.Context["rules"].([]any)
	if !ok {
		return 70
	}
	count := len(raw)
	if count == 0 {
		return 62
	}
	if count > 12 {
		return 58
	}
	return 82
}

func toolsScore(run TaskRun) float64 {
	if numberValue(run.Metrics["toolErrorCount"]) > 0 {
		return 60
	}
	return componentPresenceScore(run.Context, "tools")
}

func contextScore(run TaskRun) float64 {
	if missing, ok := run.Evidence["contextMissing"].(bool); ok && missing {
		return 55
	}
	return 78
}

func componentAttribution(score float64, issues []string) map[string]any {
	impact := "low"
	if score < 65 {
		impact = "high"
	} else if score < 78 {
		impact = "medium"
	}
	if issues == nil {
		issues = []string{}
	}
	return map[string]any{"score": round1(score), "impact": impact, "issues": stringSliceToAny(issues)}
}

func workflowIssue(run TaskRun) []string {
	if run.WorkflowTemplateID == "" && run.WorkflowCopyID == "" {
		return []string{"workflow identity missing from task evidence"}
	}
	return nil
}

func rulesIssues(run TaskRun) []string {
	raw, ok := run.Context["rules"].([]any)
	if !ok || len(raw) == 0 {
		return []string{"rules not recorded; cannot measure rule contribution"}
	}
	if len(raw) > 12 {
		return []string{"many rules loaded; possible rule overload"}
	}
	return nil
}

func contextIssues(run TaskRun) []string {
	if missing, ok := run.Evidence["contextMissing"].(bool); ok && missing {
		return []string{"task evidence reports missing project context"}
	}
	return nil
}

func primaryCauses(run TaskRun, scores map[string]any) []string {
	causes := []string{}
	if numberValue(scores["verificationScore"]) < 65 {
		causes = append(causes, "verification_gap")
	}
	if rulesScore(run) < 65 {
		causes = append(causes, "rules_overload_or_missing")
	}
	if contextScore(run) < 65 {
		causes = append(causes, "context_missing")
	}
	if numberValue(scores["efficiencyScore"]) < 70 {
		causes = append(causes, "efficiency_issue")
	}
	if numberValue(scores["tokenEfficiencyScore"]) < 70 {
		causes = append(causes, "token_overuse")
	}
	if numberValue(scores["routeHealthScore"]) < 70 {
		causes = append(causes, "route_health_issue")
	}
	return causes
}

func finalStatus(run TaskRun, overall float64) string {
	if normalizeTaskStatus(run.SubmittedStatus) == "failed" || overall < 45 {
		return "failed"
	}
	if normalizeTaskStatus(run.SubmittedStatus) == "partial_success" || overall < 75 {
		return "partial_success"
	}
	return "success"
}

func confidenceForRun(run TaskRun) float64 {
	confidence := 70.0
	if verificationScore(run.Evidence) >= 80 {
		confidence += 12
	}
	if run.WorkflowTemplateID != "" || run.WorkflowCopyID != "" {
		confidence += 8
	}
	return math.Min(confidence, 95)
}

func numberValue(value any) float64 {
	switch typed := value.(type) {
	case int:
		return float64(typed)
	case int64:
		return float64(typed)
	case float64:
		return typed
	case json.Number:
		number, _ := typed.Float64()
		return number
	case float32:
		return float64(typed)
	case int32:
		return float64(typed)
	case int16:
		return float64(typed)
	case int8:
		return float64(typed)
	case uint:
		return float64(typed)
	case uint64:
		return float64(typed)
	case uint32:
		return float64(typed)
	case uint16:
		return float64(typed)
	case uint8:
		return float64(typed)
	default:
		return 0
	}
}

func round1(value float64) float64 {
	return math.Round(value*10) / 10
}

func cloneEvaluationMap(input map[string]any) map[string]any {
	if input == nil {
		return map[string]any{}
	}
	data, err := json.Marshal(input)
	if err != nil {
		return map[string]any{}
	}
	var output map[string]any
	if err := json.Unmarshal(data, &output); err != nil {
		return map[string]any{}
	}
	return output
}

func stringSliceToAny(values []string) []any {
	output := make([]any, 0, len(values))
	for _, value := range values {
		output = append(output, value)
	}
	return output
}

type dimensionEntry struct {
	dimension string
	name      string
}

func dimensionEntries(run TaskRun) []dimensionEntry {
	entries := []dimensionEntry{}
	entries = append(entries, entriesForContextValue("agent", run.Context["agent"])...)
	entries = append(entries, entriesForContextValue("model", run.Context["model"])...)
	entries = append(entries, entriesForContextValue("rules", run.Context["rules"])...)
	return entries
}

func entriesForContextValue(dimension string, value any) []dimensionEntry {
	names := contextNames(value)
	entries := make([]dimensionEntry, 0, len(names))
	for _, name := range names {
		if strings.TrimSpace(name) == "" {
			continue
		}
		entries = append(entries, dimensionEntry{dimension: dimension, name: name})
	}
	return entries
}

func contextNames(value any) []string {
	switch typed := value.(type) {
	case string:
		return []string{typed}
	case []any:
		names := []string{}
		for _, item := range typed {
			names = append(names, contextNames(item)...)
		}
		return names
	case map[string]any:
		for _, key := range []string{"id", "name", "model", "templateId"} {
			if value, ok := typed[key].(string); ok && strings.TrimSpace(value) != "" {
				return []string{value}
			}
		}
	}
	return nil
}

type learningCaseIndex struct {
	collection *chromem.Collection
	cases      map[string]LearningCase
}

func buildLearningCaseIndex(path string, cases []LearningCase) (*learningCaseIndex, error) {
	if err := os.MkdirAll(path, 0o755); err != nil {
		return nil, fmt.Errorf("create chromem-go index directory: %w", err)
	}
	db, err := chromem.NewPersistentDB(path, false)
	if err != nil {
		return nil, fmt.Errorf("open chromem-go index: %w", err)
	}
	collection, err := db.GetOrCreateCollection("learning-cases", map[string]string{"owner": "nexus-agents"}, localHashEmbedding)
	if err != nil {
		return nil, fmt.Errorf("create learning case collection: %w", err)
	}
	index := &learningCaseIndex{collection: collection, cases: map[string]LearningCase{}}
	ctx := context.Background()
	for _, item := range cases {
		index.cases[item.ID] = item
		if _, err := collection.GetByID(ctx, item.ID); err == nil {
			continue
		}
		document := chromem.Document{
			ID:       item.ID,
			Metadata: map[string]string{"workflowType": item.WorkflowType, "caseType": item.CaseType, "projectId": item.ProjectID},
			Content:  learningCaseSearchText(item),
		}
		if err := collection.AddDocument(ctx, document); err != nil {
			return nil, fmt.Errorf("index learning case %s: %w", item.ID, err)
		}
	}
	return index, nil
}

func (i *learningCaseIndex) Search(ctx context.Context, query string, limit int) ([]LearningCaseHit, error) {
	if len(i.cases) == 0 {
		return []LearningCaseHit{}, nil
	}
	if limit > len(i.cases) {
		limit = len(i.cases)
	}
	results, err := i.collection.Query(ctx, query, limit, nil, nil)
	if err != nil {
		return nil, fmt.Errorf("query learning case index: %w", err)
	}
	hits := make([]LearningCaseHit, 0, len(results))
	for _, result := range results {
		item, ok := i.cases[result.ID]
		if !ok {
			continue
		}
		hits = append(hits, LearningCaseHit{Case: item, Similarity: result.Similarity})
	}
	return hits, nil
}

func localHashEmbedding(_ context.Context, text string) ([]float32, error) {
	const dims = 64
	vector := make([]float32, dims)
	words := strings.Fields(strings.ToLower(text))
	if len(words) == 0 {
		words = []string{text}
	}
	for _, word := range words {
		sum := sha256.Sum256([]byte(word))
		for index := 0; index < 4; index++ {
			slot := int(sum[index]) % dims
			sign := float32(1)
			if sum[index+4]%2 == 0 {
				sign = -1
			}
			vector[slot] += sign
		}
	}
	var norm float64
	for _, value := range vector {
		norm += float64(value * value)
	}
	if norm == 0 {
		vector[0] = 1
		return vector, nil
	}
	scale := float32(math.Sqrt(norm))
	for index := range vector {
		vector[index] /= scale
	}
	return vector, nil
}

func learningCaseForRun(run TaskRun, evaluation Evaluation) (LearningCase, bool) {
	verification := numberValue(evaluation.Scores["verificationScore"])
	highValueSuccess := evaluation.FinalStatus == "success" && evaluation.OverallScore >= 85 && verification >= 80 && evaluation.Confidence >= 0.8
	representativeFailure := evaluation.FinalStatus == "failed" || len(anySliceToStrings(evaluation.Analysis["primaryCauses"])) > 0
	if !highValueSuccess && !representativeFailure {
		return LearningCase{}, false
	}
	caseType := "failure"
	retention := "medium_term"
	if highValueSuccess {
		caseType = "success"
		retention = "long_term"
	}
	return LearningCase{
		ID:                 newEvaluationID("case"),
		RunID:              run.ID,
		EvaluationID:       evaluation.ID,
		CaseType:           caseType,
		ProjectID:          run.ProjectID,
		WorkflowTemplateID: run.WorkflowTemplateID,
		WorkflowType:       run.WorkflowType,
		Title:              firstNonEmpty(run.TaskTitle, run.WorkflowType+" task"),
		Summary:            evidenceString(run.Evidence, "summary", firstNonEmpty(run.TaskTitle, "Task run evidence")),
		Path:               successfulPath(run, evaluation),
		Components:         learningComponents(run),
		Scores:             evaluation.Scores,
		Tags:               learningTags(run, evaluation),
		RetentionClass:     retention,
		CreatedAt:          evaluation.CreatedAt,
	}, true
}

func learningCaseSearchText(item LearningCase) string {
	parts := []string{
		"Workflow: " + item.WorkflowType,
		"Case: " + item.CaseType,
		"Title: " + item.Title,
		"Summary: " + item.Summary,
		"Path: " + strings.Join(item.Path, " -> "),
		"Tags: " + strings.Join(item.Tags, ", "),
	}
	if model, ok := item.Components["model"].(string); ok {
		parts = append(parts, "Model: "+model)
	}
	if agent, ok := item.Components["agent"].(string); ok {
		parts = append(parts, "Agent: "+agent)
	}
	if rules, ok := item.Components["rules"].([]any); ok {
		names := []string{}
		for _, rule := range rules {
			if text, ok := rule.(string); ok {
				names = append(names, text)
			}
		}
		parts = append(parts, "Rules: "+strings.Join(names, ", "))
	}
	return strings.Join(parts, "\n")
}

func successfulPath(run TaskRun, evaluation Evaluation) []string {
	if values, ok := run.Evidence["successfulPath"].([]any); ok && len(values) > 0 {
		return anySliceToStrings(values)
	}
	path := []string{"follow " + run.WorkflowType + " workflow", "deliver result"}
	if verificationScore(run.Evidence) >= 80 {
		path = append(path, "verify result")
	}
	if evaluation.FinalStatus != "success" {
		path = append(path, "record failure causes")
	}
	return path
}

func learningComponents(run TaskRun) map[string]any {
	components := map[string]any{}
	for _, key := range []string{"agent", "model", "rules", "skills", "tools"} {
		if value, ok := run.Context[key]; ok {
			components[key] = value
		}
	}
	return components
}

func learningTags(run TaskRun, evaluation Evaluation) []string {
	tags := []string{run.WorkflowType, evaluation.FinalStatus}
	tags = append(tags, anySliceToStrings(evaluation.Analysis["primaryCauses"])...)
	if values, ok := run.Evidence["tags"].([]any); ok {
		tags = append(tags, anySliceToStrings(values)...)
	}
	return dedupeStrings(tags)
}

func evaluationModelPolicy(run TaskRun) map[string]any {
	workflow := strings.ToLower(run.WorkflowType)
	accuracyWeight := 0.62
	costWeight := 0.38
	primary := "deepseek-v4-pro"
	secondary := "deepseek-v4-flash"
	escalationModel := "gpt-5.5"
	highRiskWorkflow := workflow == "code-review" || workflow == "refactor" || workflow == "bugfix" || isUnityEvaluationWorkflow(workflow)
	failedTask := normalizeTaskStatus(run.SubmittedStatus) == "failed"
	if highRiskWorkflow {
		accuracyWeight = 0.72
		costWeight = 0.28
	}
	if failedTask {
		accuracyWeight = 0.8
		costWeight = 0.2
	}
	log.Printf("[evaluation] model policy workflow=%s primary=%s escalation=%s high_risk=%t failed=%t", run.WorkflowType, primary, escalationModel, highRiskWorkflow, failedTask)
	return map[string]any{
		"mode":             "tiered_arbiter_v2",
		"selectionGoal":    "balance_accuracy_and_cost",
		"accuracyWeight":   accuracyWeight,
		"costWeight":       costWeight,
		"primaryModel":     primary,
		"secondaryModel":   secondary,
		"arbiterModel":     primary,
		"escalationModel":  escalationModel,
		"escalateWhen":     []any{"low_confidence", "failed_task", "score_gray_zone", "high_risk_workflow", "high_severity_proposal", "cross_project_recurring_issue"},
		"highRiskWorkflow": highRiskWorkflow,
		"failedTask":       failedTask,
	}
}

func isUnityEvaluationWorkflow(workflow string) bool {
	switch strings.ToLower(strings.TrimSpace(workflow)) {
	case "bug-investigation", "logic-modification", "ui-feature-development", "unity-workflow-evaluation":
		return true
	default:
		return false
	}
}

func evidenceString(evidence map[string]any, key string, fallback string) string {
	if value, ok := evidence[key].(string); ok && strings.TrimSpace(value) != "" {
		return value
	}
	return fallback
}

func evaluationPolicyStrings(eval Evaluation, key string) []string {
	values := anySliceToStrings(eval.ModelPolicy[key])
	if len(values) == 0 {
		return nil
	}
	return values
}

func evaluationPolicyBool(eval Evaluation, key string) bool {
	value, _ := eval.ModelPolicy[key].(bool)
	return value
}

func evaluationPolicyString(eval Evaluation, key string) string {
	value, _ := eval.ModelPolicy[key].(string)
	return strings.TrimSpace(value)
}

func evaluationNeedsEscalation(eval Evaluation) bool {
	if evaluationPolicyBool(eval, "failedTask") || evaluationPolicyBool(eval, "highRiskWorkflow") {
		return true
	}
	if eval.Confidence > 0 && eval.Confidence < 0.65 {
		return true
	}
	if eval.OverallScore > 0 && eval.OverallScore < 75 {
		return true
	}
	return false
}

func evaluationEscalationReasons(eval Evaluation) []string {
	reasons := []string{}
	if evaluationPolicyBool(eval, "failedTask") {
		reasons = append(reasons, "failed_task")
	}
	if evaluationPolicyBool(eval, "highRiskWorkflow") {
		reasons = append(reasons, "high_risk_workflow")
	}
	if eval.Confidence > 0 && eval.Confidence < 0.65 {
		reasons = append(reasons, "low_confidence")
	}
	if eval.OverallScore >= 60 && eval.OverallScore <= 75 {
		reasons = append(reasons, "score_gray_zone")
	}
	if len(reasons) == 0 {
		reasons = evaluationPolicyStrings(eval, "escalateWhen")
	}
	return reasons
}

func anySliceToStrings(value any) []string {
	values, ok := value.([]any)
	if !ok {
		return nil
	}
	output := []string{}
	for _, item := range values {
		if text, ok := item.(string); ok && strings.TrimSpace(text) != "" {
			output = append(output, text)
		}
	}
	return output
}

func dedupeStrings(values []string) []string {
	seen := map[string]bool{}
	output := []string{}
	for _, value := range values {
		trimmed := strings.TrimSpace(value)
		if trimmed == "" || seen[trimmed] {
			continue
		}
		seen[trimmed] = true
		output = append(output, trimmed)
	}
	return output
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return strings.TrimSpace(value)
		}
	}
	return ""
}

func normalizeProposalStatus(status string) string {
	switch strings.ToLower(strings.TrimSpace(status)) {
	case "approved", "rejected", "later", "pending":
		return strings.ToLower(strings.TrimSpace(status))
	default:
		return "pending"
	}
}

func statisticsCutoff(rangeKey string) time.Time {
	now := time.Now().UTC()
	switch strings.ToLower(strings.TrimSpace(rangeKey)) {
	case "24h":
		return now.Add(-24 * time.Hour)
	case "7d":
		return now.Add(-7 * 24 * time.Hour)
	case "30d":
		return now.Add(-30 * 24 * time.Hour)
	default:
		return time.Time{}
	}
}

func timeInRange(value string, cutoff time.Time) bool {
	if cutoff.IsZero() {
		return true
	}
	parsed, err := time.Parse(time.RFC3339Nano, value)
	if err != nil {
		return true
	}
	return parsed.After(cutoff)
}

func statisticsItemForRun(run TaskRun, eval Evaluation) StatisticsTaskItem {
	run = enrichTaskRunMetrics(run)
	status := run.SubmittedStatus
	created := run.CreatedAt
	if eval.ID != "" {
		status = eval.FinalStatus
		created = eval.CreatedAt
	}
	return StatisticsTaskItem{
		RunID:             run.ID,
		EvaluationID:      eval.ID,
		ProjectID:         run.ProjectID,
		TaskTitle:         firstNonEmpty(run.TaskTitle, run.WorkflowType+" task"),
		WorkflowType:      run.WorkflowType,
		Status:            status,
		Score:             eval.OverallScore,
		Confidence:        eval.Confidence,
		Agent:             firstContextName(run.Context["agent"]),
		Model:             firstContextName(run.Context["model"]),
		Rules:             contextNames(run.Context["rules"]),
		ArbiterModel:      evaluationPolicyString(eval, "arbiterModel"),
		EscalationModel:   evaluationPolicyString(eval, "escalationModel"),
		NeedsEscalation:   evaluationNeedsEscalation(eval),
		EscalationReasons: evaluationEscalationReasons(eval),
		HighRiskWorkflow:  evaluationPolicyBool(eval, "highRiskWorkflow"),
		FailedTask:        evaluationPolicyBool(eval, "failedTask"),
		TokenUsage:        workflowTokenUsageFromMap(run.Metrics),
		RouteMetrics:      workflowRouteMetricsFromMap(run.Metrics),
		DurationMS:        run.DurationMS,
		CreatedAt:         created,
	}
}

func (s *EvaluationStore) enrichTaskRunMetricsLocked(run TaskRun) TaskRun {
	metrics := cloneEvaluationMap(run.Metrics)
	if _, ok := metrics["tokenUsage"]; !ok {
		if usage := aggregateTokenUsageLocked(run.ID, s.data.TokenUsages); usage.RequestCount > 0 {
			metrics["tokenUsage"] = usage
		}
	}
	if _, ok := metrics["routeMetrics"]; !ok {
		if routeMetrics := aggregateRouteMetricsLocked(run.ID, s.data.RouteEvents); routeMetrics.RequestCount > 0 {
			metrics["routeMetrics"] = routeMetrics
		}
	}
	run.Metrics = metrics
	return enrichTaskRunMetrics(run)
}

func enrichTaskRunMetrics(run TaskRun) TaskRun {
	if run.Metrics == nil {
		run.Metrics = map[string]any{}
	}
	if run.DurationMS <= 0 {
		if routeMetrics := workflowRouteMetricsFromMap(run.Metrics); routeMetrics.DurationMS > 0 {
			run.DurationMS = routeMetrics.DurationMS
		} else if duration := int64(numberValue(run.Metrics["durationMs"])); duration > 0 {
			run.DurationMS = duration
		} else if duration := durationFromTimestamps(run.StartedAt, run.EndedAt); duration > 0 {
			run.DurationMS = duration
		}
	}
	return run
}

func durationFromTimestamps(startedAt, endedAt string) int64 {
	start, err1 := time.Parse(time.RFC3339Nano, strings.TrimSpace(startedAt))
	end, err2 := time.Parse(time.RFC3339Nano, strings.TrimSpace(endedAt))
	if err1 != nil || err2 != nil || end.Before(start) {
		return 0
	}
	return end.Sub(start).Milliseconds()
}

func workflowTokenUsageFromMap(metrics map[string]any) WorkflowTokenUsage {
	if usage, ok := metrics["tokenUsage"].(WorkflowTokenUsage); ok {
		return usage
	}
	usageMap, ok := tokenUsageMap(TaskRun{Metrics: metrics})
	if !ok {
		return WorkflowTokenUsage{}
	}
	return WorkflowTokenUsage{
		WorkflowRunID:     firstNonEmpty(stringFromAny(usageMap["workflowRunId"]), stringFromAny(usageMap["workflow_run_id"])),
		RequestCount:      int(numberValue(usageMap["requestCount"])),
		InputTokens:       int(numberValue(usageMap["inputTokens"])),
		CachedInputTokens: int(numberValue(usageMap["cachedInputTokens"])),
		OutputTokens:      int(numberValue(usageMap["outputTokens"])),
		TotalTokens:       int(numberValue(usageMap["totalTokens"])),
		CacheHitRate:      numberValue(usageMap["cacheHitRate"]),
		OutputInputRatio:  numberValue(usageMap["outputInputRatio"]),
		UpdatedAt:         stringFromAny(usageMap["updatedAt"]),
	}
}

func workflowRouteMetricsFromMap(metrics map[string]any) WorkflowRouteMetrics {
	if routeMetrics, ok := metrics["routeMetrics"].(WorkflowRouteMetrics); ok {
		return routeMetrics
	}
	metricsMap, ok := routeMetricsMap(TaskRun{Metrics: metrics})
	if !ok {
		return WorkflowRouteMetrics{}
	}
	return WorkflowRouteMetrics{
		WorkflowRunID:            firstNonEmpty(stringFromAny(metricsMap["workflowRunId"]), stringFromAny(metricsMap["workflow_run_id"])),
		RequestCount:             int(numberValue(metricsMap["requestCount"])),
		SuccessCount:             int(numberValue(metricsMap["successCount"])),
		ErrorCount:               int(numberValue(metricsMap["errorCount"])),
		ErrorRate:                numberValue(metricsMap["errorRate"]),
		DurationMS:               int64(numberValue(metricsMap["durationMs"])),
		AverageRequestDurationMS: int64(numberValue(metricsMap["averageRequestDurationMs"])),
		RequestBytes:             int(numberValue(metricsMap["requestBytes"])),
		ToolCount:                int(numberValue(metricsMap["toolCount"])),
		InputItemCount:           int(numberValue(metricsMap["inputItemCount"])),
		ToolCallCount:            int(numberValue(metricsMap["toolCallCount"])),
		UpdatedAt:                stringFromAny(metricsMap["updatedAt"]),
	}
}

func stringFromAny(value any) string {
	if text, ok := value.(string); ok {
		return strings.TrimSpace(text)
	}
	return ""
}

func filterStatisticsItems(items []StatisticsTaskItem, view string) []StatisticsTaskItem {
	filtered := []StatisticsTaskItem{}
	for _, item := range items {
		switch view {
		case "failed":
			if item.Status != "failed" {
				continue
			}
		case "successful":
			if item.Status != "success" {
				continue
			}
		case "pending":
			if item.Status != "success" && item.Status != "partial_success" && item.Status != "failed" && item.EvaluationID != "" {
				continue
			}
		}
		filtered = append(filtered, item)
	}
	sort.Slice(filtered, func(i, j int) bool {
		switch view {
		case "top_scored":
			return filtered[i].Score > filtered[j].Score
		case "low_scored":
			return filtered[i].Score < filtered[j].Score
		default:
			return filtered[i].CreatedAt > filtered[j].CreatedAt
		}
	})
	return filtered
}

func aggregateTokenUsageLocked(workflowRunID string, events []codexrouter.TokenUsageEvent) WorkflowTokenUsage {
	usage := WorkflowTokenUsage{WorkflowRunID: workflowRunID, Roles: map[string]TokenUsageRollup{}}
	for _, event := range events {
		if event.WorkflowRunID != workflowRunID {
			continue
		}
		usage.RequestCount++
		usage.InputTokens += event.InputTokens
		usage.CachedInputTokens += event.CachedInputTokens
		usage.OutputTokens += event.OutputTokens
		total := event.TotalTokens
		if total == 0 {
			total = event.InputTokens + event.OutputTokens
		}
		usage.TotalTokens += total
		if event.CreatedAt > usage.UpdatedAt {
			usage.UpdatedAt = event.CreatedAt
		}
		role := normalizeTokenRole(event.Role)
		rollup := usage.Roles[role]
		if rollup.Models == nil {
			rollup.Models = map[string]int{}
		}
		rollup.RequestCount++
		rollup.InputTokens += event.InputTokens
		rollup.CachedInputTokens += event.CachedInputTokens
		rollup.OutputTokens += event.OutputTokens
		rollup.TotalTokens += total
		model := strings.TrimSpace(event.Model)
		if model == "" {
			model = "unknown"
		}
		rollup.Models[model]++
		rollup.CacheHitRate = tokenPercent(rollup.CachedInputTokens, rollup.InputTokens)
		rollup.OutputInputRatio = tokenPercent(rollup.OutputTokens, rollup.InputTokens)
		usage.Roles[role] = rollup
	}
	usage.CacheHitRate = tokenPercent(usage.CachedInputTokens, usage.InputTokens)
	usage.OutputInputRatio = tokenPercent(usage.OutputTokens, usage.InputTokens)
	return usage
}

func aggregateRouteMetricsLocked(workflowRunID string, events []codexrouter.WorkflowRouteEvent) WorkflowRouteMetrics {
	metrics := WorkflowRouteMetrics{WorkflowRunID: workflowRunID, Roles: map[string]RouteMetricRollup{}}
	for _, event := range events {
		if event.WorkflowRunID != workflowRunID {
			continue
		}
		metrics.RequestCount++
		if event.StatusCode >= 200 && event.StatusCode < 400 {
			metrics.SuccessCount++
		} else {
			metrics.ErrorCount++
		}
		metrics.DurationMS += event.DurationMS
		metrics.RequestBytes += event.RequestBytes
		metrics.ToolCount += event.ToolCount
		metrics.InputItemCount += event.InputItemCount
		metrics.ToolCallCount += event.ToolCallCount
		if event.CreatedAt > metrics.UpdatedAt {
			metrics.UpdatedAt = event.CreatedAt
		}
		role := normalizeTokenRole(event.Role)
		rollup := metrics.Roles[role]
		if rollup.Models == nil {
			rollup.Models = map[string]int{}
		}
		rollup.RequestCount++
		if event.StatusCode >= 200 && event.StatusCode < 400 {
			rollup.SuccessCount++
		} else {
			rollup.ErrorCount++
		}
		rollup.DurationMS += event.DurationMS
		rollup.RequestBytes += event.RequestBytes
		rollup.ToolCount += event.ToolCount
		rollup.InputItemCount += event.InputItemCount
		rollup.ToolCallCount += event.ToolCallCount
		model := strings.TrimSpace(event.Model)
		if model == "" {
			model = "unknown"
		}
		rollup.Models[model]++
		rollup.ErrorRate = tokenPercent(rollup.ErrorCount, rollup.RequestCount)
		if rollup.RequestCount > 0 {
			rollup.AverageRequestDurationMS = rollup.DurationMS / int64(rollup.RequestCount)
		}
		metrics.Roles[role] = rollup
	}
	metrics.ErrorRate = tokenPercent(metrics.ErrorCount, metrics.RequestCount)
	if metrics.RequestCount > 0 {
		metrics.AverageRequestDurationMS = metrics.DurationMS / int64(metrics.RequestCount)
	}
	return metrics
}

func normalizeTokenRole(role string) string {
	role = strings.TrimSpace(strings.ToLower(role))
	role = strings.ReplaceAll(role, " ", "-")
	if role == "" {
		return "unknown"
	}
	return role
}

func tokenPercent(numerator, denominator int) float64 {
	if denominator <= 0 {
		return 0
	}
	return round1(float64(numerator) * 100 / float64(denominator))
}

func stringValue(value any) string {
	if text, ok := value.(string); ok {
		return text
	}
	return ""
}
func firstContextName(value any) string {
	names := contextNames(value)
	if len(names) == 0 {
		return ""
	}
	return names[0]
}

func containsString(values []string, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}
