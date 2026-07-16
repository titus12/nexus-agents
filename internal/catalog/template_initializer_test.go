package catalog

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestPreviewTemplateInitializationProtectsKnowledgeProject(t *testing.T) {
	target := t.TempDir()

	preview, err := PreviewTemplateInitialization(TemplateInitializationInput{
		TargetPath:  target,
		ProjectType: TemplateProjectTypeGo,
	})
	if err != nil {
		t.Fatalf("preview template initialization: %v", err)
	}
	if preview.Summary.Create == 0 {
		t.Fatalf("expected create entries, got %#v", preview.Summary)
	}
	if preview.Summary.Protected != 1 {
		t.Fatalf("expected one protected KnowledgeBase/project entry, got %#v", preview.Summary)
	}
	assertInitializationAction(t, preview, "AGENTS.md", "create")
	assertInitializationAction(t, preview, "KnowledgeBase/project/", "protected")
	for _, write := range preview.Writes {
		if write.RelativePath == "KnowledgeBase/project/.gitkeep" {
			t.Fatalf("protected project content must not be included: %#v", write)
		}
	}
}

func TestApplyTemplateInitializationCreatesOnlyMissingFiles(t *testing.T) {
	target := t.TempDir()
	protectedFile := filepath.Join(target, "KnowledgeBase", "project", "local.md")
	if err := os.MkdirAll(filepath.Dir(protectedFile), 0o755); err != nil {
		t.Fatalf("create protected directory: %v", err)
	}
	if err := os.WriteFile(protectedFile, []byte("project knowledge"), 0o644); err != nil {
		t.Fatalf("write protected file: %v", err)
	}
	existingAgents := filepath.Join(target, "AGENTS.md")
	if err := os.WriteFile(existingAgents, []byte("project-specific guide"), 0o644); err != nil {
		t.Fatalf("write existing AGENTS.md: %v", err)
	}

	result, err := ApplyTemplateInitialization(TemplateInitializationInput{
		TargetPath:  target,
		ProjectType: TemplateProjectTypeUnity,
	})
	if err != nil {
		t.Fatalf("apply template initialization: %v", err)
	}
	assertInitializationAction(t, TemplateInitializationPreview{Writes: result.Writes}, "AGENTS.md", "conflict")
	assertInitializationAction(t, TemplateInitializationPreview{Writes: result.Writes}, "KnowledgeBase/project/", "protected")
	if data, err := os.ReadFile(existingAgents); err != nil || string(data) != "project-specific guide" {
		t.Fatalf("AGENTS.md should remain untouched, data=%q err=%v", data, err)
	}
	if data, err := os.ReadFile(protectedFile); err != nil || string(data) != "project knowledge" {
		t.Fatalf("KnowledgeBase/project must remain untouched, data=%q err=%v", data, err)
	}
	if _, err := os.Stat(filepath.Join(target, ".claude", "agents", "unity-debugger.md")); err != nil {
		t.Fatalf("expected Unity template output: %v", err)
	}
	if _, err := os.Stat(filepath.Join(target, ".claude", "agents", "go-debugger.md")); !os.IsNotExist(err) {
		t.Fatalf("Go output must not be included for Unity profile, err=%v", err)
	}
}

func TestApplyTemplateInitializationCreatesMissingTargetDirectory(t *testing.T) {
	target := filepath.Join(t.TempDir(), "new-project")
	result, err := ApplyTemplateInitialization(TemplateInitializationInput{
		TargetPath:  target,
		ProjectType: TemplateProjectTypeGeneral,
	})
	if err != nil {
		t.Fatalf("apply template initialization to new directory: %v", err)
	}
	if result.Summary.Create == 0 {
		t.Fatalf("expected files to be created, got %#v", result)
	}
	if _, err := os.Stat(filepath.Join(target, "AGENTS.md")); err != nil {
		t.Fatalf("expected initialized AGENTS.md: %v", err)
	}
}

func TestInitializeProjectTemplatesIncludesTestDrivenChangePolicy(t *testing.T) {
	for _, projectType := range []string{
		TemplateProjectTypeGeneral,
		TemplateProjectTypeGo,
		TemplateProjectTypeUnity,
	} {
		t.Run(projectType, func(t *testing.T) {
			target := t.TempDir()

			if _, err := ApplyTemplateInitialization(TemplateInitializationInput{
				TargetPath:  target,
				ProjectType: projectType,
			}); err != nil {
				t.Fatalf("apply template initialization: %v", err)
			}

			rule, err := os.ReadFile(filepath.Join(target, ".claude", "rules", "test-driven-change.md"))
			if err != nil {
				t.Fatalf("read test-driven change rule: %v", err)
			}
			if !strings.Contains(string(rule), "## Test Design Decision") {
				t.Fatalf("expected test-design rule, got %q", string(rule))
			}

			agents, err := os.ReadFile(filepath.Join(target, "AGENTS.md"))
			if err != nil {
				t.Fatalf("read AGENTS.md: %v", err)
			}
			if !strings.Contains(string(agents), "## Test-Driven Change Baseline") {
				t.Fatalf("expected TDD baseline in AGENTS.md, got %q", string(agents))
			}
		})
	}
}

func TestApplyTemplateInitializationMergesGitignoreAndKeepsProjectKnowledgeTracked(t *testing.T) {
	target := t.TempDir()
	gitignorePath := filepath.Join(target, ".gitignore")
	if err := os.WriteFile(gitignorePath, []byte("bin/\n"), 0o644); err != nil {
		t.Fatalf("write existing .gitignore: %v", err)
	}

	result, err := ApplyTemplateInitialization(TemplateInitializationInput{
		TargetPath:  target,
		ProjectType: TemplateProjectTypeGeneral,
	})
	if err != nil {
		t.Fatalf("apply template initialization: %v", err)
	}
	if result.Summary.Update != 1 {
		t.Fatalf("expected one .gitignore update, got %#v", result.Summary)
	}
	data, err := os.ReadFile(gitignorePath)
	if err != nil {
		t.Fatalf("read merged .gitignore: %v", err)
	}
	text := string(data)
	for _, expected := range []string{
		"bin/",
		"AGENTS.md",
		"KnowledgeBase/*",
		"!KnowledgeBase/project/",
		"!KnowledgeBase/project/**",
	} {
		if !strings.Contains(text, expected) {
			t.Fatalf("expected .gitignore to contain %q, got %s", expected, text)
		}
	}

	preview, err := PreviewTemplateInitialization(TemplateInitializationInput{
		TargetPath:  target,
		ProjectType: TemplateProjectTypeGeneral,
	})
	if err != nil {
		t.Fatalf("preview after .gitignore update: %v", err)
	}
	assertInitializationAction(t, preview, ".gitignore", "unchanged")
}

func assertInitializationAction(t *testing.T, preview TemplateInitializationPreview, relativePath string, action string) {
	t.Helper()
	for _, write := range preview.Writes {
		if write.RelativePath == relativePath {
			if write.Action != action {
				t.Fatalf("expected %s action %s, got %#v", relativePath, action, write)
			}
			return
		}
	}
	t.Fatalf("missing initialization entry %s", relativePath)
}
