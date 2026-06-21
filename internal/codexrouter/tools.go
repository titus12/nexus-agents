package codexrouter

import (
	"crypto/sha1"
	"encoding/json"
	"fmt"
	"regexp"
	"strings"
	"time"
)

const applyPatchToolName = "apply_patch"

var validChatToolName = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)

// toolContext mirrors codex-bridge buildToolContext: it tracks how Codex
// Responses tool definitions map to Chat Completions function definitions.
type toolContext struct {
	chatTools               []map[string]any
	customToolNames         map[string]bool
	specialToolTypes        map[string]string
	chatNameToResponseName  map[string]string
	responseNameToChatName  map[string]string
}

func buildToolContext(responseTools []any) *toolContext {
	ctx := &toolContext{
		chatTools:              []map[string]any{},
		customToolNames:        map[string]bool{},
		specialToolTypes:       map[string]string{},
		chatNameToResponseName: map[string]string{},
		responseNameToChatName: map[string]string{},
	}
	for _, tool := range responseTools {
		ctx.appendResponseTool(tool)
	}
	return ctx
}

func (ctx *toolContext) appendResponseTool(tool any) {
	toolMap, ok := tool.(map[string]any)
	if !ok {
		return
	}
	toolType, _ := toolMap["type"].(string)

	switch toolType {
	case "namespace":
		if inner, ok := toolMap["tools"].([]any); ok {
			for _, t := range inner {
				ctx.appendResponseTool(t)
			}
		}
		return
	case "web_search", "web_search_preview":
		return
	case "tool_search":
		name := stringOr(toolMap["name"], "tool_search")
		chatName := ctx.chatNameForResponseName(name)
		ctx.specialToolTypes[name] = "tool_search_call"
		params := toolMap["parameters"]
		if params == nil {
			params = map[string]any{
				"type":       "object",
				"properties": map[string]any{"query": map[string]any{"type": "string"}},
				"required":   []string{"query"},
			}
		}
		ctx.chatTools = append(ctx.chatTools, map[string]any{
			"type": "function",
			"function": map[string]any{
				"name":        chatName,
				"description": stringOr(toolMap["description"], "Search for deferred local tools."),
				"parameters":  params,
			},
		})
		return
	case "custom":
		name := stringOr(toolMap["name"], "custom_tool")
		chatName := ctx.chatNameForResponseName(name)
		ctx.customToolNames[name] = true
		ctx.chatTools = append(ctx.chatTools, map[string]any{
			"type": "function",
			"function": map[string]any{
				"name":        chatName,
				"description": customToolDescription(name, stringOr(toolMap["description"], "")),
				"parameters": map[string]any{
					"type": "object",
					"properties": map[string]any{
						"input": map[string]any{
							"type":        "string",
							"description": customInputDescription(name),
						},
					},
					"required": []string{"input"},
				},
			},
		})
		return
	}

	// Regular function tool.
	fn := normalizeFunctionTool(toolMap)
	if fn == nil {
		return
	}
	fnName, _ := fn["name"].(string)
	if fnName == "" {
		return
	}
	chatName := ctx.chatNameForResponseName(fnName)
	params := fn["parameters"]
	if params == nil {
		params = map[string]any{"type": "object", "properties": map[string]any{}}
	}
	ctx.chatTools = append(ctx.chatTools, map[string]any{
		"type": "function",
		"function": map[string]any{
			"name":        chatName,
			"description": stringOr(fn["description"], ""),
			"parameters":  params,
		},
	})
}

func normalizeFunctionTool(tool map[string]any) map[string]any {
	toolType, _ := tool["type"].(string)
	if toolType == "function" {
		if fn, ok := tool["function"].(map[string]any); ok {
			return fn
		}
		return map[string]any{
			"name":        tool["name"],
			"description": tool["description"],
			"parameters":  tool["parameters"],
		}
	}
	return nil
}

func (ctx *toolContext) chatNameForResponseName(responseName string) string {
	if existing, ok := ctx.responseNameToChatName[responseName]; ok {
		return existing
	}
	chatName := responseName
	if !validChatToolName.MatchString(chatName) {
		safe := regexp.MustCompile(`[^A-Za-z0-9_-]`).ReplaceAllString(chatName, "_")
		if len(safe) > 52 {
			safe = safe[:52]
		}
		chatName = fmt.Sprintf("%s_%s", safe, stableSuffix(responseName))
		if len(chatName) > 64 {
			chatName = chatName[:64]
		}
	}
	ctx.responseNameToChatName[responseName] = chatName
	ctx.chatNameToResponseName[chatName] = responseName
	return chatName
}

// responseToolCallFromChat converts a Chat Completions tool_call into a Codex
// Responses output item (function_call / custom_tool_call / tool_search_call).
func (ctx *toolContext) responseToolCallFromChat(call map[string]any) map[string]any {
	chatName := ""
	var args any
	if fn, ok := call["function"].(map[string]any); ok {
		chatName, _ = fn["name"].(string)
		args = fn["arguments"]
	}
	if chatName == "" {
		chatName, _ = call["name"].(string)
	}
	if args == nil {
		args = call["arguments"]
	}
	responseName := chatName
	if mapped, ok := ctx.chatNameToResponseName[chatName]; ok {
		responseName = mapped
	}
	callID, _ := call["id"].(string)
	if callID == "" {
		callID = fmt.Sprintf("call_%s", stableSuffix(responseName+fmt.Sprint(time.Now().UnixNano())))
	}
	specialType := ctx.specialToolTypes[responseName]

	if specialType == "tool_search_call" {
		return map[string]any{
			"id":        "ts_" + stableSuffix(callID),
			"type":      "tool_search_call",
			"call_id":   callID,
			"arguments": stringifyJSON(args),
			"status":    "completed",
		}
	}
	if responseName == applyPatchToolName || ctx.customToolNames[responseName] {
		return map[string]any{
			"id":      "ctc_" + stableSuffix(callID),
			"type":    "custom_tool_call",
			"call_id": callID,
			"name":    responseName,
			"input":   customInputFromArguments(args),
			"status":  "completed",
		}
	}
	return map[string]any{
		"id":        "fc_" + stableSuffix(callID),
		"type":      "function_call",
		"call_id":   callID,
		"name":      responseName,
		"arguments": stringifyJSON(args),
		"status":    "completed",
	}
}

// chatToolCallFromResponseItem converts a Codex Responses tool-call item from
// the request input back into a Chat Completions assistant tool_call.
func (ctx *toolContext) chatToolCallFromResponseItem(item map[string]any) map[string]any {
	responseName := stringOr(item["name"], stringOr(item["type"], "tool"))
	chatName := ctx.chatNameForResponseName(responseName)
	itemType, _ := item["type"].(string)
	id := stringOr(item["call_id"], stringOr(item["id"], ""))

	if itemType == "custom_tool_call" {
		input, _ := item["input"].(string)
		argsJSON, _ := json.Marshal(map[string]any{"input": input})
		return map[string]any{
			"id":   id,
			"type": "function",
			"function": map[string]any{
				"name":      chatName,
				"arguments": string(argsJSON),
			},
		}
	}
	if itemType == "tool_search_call" {
		return map[string]any{
			"id":   id,
			"type": "function",
			"function": map[string]any{
				"name":      ctx.chatNameForResponseName("tool_search"),
				"arguments": stringifyJSON(orDefault(item["arguments"], "{}")),
			},
		}
	}
	return map[string]any{
		"id":   id,
		"type": "function",
		"function": map[string]any{
			"name":      chatName,
			"arguments": stringifyJSON(orDefault(item["arguments"], "{}")),
		},
	}
}

func chatMessageFromToolOutput(item map[string]any) map[string]any {
	return map[string]any{
		"role":         "tool",
		"tool_call_id": stringOr(item["call_id"], stringOr(item["id"], "")),
		"content":      stringifyJSON(firstDefined(item["output"], item["result"], "")),
	}
}

func isResponseToolCallItem(item any) bool {
	m, ok := item.(map[string]any)
	if !ok {
		return false
	}
	t, _ := m["type"].(string)
	return t == "function_call" || t == "custom_tool_call" || t == "tool_search_call"
}

func isResponseToolOutputItem(item any) bool {
	m, ok := item.(map[string]any)
	if !ok {
		return false
	}
	t, _ := m["type"].(string)
	switch t {
	case "function_call_output", "custom_tool_call_output", "tool_search_call_output", "tool_result":
		return true
	}
	return false
}

func customInputFromArguments(args any) string {
	str, ok := args.(string)
	if !ok {
		return stringifyJSON(args)
	}
	if parsed := tryParseJSONObject(str); parsed != nil {
		if input, ok := parsed["input"].(string); ok {
			return input
		}
	}
	return str
}

func customInputDescription(name string) string {
	if name == applyPatchToolName {
		return "Exact V4A patch text beginning with *** Begin Patch and ending with *** End Patch."
	}
	return "Free-form input passed verbatim to the tool."
}

func customToolDescription(name, description string) string {
	if name != applyPatchToolName {
		if description != "" {
			return description
		}
		return "Run a Codex custom tool."
	}
	return strings.Join([]string{
		"Edit files by returning a V4A apply_patch payload.",
		"Call this function with input set to the exact patch text.",
		"The input must not be JSON inside the string; it must start with *** Begin Patch.",
	}, " ")
}

func stableSuffix(value string) string {
	sum := sha1.Sum([]byte(value))
	return fmt.Sprintf("%x", sum)[:10]
}

func stringifyJSON(value any) string {
	if str, ok := value.(string); ok {
		return str
	}
	data, err := json.Marshal(value)
	if err != nil {
		return ""
	}
	return string(data)
}

func tryParseJSONObject(s string) map[string]any {
	var result map[string]any
	if err := json.Unmarshal([]byte(s), &result); err != nil {
		return nil
	}
	return result
}

func stringOr(value any, fallback string) string {
	if str, ok := value.(string); ok && str != "" {
		return str
	}
	return fallback
}

func orDefault(value any, fallback any) any {
	if value == nil {
		return fallback
	}
	return value
}
