package knowledgebase

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestExportProjectKnowledgeWritesManifestAndDocs(t *testing.T) {
	root := t.TempDir()
	kb := filepath.Join(root, filepath.FromSlash(DefaultRoot))
	writeTestFile(t, filepath.Join(kb, "index.md"), okfDoc("Index", "Root", "design/KnowledgeBase/index.md", "# Root"))
	writeTestFile(t, filepath.Join(kb, "log.md"), okfDoc("Log", "Log", "design/KnowledgeBase/log.md", "# Log"))
	exportRoot := filepath.Join(t.TempDir(), "export")
	manifest, err := ExportProjectKnowledge("btd-client", root, exportRoot)
	if err != nil {
		t.Fatal(err)
	}
	if manifest.DocumentCount != 2 || manifest.SourceHash == "" {
		t.Fatalf("unexpected manifest: %#v", manifest)
	}
	if _, err := os.Stat(filepath.Join(exportRoot, "manifest.json")); err != nil {
		t.Fatalf("manifest not written: %v", err)
	}
	if _, err := os.Stat(filepath.Join(exportRoot, "docs", "design_KnowledgeBase_index.md.json")); err != nil {
		t.Fatalf("rendered doc not written: %v", err)
	}
}

func TestReadFreshExportRebuildsWhenSourceChanges(t *testing.T) {
	root := t.TempDir()
	kb := filepath.Join(root, filepath.FromSlash(DefaultRoot))
	writeTestFile(t, filepath.Join(kb, "index.md"), okfDoc("Index", "Root", "design/KnowledgeBase/index.md", "# Root"))
	writeTestFile(t, filepath.Join(kb, "log.md"), okfDoc("Log", "Log", "design/KnowledgeBase/log.md", "# Log"))
	exportRoot := filepath.Join(t.TempDir(), "export")
	first, err := ExportProjectKnowledge("sample", root, exportRoot)
	if err != nil {
		t.Fatal(err)
	}
	writeTestFile(t, filepath.Join(kb, "domains", "ui", "routing.md"), okfDoc("Routing", "UI", "design/KnowledgeBase/domains/ui/routing.md", "# UI"))

	data, err := ReadFreshExport("sample", root, exportRoot)
	if err != nil {
		t.Fatal(err)
	}
	if data.Manifest.SourceHash == first.SourceHash {
		t.Fatalf("expected source hash to change after source update")
	}
	if data.Manifest.DocumentCount != 3 {
		t.Fatalf("expected rebuilt export to include new document: %#v", data.Manifest)
	}
	rawTree, err := os.ReadFile(filepath.Join(exportRoot, "tree.json"))
	if err != nil {
		t.Fatal(err)
	}
	var tree RenderTree
	if err := json.Unmarshal(rawTree, &tree); err != nil {
		t.Fatal(err)
	}
	if len(tree.Nodes) == 0 || tree.Nodes[0].Kind == "" {
		t.Fatalf("expected rebuilt tree with kiso-like node metadata: %#v", tree)
	}
}
