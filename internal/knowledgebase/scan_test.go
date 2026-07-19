package knowledgebase

import (
	"os"
	"path/filepath"
	"testing"
)

func TestScanBundleReadsMarkdownAndLinks(t *testing.T) {
	root := t.TempDir()
	kb := filepath.Join(root, filepath.FromSlash(DefaultRoot))
	writeTestFile(t, filepath.Join(kb, "index.md"), "---\ntype: Index\ntitle: Knowledge Index\ndescription: Root.\nresource: KnowledgeBase/index.md\ntags: [index]\ntimestamp: 2026-07-07T00:00:00+08:00\n---\n\n[Actor](./domains/actor/index.md)")
	writeTestFile(t, filepath.Join(kb, "domains", "actor", "index.md"), "---\ntype: Domain\ntitle: Actor\ndescription: Actor.\nresource: KnowledgeBase/domains/actor/index.md\ntags: [actor]\ntimestamp: 2026-07-07T00:00:00+08:00\n---\n\n# Actor")

	bundle, err := ScanBundle(root)
	if err != nil {
		t.Fatal(err)
	}
	if !bundle.Exists || len(bundle.Documents) != 2 {
		t.Fatalf("unexpected bundle: %#v", bundle)
	}
	for _, doc := range bundle.Documents {
		if doc.Path == "KnowledgeBase/domains/actor/index.md" && doc.Reserved {
			t.Fatal("domain index must remain a typed Domain document")
		}
	}
	totalLinks := 0
	for _, doc := range bundle.Documents {
		totalLinks += len(doc.Links)
	}
	if totalLinks != 1 {
		t.Fatalf("expected one link, got %#v", bundle.Documents)
	}
}

func writeTestFile(t *testing.T, path string, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}
