package httpapi

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"io/fs"
	"log"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path"
	"path/filepath"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"time"

	"nexus-agents/internal/catalog"
	"nexus-agents/internal/codexrouter"
	"nexus-agents/internal/knowledgebase"
	"nexus-agents/internal/taskrunsubmit"
	"nexus-agents/internal/workflowrunner"
	webui "nexus-agents/web"
)

type workflowRouterRecorder struct {
	evaluationStore *catalog.EvaluationStore
	sessionStore    *catalog.ActiveWorkflowSessionStore
}

func (r *workflowRouterRecorder) RecordTokenUsage(event codexrouter.TokenUsageEvent) error {
	return r.evaluationStore.RecordTokenUsage(event)
}

func (r *workflowRouterRecorder) RecordWorkflowRouteEvent(event codexrouter.WorkflowRouteEvent) error {
	return r.evaluationStore.RecordWorkflowRouteEvent(event)
}

func (r *workflowRouterRecorder) LookupWorkflowSession(sessionID string) (codexrouter.WorkflowSessionContext, bool, error) {
	if r == nil || r.sessionStore == nil {
		return codexrouter.WorkflowSessionContext{}, false, nil
	}
	session, ok, err := r.sessionStore.Lookup(sessionID)
	if err != nil || !ok {
		return codexrouter.WorkflowSessionContext{}, ok, err
	}
	return codexrouter.WorkflowSessionContext{WorkflowRunID: session.WorkflowRunID, Role: session.CurrentRole}, true, nil
}

func roleString(value any) string {
	text, _ := value.(string)
	return strings.TrimSpace(text)
}

type healthResponse struct {
	Service string `json:"service"`
	Status  string `json:"status"`
}

type localDirectoryEntry struct {
	Name string `json:"name"`
	Path string `json:"path"`
}

type localDirectoriesResponse struct {
	Path      string                `json:"path"`
	Parent    string                `json:"parent,omitempty"`
	Roots     []localDirectoryEntry `json:"roots"`
	Shortcuts []localDirectoryEntry `json:"shortcuts"`
	Entries   []localDirectoryEntry `json:"entries"`
}

type localDirectoryPickerResponse struct {
	Path     string `json:"path"`
	Selected bool   `json:"selected"`
}

type LocalDirectoryPicker interface {
	PickDirectory() (path string, selected bool, err error)
}

type LocalDirectoryPickerFunc func() (path string, selected bool, err error)

func (f LocalDirectoryPickerFunc) PickDirectory() (string, bool, error) {
	return f()
}

type Server struct {
	store                *catalog.Store
	evaluationStore      *catalog.EvaluationStore
	sessionStore         *catalog.ActiveWorkflowSessionStore
	infrastructure       *catalog.InfrastructureService
	codexRouter          *codexrouter.Service
	localDirectoryPicker LocalDirectoryPicker
	workflowRunner       *workflowrunner.Runner
	mux                  *http.ServeMux
	webFS                fs.FS
	webFileServer        http.Handler
}

func NewServer() http.Handler {
	return NewServerWithStore(catalog.NewStore())
}

func NewServerWithStore(store *catalog.Store) http.Handler {
	return newServer(store, catalog.NewInfrastructureService(catalog.InfrastructureServiceOptions{}), codexrouter.NewService(codexrouter.DefaultConfig()))
}

func NewServerWithInfrastructureService(infrastructure *catalog.InfrastructureService) http.Handler {
	return newServer(catalog.NewStore(), infrastructure, codexrouter.NewService(codexrouter.DefaultConfig()))
}

func NewServerWithCodexRouter(router *codexrouter.Service) http.Handler {
	return newServer(catalog.NewStore(), catalog.NewInfrastructureService(catalog.InfrastructureServiceOptions{}), router)
}

func NewServerWithLocalDirectoryPicker(picker LocalDirectoryPicker) http.Handler {
	return newServerWithOptions(catalog.NewStore(), catalog.NewInfrastructureService(catalog.InfrastructureServiceOptions{}), codexrouter.NewService(codexrouter.DefaultConfig()), picker, nil)
}

func newServer(store *catalog.Store, infrastructure *catalog.InfrastructureService, router *codexrouter.Service) http.Handler {
	return newServerWithOptions(store, infrastructure, router, defaultLocalDirectoryPicker{}, nil)
}

func NewServerWithEvaluationStore(evaluationStore *catalog.EvaluationStore) http.Handler {
	return newServerWithOptions(catalog.NewStore(), catalog.NewInfrastructureService(catalog.InfrastructureServiceOptions{}), codexrouter.NewService(codexrouter.DefaultConfig()), defaultLocalDirectoryPicker{}, evaluationStore)
}

func newServerWithOptions(store *catalog.Store, infrastructure *catalog.InfrastructureService, router *codexrouter.Service, picker LocalDirectoryPicker, evaluationStore *catalog.EvaluationStore) http.Handler {
	if infrastructure == nil {
		infrastructure = catalog.NewInfrastructureService(catalog.InfrastructureServiceOptions{})
	}
	if router == nil {
		router = codexrouter.NewService(codexrouter.DefaultConfig())
	}
	if picker == nil {
		picker = defaultLocalDirectoryPicker{}
	}
	if evaluationStore == nil {
		var err error
		evaluationStore, err = catalog.NewDefaultEvaluationStore()
		if err != nil {
			evaluationStore, _ = catalog.NewEvaluationStore(filepath.Join(os.TempDir(), "nexus-agents-evaluation.json"))
		}
	}
	sessionStore, err := catalog.NewDefaultActiveWorkflowSessionStore()
	if err != nil {
		sessionStore, _ = catalog.NewActiveWorkflowSessionStore(filepath.Join(os.TempDir(), "nexus-agents-active-workflow-sessions.json"))
	}
	if router != nil && evaluationStore != nil && sessionStore != nil {
		router.SetTokenUsageRecorder(&workflowRouterRecorder{evaluationStore: evaluationStore, sessionStore: sessionStore})
	}
	webFS := webui.Dist()
	server := &Server{
		store:                store,
		evaluationStore:      evaluationStore,
		infrastructure:       infrastructure,
		sessionStore:         sessionStore,
		codexRouter:          router,
		localDirectoryPicker: picker,
		workflowRunner:       workflowrunner.New(taskrunsubmit.Submitter{Store: evaluationStore, TokenLookup: evaluationStore}),
		mux:                  http.NewServeMux(),
		webFS:                webFS,
		webFileServer:        http.FileServer(http.FS(webFS)),
	}
	server.routes()
	return server
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	s.mux.ServeHTTP(w, r)
}

func (s *Server) routes() {
	s.mux.HandleFunc("/api/bootstrap", s.handleBootstrap)
	s.mux.HandleFunc("/api/task-runs", s.handleTaskRuns)
	s.mux.HandleFunc("/api/task-runs/", s.handleTaskRunPath)
	s.mux.HandleFunc("/api/workflow-runs/start", s.handleWorkflowRunStart)
	s.mux.HandleFunc("/api/workflow-runs/", s.handleWorkflowRunPath)
	s.mux.HandleFunc("/api/evaluations", s.handleEvaluations)
	s.mux.HandleFunc("/api/evaluations/", s.handleEvaluationPath)
	s.mux.HandleFunc("/api/evaluation/", s.handleEvaluationReviewPath)
	s.mux.HandleFunc("/api/statistics/", s.handleStatisticsPath)
	s.mux.HandleFunc("/api/learning-cases", s.handleLearningCases)
	s.mux.HandleFunc("/api/learning-cases/", s.handleLearningCasePath)
	s.mux.HandleFunc("/api/health", s.handleHealth)
	s.mux.HandleFunc("/api/infrastructure/catalog", s.handleInfrastructureCatalog)
	s.mux.HandleFunc("/api/infrastructure", s.handleInfrastructure)
	s.mux.HandleFunc("/api/infrastructure/", s.handleInfrastructurePath)
	s.mux.HandleFunc("/api/local-directories", s.handleLocalDirectories)
	s.mux.HandleFunc("/api/local-directory-picker", s.handleLocalDirectoryPicker)
	s.mux.HandleFunc("/api/debug/codex-model-probe", s.handleCodexModelProbe)
	s.mux.HandleFunc("/api/model-routes/resolve", s.handleModelRouteResolve)
	s.mux.HandleFunc("/api/model-routes", s.handleModelRoutes)
	s.mux.HandleFunc("/api/projects", s.handleProjects)
	s.mux.HandleFunc("/api/projects/", s.handleProjectPath)
	s.mux.HandleFunc("/api/templates/", s.handleTemplates)
	s.mux.HandleFunc("/api/workflows", s.handleWorkflows)
	s.mux.HandleFunc("/api/workflows/", s.handleWorkflowPath)
	s.mux.Handle("/proxy/codex/", s.codexRouter)
	s.mux.Handle("/proxy/codex", s.codexRouter)
	s.mux.HandleFunc("/api", s.handleAPINotFound)
	s.mux.HandleFunc("/api/", s.handleAPINotFound)
	s.mux.HandleFunc("/", s.handleWebUI)
}

func (s *Server) handleAPINotFound(w http.ResponseWriter, r *http.Request) {
	http.NotFound(w, r)
}

func (s *Server) handleWebUI(w http.ResponseWriter, r *http.Request) {
	if !allowMethods(w, r, http.MethodGet, http.MethodHead) {
		return
	}

	filePath := strings.TrimPrefix(path.Clean("/"+r.URL.Path), "/")
	if filePath == "" || filePath == "." {
		filePath = "index.html"
	}
	if webFileExists(s.webFS, filePath) {
		s.webFileServer.ServeHTTP(w, r)
		return
	}

	index, err := fs.ReadFile(s.webFS, "index.html")
	if err != nil {
		http.Error(w, "web ui is not available", http.StatusInternalServerError)
		return
	}
	http.ServeContent(w, r, "index.html", time.Time{}, bytes.NewReader(index))
}

func (s *Server) handleBootstrap(w http.ResponseWriter, r *http.Request) {
	if !allowMethods(w, r, http.MethodGet) {
		return
	}
	writeJSON(w, http.StatusOK, s.store.Bootstrap())
}

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	if !allowMethods(w, r, http.MethodGet) {
		return
	}
	writeJSON(w, http.StatusOK, healthResponse{
		Service: "nexus-agents",
		Status:  "ok",
	})
}

func (s *Server) handleTaskRuns(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		runs, err := s.evaluationStore.TaskRuns()
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		writeJSON(w, http.StatusOK, runs)
	case http.MethodPost:
		var input catalog.TaskRunInput
		if !decodeRequest(w, r, &input) {
			return
		}
		input = s.withServerTaskRunMetrics(r, input)
		run, err := s.evaluationStore.SubmitTaskRun(input)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		if sessionID := sessionIDForTaskRun(r, input); sessionID != "" && s.sessionStore != nil {
			_ = s.sessionStore.Complete(sessionID)
		}
		writeJSON(w, http.StatusCreated, run)
	default:
		methodNotAllowed(w, http.MethodGet, http.MethodPost)
	}
}

func (s *Server) withServerTaskRunMetrics(r *http.Request, input catalog.TaskRunInput) catalog.TaskRunInput {
	metrics := catalogCloneMap(input.Metrics)
	delete(metrics, "tokenUsage")
	delete(metrics, "routeMetrics")
	sessionID := sessionIDForTaskRun(r, input)
	if sessionID == "" {
		log.Printf("[workflow-session] task-run submit has no Session-Id; tokenUsage and routeMetrics remain server-only and absent")
		input.Metrics = metrics
		return input
	}
	if s == nil || s.sessionStore == nil || s.evaluationStore == nil {
		log.Printf("[workflow-session] task-run submit session_id=%s cannot enrich metrics because stores are unavailable", sessionID)
		input.Metrics = metrics
		return input
	}
	session, ok, err := s.sessionStore.Lookup(sessionID)
	if err != nil {
		log.Printf("[workflow-session] task-run submit session lookup error session_id=%s err=%v", sessionID, err)
	} else if !ok || strings.TrimSpace(session.WorkflowRunID) == "" {
		log.Printf("[workflow-session] task-run submit session lookup miss session_id=%s", sessionID)
	} else {
		workflowRunID := strings.TrimSpace(session.WorkflowRunID)
		if usage, ok, err := s.evaluationStore.TokenUsageForWorkflowRun(workflowRunID); err != nil {
			log.Printf("[workflow-session] task-run submit token usage lookup error session_id=%s workflow_run=%s err=%v", sessionID, workflowRunID, err)
		} else if ok {
			metrics["tokenUsage"] = usage
		}
		if routeMetrics, ok, err := s.evaluationStore.RouteMetricsForWorkflowRun(workflowRunID); err != nil {
			log.Printf("[workflow-session] task-run submit route metrics lookup error session_id=%s workflow_run=%s err=%v", sessionID, workflowRunID, err)
		} else if ok {
			metrics["routeMetrics"] = routeMetrics
		}
		if metrics["tokenUsage"] != nil || metrics["routeMetrics"] != nil {
			log.Printf("[workflow-session] task-run submit enriched session_id=%s workflow_run=%s token_usage=%t route_metrics=%t", sessionID, workflowRunID, metrics["tokenUsage"] != nil, metrics["routeMetrics"] != nil)
			input.Metrics = metrics
			return input
		}
		log.Printf("[workflow-session] task-run submit workflow_run=%s had no telemetry; trying session time window session_id=%s", workflowRunID, sessionID)
	}
	if strings.TrimSpace(input.StartedAt) == "" {
		log.Printf("[workflow-session] task-run submit session window attribution skipped session_id=%s reason=missing startedAt", sessionID)
		input.Metrics = metrics
		return input
	}
	if usage, ok, err := s.evaluationStore.TokenUsageForSessionWindow(sessionID, input.StartedAt, input.EndedAt); err != nil {
		log.Printf("[workflow-session] task-run submit session window token usage lookup error session_id=%s started_at=%s ended_at=%s err=%v", sessionID, input.StartedAt, input.EndedAt, err)
	} else if ok {
		metrics["tokenUsage"] = usage
	}
	if routeMetrics, ok, err := s.evaluationStore.RouteMetricsForSessionWindow(sessionID, input.StartedAt, input.EndedAt); err != nil {
		log.Printf("[workflow-session] task-run submit session window route metrics lookup error session_id=%s started_at=%s ended_at=%s err=%v", sessionID, input.StartedAt, input.EndedAt, err)
	} else if ok {
		metrics["routeMetrics"] = routeMetrics
	}
	log.Printf("[workflow-session] task-run submit session window enriched session_id=%s started_at=%s ended_at=%s token_usage=%t route_metrics=%t", sessionID, input.StartedAt, input.EndedAt, metrics["tokenUsage"] != nil, metrics["routeMetrics"] != nil)
	input.Metrics = metrics
	return input
}

func sessionIDForTaskRun(r *http.Request, input catalog.TaskRunInput) string {
	if r != nil {
		if sessionID := strings.TrimSpace(r.Header.Get("Session-Id")); sessionID != "" {
			return sessionID
		}
	}
	if sessionID := strings.TrimSpace(input.SessionID); sessionID != "" {
		return sessionID
	}
	if input.Context != nil {
		if sessionID, ok := input.Context["sessionId"].(string); ok {
			return strings.TrimSpace(sessionID)
		}
	}
	return ""
}

func catalogCloneMap(values map[string]any) map[string]any {
	out := map[string]any{}
	for key, value := range values {
		out[key] = value
	}
	return out
}

func (s *Server) handleTaskRunPath(w http.ResponseWriter, r *http.Request) {
	parts := strings.Split(strings.Trim(strings.TrimPrefix(r.URL.Path, "/api/task-runs/"), "/"), "/")
	if len(parts) != 1 || parts[0] == "" {
		http.NotFound(w, r)
		return
	}
	if !allowMethods(w, r, http.MethodDelete) {
		return
	}
	ok, err := s.evaluationStore.DeleteTaskRun(parts[0])
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	if !ok {
		http.NotFound(w, r)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) handleWorkflowRunStart(w http.ResponseWriter, r *http.Request) {
	if !allowMethods(w, r, http.MethodPost) {
		return
	}
	sessionID := strings.TrimSpace(r.Header.Get("Session-Id"))
	if sessionID == "" {
		log.Printf("[workflow-session] start rejected because Session-Id header is missing")
		http.Error(w, "Session-Id header is required", http.StatusBadRequest)
		return
	}
	var input workflowrunner.StartInput
	if !decodeRequest(w, r, &input) {
		return
	}
	s.attachWorkflowKnowledgeRetrieval(&input)
	run, err := s.workflowRunner.StartRun(input)
	if err != nil {
		log.Printf("[workflow-session] start failed session_id=%s workflow_type=%s project=%s err=%v", sessionID, input.WorkflowType, input.ProjectID, err)
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	if s.sessionStore != nil {
		if err := s.sessionStore.Bind(catalog.ActiveWorkflowSession{
			SessionID:          sessionID,
			WorkflowRunID:      run.ID,
			ProjectID:          run.ProjectID,
			WorkflowTemplateID: run.WorkflowTemplateID,
			WorkflowCopyID:     run.WorkflowCopyID,
			WorkflowType:       run.WorkflowType,
			TaskTitle:          run.TaskTitle,
			CurrentRole:        roleString(input.Context["role"]),
			StartedAt:          run.StartedAt,
			Status:             "active",
		}); err != nil {
			log.Printf("[workflow-session] bind failed session_id=%s workflow_run=%s workflow_type=%s project=%s err=%v", sessionID, run.ID, run.WorkflowType, run.ProjectID, err)
			http.Error(w, "failed to bind workflow session", http.StatusInternalServerError)
			return
		}
		log.Printf("[workflow-session] bind session_id=%s workflow_run=%s workflow_type=%s project=%s", sessionID, run.ID, run.WorkflowType, run.ProjectID)
	}
	writeJSON(w, http.StatusCreated, run)
}

func (s *Server) attachWorkflowKnowledgeRetrieval(input *workflowrunner.StartInput) {
	if input == nil || strings.TrimSpace(input.ProjectID) == "" || strings.TrimSpace(input.TaskTitle) == "" {
		return
	}
	if input.Context == nil {
		input.Context = map[string]any{}
	}
	if _, exists := input.Context["knowledgeRetrieval"]; exists {
		return
	}
	projectRoot, ok := s.projectLocalPath(input.ProjectID)
	if !ok {
		return
	}
	result, err := knowledgebase.Retrieve(projectRoot, input.TaskTitle, knowledgebase.RetrieveOptions{Mode: knowledgebase.RetrieveModeContext, Limit: 8, MaxTokens: 6000})
	if err != nil {
		input.Context["knowledgeRetrieval"] = map[string]any{"query": input.TaskTitle, "error": err.Error()}
		return
	}
	requiredPaths := make([]string, 0, len(result.Required))
	for _, item := range result.Required {
		requiredPaths = append(requiredPaths, item.Path)
	}
	input.Context["knowledgeRetrieval"] = map[string]any{
		"query":                   result.Query,
		"matchedDomain":           result.MatchedDomain,
		"confidence":              result.Confidence,
		"requiredPaths":           requiredPaths,
		"usedTokens":              result.TokenBudget.UsedTokens,
		"maxTokens":               result.TokenBudget.MaxTokens,
		"loadedKnowledgeMarkdown": result.LoadedKnowledgeMarkdown,
	}
}

func (s *Server) handleWorkflowRunPath(w http.ResponseWriter, r *http.Request) {
	parts := strings.Split(strings.Trim(strings.TrimPrefix(r.URL.Path, "/api/workflow-runs/"), "/"), "/")
	if len(parts) != 2 {
		http.NotFound(w, r)
		return
	}
	runID := parts[0]
	action := parts[1]
	if !allowMethods(w, r, http.MethodPost) {
		return
	}
	var input workflowrunner.FinishInput
	if !decodeRequest(w, r, &input) {
		return
	}
	switch action {
	case "complete":
		run, taskRun, err := s.workflowRunner.CompleteRun(runID, input)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"run": run, "taskRun": taskRun})
	case "fail":
		run, taskRun, err := s.workflowRunner.FailRun(runID, input)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"run": run, "taskRun": taskRun})
	default:
		http.NotFound(w, r)
	}
}

func (s *Server) handleEvaluations(w http.ResponseWriter, r *http.Request) {
	if !allowMethods(w, r, http.MethodGet) {
		return
	}
	evaluations, err := s.evaluationStore.Evaluations()
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	writeJSON(w, http.StatusOK, evaluations)
}

func (s *Server) handleEvaluationPath(w http.ResponseWriter, r *http.Request) {
	parts := strings.Split(strings.Trim(strings.TrimPrefix(r.URL.Path, "/api/evaluations/"), "/"), "/")
	if len(parts) == 1 && parts[0] == "summary" {
		if !allowMethods(w, r, http.MethodGet) {
			return
		}
		summary, err := s.evaluationStore.Summary()
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		writeJSON(w, http.StatusOK, summary)
		return
	}
	if len(parts) == 1 && parts[0] == "run-pending" {
		if !allowMethods(w, r, http.MethodPost) {
			return
		}
		count, err := s.evaluationStore.EvaluatePending(20)
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		writeJSON(w, http.StatusOK, map[string]int{"evaluated": count})
		return
	}
	if len(parts) == 2 && parts[1] == "review" {
		if !allowMethods(w, r, http.MethodPost) {
			return
		}
		var input catalog.EvaluationReviewInput
		if !decodeRequest(w, r, &input) {
			return
		}
		review, err := s.evaluationStore.ReviewEvaluation(parts[0], input)
		if err == sql.ErrNoRows {
			http.NotFound(w, r)
			return
		}
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		writeJSON(w, http.StatusCreated, review)
		return
	}
	http.NotFound(w, r)
}

func (s *Server) handleEvaluationReviewPath(w http.ResponseWriter, r *http.Request) {
	parts := strings.Split(strings.Trim(strings.TrimPrefix(r.URL.Path, "/api/evaluation/"), "/"), "/")
	if len(parts) == 1 && parts[0] == "projects" {
		if !allowMethods(w, r, http.MethodGet) {
			return
		}
		projects, err := s.evaluationStore.EvaluationProjects()
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		writeJSON(w, http.StatusOK, projects)
		return
	}
	if len(parts) == 1 && parts[0] == "proposals" {
		if !allowMethods(w, r, http.MethodGet) {
			return
		}
		items, err := s.evaluationStore.EvaluationProposals(r.URL.Query().Get("projectId"), r.URL.Query().Get("status"))
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		writeJSON(w, http.StatusOK, items)
		return
	}
	if len(parts) == 3 && parts[0] == "proposals" && parts[2] == "review" {
		if !allowMethods(w, r, http.MethodPost) {
			return
		}
		var input catalog.EvaluationProposalReviewInput
		if !decodeRequest(w, r, &input) {
			return
		}
		proposal, err := s.evaluationStore.ReviewEvaluationProposal(parts[1], input)
		if err == sql.ErrNoRows {
			http.NotFound(w, r)
			return
		}
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		writeJSON(w, http.StatusOK, proposal)
		return
	}
	http.NotFound(w, r)
}

func (s *Server) handleStatisticsPath(w http.ResponseWriter, r *http.Request) {
	parts := strings.Split(strings.Trim(strings.TrimPrefix(r.URL.Path, "/api/statistics/"), "/"), "/")
	if len(parts) == 1 && parts[0] == "tasks" {
		if !allowMethods(w, r, http.MethodGet) {
			return
		}
		items, err := s.evaluationStore.StatisticsTasks(r.URL.Query().Get("view"), r.URL.Query().Get("range"))
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		writeJSON(w, http.StatusOK, items)
		return
	}
	http.NotFound(w, r)
}

func (s *Server) handleLearningCases(w http.ResponseWriter, r *http.Request) {
	if !allowMethods(w, r, http.MethodGet) {
		return
	}
	cases, err := s.evaluationStore.LearningCases()
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	writeJSON(w, http.StatusOK, cases)
}

func (s *Server) handleLearningCasePath(w http.ResponseWriter, r *http.Request) {
	parts := strings.Split(strings.Trim(strings.TrimPrefix(r.URL.Path, "/api/learning-cases/"), "/"), "/")
	if len(parts) == 1 && parts[0] == "search" {
		if !allowMethods(w, r, http.MethodGet) {
			return
		}
		query := strings.TrimSpace(r.URL.Query().Get("q"))
		limit := 5
		if rawLimit := strings.TrimSpace(r.URL.Query().Get("limit")); rawLimit != "" {
			if parsed, err := strconv.Atoi(rawLimit); err == nil && parsed > 0 {
				limit = parsed
			}
		}
		hits, err := s.evaluationStore.SearchLearningCases(query, limit)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		writeJSON(w, http.StatusOK, hits)
		return
	}
	if len(parts) == 1 && parts[0] == "rebuild-index" {
		if !allowMethods(w, r, http.MethodPost) {
			return
		}
		count, err := s.evaluationStore.RebuildLearningCaseIndex()
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		writeJSON(w, http.StatusOK, map[string]int{"indexed": count})
		return
	}
	http.NotFound(w, r)
}

func (s *Server) handleInfrastructure(w http.ResponseWriter, r *http.Request) {
	if !allowMethods(w, r, http.MethodGet) {
		return
	}
	writeJSON(w, http.StatusOK, s.infrastructure.Items())
}

func (s *Server) handleInfrastructureCatalog(w http.ResponseWriter, r *http.Request) {
	if !allowMethods(w, r, http.MethodGet) {
		return
	}
	writeJSON(w, http.StatusOK, s.infrastructure.Catalog())
}

func (s *Server) handleInfrastructurePath(w http.ResponseWriter, r *http.Request) {
	parts := strings.Split(strings.Trim(strings.TrimPrefix(r.URL.Path, "/api/infrastructure/"), "/"), "/")
	if len(parts) != 2 || parts[0] == "" {
		http.NotFound(w, r)
		return
	}
	if !allowMethods(w, r, http.MethodPost) {
		return
	}

	var (
		item catalog.InfrastructureItem
		ok   bool
		err  error
	)
	switch parts[1] {
	case "check":
		item, ok, err = s.infrastructure.Check(parts[0])
	case "install":
		item, ok, err = s.infrastructure.Install(parts[0])
	case "update":
		item, ok, err = s.infrastructure.Update(parts[0])
	default:
		http.NotFound(w, r)
		return
	}
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	if !ok {
		http.NotFound(w, r)
		return
	}
	writeJSON(w, http.StatusOK, item)
}

func (s *Server) handleLocalDirectories(w http.ResponseWriter, r *http.Request) {
	if !allowMethods(w, r, http.MethodGet) {
		return
	}

	dirPath := strings.TrimSpace(r.URL.Query().Get("path"))
	if dirPath == "" {
		roots := directoryChooserRoots()
		writeJSON(w, http.StatusOK, localDirectoriesResponse{
			Path:      "",
			Parent:    "",
			Roots:     roots,
			Shortcuts: directoryChooserShortcuts(),
			Entries:   roots,
		})
		return
	}
	absPath, err := filepath.Abs(dirPath)
	if err != nil {
		http.Error(w, "invalid directory path", http.StatusBadRequest)
		return
	}
	stat, err := os.Stat(absPath)
	if err != nil {
		http.Error(w, "directory path is not accessible", http.StatusBadRequest)
		return
	}
	if !stat.IsDir() {
		http.Error(w, "path is not a directory", http.StatusBadRequest)
		return
	}

	entries, err := os.ReadDir(absPath)
	if err != nil {
		http.Error(w, "directory path is not readable", http.StatusBadRequest)
		return
	}

	directories := make([]localDirectoryEntry, 0, len(entries))
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		entryPath := filepath.Join(absPath, entry.Name())
		if stat, err := os.Stat(entryPath); err != nil || !stat.IsDir() {
			continue
		}
		directories = append(directories, localDirectoryEntry{
			Name: entry.Name(),
			Path: entryPath,
		})
	}
	sort.Slice(directories, func(left, right int) bool {
		return strings.ToLower(directories[left].Name) < strings.ToLower(directories[right].Name)
	})

	parent := filepath.Dir(absPath)
	if parent == absPath {
		parent = ""
	}
	writeJSON(w, http.StatusOK, localDirectoriesResponse{
		Path:      absPath,
		Parent:    parent,
		Roots:     directoryChooserRoots(),
		Shortcuts: directoryChooserShortcuts(),
		Entries:   directories,
	})
}

func directoryChooserRoots() []localDirectoryEntry {
	roots := make([]localDirectoryEntry, 0, 26)
	for drive := 'A'; drive <= 'Z'; drive++ {
		path := fmt.Sprintf("%c:\\", drive)
		if stat, err := os.Stat(path); err == nil && stat.IsDir() {
			roots = append(roots, localDirectoryEntry{
				Name: fmt.Sprintf("%c:", drive),
				Path: path,
			})
		}
	}
	if len(roots) == 0 {
		roots = append(roots, localDirectoryEntry{Name: "/", Path: string(os.PathSeparator)})
	}
	return roots
}

func directoryChooserShortcuts() []localDirectoryEntry {
	seen := map[string]bool{}
	shortcuts := []localDirectoryEntry{}
	add := func(name string, path string) {
		if strings.TrimSpace(path) == "" {
			return
		}
		absPath, err := filepath.Abs(path)
		if err != nil {
			return
		}
		stat, err := os.Stat(absPath)
		if err != nil || !stat.IsDir() {
			return
		}
		key := strings.ToLower(absPath)
		if seen[key] {
			return
		}
		seen[key] = true
		shortcuts = append(shortcuts, localDirectoryEntry{Name: name, Path: absPath})
	}

	if home, err := os.UserHomeDir(); err == nil {
		add("Home", home)
		add("Desktop", filepath.Join(home, "Desktop"))
		add("Documents", filepath.Join(home, "Documents"))
		add("Downloads", filepath.Join(home, "Downloads"))
	}
	if wd, err := os.Getwd(); err == nil {
		add("Current workspace", wd)
	}
	return shortcuts
}

func (s *Server) handleLocalDirectoryPicker(w http.ResponseWriter, r *http.Request) {
	if !allowMethods(w, r, http.MethodPost) {
		return
	}
	selectedPath, selected, err := s.localDirectoryPicker.PickDirectory()
	if err != nil {
		http.Error(w, err.Error(), http.StatusServiceUnavailable)
		return
	}
	if !selected {
		w.WriteHeader(http.StatusNoContent)
		return
	}
	absPath, err := filepath.Abs(selectedPath)
	if err != nil {
		http.Error(w, "invalid selected directory path", http.StatusBadRequest)
		return
	}
	stat, err := os.Stat(absPath)
	if err != nil || !stat.IsDir() {
		http.Error(w, "selected path is not an accessible directory", http.StatusBadRequest)
		return
	}
	writeJSON(w, http.StatusOK, localDirectoryPickerResponse{Path: absPath, Selected: true})
}

type defaultLocalDirectoryPicker struct{}

const defaultLocalDirectoryPickerTimeout = 3 * time.Second

func (defaultLocalDirectoryPicker) PickDirectory() (string, bool, error) {
	if runtime.GOOS != "windows" {
		return "", false, fmt.Errorf("native folder picker is only available on Windows")
	}
	script := `
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = '选择项目目录'
$dialog.ShowNewFolderButton = $false
$result = $dialog.ShowDialog()
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
  [Console]::Out.Write($dialog.SelectedPath)
  exit 0
}
exit 2
`
	ctx, cancel := context.WithTimeout(context.Background(), defaultLocalDirectoryPickerTimeout)
	defer cancel()
	command := exec.CommandContext(ctx, "powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-Command", script)
	output, err := command.Output()
	if err != nil {
		if ctx.Err() == context.DeadlineExceeded {
			return "", false, fmt.Errorf("native folder picker timed out")
		}
		if exitErr, ok := err.(*exec.ExitError); ok && exitErr.ExitCode() == 2 {
			return "", false, nil
		}
		return "", false, fmt.Errorf("open native folder picker: %w", err)
	}
	path := strings.TrimSpace(string(output))
	if path == "" {
		return "", false, nil
	}
	return path, true, nil
}

func (s *Server) handleProjects(w http.ResponseWriter, r *http.Request) {
	if !allowMethods(w, r, http.MethodGet) {
		return
	}
	writeJSON(w, http.StatusOK, s.store.Projects())
}

func (s *Server) handleProjectPath(w http.ResponseWriter, r *http.Request) {
	path := strings.TrimPrefix(r.URL.Path, "/api/projects/")
	parts := strings.Split(strings.Trim(path, "/"), "/")
	if len(parts) == 0 || parts[0] == "" {
		http.NotFound(w, r)
		return
	}

	if len(parts) == 1 && parts[0] == "import" {
		s.handleProjectImport(w, r)
		return
	}

	projectID := parts[0]
	if len(parts) == 1 {
		switch r.Method {
		case http.MethodGet:
			project, ok := s.store.ProjectByID(projectID)
			if !ok {
				http.NotFound(w, r)
				return
			}
			writeJSON(w, http.StatusOK, project)
		case http.MethodDelete:
			ok, err := s.store.DeleteProject(projectID)
			if err != nil {
				http.Error(w, err.Error(), http.StatusInternalServerError)
				return
			}
			if !ok {
				http.NotFound(w, r)
				return
			}
			w.WriteHeader(http.StatusNoContent)
		default:
			methodNotAllowed(w, http.MethodGet, http.MethodDelete)
		}
		return
	}

	if len(parts) == 2 && parts[1] == "sync-preview" {
		if !allowMethods(w, r, http.MethodGet) {
			return
		}
		preview, ok := s.store.SyncPreview(projectID)
		if !ok {
			http.NotFound(w, r)
			return
		}
		writeJSON(w, http.StatusOK, preview)
		return
	}

	if len(parts) == 2 && parts[1] == "rescan" {
		if !allowMethods(w, r, http.MethodPost) {
			return
		}
		project, copies, ok, err := s.store.RescanProject(projectID)
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		if !ok {
			http.NotFound(w, r)
			return
		}
		writeJSON(w, http.StatusOK, catalog.ProjectRescanResult{Project: project, Copies: copies})
		return
	}

	if len(parts) >= 2 && parts[1] == "knowledge" {
		s.handleProjectKnowledgePath(w, r, projectID, parts[2:])
		return
	}

	if parts[1] != "config" {
		http.NotFound(w, r)
		return
	}

	if len(parts) == 2 {
		if !allowMethods(w, r, http.MethodGet) {
			return
		}
		copies, ok := s.store.ProjectConfigSet(projectID, r.URL.Query().Get("kind"))
		if !ok {
			http.NotFound(w, r)
			return
		}
		writeJSON(w, http.StatusOK, copies)
		return
	}

	if len(parts) == 3 && parts[2] == "workflows" {
		if !allowMethods(w, r, http.MethodPost) {
			return
		}
		var input catalog.WorkflowInput
		if !decodeRequest(w, r, &input) {
			return
		}
		copy, graph, ok, err := s.store.CreateProjectWorkflow(projectID, input)
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		if !ok {
			http.NotFound(w, r)
			return
		}
		writeJSON(w, http.StatusCreated, catalog.ProjectWorkflowCreateResult{Copy: copy, Graph: graph})
		return
	}

	if len(parts) == 3 && parts[2] == "from-template" {
		if !allowMethods(w, r, http.MethodPost) {
			return
		}
		var input catalog.ProjectTemplateInput
		if !decodeRequest(w, r, &input) {
			return
		}
		copy, ok, err := s.store.AddProjectCopyFromTemplate(projectID, input.Kind, input.TemplateID)
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		if !ok {
			http.NotFound(w, r)
			return
		}
		writeJSON(w, http.StatusCreated, copy)
		return
	}

	if len(parts) == 3 {
		if !allowMethods(w, r, http.MethodDelete) {
			return
		}
		ok, err := s.store.DeleteProjectWorkflow(projectID, parts[2])
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		if !ok {
			http.NotFound(w, r)
			return
		}
		w.WriteHeader(http.StatusNoContent)
		return
	}

	if len(parts) == 4 && parts[3] == "graph" {
		copyID := parts[2]
		switch r.Method {
		case http.MethodGet:
			graph, ok := s.store.ProjectWorkflowByCopyID(projectID, copyID)
			if !ok {
				http.NotFound(w, r)
				return
			}
			writeJSON(w, http.StatusOK, graph)
		case http.MethodPut:
			var input catalog.WorkflowGraph
			if !decodeRequest(w, r, &input) {
				return
			}
			graph, ok, err := s.store.UpdateProjectWorkflowGraph(projectID, copyID, input)
			if err != nil {
				http.Error(w, err.Error(), http.StatusInternalServerError)
				return
			}
			if !ok {
				http.NotFound(w, r)
				return
			}
			writeJSON(w, http.StatusOK, graph)
		default:
			methodNotAllowed(w, http.MethodGet, http.MethodPut)
		}
		return
	}

	if len(parts) == 4 {
		if !allowMethods(w, r, http.MethodPost) {
			return
		}
		copyID := parts[2]
		action := parts[3]
		switch action {
		case "sync":
			copy, ok, err := s.store.SyncProjectCopy(projectID, copyID)
			if err != nil {
				http.Error(w, err.Error(), http.StatusInternalServerError)
				return
			}
			if !ok {
				http.NotFound(w, r)
				return
			}
			writeJSON(w, http.StatusOK, copy)
		case "detach":
			copy, ok := s.store.DetachProjectCopy(projectID, copyID)
			if !ok {
				http.NotFound(w, r)
				return
			}
			writeJSON(w, http.StatusOK, copy)
		default:
			http.NotFound(w, r)
		}
		return
	}

	http.NotFound(w, r)
}

func (s *Server) handleProjectKnowledgePath(w http.ResponseWriter, r *http.Request, projectID string, parts []string) {
	projectRoot, ok := s.projectLocalPath(projectID)
	if !ok {
		http.NotFound(w, r)
		return
	}
	if len(parts) == 0 {
		if !allowMethods(w, r, http.MethodGet) {
			return
		}
		bundle, err := knowledgebase.ScanBundle(projectRoot)
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		writeJSON(w, http.StatusOK, bundle)
		return
	}
	switch parts[0] {
	case "retrieve":
		if !allowMethods(w, r, http.MethodGet) {
			return
		}
		limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
		maxTokens, _ := strconv.Atoi(r.URL.Query().Get("maxTokens"))
		result, err := knowledgebase.Retrieve(projectRoot, r.URL.Query().Get("q"), knowledgebase.RetrieveOptions{
			Mode:      r.URL.Query().Get("mode"),
			Limit:     limit,
			MaxTokens: maxTokens,
		})
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		writeJSON(w, http.StatusOK, result)
	case "validate":
		if !allowMethods(w, r, http.MethodGet) {
			return
		}
		report, err := knowledgebase.Validate(projectRoot)
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		writeJSON(w, http.StatusOK, report)
	case "route":
		if !allowMethods(w, r, http.MethodGet) {
			return
		}
		preview, err := knowledgebase.PreviewRoute(projectRoot, r.URL.Query().Get("task"))
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		writeJSON(w, http.StatusOK, preview)
	case "render":
		if !allowMethods(w, r, http.MethodGet) {
			return
		}
		if docPath := r.URL.Query().Get("path"); docPath != "" {
			doc, err := knowledgebase.RenderDocument(projectRoot, docPath)
			if err != nil {
				http.Error(w, err.Error(), http.StatusNotFound)
				return
			}
			writeJSON(w, http.StatusOK, doc)
			return
		}
		tree, err := knowledgebase.BuildRenderTree(projectRoot)
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		writeJSON(w, http.StatusOK, tree)
	case "maintenance":
		if !allowMethods(w, r, http.MethodGet) {
			return
		}
		report, err := knowledgebase.Maintenance(projectRoot)
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		writeJSON(w, http.StatusOK, report)
	case "export":
		s.handleProjectKnowledgeExport(w, r, projectID, projectRoot, parts[1:])
	default:
		http.NotFound(w, r)
	}
}

func (s *Server) handleProjectKnowledgeExport(w http.ResponseWriter, r *http.Request, projectID string, projectRoot string, parts []string) {
	exportRoot := projectKnowledgeExportRoot(projectID)
	if len(parts) == 0 {
		if !allowMethods(w, r, http.MethodGet) {
			return
		}
		data, err := knowledgebase.ReadFreshExport(projectID, projectRoot, exportRoot)
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		writeJSON(w, http.StatusOK, data)
		return
	}
	if len(parts) == 1 && parts[0] == "refresh" {
		if !allowMethods(w, r, http.MethodPost) {
			return
		}
		manifest, err := knowledgebase.ExportProjectKnowledge(projectID, projectRoot, exportRoot)
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		writeJSON(w, http.StatusOK, manifest)
		return
	}
	if len(parts) == 1 && parts[0] == "doc" {
		if !allowMethods(w, r, http.MethodGet) {
			return
		}
		doc, err := knowledgebase.ReadExportDocument(exportRoot, r.URL.Query().Get("path"))
		if err != nil {
			http.Error(w, err.Error(), http.StatusNotFound)
			return
		}
		writeJSON(w, http.StatusOK, doc)
		return
	}
	http.NotFound(w, r)
}

func (s *Server) projectLocalPath(projectID string) (string, bool) {
	project, ok := s.store.ProjectByID(projectID)
	if !ok {
		return "", false
	}
	localPath := strings.TrimSpace(project.LocalPath)
	if localPath == "" {
		localPath = strings.TrimSpace(project.Path)
	}
	return localPath, localPath != ""
}

func projectKnowledgeExportRoot(projectID string) string {
	base := strings.TrimSpace(os.Getenv("NEXUS_KNOWLEDGE_EXPORT_DIR"))
	if base == "" {
		base = filepath.Join(".nexus-agents", "knowledge-exports")
	}
	return filepath.Join(base, slugifyForPath(projectID))
}

func slugifyForPath(value string) string {
	value = strings.TrimSpace(strings.ToLower(value))
	if value == "" {
		return "project"
	}
	var builder strings.Builder
	for _, r := range value {
		if (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') || r == '-' || r == '_' {
			builder.WriteRune(r)
		} else {
			builder.WriteRune('_')
		}
	}
	return builder.String()
}

func (s *Server) handleProjectImport(w http.ResponseWriter, r *http.Request) {
	if !allowMethods(w, r, http.MethodPost) {
		return
	}
	var input catalog.ProjectInput
	if !decodeRequest(w, r, &input) {
		return
	}
	project, err := s.store.ImportProject(input)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	if project.KnowledgeSummary.Exists {
		_, _ = knowledgebase.ExportProjectKnowledge(project.ID, project.LocalPath, projectKnowledgeExportRoot(project.ID))
	}
	writeJSON(w, http.StatusCreated, project)
}

func (s *Server) handleTemplates(w http.ResponseWriter, r *http.Request) {
	path := strings.Trim(strings.TrimPrefix(r.URL.Path, "/api/templates/"), "/")
	parts := strings.Split(path, "/")
	if len(parts) == 0 || parts[0] == "" {
		http.NotFound(w, r)
		return
	}
	kind := parts[0]

	if len(parts) == 1 {
		switch r.Method {
		case http.MethodGet:
			items, ok := s.store.Templates(kind)
			if !ok {
				http.NotFound(w, r)
				return
			}
			writeJSON(w, http.StatusOK, items)
		case http.MethodPost:
			var input catalog.TemplateInput
			if !decodeRequest(w, r, &input) {
				return
			}
			item, ok := s.store.CreateTemplate(kind, input)
			if !ok {
				http.NotFound(w, r)
				return
			}
			writeJSON(w, http.StatusCreated, item)
		default:
			methodNotAllowed(w, http.MethodGet, http.MethodPost)
		}
		return
	}

	if len(parts) == 2 {
		templateID := parts[1]
		switch r.Method {
		case http.MethodPut:
			var input catalog.TemplateInput
			if !decodeRequest(w, r, &input) {
				return
			}
			item, ok := s.store.UpdateTemplate(kind, templateID, input)
			if !ok {
				http.NotFound(w, r)
				return
			}
			writeJSON(w, http.StatusOK, item)
		case http.MethodDelete:
			if !s.store.DeleteTemplate(kind, templateID) {
				http.NotFound(w, r)
				return
			}
			w.WriteHeader(http.StatusNoContent)
		default:
			methodNotAllowed(w, http.MethodPut, http.MethodDelete)
		}
		return
	}

	http.NotFound(w, r)
}

func (s *Server) handleWorkflows(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		writeJSON(w, http.StatusOK, s.store.Workflows())
	case http.MethodPost:
		var input catalog.WorkflowInput
		if !decodeRequest(w, r, &input) {
			return
		}
		writeJSON(w, http.StatusCreated, s.store.CreateWorkflow(input))
	default:
		methodNotAllowed(w, http.MethodGet, http.MethodPost)
	}
}

func (s *Server) handleWorkflowPath(w http.ResponseWriter, r *http.Request) {
	path := strings.Trim(strings.TrimPrefix(r.URL.Path, "/api/workflows/"), "/")
	parts := strings.Split(path, "/")
	if len(parts) == 0 || parts[0] == "" {
		http.NotFound(w, r)
		return
	}
	workflowID := parts[0]

	if len(parts) == 2 && parts[1] == "duplicate" {
		if !allowMethods(w, r, http.MethodPost) {
			return
		}
		workflow, ok := s.store.DuplicateWorkflow(workflowID)
		if !ok {
			http.NotFound(w, r)
			return
		}
		writeJSON(w, http.StatusCreated, workflow)
		return
	}

	if len(parts) == 2 && parts[1] == "graph" {
		switch r.Method {
		case http.MethodPut:
			var input catalog.WorkflowGraph
			if !decodeRequest(w, r, &input) {
				return
			}
			graph, ok := s.store.UpdateWorkflowGraph(workflowID, input)
			if !ok {
				http.NotFound(w, r)
				return
			}
			writeJSON(w, http.StatusOK, graph)
		default:
			methodNotAllowed(w, http.MethodPut)
		}
		return
	}

	if len(parts) != 1 {
		http.NotFound(w, r)
		return
	}

	switch r.Method {
	case http.MethodGet:
		graph, ok := s.store.WorkflowByID(workflowID)
		if !ok {
			http.NotFound(w, r)
			return
		}
		writeJSON(w, http.StatusOK, graph)
	case http.MethodPut:
		var input catalog.WorkflowInput
		if !decodeRequest(w, r, &input) {
			return
		}
		workflow, ok := s.store.UpdateWorkflow(workflowID, input)
		if !ok {
			http.NotFound(w, r)
			return
		}
		writeJSON(w, http.StatusOK, workflow)
	case http.MethodDelete:
		if !s.store.DeleteWorkflow(workflowID) {
			http.NotFound(w, r)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	default:
		methodNotAllowed(w, http.MethodGet, http.MethodPut, http.MethodDelete)
	}
}

func (s *Server) handleModelRoutes(w http.ResponseWriter, r *http.Request) {
	if !allowMethods(w, r, http.MethodGet) {
		return
	}
	writeJSON(w, http.StatusOK, catalog.ModelRoutes())
}

func (s *Server) handleModelRouteResolve(w http.ResponseWriter, r *http.Request) {
	if !allowMethods(w, r, http.MethodGet) {
		return
	}

	resolution, ok := catalog.ResolveModelRoute(r.URL.Query().Get("client"), r.URL.Query().Get("model"))
	if !ok {
		http.NotFound(w, r)
		return
	}
	writeJSON(w, http.StatusOK, resolution)
}

func (s *Server) handleCodexModelProbe(w http.ResponseWriter, r *http.Request) {
	if !allowMethods(w, r, http.MethodPost) {
		return
	}
	if !isLocalRequest(r) {
		http.Error(w, "codex model probe is only available from localhost", http.StatusForbidden)
		return
	}
	var input codexrouter.ModelProbeRequest
	if !decodeRequest(w, r, &input) {
		return
	}
	if len(input.Models) == 0 {
		http.Error(w, "models is required", http.StatusBadRequest)
		return
	}
	results := s.codexRouter.ProbeModels(r.Context(), input, strings.TrimSpace(r.Header.Get("Authorization")), r.Header)
	writeJSON(w, http.StatusOK, map[string]any{
		"results": results,
	})
}

func isLocalRequest(r *http.Request) bool {
	host := r.RemoteAddr
	if forwarded := strings.TrimSpace(r.Header.Get("X-Forwarded-For")); forwarded != "" {
		host = strings.TrimSpace(strings.Split(forwarded, ",")[0])
	}
	if strings.Contains(host, ":") {
		if parsedHost, _, err := net.SplitHostPort(host); err == nil {
			host = parsedHost
		}
	}
	parsed := net.ParseIP(strings.Trim(host, "[]"))
	return parsed != nil && parsed.IsLoopback()
}

func allowMethods(w http.ResponseWriter, r *http.Request, methods ...string) bool {
	for _, method := range methods {
		if r.Method == method {
			return true
		}
	}
	methodNotAllowed(w, methods...)
	return false
}

func methodNotAllowed(w http.ResponseWriter, methods ...string) {
	w.Header().Set("Allow", strings.Join(methods, ", "))
	http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
}

func decodeRequest(w http.ResponseWriter, r *http.Request, target any) bool {
	if r.Body == nil {
		return true
	}
	defer r.Body.Close()
	if err := json.NewDecoder(r.Body).Decode(target); err != nil {
		http.Error(w, "invalid json body", http.StatusBadRequest)
		return false
	}
	return true
}

func writeJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}

func webFileExists(webFS fs.FS, filePath string) bool {
	stat, err := fs.Stat(webFS, filePath)
	return err == nil && !stat.IsDir()
}
