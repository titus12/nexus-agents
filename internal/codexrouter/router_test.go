package codexrouter

import (
	"net/http/httptest"
	"strings"
	"testing"
)

// TestResponseToSSEFullSequence verifies the SSE stream contains the full
// Codex Responses event sequence (the fix for "发消息没通").
func TestResponseToSSEFullSequence(t *testing.T) {
	resp := map[string]any{
		"id":     "resp_test123",
		"object": "response",
		"status": "completed",
		"model":  "gpt-5.4-mini",
		"output": []map[string]any{{
			"id":     "msg_test",
			"type":   "message",
			"role":   "assistant",
			"status": "completed",
			"content": []map[string]any{{
				"type": "output_text",
				"text": "Hello there friend",
			}},
		}},
		"output_text": "Hello there friend",
	}

	sse := responseToSSE(resp)

	required := []string{
		"event: response.created",
		"event: response.in_progress",
		"event: response.output_item.added",
		"event: response.content_part.added",
		"event: response.output_text.delta",
		"event: response.output_text.done",
		"event: response.content_part.done",
		"event: response.output_item.done",
		"event: response.completed",
		"data: [DONE]",
	}
	for _, want := range required {
		if !strings.Contains(sse, want) {
			t.Errorf("SSE stream missing %q", want)
		}
	}
	if !strings.Contains(sse, "Hello there friend") {
		t.Errorf("SSE stream missing the actual text delta")
	}
}

// TestResponsesToChatRequestWithTools verifies tools, instructions and input
// are converted into a Chat Completions body correctly.
func TestResponsesToChatRequestWithTools(t *testing.T) {
	history := newResponseHistory()
	raw := map[string]any{
		"model":        "gpt-5.4-mini",
		"instructions": "You are a coding agent.",
		"input": []any{
			map[string]any{"type": "message", "role": "user", "content": "read main.go"},
		},
		"tools": []any{
			map[string]any{
				"type":        "function",
				"name":        "read_file",
				"description": "read a file",
				"parameters":  map[string]any{"type": "object"},
			},
		},
		"stream": true,
	}
	req := responseRequest{
		Model:        "gpt-5.4-mini",
		Instructions: "You are a coding agent.",
		Input:        raw["input"],
		Stream:       true,
		Raw:          raw,
	}
	route := Route{ID: "gpt-5.4-mini", Model: "deepseek-v4-pro", API: "chat_completions"}

	converted := responsesToChatRequest(req, route, history)

	messages, ok := converted.body["messages"].([]map[string]any)
	if !ok || len(messages) < 2 {
		t.Fatalf("expected system + user messages, got %v", converted.body["messages"])
	}
	if messages[0]["role"] != "system" {
		t.Errorf("expected first message to be system, got %v", messages[0]["role"])
	}
	tools, ok := converted.body["tools"].([]map[string]any)
	if !ok || len(tools) != 1 {
		t.Fatalf("expected 1 tool, got %v", converted.body["tools"])
	}
	if !converted.wantsStream {
		t.Errorf("expected wantsStream to be true")
	}
}

// TestChatResponseToResponseWithToolCall verifies a chat tool_call is converted
// into a Codex function_call output item.
func TestChatResponseToResponseWithToolCall(t *testing.T) {
	toolCtx := buildToolContext([]any{
		map[string]any{"type": "function", "name": "read_file", "parameters": map[string]any{"type": "object"}},
	})
	chat := map[string]any{
		"id": "chatcmpl-abc",
		"choices": []any{
			map[string]any{
				"message": map[string]any{
					"role":    "assistant",
					"content": "",
					"tool_calls": []any{
						map[string]any{
							"id":   "call_1",
							"type": "function",
							"function": map[string]any{
								"name":      "read_file",
								"arguments": `{"path":"main.go"}`,
							},
						},
					},
				},
			},
		},
	}

	resp := chatResponseToResponse(chat, "gpt-5.4-mini", toolCtx)
	output, ok := resp["output"].([]map[string]any)
	if !ok || len(output) == 0 {
		t.Fatalf("expected output items, got %v", resp["output"])
	}
	found := false
	for _, item := range output {
		if item["type"] == "function_call" && item["name"] == "read_file" {
			found = true
		}
	}
	if !found {
		t.Errorf("expected a function_call output item for read_file, got %v", output)
	}
}

// TestCodexOpenAIBaseURLRedirect verifies api.openai.com is redirected to the
// ChatGPT Codex backend for subscription routes.
func TestCodexOpenAIBaseURLRedirect(t *testing.T) {
	route := Route{
		ID:       "gpt-5.5",
		BaseURL:  "https://api.openai.com/v1",
		AuthMode: "codex_openai",
	}
	got := responsesBaseURLForRoute(route)
	if got != chatgptCodexBaseURL {
		t.Errorf("expected redirect to %q, got %q", chatgptCodexBaseURL, got)
	}

	// Non-openai base URL should pass through unchanged.
	route2 := Route{ID: "x", BaseURL: "https://chatgpt.com/backend-api/codex", AuthMode: "codex_openai"}
	if got := responsesBaseURLForRoute(route2); got != route2.BaseURL {
		t.Errorf("expected passthrough, got %q", got)
	}
}

func TestRequestLogStats(t *testing.T) {
	raw := map[string]any{
		"input": []any{
			map[string]any{"type": "message", "role": "user", "content": "hello"},
			map[string]any{"type": "function_call_output", "call_id": "call_1", "output": "ok"},
		},
		"tools": []any{
			map[string]any{"type": "function", "name": "read_file"},
			map[string]any{"type": "function", "name": "write_file"},
		},
		"previous_response_id": "resp_prev",
	}

	stats := requestLogStats(raw, 314730)

	if stats.BodyBytes != 314730 {
		t.Fatalf("expected body bytes, got %d", stats.BodyBytes)
	}
	if stats.ToolCount != 2 {
		t.Fatalf("expected 2 tools, got %d", stats.ToolCount)
	}
	if stats.InputItemsCount != 2 {
		t.Fatalf("expected 2 input items, got %d", stats.InputItemsCount)
	}
	if stats.PreviousResponseID != "resp_prev" {
		t.Fatalf("expected previous response id, got %q", stats.PreviousResponseID)
	}
}

func TestResponseUsageTrackerFromSSE(t *testing.T) {
	tracker := newResponseUsageTracker(true)

	tracker.observe([]byte("event: response.completed\n"))
	tracker.observe([]byte(`data: {"response":{"usage":{"input_tokens":42,"output_tokens":7,"input_tokens_details":{"cached_tokens":13}}}}` + "\n\n"))
	tracker.observe([]byte("data: [DONE]\n\n"))
	tracker.finish()

	if tracker.inputTokens() != "42" {
		t.Fatalf("expected input tokens 42, got %s", tracker.inputTokens())
	}
	if tracker.cachedTokens() != "13" {
		t.Fatalf("expected cached tokens 13, got %s", tracker.cachedTokens())
	}
	if tracker.outputTokens() != "7" {
		t.Fatalf("expected output tokens 7, got %s", tracker.outputTokens())
	}
}

func TestDefaultConfigDeepSeekUsesWinkyByDefault(t *testing.T) {
	t.Setenv("NEXUS_DEEPSEEK_BASE_URL", "")
	t.Setenv("NEXUS_DEEPSEEK_PROVIDER", "")
	t.Setenv("NEXUS_DEEPSEEK_PRO_DESCRIPTION", "")

	cfg := DefaultConfig()
	route := findRouteForTest(t, cfg, "deepseek-v4-pro")

	if route.BaseURL != "https://lumos.diandian.info/winky/deepseek/v1" {
		t.Fatalf("expected default Winky base URL, got %q", route.BaseURL)
	}
	if route.Provider != "Winky DeepSeek" {
		t.Fatalf("expected default Winky provider, got %q", route.Provider)
	}
	if !strings.Contains(route.Description, "Winky") {
		t.Fatalf("expected default description to mention Winky, got %q", route.Description)
	}
}

func TestDefaultConfigDeepSeekCanUsePersonalAPI(t *testing.T) {
	t.Setenv("NEXUS_DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
	t.Setenv("NEXUS_DEEPSEEK_PROVIDER", "DeepSeek Official")
	t.Setenv("NEXUS_DEEPSEEK_PRO_DESCRIPTION", "DeepSeek V4 Pro via personal DeepSeek API.")
	t.Setenv("NEXUS_DEEPSEEK_FLASH_DESCRIPTION", "DeepSeek V4 Flash via personal DeepSeek API.")

	cfg := DefaultConfig()
	pro := findRouteForTest(t, cfg, "deepseek-v4-pro")
	flash := findRouteForTest(t, cfg, "deepseek-v4-flash")

	for _, route := range []Route{pro, flash} {
		if route.BaseURL != "https://api.deepseek.com/v1" {
			t.Fatalf("expected personal DeepSeek base URL for %s, got %q", route.ID, route.BaseURL)
		}
		if route.Provider != "DeepSeek Official" {
			t.Fatalf("expected personal DeepSeek provider for %s, got %q", route.ID, route.Provider)
		}
	}
	if !strings.Contains(pro.Description, "personal DeepSeek API") {
		t.Fatalf("expected personal API description, got %q", pro.Description)
	}
}

func TestDefaultConfigIncludesGLMViaWinky(t *testing.T) {
	t.Setenv("NEXUS_GLM_BASE_URL", "")
	t.Setenv("NEXUS_GLM_PROVIDER", "")
	t.Setenv("NEXUS_GLM_51_DESCRIPTION", "")
	t.Setenv("NEXUS_GLM_52_DESCRIPTION", "")

	cfg := DefaultConfig()
	for _, id := range []string{"glm-5.2", "glm-5.1"} {
		route := findRouteForTest(t, cfg, id)
		if route.BaseURL != "https://lumos.diandian.info/winky/glm/v1" {
			t.Fatalf("expected default GLM Winky base URL for %s, got %q", id, route.BaseURL)
		}
		if route.Provider != "Winky GLM" {
			t.Fatalf("expected default GLM provider for %s, got %q", id, route.Provider)
		}
		if route.Model != id || route.API != "chat_completions" {
			t.Fatalf("expected GLM chat completions route for %s, got %#v", id, route)
		}
		if route.APIKeyEnv != "DEEPSEEK_API_KEY" {
			t.Fatalf("expected GLM to reuse Winky API key env DEEPSEEK_API_KEY for %s, got %q", id, route.APIKeyEnv)
		}
	}
}

func findRouteForTest(t *testing.T, cfg Config, id string) Route {
	t.Helper()
	for _, route := range cfg.Routes {
		if route.ID == id {
			return route
		}
	}
	t.Fatalf("route %s not found", id)
	return Route{}
}

type recordingTokenUsageRecorder struct {
	events      []TokenUsageEvent
	routeEvents []WorkflowRouteEvent
	session     WorkflowSessionContext
	ok          bool
}

func (r *recordingTokenUsageRecorder) RecordTokenUsage(event TokenUsageEvent) error {
	r.events = append(r.events, event)
	return nil
}

func (r *recordingTokenUsageRecorder) RecordWorkflowRouteEvent(event WorkflowRouteEvent) error {
	r.routeEvents = append(r.routeEvents, event)
	return nil
}

func (r *recordingTokenUsageRecorder) LookupWorkflowSession(sessionID string) (WorkflowSessionContext, bool, error) {
	return r.session, r.ok, nil
}

func TestRecordTokenUsageUsesBoundSession(t *testing.T) {
	service := NewService(Config{Routes: []Route{{ID: "gpt-test", Model: "gpt-test", API: "responses"}}})
	recorder := &recordingTokenUsageRecorder{session: WorkflowSessionContext{WorkflowRunID: "wf_run_123", Role: "worker-role"}, ok: true}
	service.SetTokenUsageRecorder(recorder)

	tracker := newResponseUsageTracker(false)
	tracker.updateFromObject(map[string]any{"usage": map[string]any{
		"input_tokens":         100.0,
		"output_tokens":        25.0,
		"total_tokens":         125.0,
		"input_tokens_details": map[string]any{"cached_tokens": 40.0},
	}})
	request := httptest.NewRequest("POST", "/proxy/codex/v1/responses", nil)
	request.Header.Set("Session-Id", "sess-123")
	request.Header.Set(workflowRoleHeader, "Worker Role")

	if err := service.recordTokenUsage(request, responseRequest{Model: "gpt-test"}, Route{ID: "gpt-test"}, tracker); err != nil {
		t.Fatalf("record token usage: %v", err)
	}
	if len(recorder.events) != 1 {
		t.Fatalf("expected one event, got %#v", recorder.events)
	}
	event := recorder.events[0]
	if event.WorkflowRunID != "wf_run_123" || event.SessionID != "sess-123" || event.Role != "worker-role" || event.Model != "gpt-test" {
		t.Fatalf("unexpected event identity: %#v", event)
	}
	if event.InputTokens != 100 || event.CachedInputTokens != 40 || event.OutputTokens != 25 || event.TotalTokens != 125 {
		t.Fatalf("unexpected token counts: %#v", event)
	}
}

func TestRecordTokenUsageSkipsMissingSessionID(t *testing.T) {
	service := NewService(Config{Routes: []Route{{ID: "gpt-test", Model: "gpt-test", API: "responses"}}})
	recorder := &recordingTokenUsageRecorder{session: WorkflowSessionContext{WorkflowRunID: "wf_run_123"}, ok: true}
	service.SetTokenUsageRecorder(recorder)
	tracker := newResponseUsageTracker(false)
	tracker.updateFromObject(map[string]any{"usage": map[string]any{"input_tokens": 10.0, "output_tokens": 2.0}})
	request := httptest.NewRequest("POST", "/proxy/codex/v1/responses", nil)
	if err := service.recordTokenUsage(request, responseRequest{Model: "gpt-test"}, Route{ID: "gpt-test"}, tracker); err != nil {
		t.Fatalf("record token usage without session id: %v", err)
	}
	if len(recorder.events) != 0 {
		t.Fatalf("expected no events without session id, got %#v", recorder.events)
	}
}

func TestRecordTokenUsageUsesUnboundSessionID(t *testing.T) {
	service := NewService(Config{Routes: []Route{{ID: "gpt-test", Model: "gpt-test", API: "responses"}}})
	recorder := &recordingTokenUsageRecorder{ok: false}
	service.SetTokenUsageRecorder(recorder)
	tracker := newResponseUsageTracker(false)
	tracker.updateFromObject(map[string]any{"usage": map[string]any{"input_tokens": 10.0, "output_tokens": 2.0}})
	request := httptest.NewRequest("POST", "/proxy/codex/v1/responses", nil)
	request.Header.Set("Session-Id", "sess-unbound")
	if err := service.recordTokenUsage(request, responseRequest{Model: "gpt-test"}, Route{ID: "gpt-test"}, tracker); err != nil {
		t.Fatalf("record token usage with unbound session id: %v", err)
	}
	if len(recorder.events) != 1 {
		t.Fatalf("expected one event with unbound session id, got %#v", recorder.events)
	}
	if recorder.events[0].WorkflowRunID != "" || recorder.events[0].SessionID != "sess-unbound" {
		t.Fatalf("unexpected unbound session event: %#v", recorder.events[0])
	}
}

func TestRecordRouteEventUsesBoundSession(t *testing.T) {
	service := NewService(Config{Routes: []Route{{ID: "gpt-test", Model: "gpt-test", API: "responses"}}})
	recorder := &recordingTokenUsageRecorder{session: WorkflowSessionContext{WorkflowRunID: "wf_run_route"}, ok: true}
	service.SetTokenUsageRecorder(recorder)
	request := httptest.NewRequest("POST", "/proxy/codex/v1/responses", nil)
	request.Header.Set("Session-Id", "sess-route-1")
	request.Header.Set(workflowRoleHeader, "reviewer")
	stats := requestStats{BodyBytes: 2048, ToolCount: 5, InputItemsCount: 3}
	if err := service.recordRouteEvent(request, responseRequest{Model: "gpt-test"}, Route{ID: "gpt-test"}, stats, 200, 1234, 2); err != nil {
		t.Fatalf("record route event: %v", err)
	}
	if len(recorder.routeEvents) != 1 {
		t.Fatalf("expected one route event, got %#v", recorder.routeEvents)
	}
	event := recorder.routeEvents[0]
	if event.WorkflowRunID != "wf_run_route" || event.SessionID != "sess-route-1" || event.Role != "reviewer" || event.StatusCode != 200 || event.DurationMS != 1234 {
		t.Fatalf("unexpected route event identity: %#v", event)
	}
	if event.RequestBytes != 2048 || event.ToolCount != 5 || event.InputItemCount != 3 || event.ToolCallCount != 2 {
		t.Fatalf("unexpected route event metrics: %#v", event)
	}
}
