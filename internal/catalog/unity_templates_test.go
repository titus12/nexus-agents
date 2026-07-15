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
		filepath.Join(root, "templates", ".claude", "workflows", "wf-unity-bugfix.md"),
		filepath.Join(root, "templates", ".claude", "workflows", "wf-unity-logic-mod.md"),
		filepath.Join(root, "templates", ".claude", "workflows", "wf-unity-ui-feature.md"),
		filepath.Join(root, "templates", ".claude", "workflows", "unity-ui-quick.md"),
		filepath.Join(root, "templates", ".claude", "workflows", "go-bugfix.md"),
		filepath.Join(root, "templates", ".claude", "workflows", "go-feature-development.md"),
		filepath.Join(root, "templates", ".claude", "workflows", "go-code-review.md"),
		filepath.Join(root, "templates", ".agents", "skills", "wf-unity-bugfix", "SKILL.md"),
		filepath.Join(root, "templates", ".agents", "skills", "wf-unity-logic-mod", "SKILL.md"),
		filepath.Join(root, "templates", ".agents", "skills", "wf-unity-ui-feature", "SKILL.md"),
		filepath.Join(root, "templates", ".agents", "skills", "wf-unity-ui-quick", "SKILL.md"),
		filepath.Join(root, "templates", ".agents", "skills", "wf-go-bugfix", "SKILL.md"),
	}

	for _, file := range files {
		data, err := os.ReadFile(file)
		if err != nil {
			t.Fatalf("read %s: %v", file, err)
		}
		text := string(data)
		isGoWorkflow := strings.HasSuffix(file, "go-bugfix.md") || strings.HasSuffix(file, "go-feature-development.md") || strings.HasSuffix(file, "go-code-review.md")
		isUnityWorkflow := strings.Contains(filepath.ToSlash(file), "/templates/.claude/workflows/unity-")
		var required []string
		if isGoWorkflow {
			required = []string{
				"## Task Run Evidence Protocol",
				"## Nexus TaskRun Start Gate",
				"taskrun.mjs start",
				"taskrun.mjs submit",
				"sessionId",
				"workflowRunId",
				"$nexus-taskrun-submit",
			}
		} else if isUnityWorkflow {
			required = []string{
				"## Start Gate",
				"taskrun.mjs start",
				"POST http://127.0.0.1:8766/api/task-runs",
				"projectId",
				"workflowType",
				"submittedStatus",
				"context",
				"metrics",
				"evidence",
			}
		}
		for _, needle := range required {
			if !strings.Contains(text, needle) {
				t.Fatalf("expected %s to contain %q", file, needle)
			}
		}
		if isGoWorkflow {
			if !strings.Contains(text, "taskrun.mjs submit") {
				t.Fatalf("expected %s to mention local TaskRun submission automation", file)
			}
		} else if isUnityWorkflow && !strings.Contains(text, "submit-workflow-result.ps1") {
			t.Fatalf("expected %s to mention TaskRun submission automation", file)
		}
	}
}

func TestUnityUIQuickWorkflowIsPublishedWithSkillAndGraph(t *testing.T) {
	library := TemplateBootstrapData().TemplateLibrary

	workflows := map[string]TemplateItem{}
	for _, item := range library.Workflows {
		workflows[item.ID] = item
	}
	workflow, ok := workflows["ui-quick"]
	if !ok {
		t.Fatal("expected ui-quick workflow in the template catalog")
	}
	if workflow.Entry != "templates/.claude/workflows/unity-ui-quick.md" {
		t.Fatalf("unexpected ui-quick workflow entry: %q", workflow.Entry)
	}
	if len(workflow.Files) != 2 || workflow.Files[1] != "templates/.claude/workflows/unity-ui-quick.graph.json" {
		t.Fatalf("expected ui-quick markdown and graph template files: %#v", workflow.Files)
	}

	skills := map[string]TemplateItem{}
	for _, item := range library.Skills {
		skills[item.ID] = item
	}
	skill, ok := skills["wf-unity-ui-quick"]
	if !ok {
		t.Fatal("expected wf-unity-ui-quick skill in the template catalog")
	}
	if skill.Entry != "templates/.agents/skills/wf-unity-ui-quick/SKILL.md" {
		t.Fatalf("unexpected wf-unity-ui-quick skill entry: %q", skill.Entry)
	}
}

func TestUnityWorkflowTemplatePathsMatchSkillIDs(t *testing.T) {
	tests := map[string]string{
		"bug-investigation":      "templates/.claude/workflows/wf-unity-bugfix.md",
		"logic-modification":     "templates/.claude/workflows/wf-unity-logic-mod.md",
		"ui-feature-development": "templates/.claude/workflows/wf-unity-ui-feature.md",
	}

	for workflowID, wantPath := range tests {
		if gotPath := btdWorkflowTemplatePath(workflowID); gotPath != wantPath {
			t.Fatalf("workflow %q path = %q, want %q", workflowID, gotPath, wantPath)
		}
	}
}

func TestNexusTaskrunSubmitSkillIncludesHelperAssets(t *testing.T) {
	root := filepath.Join("..", "..")
	skillPath := filepath.Join(root, "templates", ".agents", "skills", "nexus-taskrun-submit", "SKILL.md")
	templatePath := filepath.Join(root, "templates", ".agents", "skills", "nexus-taskrun-submit", "task-run-template.json")
	helperPath := filepath.Join(root, "templates", ".agents", "skills", "nexus-taskrun-submit", "submit-workflow-result.ps1")
	startHelperPath := filepath.Join(root, "templates", ".agents", "skills", "nexus-taskrun-submit", "start-workflow-run.ps1")
	nodeHelperPath := filepath.Join(root, "templates", ".agents", "skills", "nexus-taskrun-submit", "taskrun.mjs")

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
	skillPath := filepath.Join(root, "templates", ".agents", "skills", "nexus-evaluation-review", "SKILL.md")
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

func TestKBSystemCuratorSkillIncludesCuratedResources(t *testing.T) {
	root := filepath.Join("..", "..")
	skillRoot := filepath.Join(root, "templates", ".agents", "skills", "kb-system-curator")
	for _, file := range []string{
		"SKILL.md",
		"agents/openai.yaml",
		"references/decomposition-rules.md",
		"references/entry-plan-template.md",
		"references/exploration-and-validation.md",
		"references/kb-gates.md",
		"references/okf-checklist.md",
	} {
		if _, err := os.Stat(filepath.Join(skillRoot, file)); err != nil {
			t.Fatalf("expected KB curator resource %s: %v", file, err)
		}
	}
	data, err := os.ReadFile(filepath.Join(skillRoot, "SKILL.md"))
	if err != nil {
		t.Fatalf("read KB curator skill: %v", err)
	}
	for _, needle := range []string{"name: kb-system-curator", "user confirms", "Knowledge Retrieval", "references/okf-checklist.md"} {
		if !strings.Contains(string(data), needle) {
			t.Fatalf("expected KB curator skill to contain %q", needle)
		}
	}
}

func TestNexusSupportSkillsArePublishedCatalogTemplates(t *testing.T) {
	library := TemplateBootstrapData().TemplateLibrary
	items := map[string]TemplateItem{}
	for _, item := range library.Skills {
		items[item.ID] = item
	}
	for _, id := range []string{"kb-system-curator", "kb-maintenance", "nexus-evaluation-review", "nexus-taskrun-submit"} {
		item, ok := items[id]
		if !ok {
			t.Fatalf("expected support skill %q in template catalog", id)
		}
		if !isCodexSkillTemplate(item) {
			t.Fatalf("expected support skill %q to be a Codex skill bundle: %#v", id, item)
		}
		if len(item.Files) < 1 || filepath.Base(item.Entry) != "SKILL.md" {
			t.Fatalf("expected support skill %q to declare bundle files: %#v", id, item)
		}
	}
	if len(items["nexus-taskrun-submit"].Files) < 5 {
		t.Fatalf("expected taskrun submit bundle resources in catalog: %#v", items["nexus-taskrun-submit"].Files)
	}
	if len(items["kb-system-curator"].Files) < 7 {
		t.Fatalf("expected KB curator bundle resources in catalog: %#v", items["kb-system-curator"].Files)
	}
	if len(items["kb-maintenance"].Files) < 2 {
		t.Fatalf("expected KB maintenance skill bundle resources in catalog: %#v", items["kb-maintenance"].Files)
	}
}
