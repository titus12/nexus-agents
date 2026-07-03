package codexrouter

import (
	"bytes"
	"crypto/md5"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strings"
	"time"
)

const defaultBaseInstructions = "You are Codex, a coding agent. Follow the developer and user instructions in the current session."

const chatgptCodexBaseURL = "https://chatgpt.com/backend-api/codex"

const workflowRunIDHeader = "X-Nexus-Workflow-Run-Id"
const workflowRoleHeader = "X-Nexus-Workflow-Role"
const unknownWorkflowRole = "unknown"

type TokenUsageEvent struct {
	WorkflowRunID     string `json:"workflowRunId"`
	SessionID         string `json:"sessionId,omitempty"`
	Role              string `json:"role,omitempty"`
	Model             string `json:"model"`
	InputTokens       int    `json:"inputTokens"`
	CachedInputTokens int    `json:"cachedInputTokens"`
	OutputTokens      int    `json:"outputTokens"`
	TotalTokens       int    `json:"totalTokens"`
	CreatedAt         string `json:"createdAt"`
}

type WorkflowRouteEvent struct {
	WorkflowRunID  string `json:"workflowRunId"`
	SessionID      string `json:"sessionId,omitempty"`
	Role           string `json:"role,omitempty"`
	Model          string `json:"model"`
	StatusCode     int    `json:"statusCode"`
	DurationMS     int64  `json:"durationMs"`
	RequestBytes   int    `json:"requestBytes"`
	ToolCount      int    `json:"toolCount"`
	InputItemCount int    `json:"inputItemCount"`
	ToolCallCount  int    `json:"toolCallCount"`
	CreatedAt      string `json:"createdAt"`
}

type WorkflowSessionLookup interface {
	LookupWorkflowSession(sessionID string) (WorkflowSessionContext, bool, error)
}

type WorkflowSessionContext struct {
	WorkflowRunID string
	Role          string
}

type TokenUsageRecorder interface {
	RecordTokenUsage(TokenUsageEvent) error
	RecordWorkflowRouteEvent(WorkflowRouteEvent) error
	WorkflowSessionLookup
}

var regexpNonAlnum = regexp.MustCompile(`[^A-Za-z0-9]`)

type Config struct {
	DefaultModel string  `json:"defaultModel"`
	Routes       []Route `json:"routes"`
}

type Route struct {
	ID          string   `json:"id"`
	DisplayName string   `json:"displayName"`
	Description string   `json:"description"`
	API         string   `json:"api"`
	BaseURL     string   `json:"baseUrl"`
	Model       string   `json:"model"`
	Provider    string   `json:"provider"`
	AuthMode    string   `json:"authMode"`
	APIKey      string   `json:"-"`
	APIKeyEnv   string   `json:"apiKeyEnv,omitempty"`
	Priority    int      `json:"priority"`
	DropParams  []string `json:"dropParams,omitempty"`
}

type Service struct {
	config        Config
	client        *http.Client
	history       *responseHistory
	usageRecorder TokenUsageRecorder
}

func NewService(config Config) *Service {
	if len(config.Routes) == 0 {
		config = DefaultConfig()
	}
	if config.DefaultModel == "" {
		config.DefaultModel = config.Routes[0].ID
	}
	return &Service{
		config:  config,
		history: newResponseHistory(),
		// SSE responses can last for minutes; keep timeout limits at the Transport layer.
		client: &http.Client{
			Transport: &http.Transport{
				DialContext: (&net.Dialer{
					Timeout:   30 * time.Second,
					KeepAlive: 30 * time.Second,
				}).DialContext,
				ResponseHeaderTimeout: 60 * time.Second,
				TLSHandshakeTimeout:   10 * time.Second,
				IdleConnTimeout:       90 * time.Second,
				DisableCompression:    true,
			},
		},
	}
}

func (s *Service) SetTokenUsageRecorder(recorder TokenUsageRecorder) {
	s.usageRecorder = recorder
}

func DefaultConfig() Config {
	deepSeekBaseURL := getenvDefault("NEXUS_DEEPSEEK_BASE_URL", "https://lumos.diandian.info/winky/deepseek/v1")
	deepSeekProvider := getenvDefault("NEXUS_DEEPSEEK_PROVIDER", "Winky DeepSeek")
	glmBaseURL := getenvDefault("NEXUS_GLM_BASE_URL", "https://lumos.diandian.info/winky/glm/v1")
	glmProvider := getenvDefault("NEXUS_GLM_PROVIDER", "Winky GLM")
	return Config{
		DefaultModel: "gpt-5.5",
		Routes: []Route{
			{
				ID:          "gpt-5.5",
				DisplayName: "GPT-5.5",
				Description: "GPT subscription model through ChatGPT Codex backend.",
				API:         "responses",
				BaseURL:     "https://chatgpt.com/backend-api/codex",
				Model:       "gpt-5.5",
				Provider:    "Codex subscription",
				AuthMode:    "codex_openai",
				Priority:    0,
			},
			{
				ID:          "gpt-5.4",
				DisplayName: "GPT-5.4",
				Description: "GPT subscription model through ChatGPT Codex backend.",
				API:         "responses",
				BaseURL:     "https://chatgpt.com/backend-api/codex",
				Model:       "gpt-5.4",
				Provider:    "Codex subscription",
				AuthMode:    "codex_openai",
				Priority:    1,
			},
			{
				ID:          "gpt-5.4-mini",
				DisplayName: "GPT-5.4 Mini",
				Description: "GPT subscription model through ChatGPT Codex backend.",
				API:         "responses",
				BaseURL:     "https://chatgpt.com/backend-api/codex",
				Model:       "gpt-5.4-mini",
				Provider:    "Codex subscription",
				AuthMode:    "codex_openai",
				Priority:    2,
			},
			{
				ID:          "deepseek-v4-pro",
				DisplayName: "DeepSeek V4 Pro",
				Description: getenvDefault("NEXUS_DEEPSEEK_PRO_DESCRIPTION", "DeepSeek V4 Pro via Winky API."),
				API:         "chat_completions",
				BaseURL:     deepSeekBaseURL,
				Model:       "deepseek-v4-pro",
				Provider:    deepSeekProvider,
				AuthMode:    "api_key",
				APIKeyEnv:   "DEEPSEEK_API_KEY",
				Priority:    3,
				DropParams:  []string{"response_format", "parallel_tool_calls"},
			},
			{
				ID:          "deepseek-v4-flash",
				DisplayName: "DeepSeek V4 Flash",
				Description: getenvDefault("NEXUS_DEEPSEEK_FLASH_DESCRIPTION", "DeepSeek V4 Flash via Winky API."),
				API:         "chat_completions",
				BaseURL:     deepSeekBaseURL,
				Model:       "deepseek-v4-flash",
				Provider:    deepSeekProvider,
				AuthMode:    "api_key",
				APIKeyEnv:   "DEEPSEEK_API_KEY",
				Priority:    4,
				DropParams:  []string{"response_format", "parallel_tool_calls"},
			},
			{
				ID:          "glm-5.2",
				DisplayName: "GLM-5.2",
				Description: getenvDefault("NEXUS_GLM_52_DESCRIPTION", "GLM-5.2 via Winky API."),
				API:         "chat_completions",
				BaseURL:     glmBaseURL,
				Model:       "glm-5.2",
				Provider:    glmProvider,
				AuthMode:    "api_key",
				APIKeyEnv:   "DEEPSEEK_API_KEY",
				Priority:    5,
				DropParams:  []string{"response_format", "parallel_tool_calls"},
			},
			{
				ID:          "glm-5.1",
				DisplayName: "GLM-5.1",
				Description: getenvDefault("NEXUS_GLM_51_DESCRIPTION", "GLM-5.1 via Winky API."),
				API:         "chat_completions",
				BaseURL:     glmBaseURL,
				Model:       "glm-5.1",
				Provider:    glmProvider,
				AuthMode:    "api_key",
				APIKeyEnv:   "DEEPSEEK_API_KEY",
				Priority:    6,
				DropParams:  []string{"response_format", "parallel_tool_calls"},
			},
		},
	}
}

func (s *Service) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	cleanPath := strings.TrimPrefix(r.URL.Path, "/proxy/codex")
	if cleanPath == "" {
		cleanPath = "/"
	}
	switch {
	case r.Method == http.MethodGet && cleanPath == "/health":
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "models": s.modelIDs()})
	case r.Method == http.MethodGet && (cleanPath == "/model-catalog.json" || cleanPath == "/v1/model-catalog.json"):
		s.serveModelCatalog(w, r)
	case r.Method == http.MethodGet && (cleanPath == "/v1/models" || cleanPath == "/models"):
		writeJSON(w, http.StatusOK, s.ModelsList())
	case r.Method == http.MethodPost && (cleanPath == "/v1/responses" || cleanPath == "/responses"):
		s.handleResponses(w, r)
	default:
		writeJSON(w, http.StatusNotFound, openAIError(fmt.Sprintf("No Codex route for %s %s", r.Method, cleanPath), http.StatusNotFound))
	}
}

func (s *Service) serveModelCatalog(w http.ResponseWriter, r *http.Request) {
	catalog := s.ModelCatalog()
	data, err := json.Marshal(catalog)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, openAIError("failed to marshal catalog", http.StatusInternalServerError))
		return
	}
	etag := fmt.Sprintf(`W/"%x"`, md5.Sum(data))
	if r.Header.Get("If-None-Match") == etag {
		w.WriteHeader(http.StatusNotModified)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("ETag", etag)
	w.Header().Set("Cache-Control", "no-cache")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(data)
}

func (s *Service) ModelCatalog() map[string]any {
	models := make([]map[string]any, 0, len(s.config.Routes))
	for index, route := range s.config.Routes {
		models = append(models, modelCatalogEntry(route, index))
	}
	return map[string]any{"models": models}
}

func (s *Service) ModelsList() map[string]any {
	data := make([]map[string]any, 0, len(s.config.Routes))
	for _, route := range s.config.Routes {
		owner := route.Provider
		if owner == "" {
			owner = "nexus-codex-router"
		}
		data = append(data, map[string]any{
			"id":       route.ID,
			"object":   "model",
			"created":  0,
			"owned_by": owner,
		})
	}
	return map[string]any{"object": "list", "data": data}
}

func (s *Service) handleResponses(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, openAIError("failed to read request body", http.StatusBadRequest))
		return
	}
	var request responseRequest
	if err := json.Unmarshal(body, &request); err != nil {
		writeJSON(w, http.StatusBadRequest, openAIError("invalid json body", http.StatusBadRequest))
		return
	}
	route := s.routeForModel(request.Model)
	reqStats := requestLogStats(request.Raw, len(body))
	sessionID := strings.TrimSpace(r.Header.Get("Session-Id"))
	workflowRunID, workflowRole := s.workflowContext(r)
	log.Printf("[codex] <- session_id=%s workflow_run=%s role=%s model=%s route=%s api=%s body_bytes=%d stream=%v tool_count=%d input_items_count=%d previous_response_id=%s",
		stringOr(sessionID, "-"), stringOr(workflowRunID, "-"), stringOr(workflowRole, "unknown"), request.Model, route.ID, route.API, reqStats.BodyBytes, request.Stream,
		reqStats.ToolCount, reqStats.InputItemsCount, reqStats.PreviousResponseID,
	)
	switch route.API {
	case "responses":
		s.proxyResponses(w, r, request, route, reqStats)
	case "chat_completions":
		s.proxyChatCompletions(w, r, request, route, reqStats)
	default:
		writeJSON(w, http.StatusInternalServerError, openAIError("unsupported route api: "+route.API, http.StatusInternalServerError))
	}
}

func (s *Service) proxyResponses(w http.ResponseWriter, r *http.Request, request responseRequest, route Route, reqStats requestStats) {
	payload := request.Raw
	if len(payload) == 0 {
		payload = map[string]any{}
	}
	payload["model"] = route.Model
	data, err := json.Marshal(payload)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, openAIError("invalid request body", http.StatusBadRequest))
		return
	}

	baseURL := responsesBaseURLForRoute(route)
	upstream, err := http.NewRequestWithContext(r.Context(), http.MethodPost, joinUpstreamURL(baseURL, "/responses"), bytes.NewReader(data))
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, openAIError(err.Error(), http.StatusInternalServerError))
		return
	}
	upstream.Header.Set("Content-Type", "application/json")
	if request.Stream {
		upstream.Header.Set("Accept", "text/event-stream")
	}
	if err := setUpstreamAuth(upstream.Header, route, r.Header.Get("Authorization")); err != nil {
		writeJSON(w, http.StatusUnauthorized, openAIError(err.Error(), http.StatusUnauthorized))
		return
	}
	copyCodexHeaders(upstream.Header, r.Header)

	t0 := time.Now()
	response, err := s.client.Do(upstream)
	if err != nil {
		log.Printf("[codex] -> route=%s upstream_error=%v", route.ID, err)
		if recErr := s.recordRouteEvent(r, request, route, reqStats, http.StatusBadGateway, time.Since(t0).Milliseconds(), 0); recErr != nil {
			log.Printf("[codex] route_event_record_error route=%s err=%v", route.ID, recErr)
		}
		writeJSON(w, http.StatusBadGateway, openAIError(err.Error(), http.StatusBadGateway))
		return
	}
	defer response.Body.Close()
	log.Printf("[codex] -> route=%s upstream_status=%d ttfb=%dms", route.ID, response.StatusCode, time.Since(t0).Milliseconds())

	copyResponseHeaders(w.Header(), response.Header)
	w.WriteHeader(response.StatusCode)
	// 鐎?SSE 濞翠礁绻€妞ゅ鈧劕娼?flush閿涘苯鎯侀崚娆愭殶閹诡喚袧閸樺婀紓鎾冲暱閸栫尨绱濈€广垺鍩涚粩顖滄箙閸?娑撯偓閻╂潙顦╅悶鍡曡厬"閵?
	usage := newResponseUsageTracker(request.Stream)
	flushWriter(w, response.Body, usage)
	usage.finish()
	if err := s.recordTokenUsage(r, request, route, usage); err != nil {
		log.Printf("[codex] token_usage_record_error route=%s err=%v", route.ID, err)
	}
	if err := s.recordRouteEvent(r, request, route, reqStats, response.StatusCode, time.Since(t0).Milliseconds(), usage.toolCallCount()); err != nil {
		log.Printf("[codex] route_event_record_error route=%s err=%v", route.ID, err)
	}
	workflowRunID, workflowRole := s.workflowContext(r)
	log.Printf("[codex] -> session_id=%s workflow_run=%s role=%s route=%s status=%d duration_ms=%d usage_input=%s cached=%s output=%s total_tokens=%d tool_calls=%d",
		stringOr(strings.TrimSpace(r.Header.Get("Session-Id")), "-"), stringOr(workflowRunID, "-"), stringOr(workflowRole, "unknown"), route.ID, response.StatusCode, time.Since(t0).Milliseconds(), usage.inputTokens(), usage.cachedTokens(), usage.outputTokens(), usage.totalTokenCount(), usage.toolCallCount())
}

// flushWriter copies response chunks and flushes after each write for SSE clients.
func flushWriter(w http.ResponseWriter, src io.Reader, usage *responseUsageTracker) {
	flusher, canFlush := w.(http.Flusher)
	buf := make([]byte, 4096)
	for {
		n, readErr := src.Read(buf)
		if n > 0 {
			chunk := buf[:n]
			if usage != nil {
				usage.observe(chunk)
			}
			_, _ = w.Write(chunk)
			if canFlush {
				flusher.Flush()
			}
		}
		if readErr != nil {
			break
		}
	}
}

// responsesBaseURLForRoute redirects public OpenAI API base URLs to the
// ChatGPT Codex backend when the route uses Codex subscription auth.
func responsesBaseURLForRoute(route Route) string {
	if route.AuthMode == "codex_openai" && isPublicOpenAIAPIBaseURL(route.BaseURL) {
		if override := getenv("CODEXBRIDGE_CHATGPT_CODEX_BASE_URL"); override != "" {
			return override
		}
		return chatgptCodexBaseURL
	}
	return route.BaseURL
}

func isPublicOpenAIAPIBaseURL(value string) bool {
	parsed, err := url.Parse(value)
	if err != nil {
		return false
	}
	return strings.EqualFold(parsed.Hostname(), "api.openai.com")
}

func (s *Service) proxyChatCompletions(w http.ResponseWriter, r *http.Request, request responseRequest, route Route, reqStats requestStats) {
	converted := responsesToChatRequest(request, route, s.history)
	data, err := json.Marshal(converted.body)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, openAIError("invalid chat completion body", http.StatusBadRequest))
		return
	}

	upstream, err := http.NewRequestWithContext(r.Context(), http.MethodPost, joinUpstreamURL(route.BaseURL, "/chat/completions"), bytes.NewReader(data))
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, openAIError(err.Error(), http.StatusInternalServerError))
		return
	}
	upstream.Header.Set("Content-Type", "application/json")
	if err := setUpstreamAuth(upstream.Header, route, ""); err != nil {
		writeJSON(w, http.StatusInternalServerError, openAIError(err.Error(), http.StatusInternalServerError))
		return
	}

	t0 := time.Now()
	response, err := s.client.Do(upstream)
	if err != nil {
		if recErr := s.recordRouteEvent(r, request, route, reqStats, http.StatusBadGateway, time.Since(t0).Milliseconds(), 0); recErr != nil {
			log.Printf("[codex] route_event_record_error route=%s err=%v", route.ID, recErr)
		}
		writeJSON(w, http.StatusBadGateway, openAIError(err.Error(), http.StatusBadGateway))
		return
	}
	defer response.Body.Close()
	body, err := io.ReadAll(response.Body)
	if err != nil {
		if recErr := s.recordRouteEvent(r, request, route, reqStats, http.StatusBadGateway, time.Since(t0).Milliseconds(), 0); recErr != nil {
			log.Printf("[codex] route_event_record_error route=%s err=%v", route.ID, recErr)
		}
		writeJSON(w, http.StatusBadGateway, openAIError(err.Error(), http.StatusBadGateway))
		return
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		if recErr := s.recordRouteEvent(r, request, route, reqStats, response.StatusCode, time.Since(t0).Milliseconds(), 0); recErr != nil {
			log.Printf("[codex] route_event_record_error route=%s err=%v", route.ID, recErr)
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(response.StatusCode)
		_, _ = w.Write(body)
		return
	}

	var chat map[string]any
	if err := json.Unmarshal(body, &chat); err != nil {
		if recErr := s.recordRouteEvent(r, request, route, reqStats, http.StatusBadGateway, time.Since(t0).Milliseconds(), 0); recErr != nil {
			log.Printf("[codex] route_event_record_error route=%s err=%v", route.ID, recErr)
		}
		writeJSON(w, http.StatusBadGateway, openAIError("upstream returned non-JSON body: "+truncate(string(body), 500), http.StatusBadGateway))
		return
	}

	requestedModel := request.Model
	if requestedModel == "" {
		requestedModel = route.ID
	}
	resp := chatResponseToResponse(chat, requestedModel, converted.toolContext)
	usage := newResponseUsageTracker(false)
	usage.updateFromObject(resp)
	if err := s.recordTokenUsage(r, request, route, usage); err != nil {
		log.Printf("[codex] token_usage_record_error route=%s err=%v", route.ID, err)
	}
	if err := s.recordRouteEvent(r, request, route, reqStats, response.StatusCode, time.Since(t0).Milliseconds(), countResponseToolCalls(resp)); err != nil {
		log.Printf("[codex] route_event_record_error route=%s err=%v", route.ID, err)
	}

	// Record conversation history for previous_response_id chaining.
	if respID, ok := resp["id"].(string); ok {
		historyMessages := append([]map[string]any{}, converted.messagesForHistory...)
		historyMessages = append(historyMessages, assistantHistoryMessageFromChat(chat))
		s.history.record(respID, historyMessages)
	}

	if converted.wantsStream {
		w.Header().Set("Content-Type", "text/event-stream; charset=utf-8")
		w.Header().Set("Cache-Control", "no-cache")
		w.Header().Set("Connection", "keep-alive")
		w.WriteHeader(http.StatusOK)
		_, _ = io.WriteString(w, responseToSSE(resp))
		return
	}
	writeJSON(w, http.StatusOK, resp)
}

func (s *Service) workflowContext(r *http.Request) (string, string) {
	role := normalizeWorkflowRole(r.Header.Get(workflowRoleHeader))
	externalWorkflowRunID := strings.TrimSpace(r.Header.Get(workflowRunIDHeader))
	if s == nil || s.usageRecorder == nil {
		if externalWorkflowRunID != "" {
			log.Printf("[workflow-session] ignore external workflow_run=%s because usage recorder is unavailable", externalWorkflowRunID)
		}
		return "", role
	}
	sessionID := strings.TrimSpace(r.Header.Get("Session-Id"))
	if sessionID == "" {
		if externalWorkflowRunID != "" {
			log.Printf("[workflow-session] ignore external workflow_run=%s because Session-Id header is missing", externalWorkflowRunID)
		}
		return "", role
	}
	ctx, ok, err := s.usageRecorder.LookupWorkflowSession(sessionID)
	if err != nil {
		log.Printf("[workflow-session] lookup error session_id=%s err=%v", sessionID, err)
		return "", role
	}
	if !ok {
		if externalWorkflowRunID != "" {
			log.Printf("[workflow-session] ignore external workflow_run=%s because session_id=%s is not bound", externalWorkflowRunID, sessionID)
		}
		log.Printf("[workflow-session] lookup miss session_id=%s", sessionID)
		return "", role
	}
	if strings.TrimSpace(ctx.Role) != "" && role == unknownWorkflowRole {
		role = normalizeWorkflowRole(ctx.Role)
	}
	if externalWorkflowRunID != "" && strings.TrimSpace(ctx.WorkflowRunID) != "" && externalWorkflowRunID != strings.TrimSpace(ctx.WorkflowRunID) {
		log.Printf("[workflow-session] ignore external workflow_run=%s and use bound workflow_run=%s for session_id=%s", externalWorkflowRunID, strings.TrimSpace(ctx.WorkflowRunID), sessionID)
	}
	return strings.TrimSpace(ctx.WorkflowRunID), role
}

func (s *Service) recordTokenUsage(r *http.Request, request responseRequest, route Route, usage *responseUsageTracker) error {
	if s == nil || s.usageRecorder == nil || usage == nil {
		return nil
	}
	sessionID := sessionIDFromRequest(r)
	workflowRunID, role := s.workflowContext(r)
	if workflowRunID == "" && sessionID == "" {
		return nil
	}
	model := strings.TrimSpace(request.Model)
	if model == "" {
		model = route.ID
	}
	input := usage.inputTokenCount()
	cached := usage.cachedTokenCount()
	output := usage.outputTokenCount()
	total := usage.totalTokenCount()
	if total == 0 {
		total = input + output
	}
	if input == 0 && cached == 0 && output == 0 && total == 0 {
		return nil
	}
	return s.usageRecorder.RecordTokenUsage(TokenUsageEvent{
		WorkflowRunID:     workflowRunID,
		SessionID:         sessionID,
		Role:              role,
		Model:             model,
		InputTokens:       input,
		CachedInputTokens: cached,
		OutputTokens:      output,
		TotalTokens:       total,
		CreatedAt:         time.Now().UTC().Format(time.RFC3339Nano),
	})
}

func (s *Service) recordRouteEvent(r *http.Request, request responseRequest, route Route, stats requestStats, statusCode int, durationMS int64, observedToolCallCount int) error {
	if s == nil || s.usageRecorder == nil {
		return nil
	}
	sessionID := sessionIDFromRequest(r)
	workflowRunID, role := s.workflowContext(r)
	if workflowRunID == "" && sessionID == "" {
		return nil
	}
	model := strings.TrimSpace(request.Model)
	if model == "" {
		model = route.ID
	}
	return s.usageRecorder.RecordWorkflowRouteEvent(WorkflowRouteEvent{
		WorkflowRunID:  workflowRunID,
		SessionID:      sessionID,
		Role:           role,
		Model:          model,
		StatusCode:     statusCode,
		DurationMS:     durationMS,
		RequestBytes:   stats.BodyBytes,
		ToolCount:      stats.ToolCount,
		InputItemCount: stats.InputItemsCount,
		ToolCallCount:  observedToolCallCount,
		CreatedAt:      time.Now().UTC().Format(time.RFC3339Nano),
	})
}

func sessionIDFromRequest(r *http.Request) string {
	if r == nil {
		return ""
	}
	return strings.TrimSpace(r.Header.Get("Session-Id"))
}

func normalizeWorkflowRole(role string) string {
	role = strings.TrimSpace(strings.ToLower(role))
	role = strings.ReplaceAll(role, " ", "-")
	if role == "" {
		return unknownWorkflowRole
	}
	return role
}

func truncate(s string, max int) string {
	if len(s) <= max {
		return s
	}
	return s[:max]
}

func (s *Service) routeForModel(model string) Route {
	modelID := model
	if modelID == "" {
		modelID = s.config.DefaultModel
	}
	for _, route := range s.config.Routes {
		if route.ID == modelID || route.DisplayName == modelID {
			return route
		}
	}
	for _, route := range s.config.Routes {
		if route.ID == s.config.DefaultModel {
			return route
		}
	}
	return s.config.Routes[0]
}

func (s *Service) modelIDs() []string {
	ids := make([]string, 0, len(s.config.Routes))
	for _, route := range s.config.Routes {
		ids = append(ids, route.ID)
	}
	return ids
}

type responseRequest struct {
	Model        string         `json:"model"`
	Input        any            `json:"input"`
	Messages     any            `json:"messages"`
	Instructions string         `json:"instructions"`
	Stream       bool           `json:"stream"`
	Raw          map[string]any `json:"-"`
}

func (r *responseRequest) UnmarshalJSON(data []byte) error {
	type alias responseRequest
	var decoded alias
	if err := json.Unmarshal(data, &decoded); err != nil {
		return err
	}
	var raw map[string]any
	if err := json.Unmarshal(data, &raw); err != nil {
		return err
	}
	*r = responseRequest(decoded)
	r.Raw = raw
	return nil
}

type requestStats struct {
	BodyBytes          int
	ToolCount          int
	InputItemsCount    int
	PreviousResponseID string
}

func requestLogStats(raw map[string]any, bodyBytes int) requestStats {
	return requestStats{
		BodyBytes:          bodyBytes,
		ToolCount:          arrayLen(raw["tools"]),
		InputItemsCount:    inputItemsCount(raw["input"]),
		PreviousResponseID: stringOr(raw["previous_response_id"], "-"),
	}
}

func arrayLen(value any) int {
	items, ok := value.([]any)
	if !ok {
		return 0
	}
	return len(items)
}

func inputItemsCount(value any) int {
	switch v := value.(type) {
	case []any:
		return len(v)
	case nil:
		return 0
	default:
		return 1
	}
}

type upstreamResponseUsage struct {
	InputTokens       *int
	CachedInputTokens *int
	OutputTokens      *int
	TotalTokens       *int
}

type responseUsageTracker struct {
	stream                bool
	buf                   strings.Builder
	usage                 upstreamResponseUsage
	observedToolCallCount int
}

func newResponseUsageTracker(stream bool) *responseUsageTracker {
	return &responseUsageTracker{stream: stream}
}

func (t *responseUsageTracker) observe(chunk []byte) {
	if t == nil || len(chunk) == 0 {
		return
	}
	t.buf.Write(chunk)
	if t.stream {
		t.consumeSSE(false)
	}
}

func (t *responseUsageTracker) finish() {
	if t == nil {
		return
	}
	if t.stream {
		t.consumeSSE(true)
		return
	}
	var body map[string]any
	if err := json.Unmarshal([]byte(t.buf.String()), &body); err == nil {
		t.updateFromObject(body)
	}
}

func (t *responseUsageTracker) inputTokens() string {
	return optionalInt(t.usage.InputTokens)
}

func (t *responseUsageTracker) cachedTokens() string {
	return optionalInt(t.usage.CachedInputTokens)
}

func (t *responseUsageTracker) outputTokens() string {
	return optionalInt(t.usage.OutputTokens)
}

func (t *responseUsageTracker) inputTokenCount() int {
	return optionalIntValue(t.usage.InputTokens)
}

func (t *responseUsageTracker) cachedTokenCount() int {
	return optionalIntValue(t.usage.CachedInputTokens)
}

func (t *responseUsageTracker) outputTokenCount() int {
	return optionalIntValue(t.usage.OutputTokens)
}

func (t *responseUsageTracker) totalTokenCount() int {
	return optionalIntValue(t.usage.TotalTokens)
}

func (t *responseUsageTracker) toolCallCount() int {
	if t == nil {
		return 0
	}
	return t.observedToolCallCount
}

func optionalInt(value *int) string {
	if value == nil {
		return "-"
	}
	return fmt.Sprint(*value)
}

func optionalIntValue(value *int) int {
	if value == nil {
		return 0
	}
	return *value
}

func (t *responseUsageTracker) consumeSSE(final bool) {
	data := strings.ReplaceAll(t.buf.String(), "\r\n", "\n")
	events := strings.Split(data, "\n\n")
	keep := ""
	if !final && !strings.HasSuffix(data, "\n\n") {
		keep = events[len(events)-1]
		events = events[:len(events)-1]
	}
	for _, event := range events {
		for _, line := range strings.Split(event, "\n") {
			line = strings.TrimSpace(line)
			if !strings.HasPrefix(line, "data:") {
				continue
			}
			payload := strings.TrimSpace(strings.TrimPrefix(line, "data:"))
			if payload == "" || payload == "[DONE]" {
				continue
			}
			var item map[string]any
			if err := json.Unmarshal([]byte(payload), &item); err == nil {
				t.updateFromObject(item)
			}
		}
	}
	t.buf.Reset()
	t.buf.WriteString(keep)
}

func (t *responseUsageTracker) updateFromObject(obj map[string]any) {
	if obj == nil {
		return
	}
	if usage, ok := obj["usage"].(map[string]any); ok {
		t.updateFromUsage(usage)
	}
	if object, _ := obj["object"].(string); object == "response" {
		t.observedToolCallCount = countResponseToolCalls(obj)
	}
	if response, ok := obj["response"].(map[string]any); ok {
		t.updateFromObject(response)
	}
}

func (t *responseUsageTracker) updateFromUsage(usage map[string]any) {
	setIntFromAny(&t.usage.InputTokens, firstDefined(usage["input_tokens"], usage["prompt_tokens"]))
	setIntFromAny(&t.usage.OutputTokens, firstDefined(usage["output_tokens"], usage["completion_tokens"]))
	setIntFromAny(&t.usage.TotalTokens, usage["total_tokens"])

	if inputDetails, ok := firstDefined(usage["input_tokens_details"], usage["prompt_tokens_details"]).(map[string]any); ok {
		setIntFromAny(&t.usage.CachedInputTokens, firstDefined(inputDetails["cached_tokens"], inputDetails["cached_input_tokens"]))
	}
}

func setIntFromAny(target **int, value any) {
	n, ok := intFromAny(value)
	if !ok {
		return
	}
	*target = &n
}

func intFromAny(value any) (int, bool) {
	switch v := value.(type) {
	case float64:
		return int(v), true
	case int:
		return v, true
	case json.Number:
		n, err := v.Int64()
		return int(n), err == nil
	default:
		return 0, false
	}
}

func countResponseToolCalls(response map[string]any) int {
	if response == nil {
		return 0
	}
	count := 0
	if output, ok := response["output"].([]map[string]any); ok {
		for _, item := range output {
			if itemType, _ := item["type"].(string); itemType != "" && itemType != "message" {
				count++
			}
		}
		return count
	}
	if output, ok := response["output"].([]any); ok {
		for _, raw := range output {
			item, ok := raw.(map[string]any)
			if !ok {
				continue
			}
			if itemType, _ := item["type"].(string); itemType != "" && itemType != "message" {
				count++
			}
		}
	}
	return count
}

func modelCatalogEntry(route Route, index int) map[string]any {
	contextWindow := 1000000
	if strings.Contains(strings.ToLower(route.Model), "kimi") {
		contextWindow = 258400
	}
	defaultReasoning := "medium"
	reasoningLevels := []map[string]string{
		{"effort": "low", "description": "Fast responses with lighter reasoning"},
		{"effort": "medium", "description": "Balanced speed and reasoning depth"},
		{"effort": "high", "description": "Greater reasoning depth for complex tasks"},
		{"effort": "xhigh", "description": "Extra high reasoning depth for complex tasks"},
	}
	modelName := strings.ToLower(route.Model)
	if strings.Contains(modelName, "deepseek") {
		defaultReasoning = "high"
		reasoningLevels = []map[string]string{
			{"effort": "none", "description": "Disable reasoning"},
			{"effort": "high", "description": "Use stronger reasoning"},
			{"effort": "max", "description": "Use maximum reasoning"},
		}
	}
	if strings.Contains(modelName, "glm") {
		defaultReasoning = "none"
		reasoningLevels = []map[string]string{
			{"effort": "none", "description": "Use the model default reasoning behavior"},
		}
	}
	description := route.Description
	if description == "" {
		description = route.DisplayName
	}
	priority := route.Priority
	if priority == 0 && index > 0 {
		priority = index
	}
	// Responses routes support image input; chat-completions routes are text-only in v1.
	inputModalities := []string{"text"}
	if route.API == "responses" {
		inputModalities = []string{"text", "image"}
	}

	return map[string]any{
		"slug":                             route.ID,
		"display_name":                     route.DisplayName,
		"description":                      description,
		"shell_type":                       "shell_command",
		"visibility":                       "list",
		"supported_in_api":                 true,
		"priority":                         priority,
		"additional_speed_tiers":           []string{},
		"service_tiers":                    []string{},
		"availability_nux":                 nil,
		"upgrade":                          nil,
		"base_instructions":                defaultBaseInstructions,
		"supports_reasoning_summaries":     false,
		"default_reasoning_summary":        "auto",
		"support_verbosity":                false,
		"default_verbosity":                nil,
		"apply_patch_tool_type":            "freeform",
		"web_search_tool_type":             "text",
		"truncation_policy":                map[string]any{"mode": "tokens", "limit": 10000},
		"supports_parallel_tool_calls":     true,
		"supports_image_detail_original":   false,
		"context_window":                   contextWindow,
		"max_context_window":               contextWindow,
		"effective_context_window_percent": 95,
		"auto_compact_token_limit":         int(float64(contextWindow) * 0.8),
		"experimental_supported_tools":     []string{},
		"input_modalities":                 inputModalities,
		"supports_search_tool":             false,
		"default_reasoning_level":          defaultReasoning,
		"supported_reasoning_levels":       reasoningLevels,
	}
}

func setUpstreamAuth(header http.Header, route Route, incomingAuthorization string) error {
	switch route.AuthMode {
	case "codex_openai":
		if strings.TrimSpace(incomingAuthorization) == "" {
			return fmt.Errorf("route %s requires Codex bearer authentication", route.ID)
		}
		header.Set("Authorization", incomingAuthorization)
	case "", "api_key":
		key := route.APIKey
		if key == "" && route.APIKeyEnv != "" {
			key = getenv(route.APIKeyEnv)
		}
		if key == "" {
			return fmt.Errorf("missing API key for %s", route.ID)
		}
		header.Set("Authorization", "Bearer "+key)
	default:
		return fmt.Errorf("unsupported auth mode %s", route.AuthMode)
	}
	return nil
}

var getenv = os.Getenv

func getenvDefault(name string, fallback string) string {
	if value := strings.TrimSpace(getenv(name)); value != "" {
		return value
	}
	return fallback
}

func writeJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}

func openAIError(message string, status int) map[string]any {
	return map[string]any{
		"error": map[string]any{
			"message": message,
			"type":    "invalid_request_error",
			"code":    status,
		},
	}
}

func joinUpstreamURL(baseURL string, endpoint string) string {
	cleanBase := strings.TrimRight(baseURL, "/")
	if strings.HasSuffix(cleanBase, endpoint) {
		return cleanBase
	}
	return cleanBase + endpoint
}

func copyResponseHeaders(target http.Header, source http.Header) {
	for key, values := range source {
		lower := strings.ToLower(key)
		if lower == "content-length" || lower == "content-encoding" || lower == "connection" {
			continue
		}
		for _, value := range values {
			target.Add(key, value)
		}
	}
}

func copyCodexHeaders(target http.Header, source http.Header) {
	for _, name := range []string{
		"Chatgpt-Account-Id",
		"X-Openai-Fedramp",
		"Session-Id",
		"Thread-Id",
		"X-Client-Request-Id",
		"X-Codex-Beta-Features",
		"X-Codex-Turn-State",
		"X-Codex-Turn-Metadata",
		"X-Codex-Parent-Thread-Id",
		"X-Codex-Window-Id",
		"X-Codex-Installation-Id",
		"X-Oai-Attestation",
		"X-Responsesapi-Include-Timing-Metrics",
		"X-Openai-Internal-Codex-Responses-Lite",
		"Openai-Beta",
		"Openai-Organization",
		"Openai-Project",
	} {
		if value := source.Get(name); value != "" {
			target.Set(name, value)
		}
	}
}

func copyScalar(source map[string]any, target map[string]any, key string) {
	if source == nil {
		return
	}
	if value, ok := source[key]; ok {
		target[key] = value
	}
}

func contentToText(content any) string {
	switch value := content.(type) {
	case nil:
		return ""
	case string:
		return value
	case []any:
		parts := make([]string, 0, len(value))
		for _, item := range value {
			text := contentToText(item)
			if text != "" {
				parts = append(parts, text)
			}
		}
		return strings.Join(parts, "\n")
	case map[string]any:
		if text, ok := value["text"].(string); ok {
			return text
		}
		if text, ok := value["output_text"].(string); ok {
			return text
		}
		data, _ := json.Marshal(value)
		return string(data)
	default:
		return fmt.Sprint(value)
	}
}

func firstDefined(values ...any) any {
	for _, value := range values {
		if value != nil {
			return value
		}
	}
	return nil
}

func normalizeRole(role string) string {
	switch strings.ToLower(role) {
	case "developer":
		return "system"
	case "system", "user", "assistant", "tool":
		return strings.ToLower(role)
	default:
		return ""
	}
}

func routeDropsParam(route Route, param string) bool {
	for _, p := range route.DropParams {
		if p == param {
			return true
		}
	}
	return false
}
