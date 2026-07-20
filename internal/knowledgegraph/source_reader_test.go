package knowledgegraph

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestServiceSearchAndLoadSearchDocuments(t *testing.T) {
	root := t.TempDir()
	exportRoot := func(projectID string) string { return filepath.Join(root, projectID) }
	projectRoot := t.TempDir()
	writeSourceReaderTestFile(t, projectRoot, "KnowledgeBase/index.md", "# KnowledgeBase\n\n[Project](project/index.md)\n")
	writeSourceReaderTestFile(t, projectRoot, "KnowledgeBase/log.md", "# Log\n\nInitial knowledge log entry.\n")
	writeSourceReaderTestFile(t, projectRoot, "KnowledgeBase/project/index.md", "# Sample Project\n\nApproved project knowledge.\n")
	exported, err := ExportApprovedKnowledge(ExportRequest{
		ProjectID: "sample", ProjectRoot: projectRoot,
		SourceID: "project:sample", ProviderSourceID: "project-sample",
		Revision: "rev-1", ExportRoot: exportRoot("sample"),
		IncludeDomains: true, IncludeFeatures: true,
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(exported.Documents) != 1 {
		t.Fatalf("exported documents = %#v", exported.Documents)
	}

	provider := &FakeProvider{SearchResult: GraphSearchResult{Hits: []GraphSearchHit{{
		SourceID: "project:sample", Path: "project", Title: "Sample Project",
		Snippet: "Approved project knowledge.", Score: 0.9,
	}}}}
	service := NewService(ServiceOptions{Provider: provider, ExportRoot: exportRoot})
	search, err := service.Search(context.Background(), GraphSearchQuery{
		Query: "sample", SourceIDs: []string{"project:sample"}, Limit: 5,
	})
	if err != nil {
		t.Fatal(err)
	}
	documents, err := service.LoadSearchDocuments([]string{"sample"}, search.Hits)
	if err != nil {
		t.Fatal(err)
	}
	if len(documents) != 1 ||
		documents[0].CanonicalPath != "KnowledgeBase/project/index.md" ||
		documents[0].Revision != "rev-1" ||
		!strings.Contains(documents[0].Content, "Approved project knowledge") {
		t.Fatalf("documents = %#v", documents)
	}
}

func TestLoadSearchDocumentsRejectsEscapingManifestPath(t *testing.T) {
	root := t.TempDir()
	exportRoot := func(projectID string) string { return filepath.Join(root, projectID) }
	projectExport := exportRoot("sample")
	if err := os.MkdirAll(projectExport, 0o755); err != nil {
		t.Fatal(err)
	}
	writeSourceReaderTestFile(t, projectExport, "manifest.json", `{
  "schemaVersion": 1,
  "projectId": "sample",
  "sourceId": "project:sample",
  "providerSourceId": "project-sample",
  "documents": [{
    "slug": "project",
    "canonicalPath": "KnowledgeBase/project/index.md",
    "exportPath": "../outside.md"
  }]
}`)
	service := NewService(ServiceOptions{ExportRoot: exportRoot})
	_, err := service.LoadSearchDocuments([]string{"sample"}, []GraphSearchHit{{
		SourceID: "project:sample", Path: "project",
	}})
	if err == nil || !strings.Contains(err.Error(), "escapes export root") {
		t.Fatalf("error = %v", err)
	}
}

func writeSourceReaderTestFile(t *testing.T, root, relative, content string) {
	t.Helper()
	target := filepath.Join(root, filepath.FromSlash(relative))
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(target, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}
