package catalog

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestDotNetWorkflowTemplatesExistAndUseTaskRunProtocol(t *testing.T) {
	root := filepath.Join("..", "..")
	workflows := []struct {
		id    string
		owner string
	}{
		{id: "dotnet-feature-development", owner: "dotnet-developer"},
		{id: "dotnet-bugfix", owner: "dotnet-debugger"},
		{id: "dotnet-code-review", owner: "dotnet-reviewer"},
	}

	for _, workflow := range workflows {
		markdownPath := filepath.Join(root, "templates", ".claude", "workflows", workflow.id+".md")
		graphPath := filepath.Join(root, "templates", ".claude", "workflows", workflow.id+".graph.json")
		for _, path := range []string{markdownPath, graphPath} {
			if _, err := os.Stat(path); err != nil {
				t.Fatalf("expected .NET workflow asset %s: %v", path, err)
			}
		}

		markdown, err := os.ReadFile(markdownPath)
		if err != nil {
			t.Fatalf("read .NET workflow %s: %v", markdownPath, err)
		}
		for _, needle := range []string{
			"dotnet-00-routing.md",
			workflow.id,
			"nexus-taskrun-submit",
		} {
			if !strings.Contains(string(markdown), needle) {
				t.Fatalf("expected %s to contain %q", markdownPath, needle)
			}
		}

		graph, err := os.ReadFile(graphPath)
		if err != nil {
			t.Fatalf("read .NET workflow graph %s: %v", graphPath, err)
		}
		for _, needle := range []string{`"id": "` + workflow.id + `"`, `"agent": "` + workflow.owner + `"`} {
			if !strings.Contains(string(graph), needle) {
				t.Fatalf("expected %s to contain %q", graphPath, needle)
			}
		}
	}
}

func TestDotNetTemplateCatalogPaths(t *testing.T) {
	library := TemplateBootstrapData().TemplateLibrary
	agents := dotNetTemplateItemsByID(library.Agents)
	rules := dotNetTemplateItemsByID(library.Rules)
	skills := dotNetTemplateItemsByID(library.Skills)
	workflows := dotNetTemplateItemsByID(library.Workflows)

	for _, id := range []string{"dotnet-developer", "dotnet-debugger", "dotnet-reviewer"} {
		item, ok := agents[id]
		if !ok {
			t.Fatalf("expected .NET agent %q in catalog", id)
		}
		for _, path := range []string{
			"templates/.claude/agents/" + id + ".md",
			"templates/.codex/agents/" + id + ".toml",
		} {
			if !containsTemplatePath(item.SourcePaths, path) {
				t.Fatalf("expected .NET agent %q to contain source path %q: %#v", id, path, item.SourcePaths)
			}
		}
	}
	for _, id := range []string{
		"dotnet-00-routing",
		"dotnet-01-project-model",
		"dotnet-02-runtime-safety",
		"dotnet-03-library-compatibility",
	} {
		if _, ok := rules[id]; !ok {
			t.Fatalf("expected .NET rule %q in catalog", id)
		}
	}
	for _, id := range []string{
		"dotnet-development",
		"dotnet-testing",
		"dotnet-dependency-safety",
		"wf-dotnet-feature",
		"wf-dotnet-bugfix",
		"wf-dotnet-review",
	} {
		if _, ok := skills[id]; !ok {
			t.Fatalf("expected .NET skill %q in catalog", id)
		}
	}

	expectedWorkflows := map[string]string{
		"dotnet-feature-development": "wf-dotnet-feature",
		"dotnet-bugfix":              "wf-dotnet-bugfix",
		"dotnet-code-review":         "wf-dotnet-review",
	}
	for workflowID, skillID := range expectedWorkflows {
		item, ok := workflows[workflowID]
		if !ok {
			t.Fatalf("expected .NET workflow %q in catalog", workflowID)
		}
		if got := btdWorkflowTemplatePath(workflowID); got != "templates/.claude/workflows/"+workflowID+".md" {
			t.Fatalf("unexpected .NET workflow path %q", got)
		}
		if got, ok := workflowSkillTemplateID(workflowID); !ok || got != skillID {
			t.Fatalf("workflow %q skill = %q, %v; want %q, true", workflowID, got, ok, skillID)
		}
		if !containsTemplatePath(item.SourcePaths, "templates/.claude/rules/dotnet-00-routing.md") {
			t.Fatalf("expected .NET routing source path: %#v", item.SourcePaths)
		}
		if !containsTemplatePath(item.SourcePaths, "templates/.agents/skills/"+skillID+"/SKILL.md") {
			t.Fatalf("expected .NET workflow skill source path: %#v", item.SourcePaths)
		}
	}
}

func dotNetTemplateItemsByID(items []TemplateItem) map[string]TemplateItem {
	result := make(map[string]TemplateItem, len(items))
	for _, item := range items {
		result[item.ID] = item
	}
	return result
}

func containsTemplatePath(paths []string, target string) bool {
	for _, path := range paths {
		if path == target {
			return true
		}
	}
	return false
}
