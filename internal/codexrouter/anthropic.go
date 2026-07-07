package codexrouter

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"strings"
	"time"
)

// anthropicVersion is the required header value for the Anthropic Messages API.
const anthropicVersion = "2023-06-01"

// defaultAnthropicMaxTokens is used when the incoming Codex Responses request
// does not specify a max_output_tokens value. Anthropic Messages requires
// max_tokens, so a sensible default is required.
const defaultAnthropicMaxTokens = 4096

// anthropicConvertedRequest is the result of turning a Codex Responses request
// into an Anthropic Messages request body. It mirrors convertedRequest but for
// the Anthropic protocol.
type anthropicConvertedRequest struct {
	body        map[string]any
	wantsStream bool
	// messagesForHistory is the OpenAI-style message list used to record
	// conversation history for previous_response_id chaining.
	messagesForHistory []map[string]any
}

// proxyAnthropicMessages proxies a Codex Responses request to an upstream that
// speaks the native Anthropic Messages protocol (POST {BaseURL}/messages). It
// mirrors proxyChatCompletions: convert the request, call the upstream, convert
// the response back to a Codex Responses object, and fake the SSE stream when
// the client asked for streaming.
func (s *Service) proxyAnthropicMessages(w http.ResponseWriter, r *http.Request, request responseRequest, route Route, reqStats requestStats) {
	converted := responsesToAnthropicRequest(request, route, s.history)
	data, err := json.Marshal(converted.body)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, openAIError("invalid anthropic messages body", http.StatusBadRequest))
		return
	}

	upstream, err := http.NewRequestWithContext(r.Context(), http.MethodPost, joinUpstreamURL(route.BaseURL, "/messages"), bytes.NewReader(data))
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, openAIError(err.Error(), http.StatusInternalServerError))
		return
	}
	upstream.Header.Set("Content-Type", "application/json")
	if err := setAnthropicAuth(upstream.Header, route); err != nil {
		writeJSON(w, http.StatusInternalServerError, openAIError(err.Error(), http.StatusInternalServerError))
		return
	}

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

	var anthropic map[string]any
	if err := json.Unmarshal(body, &anthropic); err != nil {
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
	resp := anthropicResponseToResponse(anthropic, requestedModel)
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
		historyMessages = append(historyMessages, assistantHistoryMessageFromAnthropic(anthropic))
		s.history.record(respID, historyMessages)
	}

	log.Printf("[codex] -> route=%s upstream_status=%d duration_ms=%d usage_input=%s output=%s total_tokens=%d",
		route.ID, response.StatusCode, time.Since(t0).Milliseconds(), usage.inputTokens(), usage.outputTokens(), usage.totalTokenCount())

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

// responsesToAnthropicRequest converts a Codex Responses request into an
// Anthropic Messages request body. It reuses the same input/message extraction
// logic as responsesToChatRequest so instructions, input items and prior
// history are handled consistently.
func responsesToAnthropicRequest(request responseRequest, route Route, history *responseHistory) anthropicConvertedRequest {
	previousID, _ := request.Raw["previous_response_id"].(string)
	priorMessages := history.get(previousID)

	inputValue := request.Input
	if request.Messages != nil {
		inputValue = request.Messages
	}
	currentMessages := responseInputToChatMessages(inputValue, buildToolContext(nil))

	// Anthropic Messages only accepts user/assistant roles in the messages
	// array; system prompts go into the top-level "system" field. Tool output
	// messages are mapped to user messages with their text content.
	messages := []map[string]any{}
	messages = append(messages, priorMessages...)
	for _, msg := range currentMessages {
		role, _ := msg["role"].(string)
		switch role {
		case "user", "assistant":
			// already valid
		case "tool":
			role = "user"
		default:
			role = "user"
		}
		messages = append(messages, map[string]any{
			"role":    role,
			"content": contentToText(msg["content"]),
		})
	}

	body := map[string]any{
		"model":    route.Model,
		"messages": messages,
		"stream":   false,
	}

	if system := systemInstructionsFromRequest(request); system != "" {
		body["system"] = system
	}

	// max_tokens is required by the Anthropic Messages API.
	if value, ok := request.Raw["max_output_tokens"]; ok {
		body["max_tokens"] = value
	} else if value, ok := request.Raw["max_tokens"]; ok {
		body["max_tokens"] = value
	} else {
		body["max_tokens"] = defaultAnthropicMaxTokens
	}

	copyScalar(request.Raw, body, "temperature")
	copyScalar(request.Raw, body, "top_p")
	if value, ok := request.Raw["stop"]; ok {
		body["stop_sequences"] = value
	}

	// messagesForHistory keeps the OpenAI-style messages so the existing
	// responseHistory (which stores chat-style messages) stays consistent with
	// the chat_completions path.
	messagesForHistory := append([]map[string]any{}, priorMessages...)
	messagesForHistory = append(messagesForHistory, currentMessages...)

	return anthropicConvertedRequest{
		body:               body,
		wantsStream:        request.Stream,
		messagesForHistory: messagesForHistory,
	}
}

// anthropicResponseToResponse converts an Anthropic Messages JSON response into
// a Codex Responses object. It mirrors chatResponseToResponse: concatenate the
// text content blocks, emit a message output item, and map the usage fields.
func anthropicResponseToResponse(anthropic map[string]any, requestedModel string) map[string]any {
	id := responseIDFromAnthropic(anthropic["id"])
	text := anthropicContentText(anthropic["content"])

	output := []map[string]any{}
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

	return map[string]any{
		"id":                 id,
		"object":             "response",
		"created_at":         time.Now().Unix(),
		"status":             "completed",
		"model":              requestedModel,
		"output":             output,
		"output_text":        text,
		"parallel_tool_calls": true,
		"error":              nil,
		"incomplete_details": nil,
		"usage":              anthropicUsage(anthropic["usage"]),
	}
}

// anthropicContentText concatenates the text fields of all type:"text" content
// blocks returned by the Anthropic Messages API.
func anthropicContentText(content any) string {
	blocks, ok := content.([]any)
	if !ok {
		return ""
	}
	parts := make([]string, 0, len(blocks))
	for _, block := range blocks {
		m, ok := block.(map[string]any)
		if !ok {
			continue
		}
		if t, _ := m["type"].(string); t == "text" {
			if text, _ := m["text"].(string); text != "" {
				parts = append(parts, text)
			}
		}
	}
	return strings.Join(parts, "")
}

// anthropicUsage converts an Anthropic usage object into the Codex Responses
// usage shape. Anthropic reports input_tokens/output_tokens directly.
func anthropicUsage(usage any) map[string]any {
	u, _ := usage.(map[string]any)
	inputTokens := int(numberFrom(u, "input_tokens"))
	outputTokens := int(numberFrom(u, "output_tokens"))
	cached := 0
	if u != nil {
		if details, ok := u["cache_read_input_tokens"].(float64); ok {
			cached = int(details)
		}
	}
	total := inputTokens + outputTokens
	return map[string]any{
		"input_tokens":       float64(inputTokens),
		"output_tokens":      float64(outputTokens),
		"total_tokens":       float64(total),
		"input_tokens_details":  map[string]any{"cached_tokens": float64(cached)},
		"output_tokens_details": map[string]any{"reasoning_tokens": float64(0)},
	}
}

// responseIDFromAnthropic turns an Anthropic message id (e.g. "msg_xxx") into a
// Codex Responses id with the resp_ prefix.
func responseIDFromAnthropic(anthropicID any) string {
	id, _ := anthropicID.(string)
	if id == "" {
		return fmt.Sprintf("resp_%d", time.Now().UnixNano())
	}
	if strings.HasPrefix(id, "resp_") {
		return id
	}
	return "resp_" + id
}

// assistantHistoryMessageFromAnthropic builds an OpenAI-style assistant history
// message from an Anthropic response so the shared responseHistory stays
// consistent across chat_completions and anthropic_messages routes.
func assistantHistoryMessageFromAnthropic(anthropic map[string]any) map[string]any {
	history := map[string]any{"role": "assistant"}
	if text := anthropicContentText(anthropic["content"]); text != "" {
		history["content"] = text
	} else {
		history["content"] = nil
	}
	return history
}

// setAnthropicAuth configures the headers required by the Anthropic Messages
// API: x-api-key (not Authorization: Bearer) and anthropic-version. The key is
// sourced from route.APIKey or the route.APIKeyEnv environment variable, the
// same convention used by setUpstreamAuth for the other Winky routes.
func setAnthropicAuth(header http.Header, route Route) error {
	key := route.APIKey
	if key == "" && route.APIKeyEnv != "" {
		key = getenv(route.APIKeyEnv)
	}
	if key == "" {
		return fmt.Errorf("missing API key for %s", route.ID)
	}
	header.Set("x-api-key", key)
	header.Set("anthropic-version", anthropicVersion)
	return nil
}
