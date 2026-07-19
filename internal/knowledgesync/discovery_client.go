package knowledgesync

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"
)

type HTTPDiscoveryClient struct {
	URL    string
	Model  string
	APIKey string
	Client *http.Client
}

func NewEnvironmentDiscoveryClient() DiscoveryClient {
	url := strings.TrimSpace(os.Getenv("NEXUS_KNOWLEDGE_DISCOVERY_URL"))
	model := strings.TrimSpace(os.Getenv("NEXUS_KNOWLEDGE_DISCOVERY_MODEL"))
	if url == "" || model == "" {
		return nil
	}
	return &HTTPDiscoveryClient{
		URL: url, Model: model, APIKey: strings.TrimSpace(os.Getenv("NEXUS_KNOWLEDGE_DISCOVERY_API_KEY")),
		Client: &http.Client{Timeout: 90 * time.Second},
	}
}

func (c *HTTPDiscoveryClient) ProposeScanPolicy(ctx context.Context, inventory RepositoryInventory) ([]byte, error) {
	if c == nil || strings.TrimSpace(c.URL) == "" || strings.TrimSpace(c.Model) == "" {
		return nil, fmt.Errorf("knowledge discovery model is not configured")
	}
	inventoryJSON, err := json.Marshal(inventory)
	if err != nil {
		return nil, err
	}
	prompt := strings.Join([]string{
		"You propose a safe repository scan policy for an AI-friendly engineering knowledge base.",
		"Return JSON only with this exact top-level shape:",
		`{"revision":"...","rules":[{"pattern":"...","action":"include|exclude","category":"...","priority":"...","reason":"...","confidence":0.0}],"requiredTopics":["..."],"uncertain":[{"path":"...","reason":"...","confidence":0.0}],"warnings":["..."]}`,
		"Use only project-relative slash-normalized patterns. Never include secrets, dependencies, build output, binaries, or generated artifacts.",
		"Prefer Git-tracked source, API/schema contracts, manifests, repository docs, tests that explain behavior, and existing approved knowledge.",
		"Repository inventory:",
		string(inventoryJSON),
	}, "\n\n")
	body, err := json.Marshal(map[string]any{
		"model": c.Model,
		"messages": []map[string]string{
			{"role": "system", "content": "You are a repository knowledge scan-policy planner. Output strict JSON only."},
			{"role": "user", "content": prompt},
		},
		"temperature":     0,
		"response_format": map[string]string{"type": "json_object"},
	})
	if err != nil {
		return nil, err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, c.URL, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "application/json")
	if c.APIKey != "" {
		request.Header.Set("Authorization", "Bearer "+c.APIKey)
	}
	client := c.Client
	if client == nil {
		client = &http.Client{Timeout: 90 * time.Second}
	}
	response, err := client.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	responseBody, err := io.ReadAll(io.LimitReader(response.Body, 2*1024*1024))
	if err != nil {
		return nil, err
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, fmt.Errorf("discovery model returned status %d: %s", response.StatusCode, boundedText(string(responseBody), 1000))
	}
	var envelope struct {
		Choices []struct {
			Message struct {
				Content string `json:"content"`
			} `json:"message"`
		} `json:"choices"`
		OutputText string `json:"output_text"`
	}
	if err := json.Unmarshal(responseBody, &envelope); err != nil {
		return nil, fmt.Errorf("decode discovery model response: %w", err)
	}
	if len(envelope.Choices) > 0 && strings.TrimSpace(envelope.Choices[0].Message.Content) != "" {
		return []byte(strings.TrimSpace(envelope.Choices[0].Message.Content)), nil
	}
	if strings.TrimSpace(envelope.OutputText) != "" {
		return []byte(strings.TrimSpace(envelope.OutputText)), nil
	}
	return nil, fmt.Errorf("discovery model response did not contain JSON content")
}
