package knowledgebase

import (
	"path/filepath"
	"testing"
)

func TestRoutePreviewMatchesUITask(t *testing.T) {
	root := t.TempDir()
	kb := filepath.Join(root, filepath.FromSlash(DefaultRoot))
	writeTestFile(t, filepath.Join(kb, "index.md"), okfDoc("Index", "Root", "KnowledgeBase/index.md", "# Root"))
	writeTestFile(t, filepath.Join(kb, "log.md"), okfDoc("Log", "Log", "KnowledgeBase/log.md", "# Log"))
	writeTestFile(t, filepath.Join(kb, "project", "routing.md"), okfDoc("Routing", "Project Routing", "KnowledgeBase/project/routing.md", "# Routing"))
	writeTestFile(t, filepath.Join(kb, "domains", "ui", "routing.md"), okfDoc("Routing", "UI Routing", "KnowledgeBase/domains/ui/routing.md", "# UI"))
	writeTestFile(t, filepath.Join(kb, "domains", "ui", "coding_rules.md"), okfDoc("CodingRules", "UI Rules", "KnowledgeBase/domains/ui/coding_rules.md", "# UI"))
	writeTestFile(t, filepath.Join(kb, "domains", "ui", "data_flow_rules.md"), okfDoc("Guide", "Data Flow", "KnowledgeBase/domains/ui/data_flow_rules.md", "# UI"))
	writeTestFile(t, filepath.Join(kb, "domains", "ui", "development_workflow.md"), okfDoc("Workflow", "Dev", "KnowledgeBase/domains/ui/development_workflow.md", "# UI"))

	preview, err := PreviewRoute(root, "新增活动奖励弹窗 UI")
	if err != nil {
		t.Fatal(err)
	}
	if preview.MatchedDomain != "ui" {
		t.Fatalf("expected ui domain, got %#v", preview)
	}
	if len(preview.RequiredFiles) == 0 || preview.RequiredFiles[0] != "KnowledgeBase/domains/ui/routing.md" {
		t.Fatalf("unexpected required files: %#v", preview.RequiredFiles)
	}
	if len(preview.MissingFiles) != 0 {
		t.Fatalf("expected no missing files, got %#v", preview.MissingFiles)
	}
}

func okfDoc(kind, title, resource, body string) string {
	return "---\ntype: " + kind + "\ntitle: " + title + "\ndescription: Test.\nresource: " + resource + "\ntags: [test]\ntimestamp: 2026-07-07T00:00:00+08:00\n---\n\n" + body
}
