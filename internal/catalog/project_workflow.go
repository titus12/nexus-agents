package catalog

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

func (s *Store) ProjectWorkflowByCopyID(projectID string, copyID string) (WorkflowGraph, bool) {
	project, copy, ok := s.projectWorkflowContext(projectID, copyID)
	if !ok {
		return WorkflowGraph{}, false
	}

	if graph, ok := readProjectWorkflowGraph(project.Path, copy); ok {
		return graph, true
	}

	s.mu.RLock()
	defer s.mu.RUnlock()
	if copy.Origin != nil {
		if graph, ok := s.workflowGraphs[copy.Origin.TemplateID]; ok {
			graph = cloneWorkflowGraph(graph)
			graph.ID = projectWorkflowStem(copy)
			if strings.TrimSpace(copy.Name) != "" {
				graph.Name = copy.Name
			}
			return graph, true
		}
	}
	return WorkflowGraph{}, false
}

func (s *Store) UpdateProjectWorkflowGraph(projectID string, copyID string, graph WorkflowGraph) (WorkflowGraph, bool, error) {
	project, copy, ok := s.projectWorkflowContext(projectID, copyID)
	if !ok {
		return WorkflowGraph{}, false, nil
	}

	stem := projectWorkflowStem(copy)
	graph.ID = stem
	if strings.TrimSpace(graph.Name) == "" {
		graph.Name = copy.Name
	}
	graph = cloneWorkflowGraph(graph)

	graphPath := projectWorkflowGraphPath(project.Path, stem)
	if err := os.MkdirAll(filepath.Dir(graphPath), 0o755); err != nil {
		return WorkflowGraph{}, true, fmt.Errorf("create workflow graph directory: %w", err)
	}
	data, err := json.MarshalIndent(graph, "", "  ")
	if err != nil {
		return WorkflowGraph{}, true, fmt.Errorf("marshal workflow graph: %w", err)
	}
	data = append(data, '\n')
	if err := os.WriteFile(graphPath, data, 0o644); err != nil {
		return WorkflowGraph{}, true, fmt.Errorf("write workflow graph: %w", err)
	}

	s.mu.Lock()
	defer s.mu.Unlock()
	copies := s.data.ProjectConfigSets[projectID]
	for index := range copies {
		if copies[index].ID == copyID {
			copies[index].LocalVersion++
			if copies[index].Status == "synced" {
				copies[index].Status = "project_modified"
			}
			copies[index].Diff = "Project workflow graph updated in " + filepath.ToSlash(filepath.Join(".claude", "workflows", stem+".graph.json")) + "."
			s.data.ProjectConfigSets[projectID] = copies
			break
		}
	}

	return cloneWorkflowGraph(graph), true, nil
}

type ProjectWorkflowCreateResult struct {
	Copy  ProjectCopy   `json:"copy"`
	Graph WorkflowGraph `json:"graph"`
}

func (s *Store) CreateProjectWorkflow(projectID string, input WorkflowInput) (ProjectCopy, WorkflowGraph, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	project, ok := s.projectByIDLocked(projectID)
	if !ok {
		return ProjectCopy{}, WorkflowGraph{}, false, nil
	}

	name := defaultString(input.Name, "new workflow")
	stem := uniqueProjectWorkflowStem(project.Path, s.data.ProjectConfigSets[projectID], slugify(name))
	markdownPath := filepath.Join(project.Path, ".claude", "workflows", stem+".md")
	graphPath := projectWorkflowGraphPath(project.Path, stem)

	if err := os.MkdirAll(filepath.Dir(markdownPath), 0o755); err != nil {
		return ProjectCopy{}, WorkflowGraph{}, true, fmt.Errorf("create workflow markdown directory: %w", err)
	}
	if err := os.MkdirAll(filepath.Dir(graphPath), 0o755); err != nil {
		return ProjectCopy{}, WorkflowGraph{}, true, fmt.Errorf("create workflow graph directory: %w", err)
	}

	trigger := defaultString(input.Trigger, "manual")
	summary := defaultString(input.Summary, "Project workflow.")
	markdown := fmt.Sprintf("# %s\n\nTrigger: `%s`\n\n%s\n", name, trigger, summary)
	if err := os.WriteFile(markdownPath, []byte(markdown), 0o644); err != nil {
		return ProjectCopy{}, WorkflowGraph{}, true, fmt.Errorf("write workflow markdown: %w", err)
	}

	graph := newProjectWorkflowGraph(stem, name, trigger, summary)
	data, err := json.MarshalIndent(graph, "", "  ")
	if err != nil {
		return ProjectCopy{}, WorkflowGraph{}, true, fmt.Errorf("marshal workflow graph: %w", err)
	}
	data = append(data, '\n')
	if err := os.WriteFile(graphPath, data, 0o644); err != nil {
		return ProjectCopy{}, WorkflowGraph{}, true, fmt.Errorf("write workflow graph: %w", err)
	}

	copy := ProjectCopy{
		ID:           s.uniqueProjectCopyIDLocked(projectID, "workflow", stem),
		Kind:         "workflow",
		Name:         name,
		Origin:       nil,
		LocalVersion: 1,
		SyncMode:     "manual",
		Status:       "detached",
		Path:         filepath.ToSlash(filepath.Join(".claude", "workflows", stem+".md")),
		Diff:         "Project-only workflow. No template origin.",
	}
	s.data.ProjectConfigSets[projectID] = append(s.data.ProjectConfigSets[projectID], copy)
	s.updateProjectSummaryLocked(projectID)
	return cloneProjectCopy(copy), cloneWorkflowGraph(graph), true, nil
}

func (s *Store) DeleteProjectWorkflow(projectID string, copyID string) (bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	project, ok := s.projectByIDLocked(projectID)
	if !ok {
		return false, nil
	}
	copies := s.data.ProjectConfigSets[projectID]
	for index, copy := range copies {
		if copy.ID != copyID || copy.Kind != "workflow" {
			continue
		}
		stem := projectWorkflowStem(copy)
		for _, filePath := range []string{
			filepath.Join(project.Path, ".claude", "workflows", stem+".md"),
			projectWorkflowGraphPath(project.Path, stem),
		} {
			if err := os.Remove(filePath); err != nil && !os.IsNotExist(err) {
				return true, fmt.Errorf("delete workflow file %s: %w", filePath, err)
			}
		}
		s.data.ProjectConfigSets[projectID] = append(copies[:index], copies[index+1:]...)
		s.updateProjectSummaryLocked(projectID)
		return true, nil
	}
	return false, nil
}

func (s *Store) projectWorkflowContext(projectID string, copyID string) (Project, ProjectCopy, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	project, ok := s.projectByIDLocked(projectID)
	if !ok {
		return Project{}, ProjectCopy{}, false
	}
	for _, copy := range s.data.ProjectConfigSets[projectID] {
		if copy.ID == copyID && copy.Kind == "workflow" {
			return project, cloneProjectCopy(copy), true
		}
	}
	return Project{}, ProjectCopy{}, false
}

func (s *Store) uniqueProjectCopyIDLocked(projectID string, kind string, stem string) string {
	existing := make(map[string]bool, len(s.data.ProjectConfigSets[projectID]))
	for _, copy := range s.data.ProjectConfigSets[projectID] {
		existing[copy.ID] = true
	}
	base := fmt.Sprintf("proj_%s_%s_%s", kind, strings.ReplaceAll(slugify(projectID), "-", "_"), strings.ReplaceAll(slugify(stem), "-", "_"))
	return uniqueID(base, existing)
}

func (s *Store) updateProjectSummaryLocked(projectID string) {
	for index := range s.data.Projects {
		if s.data.Projects[index].ID == projectID {
			s.data.Projects[index].ConfigSummary = summarizeProjectCopies(s.data.ProjectConfigSets[projectID])
			s.data.Projects[index].UpdatedAt = nowStamp()
			return
		}
	}
}

func readProjectWorkflowGraph(projectRoot string, copy ProjectCopy) (WorkflowGraph, bool) {
	stem := projectWorkflowStem(copy)
	if stem == "" {
		return WorkflowGraph{}, false
	}
	data, err := os.ReadFile(projectWorkflowGraphPath(projectRoot, stem))
	if err != nil {
		return WorkflowGraph{}, false
	}
	var graph WorkflowGraph
	if err := json.Unmarshal(stripUTF8BOM(data), &graph); err != nil {
		return WorkflowGraph{}, false
	}
	if strings.TrimSpace(graph.ID) == "" {
		graph.ID = stem
	}
	if strings.TrimSpace(graph.Name) == "" {
		graph.Name = copy.Name
	}
	return graph, true
}

func projectWorkflowGraphPath(projectRoot string, stem string) string {
	return filepath.Join(projectRoot, ".claude", "workflows", stem+".graph.json")
}

func uniqueProjectWorkflowStem(projectRoot string, copies []ProjectCopy, base string) string {
	if base == "" {
		base = "workflow"
	}
	existing := map[string]bool{}
	for _, copy := range copies {
		existing[projectWorkflowStem(copy)] = true
	}
	for suffix := 1; ; suffix++ {
		candidate := base
		if suffix > 1 {
			candidate = fmt.Sprintf("%s-%d", base, suffix)
		}
		if existing[candidate] {
			continue
		}
		if _, err := os.Stat(filepath.Join(projectRoot, ".claude", "workflows", candidate+".md")); err == nil {
			continue
		}
		if _, err := os.Stat(projectWorkflowGraphPath(projectRoot, candidate)); err == nil {
			continue
		}
		return candidate
	}
}

func newProjectWorkflowGraph(stem string, name string, trigger string, summary string) WorkflowGraph {
	return WorkflowGraph{
		ID:   stem,
		Name: name,
		Nodes: []WorkflowNode{
			{ID: "trigger", Type: "input", Category: "event", Label: trigger, Agent: "-", Detail: "Start this project workflow.", X: 48, Y: 210},
			{ID: "action", Type: "agent", Category: "action", Label: "agent action", Agent: "worker", Detail: summary, X: 320, Y: 210},
		},
		Edges: []WorkflowEdge{{From: "trigger", To: "action", Label: "request"}},
	}
}

func projectWorkflowStem(copy ProjectCopy) string {
	if before, after, found := strings.Cut(copy.Path, "#"); found {
		if strings.TrimSpace(after) != "" {
			return slugify(after)
		}
		copy.Path = before
	}

	if copy.Path != "" {
		base := filepath.Base(filepath.FromSlash(copy.Path))
		if strings.HasSuffix(strings.ToLower(base), ".graph.json") {
			base = strings.TrimSuffix(base, ".graph.json")
		} else if ext := filepath.Ext(base); ext != "" {
			base = strings.TrimSuffix(base, ext)
		}
		if base != "" && base != "00-routing" {
			return base
		}
	}
	if copy.Origin != nil && strings.TrimSpace(copy.Origin.TemplateID) != "" {
		return copy.Origin.TemplateID
	}
	return slugify(copy.Name)
}
