package catalog

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestUnityWorkflowTemplatesIncludeTaskRunProtocol(t *testing.T) {
	root := filepath.Join("..", "..")
	files := []string{
		filepath.Join(root, "templates", "workflows", "unity-bug-investigation.md"),
		filepath.Join(root, "templates", "workflows", "unity-logic-modification.md"),
		filepath.Join(root, "templates", "workflows", "unity-ui-feature-development.md"),
		filepath.Join(root, "templates", "workflows", "go-bugfix.md"),
		filepath.Join(root, "templates", "workflows", "go-feature-development.md"),
		filepath.Join(root, "templates", "skills", "codex", "wf-unity-bugfix", "SKILL.md"),
		filepath.Join(root, "templates", "skills", "codex", "wf-unity-logic-mod", "SKILL.md"),
		filepath.Join(root, "templates", "skills", "codex", "wf-unity-ui-feature", "SKILL.md"),
		filepath.Join(root, "templates", "skills", "codex", "wf-go-bugfix", "SKILL.md"),
	}

	for _, file := range files {
		data, err := os.ReadFile(file)
		if err != nil {
			t.Fatalf("read %s: %v", file, err)
		}
		text := string(data)
		required := []string{
			"## Task Run Evidence Protocol",
			"POST http://127.0.0.1:8766/api/task-runs",
			"\"projectId\"",
			"\"workflowType\"",
			"\"submittedStatus\"",
			"\"context\"",
			"\"metrics\"",
			"\"evidence\"",
		}
		if strings.HasSuffix(file, "go-bugfix.md") || strings.HasSuffix(file, "go-feature-development.md") {
			required = append(required,
				"## Nexus TaskRun Start Gate",
				"taskrun.mjs start",
				"\"sessionId\"",
				"\"changedFiles\"",
				"workflowRunId",
			)
		} else {
			required = append(required, "\"workflowTemplateId\"")
		}
		for _, needle := range required {
			if !strings.Contains(text, needle) {
				t.Fatalf("expected %s to contain %q", file, needle)
			}
		}
		if strings.HasSuffix(file, "go-bugfix.md") || strings.HasSuffix(file, "go-feature-development.md") {
			if !strings.Contains(text, "taskrun.mjs submit") {
				t.Fatalf("expected %s to mention local TaskRun submission automation", file)
			}
		} else if !strings.Contains(text, "submit-task-run") {
			t.Fatalf("expected %s to mention submit-task-run automation", file)
		}
	}
}

func TestNexusTaskrunSubmitSkillIncludesHelperAssets(t *testing.T) {
	root := filepath.Join("..", "..")
	skillPath := filepath.Join(root, "templates", "skills", "codex", "nexus-taskrun-submit", "SKILL.md")
	templatePath := filepath.Join(root, "templates", "skills", "codex", "nexus-taskrun-submit", "task-run-template.json")
	helperPath := filepath.Join(root, "templates", "skills", "codex", "nexus-taskrun-submit", "submit-workflow-result.ps1")
	startHelperPath := filepath.Join(root, "templates", "skills", "codex", "nexus-taskrun-submit", "start-workflow-run.ps1")
	nodeHelperPath := filepath.Join(root, "templates", "skills", "codex", "nexus-taskrun-submit", "taskrun.mjs")

	for _, file := range []string{skillPath, templatePath, helperPath, startHelperPath, nodeHelperPath} {
		if _, err := os.Stat(file); err != nil {
			t.Fatalf("expected helper asset %s: %v", file, err)
		}
	}
	data, err := os.ReadFile(skillPath)
	if err != nil {
		t.Fatalf("read %s: %v", skillPath, err)
	}
	text := string(data)
	for _, needle := range []string{"task-run-template.json", "start-workflow-run.ps1", "submit-workflow-result.ps1", "taskrun.mjs", "sessionId"} {
		if !strings.Contains(text, needle) {
			t.Fatalf("expected %s to contain %q", skillPath, needle)
		}
	}
}

func TestNexusEvaluationReviewSkillExists(t *testing.T) {
	root := filepath.Join("..", "..")
	skillPath := filepath.Join(root, "templates", "skills", "codex", "nexus-evaluation-review", "SKILL.md")
	data, err := os.ReadFile(skillPath)
	if err != nil {
		t.Fatalf("read %s: %v", skillPath, err)
	}
	text := string(data)
	for _, needle := range []string{
		"name: nexus-evaluation-review",
		"/api/evaluations/summary",
		"/api/evaluation/projects",
		"/api/evaluations",
		"/api/learning-cases",
		"/api/statistics/tasks",
		"objective",
		"evidence-first",
		"Review target",
		"Objective diagnosis",
	} {
		if !strings.Contains(text, needle) {
			t.Fatalf("expected %s to contain %q", skillPath, needle)
		}
	}
}
