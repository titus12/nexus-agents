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
	config  Config
	client  *http.Client
	history *responseHistory
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
		// 不设全局 Timeout：SSE 流式响应可能持续数分钟，全局超时会提前断流。
		// 仅在 Transport 层限制 TCP 连接建立时间（30s）和响应头等待时间（60s）。
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

func DefaultConfig() Config {
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
				Description: "DeepSeek V4 Pro via Winky API.",
				API:         "chat_completions",
				BaseURL:     "https://lumos.diandian.info/winky/deepseek/v1",
				Model:       "deepseek-v4-pro",
				Provider:    "Winky DeepSeek",
				AuthMode:    "api_key",
				APIKeyEnv:   "DEEPSEEK_API_KEY",
				Priority:    3,
				DropParams:  []string{"response_format", "parallel_tool_calls"},
			},
			{
				ID:          "deepseek-v4-flash",
				DisplayName: "DeepSeek V4 Flash",
				Description: "DeepSeek V4 Flash via Winky API.",
				API:         "chat_completions",
				BaseURL:     "https://lumos.diandian.info/winky/deepseek/v1",
				Model:       "deepseek-v4-flash",
				Provider:    "Winky DeepSeek",
				AuthMode:    "api_key",
				APIKeyEnv:   "DEEPSEEK_API_KEY",
				Priority:    4,
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
	var request responseRequest
	if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
		writeJSON(w, http.StatusBadRequest, openAIError("invalid json body", http.StatusBadRequest))
		return
	}
	route := s.routeForModel(request.Model)
	log.Printf("[codex] <- model=%s route=%s api=%s stream=%v previous_response_id=%s",
		request.Model, route.ID, route.API, request.Stream,
		stringOr(request.Raw["previous_response_id"], "-"),
	)
	switch route.API {
	case "responses":
		s.proxyResponses(w, r, request, route)
	case "chat_completions":
		s.proxyChatCompletions(w, r, request, route)
	default:
		writeJSON(w, http.StatusInternalServerError, openAIError("unsupported route api: "+route.API, http.StatusInternalServerError))
	}
}

func (s *Service) proxyResponses(w http.ResponseWriter, r *http.Request, request responseRequest, route Route) {
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
		writeJSON(w, http.StatusBadGateway, openAIError(err.Error(), http.StatusBadGateway))
		return
	}
	defer response.Body.Close()
	log.Printf("[codex] -> route=%s upstream_status=%d ttfb=%dms", route.ID, response.StatusCode, time.Since(t0).Milliseconds())

	copyResponseHeaders(w.Header(), response.Header)
	w.WriteHeader(response.StatusCode)
	// 对 SSE 流必须逐块 flush，否则数据积压在缓冲区，客户端看到"一直处理中"。
	flushWriter(w, response.Body)
	log.Printf("[codex] -> route=%s done total=%dms", route.ID, time.Since(t0).Milliseconds())
}

// flushWriter 从 src 读取数据并逐块写入 dst，每写一块立即 flush。
// 这对 SSE 流式响应至关重要：Codex Desktop 依赖每个事件的即时到达来更新 UI。
func flushWriter(w http.ResponseWriter, src io.Reader) {
	flusher, canFlush := w.(http.Flusher)
	buf := make([]byte, 4096)
	for {
		n, readErr := src.Read(buf)
		if n > 0 {
			_, _ = w.Write(buf[:n])
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

func (s *Service) proxyChatCompletions(w http.ResponseWriter, r *http.Request, request responseRequest, route Route) {
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

	response, err := s.client.Do(upstream)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, openAIError(err.Error(), http.StatusBadGateway))
		return
	}
	defer response.Body.Close()
	body, err := io.ReadAll(response.Body)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, openAIError(err.Error(), http.StatusBadGateway))
		return
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(response.StatusCode)
		_, _ = w.Write(body)
		return
	}

	var chat map[string]any
	if err := json.Unmarshal(body, &chat); err != nil {
		writeJSON(w, http.StatusBadGateway, openAIError("upstream returned non-JSON body: "+truncate(string(body), 500), http.StatusBadGateway))
		return
	}

	requestedModel := request.Model
	if requestedModel == "" {
		requestedModel = route.ID
	}
	resp := chatResponseToResponse(chat, requestedModel, converted.toolContext)

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
	if strings.Contains(strings.ToLower(route.Model), "deepseek") {
		defaultReasoning = "high"
		reasoningLevels = []map[string]string{
			{"effort": "none", "description": "Disable reasoning"},
			{"effort": "high", "description": "Use stronger reasoning"},
			{"effort": "max", "description": "Use maximum reasoning"},
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
		"input_modalities":                 []string{"text"},
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
