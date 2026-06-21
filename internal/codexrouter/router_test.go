package codexrouter

import (
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
