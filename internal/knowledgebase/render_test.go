package knowledgebase

import (
	"path/filepath"
	"strings"
	"testing"
)

func TestRenderTreeUsesFrontmatterTitles(t *testing.T) {
	root := t.TempDir()
	kb := filepath.Join(root, filepath.FromSlash(DefaultRoot))
	writeTestFile(t, filepath.Join(kb, "index.md"), okfDoc("Index", "Root KB", "design/KnowledgeBase/index.md", "# Root"))
	writeTestFile(t, filepath.Join(kb, "domains", "ui", "routing.md"), okfDoc("Routing", "UI Routing", "design/KnowledgeBase/domains/ui/routing.md", "# UI"))
	tree, err := BuildRenderTree(root)
	if err != nil {
		t.Fatal(err)
	}
	if len(tree.Nodes) != 2 || tree.Nodes[1].Title != "Root KB" || tree.Nodes[1].Kind != "document" {
		t.Fatalf("unexpected tree: %#v", tree)
	}
	domains := tree.Nodes[0]
	if domains.Kind != "directory" || domains.Path != "design/KnowledgeBase/domains" || len(domains.Children) != 1 {
		t.Fatalf("expected hierarchical domains tree, got %#v", tree)
	}
}

func TestRenderDocumentEscapesHTMLAndRendersHeadings(t *testing.T) {
	root := t.TempDir()
	kb := filepath.Join(root, filepath.FromSlash(DefaultRoot))
	writeTestFile(t, filepath.Join(kb, "index.md"), okfDoc("Index", "Root", "design/KnowledgeBase/index.md", "# Heading\n\n<script>alert(1)</script>"))
	doc, err := RenderDocument(root, "design/KnowledgeBase/index.md")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(doc.HTML, "<h1>Heading</h1>") {
		t.Fatalf("heading not rendered: %s", doc.HTML)
	}
	if strings.Contains(doc.HTML, "<script>") {
		t.Fatalf("html was not escaped: %s", doc.HTML)
	}
}

func TestRenderDocumentRendersTablesLinksAndLists(t *testing.T) {
	root := t.TempDir()
	kb := filepath.Join(root, filepath.FromSlash(DefaultRoot))
	body := "# Routing\n\n[UI](./domains/ui/README.md)\n\n1. Open routing\n2. Read rules\n\n| Task | File |\n|---|---|\n| UI | `routing.md` |\n"
	writeTestFile(t, filepath.Join(kb, "index.md"), okfDoc("Index", "Root", "design/KnowledgeBase/index.md", body))
	doc, err := RenderDocument(root, "design/KnowledgeBase/index.md")
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"<table>", "<ol>", `data-kb-link="./domains/ui/README.md"`, "<code>routing.md</code>"} {
		if !strings.Contains(doc.HTML, want) {
			t.Fatalf("expected %s in rendered html: %s", want, doc.HTML)
		}
	}
}
