package knowledgebase

import (
	"fmt"
	"reflect"
	"testing"
)

type mockQueryRewriteClient struct {
	result QueryRewriteResult
	err    error
	calls  int
}

func (m *mockQueryRewriteClient) RewriteKnowledgeQuery(query string, options QueryRewriteOptions) (QueryRewriteResult, error) {
	m.calls++
	if m.err != nil {
		return QueryRewriteResult{}, m.err
	}
	return m.result, nil
}

func TestContainsCJK(t *testing.T) {
	if !containsCJK("角色移动卡顿") {
		t.Fatal("expected Chinese text to contain CJK")
	}
	if containsCJK("character movement stutter") {
		t.Fatal("expected English text not to contain CJK")
	}
}

func TestRewriteKnowledgeQueryUsesClientForChineseQuery(t *testing.T) {
	client := &mockQueryRewriteClient{result: QueryRewriteResult{
		EnglishQuery: "character movement stutter",
		Keywords:     []string{"character", "movement", "stutter", "movement"},
	}}
	result := rewriteKnowledgeQuery("角色移动卡顿", QueryRewriteOptions{Client: client, Model: "deepseek-v4-flash", MaxKeywords: 3})
	if client.calls != 1 {
		t.Fatalf("expected one rewrite call, got %d", client.calls)
	}
	if !result.Triggered || !result.Used {
		t.Fatalf("expected triggered and used rewrite, got %#v", result)
	}
	if result.Model != "deepseek-v4-flash" {
		t.Fatalf("expected model metadata, got %#v", result)
	}
	expectedKeywords := []string{"character", "movement", "stutter"}
	if !reflect.DeepEqual(result.Keywords, expectedKeywords) {
		t.Fatalf("expected deduped/limited keywords %#v, got %#v", expectedKeywords, result.Keywords)
	}
}

func TestRewriteKnowledgeQuerySkipsEnglishQuery(t *testing.T) {
	client := &mockQueryRewriteClient{}
	result := rewriteKnowledgeQuery("character movement stutter", QueryRewriteOptions{Client: client})
	if client.calls != 0 {
		t.Fatalf("expected no rewrite call, got %d", client.calls)
	}
	if result.Triggered || result.Used {
		t.Fatalf("expected rewrite to be skipped, got %#v", result)
	}
}

func TestRewriteKnowledgeQueryFallsBackOnClientError(t *testing.T) {
	client := &mockQueryRewriteClient{err: fmt.Errorf("upstream timeout")}
	result := rewriteKnowledgeQuery("角色移动卡顿", QueryRewriteOptions{Client: client})
	if client.calls != 1 {
		t.Fatalf("expected one rewrite call, got %d", client.calls)
	}
	if !result.Triggered || result.Used {
		t.Fatalf("expected triggered but unused fallback, got %#v", result)
	}
	if result.Error != "upstream timeout" {
		t.Fatalf("expected error metadata, got %#v", result)
	}
}

func TestTermsFromQueryRewriteSplitsEnglishQueryAndKeywords(t *testing.T) {
	terms := termsFromQueryRewrite(QueryRewriteResult{
		EnglishQuery: "character movement stutter",
		Keywords:     []string{"animation event", "movement"},
	})
	expected := []string{"character", "movement", "stutter", "animation", "event"}
	if !reflect.DeepEqual(terms, expected) {
		t.Fatalf("expected %#v, got %#v", expected, terms)
	}
}

func TestParseQueryRewriteJSONAcceptsFencedJSON(t *testing.T) {
	parsed, err := parseQueryRewriteJSON("```json\n{\"english_query\":\"combat sync\",\"keywords\":[\"combat\",\"sync\",\"combat\"]}\n```")
	if err != nil {
		t.Fatal(err)
	}
	if parsed.EnglishQuery != "combat sync" {
		t.Fatalf("unexpected english query: %#v", parsed)
	}
	expected := []string{"combat", "sync"}
	if !reflect.DeepEqual(parsed.Keywords, expected) {
		t.Fatalf("expected deduped keywords %#v, got %#v", expected, parsed.Keywords)
	}
}
