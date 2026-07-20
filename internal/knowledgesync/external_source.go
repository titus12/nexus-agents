package knowledgesync

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const MaxExternalReferences = 10

type ExternalReference struct {
	URL  string `json:"url"`
	Kind string `json:"kind,omitempty"`
}

type ExternalSnapshot struct {
	Title     string   `json:"title"`
	SourceURL string   `json:"sourceUrl"`
	Revision  string   `json:"revision,omitempty"`
	UpdatedAt string   `json:"updatedAt,omitempty"`
	Markdown  string   `json:"markdown,omitempty"`
	Status    string   `json:"status"`
	Warnings  []string `json:"warnings"`
	LocalPath string   `json:"localPath,omitempty"`
}

type ExternalSourceClient interface {
	FetchMarkdown(ctx context.Context, sourceURL string) (ExternalSnapshot, error)
}

type HTTPExternalSourceClient struct {
	URL    string
	Token  string
	Client *http.Client
}

func NewEnvironmentExternalSourceClient() ExternalSourceClient {
	endpoint := strings.TrimSpace(os.Getenv("NEXUS_EXTERNAL_MARKDOWN_URL"))
	if endpoint == "" {
		return nil
	}
	return &HTTPExternalSourceClient{
		URL: endpoint, Token: strings.TrimSpace(os.Getenv("NEXUS_EXTERNAL_MARKDOWN_TOKEN")),
		Client: &http.Client{Timeout: 90 * time.Second},
	}
}

func (c *HTTPExternalSourceClient) FetchMarkdown(ctx context.Context, sourceURL string) (ExternalSnapshot, error) {
	body, err := json.Marshal(map[string]string{"url": sourceURL})
	if err != nil {
		return ExternalSnapshot{}, err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, c.URL, bytes.NewReader(body))
	if err != nil {
		return ExternalSnapshot{}, err
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "application/json")
	if c.Token != "" {
		request.Header.Set("Authorization", "Bearer "+c.Token)
	}
	client := c.Client
	if client == nil {
		client = &http.Client{Timeout: 90 * time.Second}
	}
	response, err := client.Do(request)
	if err != nil {
		return ExternalSnapshot{}, err
	}
	defer response.Body.Close()
	data, err := io.ReadAll(io.LimitReader(response.Body, 8*1024*1024))
	if err != nil {
		return ExternalSnapshot{}, err
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return ExternalSnapshot{}, fmt.Errorf("external Markdown converter returned status %d: %s", response.StatusCode, boundedText(string(data), 1000))
	}
	var snapshot ExternalSnapshot
	if err := json.Unmarshal(data, &snapshot); err != nil {
		return ExternalSnapshot{}, err
	}
	if strings.TrimSpace(snapshot.Title) == "" || strings.TrimSpace(snapshot.Markdown) == "" {
		return ExternalSnapshot{}, fmt.Errorf("external Markdown converter returned empty title or Markdown")
	}
	return snapshot, nil
}

func ProjectExternalReferences(ctx context.Context, workspaceRoot string, references []ExternalReference, client ExternalSourceClient) ([]ExternalSnapshot, error) {
	if len(references) > MaxExternalReferences {
		return nil, fmt.Errorf("external references exceed the %d item limit", MaxExternalReferences)
	}
	if len(references) == 0 {
		return []ExternalSnapshot{}, nil
	}
	if client == nil {
		return nil, fmt.Errorf("external source client is unavailable")
	}
	root := filepath.Join(workspaceRoot, ".nexus-external")
	if err := os.MkdirAll(root, 0o700); err != nil {
		return nil, err
	}
	snapshots := make([]ExternalSnapshot, 0, len(references))
	for index, reference := range references {
		parsed, err := url.Parse(strings.TrimSpace(reference.URL))
		if err != nil || parsed.Scheme != "https" || parsed.Host == "" {
			return nil, fmt.Errorf("external reference %d must be an https URL", index)
		}
		kind := strings.ToLower(strings.TrimSpace(reference.Kind))
		if kind != "" && kind != "feishu" {
			return nil, fmt.Errorf("external reference %d has unsupported kind %q", index, reference.Kind)
		}
		snapshot, err := client.FetchMarkdown(ctx, reference.URL)
		if err != nil {
			snapshots = append(snapshots, ExternalSnapshot{
				SourceURL: reference.URL, Status: "failed", Warnings: []string{boundedText(err.Error(), 500)},
			})
			continue
		}
		snapshot.SourceURL = reference.URL
		snapshot.Status = "ready"
		if snapshot.Warnings == nil {
			snapshot.Warnings = []string{}
		}
		name := fmt.Sprintf("%02d-%s.md", index+1, safeID(snapshot.Title))
		target := filepath.Join(root, name)
		header := fmt.Sprintf("# %s\n\n> Auxiliary initialization evidence. Code and repository contracts take precedence.\n\n", snapshot.Title)
		if err := os.WriteFile(target, []byte(header+snapshot.Markdown), 0o600); err != nil {
			return nil, err
		}
		snapshot.LocalPath = target
		snapshots = append(snapshots, snapshot)
	}
	return snapshots, nil
}

func RemoveExternalSnapshots(workspaceRoot string) error {
	return os.RemoveAll(filepath.Join(workspaceRoot, ".nexus-external"))
}

func externalSnapshotStamp() string {
	return time.Now().Format(time.RFC3339)
}
