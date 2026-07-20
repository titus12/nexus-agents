package knowledgesync

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestHTTPDiscoveryClientReturnsStructuredContent(t *testing.T) {
	var authorization string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		authorization = r.Header.Get("Authorization")
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"choices":[{"message":{"content":"{\"revision\":\"abc\",\"rules\":[],\"requiredTopics\":[],\"uncertain\":[],\"warnings\":[]}"}}]}`))
	}))
	defer server.Close()
	client := &HTTPDiscoveryClient{URL: server.URL, Model: "test", APIKey: "secret", Client: server.Client()}
	data, err := client.ProposeScanPolicy(context.Background(), RepositoryInventory{Revision: "abc"})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(data), `"revision":"abc"`) {
		t.Fatalf("content = %s", data)
	}
	if authorization != "Bearer secret" {
		t.Fatalf("authorization header = %q", authorization)
	}
}

func TestHTTPExternalSourceClientUsesRunScopedConverter(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"title":"Feishu design","revision":"v2","markdown":"# Design\n\nIntent only."}`))
	}))
	defer server.Close()
	client := &HTTPExternalSourceClient{URL: server.URL, Client: server.Client()}
	snapshot, err := client.FetchMarkdown(context.Background(), "https://example.feishu.cn/docx/abc")
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.Title != "Feishu design" || snapshot.Markdown == "" {
		t.Fatalf("snapshot = %#v", snapshot)
	}
}
