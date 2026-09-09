package codexrouter

import (
	"encoding/json"
	"fmt"
	"strings"
	"sync"
	"time"
)

// responseHistory keeps a small in-memory map of response_id -> prior chat
// messages so multi-turn conversations using previous_response_id keep context.
type responseHistory struct {
	mu    sync.Mutex
	store map[string][]map[string]any
	order []string
}

func newResponseHistory() *responseHistory {
	return &responseHistory{store: map[string][]map[string]any{}}
}

func (h *responseHistory) get(id string) []map[string]any {
	if id == "" {
		return nil
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.store[id]
}

func (h *responseHistory) record(id string, messages []map[string]any) {
	if id == "" {
		return
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	if _, exists := h.store[id]; !exists {
		h.order = append(h.order, id)
	}
	h.store[id] = messages
	// Cap history to avoid unbounded growth.
	const maxEntries = 200
	for len(h.order) > maxEntries {
		oldest := h.order[0]
		h.order = h.order[1:]
		delete(h.store, oldest)
	}
}

// convertedRequest is the result of turning a Codex Responses request into a
// Chat Completions request body.
type convertedRequest struct {
	body               map[string]any
	toolContext        *toolContext
	wantsStream        bool
	messagesForHistory []map[string]any
}

// responsesToChatRequest mirrors codex-bridge responsesToChatRequest.
func responsesToChatRequest(request responseRequest, route Route, history *responseHistory) convertedRequest {
	var responseTools []any
	if rawTools, ok := request.Raw["tools"].([]any); ok {
		responseTools = rawTools
	}
	toolCtx := buildToolContext(responseTools)

	previousID, _ := request.Raw["previous_response_id"].(string)
	priorMessages := history.get(previousID)

	inputValue := request.Input
	if request.Messages != nil {
		inputValue = request.Messages
	}
	currentMessages := responseInputToChatMessages(inputValue, toolCtx)

	messages := []map[string]any{}
	if instructions := systemInstructionsFromRequest(request); instructions != "" {
		messages = append(messages, map[string]any{"role": "system", "content": instructions})
	}
	messages = append(messages, priorMessages...)
	messages = append(messages, currentMessages...)

	body := map[string]any{
		"model":    route.Model,
		"messages": messages,
		"stream":   false,
	}

	if len(toolCtx.chatTools) > 0 {
		body["tools"] = toolCtx.chatTools
		if tc := chatToolChoice(request.Raw["tool_choice"], toolCtx); tc != nil {
			body["tool_choice"] = tc
		}
		if !routeDropsParam(route, "parallel_tool_calls") {
			parallel := true
			if v, ok := request.Raw["parallel_tool_calls"].(bool); ok {
				parallel = v
			}
			body["parallel_tool_calls"] = parallel
		}
	}

	copyScalar(request.Raw, body, "temperature")
	copyScalar(request.Raw, body, "top_p")
	copyScalar(request.Raw, body, "presence_penalty")
	copyScalar(request.Raw, body, "frequency_penalty")
	copyScalar(request.Raw, body, "seed")
	copyScalar(request.Raw, body, "user")

	if value, ok := request.Raw["max_output_tokens"]; ok {
		body["max_tokens"] = value
	} else {
		copyScalar(request.Raw, body, "max_tokens")
		copyScalar(request.Raw, body, "max_completion_tokens")
	}
	if value, ok := request.Raw["stop"]; ok {
		body["stop"] = value
	}
	// handleResponses performs the route capability preflight before this
	// conversion runs, so this forwarding path cannot silently degrade a
	// requested structured response into free text.
	if value, ok := request.Raw["response_format"]; ok && !routeDropsParam(route, "response_format") {
		body["response_format"] = value
	}

	return convertedRequest{
		body:               body,
		toolContext:        toolCtx,
		wantsStream:        request.Stream,
		messagesForHistory: messages,
	}
}

func responseInputToChatMessages(input any, toolCtx *toolContext) []map[string]any {
	if input == nil {
		return nil
	}
	if str, ok := input.(string); ok {
		if strings.TrimSpace(str) == "" {
			return nil
		}
		return []map[string]any{{"role": "user", "content": str}}
	}

	var items []any
	if arr, ok := input.([]any); ok {
		items = arr
	} else {
		items = []any{input}
	}

	messages := []map[string]any{}
	var pendingToolCalls []map[string]any

	flush := func() {
		if len(pendingToolCalls) == 0 {
			return
		}
		messages = append(messages, map[string]any{
			"role":       "assistant",
			"content":    nil,
			"tool_calls": pendingToolCalls,
		})
		pendingToolCalls = nil
	}

	for _, item := range items {
		if isResponseToolCallItem(item) {
			pendingToolCalls = append(pendingToolCalls, toolCtx.chatToolCallFromResponseItem(item.(map[string]any)))
			continue
		}
		flush()

		if m, ok := item.(map[string]any); ok {
			role, _ := m["role"].(string)
			if role == "system" || role == "developer" {
				continue
			}
		}
		if isResponseToolOutputItem(item) {
			messages = append(messages, chatMessageFromToolOutput(item.(map[string]any)))
			continue
		}
		if msg := responseMessageToChatMessage(item); msg != nil {
			messages = append(messages, msg)
		}
	}
	flush()
	return messages
}

func responseMessageToChatMessage(item any) map[string]any {
	if str, ok := item.(string); ok {
		return map[string]any{"role": "user", "content": str}
	}
	m, ok := item.(map[string]any)
	if !ok {
		return nil
	}
	if t, _ := m["type"].(string); t == "reasoning" {
		return nil
	}
	role := normalizeRole(stringOr(m["role"], roleFromTypeStr(m["type"])))
	if role == "" {
		return nil
	}
	content := firstDefined(m["content"], m["text"], m["output"], "")
	message := map[string]any{
		"role":    role,
		"content": contentToText(content),
	}
	if toolCalls, ok := m["tool_calls"].([]any); ok {
		message["tool_calls"] = toolCalls
		if message["content"] == "" {
			message["content"] = nil
		}
	}
	return message
}

func systemInstructionsFromRequest(request responseRequest) string {
	var parts []string
	if strings.TrimSpace(request.Instructions) != "" {
		parts = append(parts, strings.TrimSpace(request.Instructions))
	}
	if arr, ok := request.Input.([]any); ok {
		for _, item := range arr {
			if m, ok := item.(map[string]any); ok {
				role, _ := m["role"].(string)
				if role == "system" || role == "developer" {
					if text := contentToText(m["content"]); text != "" {
						parts = append(parts, text)
					}
				}
			}
		}
	}
	return strings.Join(parts, "\n\n")
}

func chatToolChoice(toolChoice any, toolCtx *toolContext) any {
	if toolChoice == nil {
		return "auto"
	}
	if str, ok := toolChoice.(string); ok {
		return str
	}
	m, ok := toolChoice.(map[string]any)
	if !ok {
		return "auto"
	}
	name, _ := m["name"].(string)
	if name == "" {
		if fn, ok := m["function"].(map[string]any); ok {
			name, _ = fn["name"].(string)
		}
	}
	if name == "" {
		return "auto"
	}
	chatName := name
	if mapped, ok := toolCtx.responseNameToChatName[name]; ok {
		chatName = mapped
	}
	return map[string]any{"type": "function", "function": map[string]any{"name": chatName}}
}

// chatResponseToResponse converts a Chat Completions response into a Codex
// Responses object, including tool calls.
func chatResponseToResponse(chat map[string]any, requestedModel string, toolCtx *toolContext) map[string]any {
	id := responseIDFromChat(chat["id"])
	output := []map[string]any{}
	text := ""

	var message map[string]any
	if choices, ok := chat["choices"].([]any); ok && len(choices) > 0 {
		if choice, ok := choices[0].(map[string]any); ok {
			message, _ = choice["message"].(map[string]any)
		}
	}
	if message != nil {
		text = messageText(message)
		if text != "" {
			output = append(output, map[string]any{
				"id":     "msg_" + stableFragment(id),
				"type":   "message",
				"role":   "assistant",
				"status": "completed",
				"content": []map[string]any{{
					"type":        "output_text",
					"text":        text,
					"annotations": []any{},
				}},
			})
		}
		if toolCalls, ok := message["tool_calls"].([]any); ok {
			for _, tc := range toolCalls {
				if tcMap, ok := tc.(map[string]any); ok {
					output = append(output, toolCtx.responseToolCallFromChat(tcMap))
				}
			}
		}
	}

	return map[string]any{
		"id":                  id,
		"object":              "response",
		"created_at":          time.Now().Unix(),
		"status":              "completed",
		"model":               requestedModel,
		"output":              output,
		"output_text":         text,
		"parallel_tool_calls": true,
		"error":               nil,
		"incomplete_details":  nil,
		"usage":               responseUsage(chat["usage"]),
	}
}

func assistantHistoryMessageFromChat(chat map[string]any) map[string]any {
	var message map[string]any
	if choices, ok := chat["choices"].([]any); ok && len(choices) > 0 {
		if choice, ok := choices[0].(map[string]any); ok {
			message, _ = choice["message"].(map[string]any)
		}
	}
	history := map[string]any{"role": "assistant"}
	if text := messageText(message); text != "" {
		history["content"] = text
	} else {
		history["content"] = nil
	}
	if message != nil {
		if toolCalls, ok := message["tool_calls"].([]any); ok && len(toolCalls) > 0 {
			history["tool_calls"] = toolCalls
		}
	}
	return history
}

// responseToSSE builds the full Codex Responses SSE event stream from a
// completed response object. This is the critical fix: Codex Desktop expects
// the full event sequence, not just response.completed.
func responseToSSE(response map[string]any) string {
	var events []string

	inProgress := cloneMap(response)
	inProgress["status"] = "in_progress"
	inProgress["output"] = []any{}

	events = append(events, sseEvent("response.created", map[string]any{
		"type":     "response.created",
		"response": inProgress,
	}))
	events = append(events, sseEvent("response.in_progress", map[string]any{
		"type":     "response.in_progress",
		"response": inProgress,
	}))

	outputItems, _ := response["output"].([]map[string]any)
	for outputIndex, item := range outputItems {
		itemType, _ := item["type"].(string)
		if itemType == "message" {
			text := ""
			if content, ok := item["content"].([]map[string]any); ok && len(content) > 0 {
				text, _ = content[0]["text"].(string)
			}
			itemID, _ := item["id"].(string)

			addedItem := cloneMap(item)
			addedItem["status"] = "in_progress"
			addedItem["content"] = []any{}

			events = append(events, sseEvent("response.output_item.added", map[string]any{
				"type":         "response.output_item.added",
				"output_index": outputIndex,
				"item":         addedItem,
			}))
			events = append(events, sseEvent("response.content_part.added", map[string]any{
				"type":          "response.content_part.added",
				"item_id":       itemID,
				"output_index":  outputIndex,
				"content_index": 0,
				"part":          map[string]any{"type": "output_text", "text": "", "annotations": []any{}},
			}))
			if text != "" {
				events = append(events, sseEvent("response.output_text.delta", map[string]any{
					"type":          "response.output_text.delta",
					"item_id":       itemID,
					"output_index":  outputIndex,
					"content_index": 0,
					"delta":         text,
				}))
			}
			events = append(events, sseEvent("response.output_text.done", map[string]any{
				"type":          "response.output_text.done",
				"item_id":       itemID,
				"output_index":  outputIndex,
				"content_index": 0,
				"text":          text,
			}))
			events = append(events, sseEvent("response.content_part.done", map[string]any{
				"type":          "response.content_part.done",
				"item_id":       itemID,
				"output_index":  outputIndex,
				"content_index": 0,
				"part":          map[string]any{"type": "output_text", "text": text, "annotations": []any{}},
			}))
			events = append(events, sseEvent("response.output_item.done", map[string]any{
				"type":         "response.output_item.done",
				"output_index": outputIndex,
				"item":         item,
			}))
			continue
		}

		// Non-message items (tool calls) are emitted as added + done.
		events = append(events, sseEvent("response.output_item.added", map[string]any{
			"type":         "response.output_item.added",
			"output_index": outputIndex,
			"item":         item,
		}))
		events = append(events, sseEvent("response.output_item.done", map[string]any{
			"type":         "response.output_item.done",
			"output_index": outputIndex,
			"item":         item,
		}))
	}

	events = append(events, sseEvent("response.completed", map[string]any{
		"type":     "response.completed",
		"response": response,
	}))
	events = append(events, "data: [DONE]\n\n")
	return strings.Join(events, "")
}

func sseEvent(event string, payload map[string]any) string {
	data, _ := json.Marshal(payload)
	return fmt.Sprintf("event: %s\ndata: %s\n\n", event, data)
}

func responseUsage(usage any) map[string]any {
	u, _ := usage.(map[string]any)
	inputTokens := numberFrom(u, "prompt_tokens")
	outputTokens := numberFrom(u, "completion_tokens")
	total := numberFrom(u, "total_tokens")
	if total == 0 {
		total = inputTokens + outputTokens
	}
	cached := 0.0
	reasoning := 0.0
	if u != nil {
		if details, ok := u["prompt_tokens_details"].(map[string]any); ok {
			cached = toFloat(details["cached_tokens"])
		}
		if details, ok := u["completion_tokens_details"].(map[string]any); ok {
			reasoning = toFloat(details["reasoning_tokens"])
		}
	}
	return map[string]any{
		"input_tokens":          inputTokens,
		"output_tokens":         outputTokens,
		"total_tokens":          total,
		"input_tokens_details":  map[string]any{"cached_tokens": cached},
		"output_tokens_details": map[string]any{"reasoning_tokens": reasoning},
	}
}

func numberFrom(m map[string]any, key string) float64 {
	if m == nil {
		return 0
	}
	return toFloat(m[key])
}

func toFloat(v any) float64 {
	switch n := v.(type) {
	case float64:
		return n
	case int:
		return float64(n)
	case json.Number:
		f, _ := n.Float64()
		return f
	}
	return 0
}

func messageText(message map[string]any) string {
	if message == nil {
		return ""
	}
	return contentToText(message["content"])
}

func responseIDFromChat(chatID any) string {
	id, _ := chatID.(string)
	if id == "" {
		return fmt.Sprintf("resp_%d", time.Now().UnixNano())
	}
	if strings.HasPrefix(id, "resp_") {
		return id
	}
	return "resp_" + id
}

func stableFragment(value string) string {
	cleaned := regexpNonAlnum.ReplaceAllString(value, "")
	if len(cleaned) > 16 {
		cleaned = cleaned[len(cleaned)-16:]
	}
	if cleaned == "" {
		return "message"
	}
	return cleaned
}

func cloneMap(m map[string]any) map[string]any {
	out := make(map[string]any, len(m))
	for k, v := range m {
		out[k] = v
	}
	return out
}

func roleFromTypeStr(value any) string {
	if value == "message" {
		return "user"
	}
	return ""
}
