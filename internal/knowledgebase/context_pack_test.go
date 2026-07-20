package knowledgebase

import (
	"strings"
	"testing"
)

func TestBuildContextPackLoadsApprovedContentAndProvenance(t *testing.T) {
	markdown, sources, items, omitted, budget := BuildContextPack([]ContextPackDocument{{
		KnowledgeSource: KnowledgeSource{
			ProjectID: "a1", SourceID: "project:a1",
			Path:  "KnowledgeBase/project/domains/guild/member.md",
			Title: "Guild Member", Revision: "rev-1", Score: 0.9,
		},
		Content: "# Guild Member\n\nApproved member lifecycle.",
	}}, 6000)
	if len(sources) != 1 || len(items) != 1 || len(omitted) != 0 {
		t.Fatalf("sources=%#v items=%#v omitted=%#v", sources, items, omitted)
	}
	for _, expected := range []string{
		"Project: `a1`", "Source: `project:a1`",
		"Revision: `rev-1`", "Approved member lifecycle.",
	} {
		if !strings.Contains(markdown, expected) {
			t.Fatalf("context pack missing %q:\n%s", expected, markdown)
		}
	}
	if budget.UsedTokens <= 0 || budget.UsedTokens > budget.MaxTokens {
		t.Fatalf("budget = %#v", budget)
	}
}

func TestBuildContextPackDeduplicatesSourceAndPath(t *testing.T) {
	document := ContextPackDocument{
		KnowledgeSource: KnowledgeSource{
			ProjectID: "a1", SourceID: "project:a1",
			Path: "KnowledgeBase/project/index.md", Title: "Project",
		},
		Content: "# Project\n\nApproved.",
	}
	_, sources, _, _, _ := BuildContextPack([]ContextPackDocument{document, document}, 6000)
	if len(sources) != 1 {
		t.Fatalf("sources = %#v", sources)
	}
}
