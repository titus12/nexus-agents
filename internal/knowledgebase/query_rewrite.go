package knowledgebase

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"strings"
	"time"
)

const (
	defaultQueryRewriteModel       = "deepseek-v4-flash"
	defaultQueryRewriteBaseURL     = "https://lumos.diandian.info/winky/deepseek/v1"
	defaultQueryRewriteAPIKeyEnv   = "DEEPSEEK_API_KEY"
	defaultQueryRewriteTimeoutMS   = 10000
	defaultQueryRewriteMaxKeywords = 12
)

type openAIChatQueryRewriteClient struct {
	httpClient *http.Client
}

type queryRewriteLLMResponse struct {
	EnglishQuery string   `json:"english_query"`
	Keywords     []string `json:"keywords"`
}

func rewriteKnowledgeQuery(query string, options QueryRewriteOptions) QueryRewriteResult {
	query = strings.TrimSpace(query)
	result := QueryRewriteResult{OriginalQuery: query}
	if query == "" || !containsCJK(query) {
		return result
	}
	result.Triggered = true
	options = normalizeQueryRewriteOptions(options)
	if !queryRewriteEnabled(options) {
		result.Error = "query rewrite disabled"
		return result
	}
	client := options.Client
	if client == nil {
		client = openAIChatQueryRewriteClient{httpClient: &http.Client{}}
	}
	rewritten, err := client.RewriteKnowledgeQuery(query, options)
	if err != nil {
		result.Error = err.Error()
		result.Model = options.Model
		return result
	}
	rewritten.OriginalQuery = query
	rewritten.Model = firstNonEmpty(rewritten.Model, options.Model)
	rewritten.Triggered = true
	rewritten.Keywords = limitStrings(uniqueStrings(rewritten.Keywords), options.MaxKeywords)
	if strings.TrimSpace(rewritten.EnglishQuery) == "" && len(rewritten.Keywords) == 0 {
		rewritten.Used = false
		rewritten.Error = "query rewrite returned no keywords"
		return rewritten
	}
	rewritten.Used = true
	return rewritten
}

func logQueryRewriteResult(result QueryRewriteResult) {
	if !result.Triggered {
		return
	}
	status := "success"
	errCode := ""
	if result.Error != "" {
		status = "failed"
		errCode = result.Error
	} else if !result.Used {
		status = "noop"
		errCode = "no_keywords"
	}
	log.Printf("[knowledge] STRUCTURED: {\"event\":\"knowledge_query_rewrite\",\"original_query\":%q,\"english_query\":%q,\"keywords\":%q,\"model\":%q,\"status\":%q,\"error_code\":%q,\"used\":%t}",
		result.OriginalQuery, result.EnglishQuery, strings.Join(result.Keywords, ","), result.Model, status, errCode, result.Used)
}

func normalizeQueryRewriteOptions(options QueryRewriteOptions) QueryRewriteOptions {
	if strings.TrimSpace(options.Model) == "" {
		options.Model = getenvDefaultKB("NEXUS_KB_QUERY_REWRITE_MODEL", defaultQueryRewriteModel)
	}
	if strings.TrimSpace(options.BaseURL) == "" {
		options.BaseURL = getenvDefaultKB("NEXUS_KB_QUERY_REWRITE_BASE_URL", defaultQueryRewriteBaseURL)
	}
	if strings.TrimSpace(options.APIKeyEnv) == "" {
		options.APIKeyEnv = getenvDefaultKB("NEXUS_KB_QUERY_REWRITE_API_KEY_ENV", defaultQueryRewriteAPIKeyEnv)
	}
	if options.TimeoutMS <= 0 {
		options.TimeoutMS = getenvIntDefaultKB("NEXUS_KB_QUERY_REWRITE_TIMEOUT_MS", defaultQueryRewriteTimeoutMS)
	}
	if options.MaxKeywords <= 0 {
		options.MaxKeywords = getenvIntDefaultKB("NEXUS_KB_QUERY_REWRITE_MAX_KEYWORDS", defaultQueryRewriteMaxKeywords)
	}
	return options
}

func queryRewriteEnabled(options QueryRewriteOptions) bool {
	if options.Enabled != nil {
		return *options.Enabled
	}
	value := strings.TrimSpace(strings.ToLower(os.Getenv("NEXUS_KB_QUERY_REWRITE_ENABLED")))
	if value == "" {
		return true
	}
	return value == "1" || value == "true" || value == "yes" || value == "on"
}

func (c openAIChatQueryRewriteClient) RewriteKnowledgeQuery(query string, options QueryRewriteOptions) (QueryRewriteResult, error) {
	apiKey := strings.TrimSpace(options.APIKey)
	if apiKey == "" && strings.TrimSpace(options.APIKeyEnv) != "" {
		apiKey = strings.TrimSpace(os.Getenv(options.APIKeyEnv))
	}
	if apiKey == "" {
		return QueryRewriteResult{}, fmt.Errorf("missing API key for query rewrite")
	}
	timeout := time.Duration(options.TimeoutMS) * time.Millisecond
	if timeout <= 0 {
		timeout = defaultQueryRewriteTimeoutMS * time.Millisecond
	}
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	body := map[string]any{
		"model":       options.Model,
		"temperature": 0,
		"max_tokens":  300,
		"messages": []map[string]string{
			{
				"role":    "system",
				"content": queryRewriteSystemPrompt(options.MaxKeywords),
			},
			{
				"role":    "user",
				"content": query,
			},
		},
	}
	data, err := json.Marshal(body)
	if err != nil {
		return QueryRewriteResult{}, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, joinURL(options.BaseURL, "/chat/completions"), bytes.NewReader(data))
	if err != nil {
		return QueryRewriteResult{}, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+apiKey)
	httpClient := c.httpClient
	if httpClient == nil {
		httpClient = &http.Client{}
	}
	resp, err := httpClient.Do(req)
	if err != nil {
		return QueryRewriteResult{}, err
	}
	defer resp.Body.Close()
	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return QueryRewriteResult{}, err
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return QueryRewriteResult{}, fmt.Errorf("query rewrite upstream status %d: %s", resp.StatusCode, truncateForError(string(respBody), 300))
	}
	text, err := chatCompletionText(respBody)
	if err != nil {
		return QueryRewriteResult{}, err
	}
	parsed, err := parseQueryRewriteJSON(text)
	if err != nil {
		return QueryRewriteResult{}, err
	}
	return QueryRewriteResult{
		EnglishQuery: strings.TrimSpace(parsed.EnglishQuery),
		Keywords:     parsed.Keywords,
		Model:        options.Model,
		Used:         true,
	}, nil
}

func queryRewriteSystemPrompt(maxKeywords int) string {
	if maxKeywords <= 0 {
		maxKeywords = defaultQueryRewriteMaxKeywords
	}
	return fmt.Sprintf(`You are a query rewriting assistant for a technical KnowledgeBase.

The user query is in Chinese. Generate English search keywords suitable for retrieving technical documents.

Rules:
- Do not answer the user's question.
- Do not explain.
- Prefer concise technical terms.
- Include game development, game engine, gameplay, backend, tooling, and agent-system synonyms when useful.
- Preserve product names, identifiers, file names, API names, and acronyms.
- Return strict JSON only.
- Limit keywords to %d items.

Return JSON:
{"english_query":"...","keywords":["...","..."]}`, maxKeywords)
}

func chatCompletionText(body []byte) (string, error) {
	var decoded struct {
		Choices []struct {
			Message struct {
				Content string `json:"content"`
			} `json:"message"`
		} `json:"choices"`
	}
	if err := json.Unmarshal(body, &decoded); err != nil {
		return "", err
	}
	if len(decoded.Choices) == 0 {
		return "", fmt.Errorf("query rewrite returned no choices")
	}
	text := strings.TrimSpace(decoded.Choices[0].Message.Content)
	if text == "" {
		return "", fmt.Errorf("query rewrite returned empty content")
	}
	return text, nil
}

func parseQueryRewriteJSON(text string) (queryRewriteLLMResponse, error) {
	text = strings.TrimSpace(text)
	text = strings.TrimPrefix(text, "```json")
	text = strings.TrimPrefix(text, "```")
	text = strings.TrimSuffix(text, "```")
	text = strings.TrimSpace(text)
	start := strings.Index(text, "{")
	end := strings.LastIndex(text, "}")
	if start >= 0 && end > start {
		text = text[start : end+1]
	}
	var parsed queryRewriteLLMResponse
	if err := json.Unmarshal([]byte(text), &parsed); err != nil {
		return queryRewriteLLMResponse{}, err
	}
	parsed.EnglishQuery = strings.TrimSpace(parsed.EnglishQuery)
	parsed.Keywords = uniqueStrings(parsed.Keywords)
	return parsed, nil
}

func mergeQueryTerms(groups ...[]string) []string {
	var merged []string
	for _, group := range groups {
		for _, term := range group {
			term = strings.TrimSpace(term)
			if term == "" {
				continue
			}
			merged = append(merged, term)
		}
	}
	return uniqueStrings(merged)
}

func termsFromQueryRewrite(rewrite QueryRewriteResult) []string {
	var terms []string
	terms = append(terms, expandQueryTerms(rewrite.EnglishQuery)...)
	for _, keyword := range rewrite.Keywords {
		terms = append(terms, expandQueryTerms(keyword)...)
	}
	return uniqueStrings(terms)
}

func uniqueStrings(values []string) []string {
	seen := map[string]bool{}
	var out []string
	for _, value := range values {
		value = strings.TrimSpace(value)
		if value == "" {
			continue
		}
		key := strings.ToLower(value)
		if seen[key] {
			continue
		}
		seen[key] = true
		out = append(out, value)
	}
	return out
}

func limitStrings(values []string, limit int) []string {
	if limit <= 0 || len(values) <= limit {
		return values
	}
	return values[:limit]
}

func joinURL(baseURL string, endpoint string) string {
	baseURL = strings.TrimRight(strings.TrimSpace(baseURL), "/")
	if strings.HasSuffix(baseURL, endpoint) {
		return baseURL
	}
	return baseURL + endpoint
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return strings.TrimSpace(value)
		}
	}
	return ""
}

func getenvDefaultKB(name string, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(name)); value != "" {
		return value
	}
	return fallback
}

func getenvIntDefaultKB(name string, fallback int) int {
	value := strings.TrimSpace(os.Getenv(name))
	if value == "" {
		return fallback
	}
	var parsed int
	if _, err := fmt.Sscanf(value, "%d", &parsed); err != nil || parsed <= 0 {
		return fallback
	}
	return parsed
}

func truncateForError(text string, max int) string {
	if len(text) <= max {
		return text
	}
	return text[:max]
}
