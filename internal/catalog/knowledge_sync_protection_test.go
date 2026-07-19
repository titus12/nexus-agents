package catalog

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestTemplateInitializationGitignorePreservesKnowledgeSettingsAndProject(t *testing.T) {
	target := t.TempDir()
	data, err := mergedTemplateInitializationGitignore(target)
	if err != nil {
		t.Fatal(err)
	}
	text := strings.ReplaceAll(string(data), "\r\n", "\n")
	for _, expected := range []string{
		"KnowledgeBase/*",
		"!KnowledgeBase/Setting.yaml",
		"!KnowledgeBase/project/",
		"!KnowledgeBase/project/**",
	} {
		if !strings.Contains(text, expected) {
			t.Fatalf("gitignore block does not contain %q:\n%s", expected, text)
		}
	}
}

func TestSynchronizeTemplateTreeProtectsKnowledgeSettingsAndProject(t *testing.T) {
	templateRoot := t.TempDir()
	projectRoot := t.TempDir()
	writeTestFile := func(root, relative, content string) {
		t.Helper()
		target := filepath.Join(root, filepath.FromSlash(relative))
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(target, []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	writeTestFile(templateRoot, "KnowledgeBase/Setting.yaml", "template-setting")
	writeTestFile(templateRoot, "KnowledgeBase/project/domain.md", "template-project")
	writeTestFile(templateRoot, "KnowledgeBase/framework/rules.md", "template-framework")
	writeTestFile(projectRoot, "KnowledgeBase/Setting.yaml", "project-setting")
	writeTestFile(projectRoot, "KnowledgeBase/project/domain.md", "project-owned")

	counts, err := synchronizeTemplateTree(templateRoot, projectRoot)
	if err != nil {
		t.Fatal(err)
	}
	if counts.skipped != 2 {
		t.Fatalf("skipped = %d, want 2", counts.skipped)
	}
	for relative, expected := range map[string]string{
		"KnowledgeBase/Setting.yaml":       "project-setting",
		"KnowledgeBase/project/domain.md":  "project-owned",
		"KnowledgeBase/framework/rules.md": "template-framework",
	} {
		data, err := os.ReadFile(filepath.Join(projectRoot, filepath.FromSlash(relative)))
		if err != nil {
			t.Fatal(err)
		}
		if string(data) != expected {
			t.Fatalf("%s = %q, want %q", relative, string(data), expected)
		}
	}
}
