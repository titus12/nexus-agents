package catalog

import (
	"fmt"
	"strings"
	"sync"
	"time"

	"nexus-agents/internal/codexrouter"
)

type BootstrapData struct {
	TemplateLibrary   TemplateLibrary          `json:"templateLibrary"`
	Projects          []Project                `json:"projects"`
	ProjectConfigSets map[string][]ProjectCopy `json:"projectConfigSets"`
}

type TemplateLibrary struct {
	Agents    []TemplateItem `json:"agents"`
	Rules     []TemplateItem `json:"rules"`
	Skills    []TemplateItem `json:"skills"`
	Workflows []TemplateItem `json:"workflows"`
}

type TemplateItem struct {
	ID               string   `json:"id"`
	Kind             string   `json:"kind"`
	Slug             string   `json:"slug"`
	Name             string   `json:"name"`
	Version          int      `json:"version"`
	Summary          string   `json:"summary"`
	Entry            string   `json:"entry,omitempty"`
	Files            []string `json:"files,omitempty"`
	UpdatedAt        string   `json:"updatedAt"`
	Status           string   `json:"status"`
	ModelTier        string   `json:"modelTier,omitempty"`
	RulesCount       int      `json:"rulesCount,omitempty"`
	SkillsCount      int      `json:"skillsCount,omitempty"`
	Source           string   `json:"source,omitempty"`
	ApplicableAgents []string `json:"applicableAgents,omitempty"`
	RelatedRules     []string `json:"relatedRules,omitempty"`
	RelatedSkills    []string `json:"relatedSkills,omitempty"`
	Tools            []string `json:"tools,omitempty"`
	MCP              []string `json:"mcp,omitempty"`
	SourcePaths      []string `json:"sourcePaths,omitempty"`
	Content          string   `json:"content,omitempty"`
	CodexProjection  string   `json:"codexProjection,omitempty"`
	ClaudeSource     string   `json:"claudeSource,omitempty"`
}

type TemplateInput struct {
	Name    string   `json:"name"`
	Slug    string   `json:"slug"`
	Summary string   `json:"summary"`
	Entry   string   `json:"entry"`
	Files   []string `json:"files"`
	Status  string   `json:"status"`
}

type Project struct {
	ID                 string        `json:"id"`
	Name               string        `json:"name"`
	Path               string        `json:"path"`
	Status             string        `json:"status"`
	UpdatedAt          string        `json:"updatedAt"`
	ConfigSummary      ConfigSummary `json:"configSummary"`
	RepoKey            string        `json:"repoKey,omitempty"`
	LocalPath          string        `json:"localPath,omitempty"`
	LocalConfigPath    string        `json:"localConfigPath,omitempty"`
	LocalConfigIgnored bool          `json:"localConfigIgnored,omitempty"`
}

type ProjectInput struct {
	Name string `json:"name"`
	Path string `json:"path"`
}

type ProjectRescanResult struct {
	Project Project       `json:"project"`
	Copies  []ProjectCopy `json:"copies"`
}

type ConfigSummary struct {
	Agents    int `json:"agents"`
	Rules     int `json:"rules"`
	Skills    int `json:"skills"`
	Workflows int `json:"workflows"`
}

type ProjectCopy struct {
	ID           string  `json:"id"`
	Kind         string  `json:"kind"`
	Name         string  `json:"name"`
	Origin       *Origin `json:"origin"`
	LocalVersion int     `json:"localVersion"`
	SyncMode     string  `json:"syncMode"`
	Status       string  `json:"status"`
	Path         string  `json:"path"`
	Diff         string  `json:"diff"`
}

type Origin struct {
	TemplateID  string `json:"templateId"`
	BaseVersion int    `json:"baseVersion"`
	BaseHash    string `json:"baseHash"`
}

type WorkflowSummary struct {
	ID        string   `json:"id"`
	Name      string   `json:"name"`
	Status    string   `json:"status"`
	UpdatedAt string   `json:"updatedAt"`
	Owner     string   `json:"owner"`
	Trigger   string   `json:"trigger"`
	Tags      []string `json:"tags"`
	Summary   string   `json:"summary"`
	NodeCount int      `json:"nodeCount"`
	EdgeCount int      `json:"edgeCount"`
}

type WorkflowInput struct {
	Name    string `json:"name"`
	Status  string `json:"status"`
	Trigger string `json:"trigger"`
	Summary string `json:"summary"`
}

type WorkflowGraph struct {
	ID    string         `json:"id"`
	Name  string         `json:"name"`
	Nodes []WorkflowNode `json:"nodes"`
	Edges []WorkflowEdge `json:"edges"`
}

type WorkflowNode struct {
	ID       string `json:"id"`
	Type     string `json:"type"`
	Category string `json:"category"`
	Label    string `json:"label"`
	Agent    string `json:"agent"`
	Detail   string `json:"detail"`
	X        int    `json:"x"`
	Y        int    `json:"y"`
}

type WorkflowEdge struct {
	From  string `json:"from"`
	To    string `json:"to"`
	Label string `json:"label"`
}

type ModelRoute struct {
	ID       string `json:"id"`
	Client   string `json:"client"`
	Source   string `json:"source"`
	Target   string `json:"target"`
	Provider string `json:"provider"`
	Endpoint string `json:"endpoint"`
	Auth     string `json:"auth"`
	Status   string `json:"status"`
}

type ModelRouteResolution struct {
	RouteID     string `json:"routeId"`
	Client      string `json:"client"`
	SourceModel string `json:"sourceModel"`
	TargetModel string `json:"targetModel"`
	Provider    string `json:"provider"`
	Endpoint    string `json:"endpoint"`
	Auth        string `json:"auth"`
	Passthrough bool   `json:"passthrough"`
}

type Store struct {
	mu             sync.RWMutex
	data           BootstrapData
	workflows      []WorkflowSummary
	workflowGraphs map[string]WorkflowGraph
}

func NewStore() *Store {
	data := TemplateBootstrapData()
	return &Store{
		data:           cloneBootstrap(data),
		workflows:      cloneWorkflowSummaries(mockWorkflows()),
		workflowGraphs: cloneWorkflowGraphMap(btdWorkflowGraphs()),
	}
}

func NewStoreFromData(data BootstrapData, workflows []WorkflowSummary, workflowGraphs map[string]WorkflowGraph) *Store {
	if data.ProjectConfigSets == nil {
		data.ProjectConfigSets = map[string][]ProjectCopy{}
	}
	if workflowGraphs == nil {
		workflowGraphs = map[string]WorkflowGraph{}
	}
	return &Store{
		data:           cloneBootstrap(data),
		workflows:      cloneWorkflowSummaries(workflows),
		workflowGraphs: cloneWorkflowGraphMap(workflowGraphs),
	}
}

func (s *Store) Bootstrap() BootstrapData {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return cloneBootstrap(s.data)
}

func (s *Store) Projects() []Project {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return cloneProjects(s.data.Projects)
}

func (s *Store) ProjectByID(projectID string) (Project, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.projectByIDLocked(projectID)
}

func (s *Store) ImportProject(input ProjectInput) (Project, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	name := strings.TrimSpace(input.Name)
	if name == "" {
		name = baseName(input.Path)
	}
	if name == "" {
		name = "imported-project"
	}

	localPath, repoKey, err := prepareImportedProject(name, strings.TrimSpace(input.Path))
	if err != nil {
		return Project{}, err
	}
	id := s.uniqueProjectIDLocked(slugify(name))
	for _, project := range s.data.Projects {
		if project.RepoKey != "" && project.RepoKey == repoKey {
			id = project.ID
			name = project.Name
			break
		}
	}

	if err := writeProjectLocalMetadata(localPath, ProjectLocalMetadata{
		SchemaVersion: 1,
		ProjectID:     id,
		ProjectName:   name,
		RepoKey:       repoKey,
		LocalPath:     localPath,
	}); err != nil {
		return Project{}, err
	}
	localConfigIgnored, err := ensureGitIgnoreRule(localPath, ".nexus")
	if err != nil {
		return Project{}, err
	}

	copies, err := scanProjectConfigSetForProject(projectTokenFromID(id), localPath, s.data.TemplateLibrary)
	if err != nil {
		return Project{}, err
	}
	project := Project{
		ID:                 id,
		Name:               name,
		Path:               localPath,
		Status:             "draft",
		UpdatedAt:          nowStamp(),
		ConfigSummary:      summarizeProjectCopies(copies),
		RepoKey:            repoKey,
		LocalPath:          localPath,
		LocalConfigPath:    ".nexus",
		LocalConfigIgnored: localConfigIgnored,
	}
	for index := range s.data.Projects {
		if s.data.Projects[index].ID == id {
			s.data.Projects[index] = project
			s.data.ProjectConfigSets[id] = copies
			return project, nil
		}
	}
	s.data.Projects = append(s.data.Projects, project)
	s.data.ProjectConfigSets[id] = copies
	return project, nil
}

func (s *Store) RescanProject(projectID string) (Project, []ProjectCopy, bool, error) {
	s.mu.RLock()
	project, ok := s.projectByIDLocked(projectID)
	library := TemplateLibrary{
		Agents:    cloneTemplateItems(s.data.TemplateLibrary.Agents),
		Rules:     cloneTemplateItems(s.data.TemplateLibrary.Rules),
		Skills:    cloneTemplateItems(s.data.TemplateLibrary.Skills),
		Workflows: cloneTemplateItems(s.data.TemplateLibrary.Workflows),
	}
	s.mu.RUnlock()
	if !ok {
		return Project{}, nil, false, nil
	}

	localPath := strings.TrimSpace(project.LocalPath)
	if localPath == "" {
		localPath = strings.TrimSpace(project.Path)
	}
	if localPath == "" {
		return Project{}, nil, true, fmt.Errorf("project %s has no local path", projectID)
	}

	copies, err := scanProjectConfigSetForProject(projectTokenFromID(project.ID), localPath, library)
	if err != nil {
		return Project{}, nil, true, err
	}
	repoKey := strings.TrimSpace(project.RepoKey)
	if repoKey == "" {
		repoKey = detectRepoKey(localPath, project.Name)
	}
	if err := writeProjectLocalMetadata(localPath, ProjectLocalMetadata{
		SchemaVersion: 1,
		ProjectID:     project.ID,
		ProjectName:   project.Name,
		RepoKey:       repoKey,
		LocalPath:     localPath,
	}); err != nil {
		return Project{}, nil, true, err
	}
	localConfigIgnored, err := ensureGitIgnoreRule(localPath, ".nexus")
	if err != nil {
		return Project{}, nil, true, err
	}

	project.Path = localPath
	project.LocalPath = localPath
	project.RepoKey = repoKey
	project.LocalConfigPath = ".nexus"
	project.LocalConfigIgnored = localConfigIgnored
	project.ConfigSummary = summarizeProjectCopies(copies)
	project.UpdatedAt = latestProjectConfigStamp(localPath)

	s.mu.Lock()
	defer s.mu.Unlock()
	for index := range s.data.Projects {
		if s.data.Projects[index].ID == projectID {
			s.data.Projects[index] = project
			s.data.ProjectConfigSets[projectID] = copies
			return project, cloneProjectCopies(copies), true, nil
		}
	}
	return Project{}, nil, false, nil
}

func (s *Store) DeleteProject(projectID string) (bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	for index, project := range s.data.Projects {
		if project.ID == projectID {
			if err := removeProjectLocalMetadata(project); err != nil {
				return true, err
			}
			s.data.Projects = append(s.data.Projects[:index], s.data.Projects[index+1:]...)
			delete(s.data.ProjectConfigSets, projectID)
			return true, nil
		}
	}
	return false, nil
}

func (s *Store) ProjectConfigSet(projectID string, kind string) ([]ProjectCopy, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	if _, ok := s.projectByIDLocked(projectID); !ok {
		return nil, false
	}
	copies := cloneProjectCopies(s.data.ProjectConfigSets[projectID])
	if kind == "" {
		return copies, true
	}

	filtered := make([]ProjectCopy, 0, len(copies))
	for _, copy := range copies {
		if copy.Kind == kind {
			filtered = append(filtered, copy)
		}
	}
	return filtered, true
}

func (s *Store) SyncPreview(projectID string) ([]ProjectCopy, bool) {
	return s.ProjectConfigSet(projectID, "")
}

func (s *Store) SyncProjectCopy(projectID string, copyID string) (ProjectCopy, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()

	copies := s.data.ProjectConfigSets[projectID]
	for index := range copies {
		if copies[index].ID == copyID {
			copies[index].Status = "synced"
			copies[index].LocalVersion++
			if copies[index].Origin != nil {
				copies[index].Origin.BaseHash = contentHash(copies[index].Origin.TemplateID, copies[index].LocalVersion)
			}
			copies[index].Diff = "Synced from Template Library. Project copy keeps manual sync mode."
			s.data.ProjectConfigSets[projectID] = copies
			return cloneProjectCopy(copies[index]), true
		}
	}
	return ProjectCopy{}, false
}

func (s *Store) DetachProjectCopy(projectID string, copyID string) (ProjectCopy, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()

	copies := s.data.ProjectConfigSets[projectID]
	for index := range copies {
		if copies[index].ID == copyID {
			copies[index].Origin = nil
			copies[index].Status = "detached"
			copies[index].LocalVersion++
			copies[index].Diff = "Template origin detached. This project copy is now project-only."
			s.data.ProjectConfigSets[projectID] = copies
			return cloneProjectCopy(copies[index]), true
		}
	}
	return ProjectCopy{}, false
}

func (s *Store) Templates(kind string) ([]TemplateItem, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	items, ok := s.templateItemsLocked(kind)
	if !ok {
		return nil, false
	}
	return cloneTemplateItems(*items), true
}

func (s *Store) CreateTemplate(kind string, input TemplateInput) (TemplateItem, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()

	items, ok := s.templateItemsLocked(kind)
	if !ok {
		return TemplateItem{}, false
	}
	itemKind, _ := singularKind(kind)
	name := strings.TrimSpace(input.Name)
	if name == "" {
		name = fmt.Sprintf("new-%s", itemKind)
	}
	slug := strings.TrimSpace(input.Slug)
	if slug == "" {
		slug = slugify(name)
	}
	id := uniqueTemplateID(*items, slug)
	status := strings.TrimSpace(input.Status)
	if status == "" {
		status = "ready"
	}
	if itemKind == "rule" && input.Status == "" {
		status = "active"
	}

	item := TemplateItem{
		ID:        id,
		Kind:      itemKind,
		Slug:      slug,
		Name:      name,
		Version:   1,
		Summary:   defaultString(input.Summary, "New template item."),
		Entry:     input.Entry,
		Files:     append([]string(nil), input.Files...),
		UpdatedAt: nowStamp(),
		Status:    status,
	}
	*items = append(*items, item)
	return cloneTemplateItem(item), true
}

func (s *Store) UpdateTemplate(kind string, templateID string, input TemplateInput) (TemplateItem, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()

	items, ok := s.templateItemsLocked(kind)
	if !ok {
		return TemplateItem{}, false
	}
	for index := range *items {
		if (*items)[index].ID == templateID {
			item := &(*items)[index]
			if input.Name != "" {
				item.Name = input.Name
			}
			if input.Slug != "" {
				item.Slug = input.Slug
			}
			if input.Summary != "" {
				item.Summary = input.Summary
			}
			if input.Entry != "" {
				item.Entry = input.Entry
			}
			if input.Files != nil {
				item.Files = append([]string(nil), input.Files...)
			}
			if input.Status != "" {
				item.Status = input.Status
			}
			item.Version++
			item.UpdatedAt = nowStamp()
			return cloneTemplateItem(*item), true
		}
	}
	return TemplateItem{}, false
}

func (s *Store) DeleteTemplate(kind string, templateID string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()

	items, ok := s.templateItemsLocked(kind)
	if !ok {
		return false
	}
	for index, item := range *items {
		if item.ID == templateID {
			*items = append((*items)[:index], (*items)[index+1:]...)
			return true
		}
	}
	return false
}

func (s *Store) Workflows() []WorkflowSummary {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return cloneWorkflowSummaries(s.workflows)
}

func (s *Store) WorkflowByID(workflowID string) (WorkflowGraph, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	graph, ok := s.workflowGraphs[workflowID]
	if !ok {
		return WorkflowGraph{}, false
	}
	return cloneWorkflowGraph(graph), true
}

func (s *Store) CreateWorkflow(input WorkflowInput) WorkflowSummary {
	s.mu.Lock()
	defer s.mu.Unlock()

	name := strings.TrimSpace(input.Name)
	if name == "" {
		name = "new workflow"
	}
	id := s.uniqueWorkflowIDLocked(slugify(name))
	status := defaultString(input.Status, "draft")
	trigger := defaultString(input.Trigger, "manual")
	summary := defaultString(input.Summary, "New workflow process.")

	graph := WorkflowGraph{
		ID:   id,
		Name: name,
		Nodes: []WorkflowNode{
			{ID: "trigger", Type: "trigger", Category: "event", Label: "Manual trigger", Agent: "-", Detail: "Start the workflow from the console.", X: 48, Y: 180},
			{ID: "agent-action", Type: "agent", Category: "action", Label: "Agent action", Agent: "worker", Detail: "Run the first assigned role action.", X: 320, Y: 180},
		},
		Edges: []WorkflowEdge{{From: "trigger", To: "agent-action", Label: "request"}},
	}
	workflow := WorkflowSummary{
		ID:        id,
		Name:      name,
		Status:    status,
		UpdatedAt: nowStamp(),
		Owner:     "template-library",
		Trigger:   trigger,
		Tags:      []string{"manual", "draft"},
		Summary:   summary,
		NodeCount: len(graph.Nodes),
		EdgeCount: len(graph.Edges),
	}
	s.workflows = append(s.workflows, workflow)
	s.workflowGraphs[id] = graph
	s.data.TemplateLibrary.Workflows = append(s.data.TemplateLibrary.Workflows, TemplateItem{
		ID:        id,
		Kind:      "workflow",
		Slug:      id,
		Name:      name,
		Version:   1,
		Summary:   summary,
		Entry:     "templates/workflows/" + id + ".md",
		Files:     []string{"templates/workflows/" + id + ".md", "templates/workflows/" + id + ".graph.json"},
		UpdatedAt: workflow.UpdatedAt,
		Status:    status,
		Source:    "Template Library markdown",
		SourcePaths: []string{
			"templates/workflows/" + id + ".md",
			"templates/workflows/" + id + ".graph.json",
		},
		Content: "New workflow template represented as markdown plus graph JSON.",
	})
	return workflow
}

func (s *Store) DuplicateWorkflow(workflowID string) (WorkflowSummary, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()

	var source WorkflowSummary
	found := false
	for _, workflow := range s.workflows {
		if workflow.ID == workflowID {
			source = workflow
			found = true
			break
		}
	}
	if !found {
		return WorkflowSummary{}, false
	}

	sourceGraph, ok := s.workflowGraphs[workflowID]
	if !ok {
		return WorkflowSummary{}, false
	}

	id := s.uniqueWorkflowIDLocked(workflowID + "-copy")
	graph := cloneWorkflowGraph(sourceGraph)
	graph.ID = id
	graph.Name = source.Name + " copy"

	workflow := source
	workflow.ID = id
	workflow.Name = graph.Name
	workflow.Status = "draft"
	workflow.UpdatedAt = nowStamp()
	workflow.Tags = append([]string(nil), source.Tags...)
	workflow.Tags = append(workflow.Tags, "copy")

	s.workflows = append(s.workflows, workflow)
	s.workflowGraphs[id] = graph
	s.data.TemplateLibrary.Workflows = append(s.data.TemplateLibrary.Workflows, TemplateItem{
		ID:        id,
		Kind:      "workflow",
		Slug:      id,
		Name:      workflow.Name,
		Version:   1,
		Summary:   workflow.Summary,
		Entry:     "templates/workflows/" + id + ".md",
		Files:     []string{"templates/workflows/" + id + ".md", "templates/workflows/" + id + ".graph.json"},
		UpdatedAt: workflow.UpdatedAt,
		Status:    workflow.Status,
		Source:    "Template Library markdown",
		SourcePaths: []string{
			"templates/workflows/" + id + ".md",
			"templates/workflows/" + id + ".graph.json",
		},
		Content: "Duplicated workflow template represented as markdown plus graph JSON.",
	})
	return workflow, true
}

func (s *Store) UpdateWorkflow(workflowID string, input WorkflowInput) (WorkflowSummary, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()

	for index := range s.workflows {
		if s.workflows[index].ID == workflowID {
			if input.Name != "" {
				s.workflows[index].Name = input.Name
			}
			if input.Status != "" {
				s.workflows[index].Status = input.Status
			}
			if input.Trigger != "" {
				s.workflows[index].Trigger = input.Trigger
			}
			if input.Summary != "" {
				s.workflows[index].Summary = input.Summary
			}
			s.workflows[index].UpdatedAt = nowStamp()
			if graph, ok := s.workflowGraphs[workflowID]; ok {
				graph.Name = s.workflows[index].Name
				s.workflowGraphs[workflowID] = graph
			}
			return s.workflows[index], true
		}
	}
	return WorkflowSummary{}, false
}

func (s *Store) UpdateWorkflowGraph(workflowID string, graph WorkflowGraph) (WorkflowGraph, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if _, ok := s.workflowGraphs[workflowID]; !ok {
		return WorkflowGraph{}, false
	}
	graph.ID = workflowID
	if strings.TrimSpace(graph.Name) == "" {
		graph.Name = s.workflowGraphs[workflowID].Name
	}
	cloned := cloneWorkflowGraph(graph)
	s.workflowGraphs[workflowID] = cloned

	for index := range s.workflows {
		if s.workflows[index].ID == workflowID {
			s.workflows[index].Name = graph.Name
			s.workflows[index].NodeCount = len(graph.Nodes)
			s.workflows[index].EdgeCount = len(graph.Edges)
			s.workflows[index].UpdatedAt = nowStamp()
			break
		}
	}
	for index := range s.data.TemplateLibrary.Workflows {
		if s.data.TemplateLibrary.Workflows[index].ID == workflowID {
			s.data.TemplateLibrary.Workflows[index].Name = graph.Name
			s.data.TemplateLibrary.Workflows[index].UpdatedAt = nowStamp()
			break
		}
	}

	return cloneWorkflowGraph(cloned), true
}

func (s *Store) DeleteWorkflow(workflowID string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()

	for index, workflow := range s.workflows {
		if workflow.ID == workflowID {
			s.workflows = append(s.workflows[:index], s.workflows[index+1:]...)
			delete(s.workflowGraphs, workflowID)
			for templateIndex, item := range s.data.TemplateLibrary.Workflows {
				if item.ID == workflowID {
					s.data.TemplateLibrary.Workflows = append(s.data.TemplateLibrary.Workflows[:templateIndex], s.data.TemplateLibrary.Workflows[templateIndex+1:]...)
					break
				}
			}
			return true
		}
	}
	return false
}

func (s *Store) projectByIDLocked(projectID string) (Project, bool) {
	for _, project := range s.data.Projects {
		if project.ID == projectID {
			return project, true
		}
	}
	return Project{}, false
}

func (s *Store) templateItemsLocked(kind string) (*[]TemplateItem, bool) {
	normalized, ok := collectionKind(kind)
	if !ok {
		return nil, false
	}
	switch normalized {
	case "agents":
		return &s.data.TemplateLibrary.Agents, true
	case "rules":
		return &s.data.TemplateLibrary.Rules, true
	case "skills":
		return &s.data.TemplateLibrary.Skills, true
	case "workflows":
		return &s.data.TemplateLibrary.Workflows, true
	default:
		return nil, false
	}
}

func (s *Store) uniqueProjectIDLocked(base string) string {
	existing := make(map[string]bool, len(s.data.Projects))
	for _, project := range s.data.Projects {
		existing[project.ID] = true
	}
	return uniqueID(base, existing)
}

func (s *Store) uniqueWorkflowIDLocked(base string) string {
	existing := make(map[string]bool, len(s.workflows))
	for _, workflow := range s.workflows {
		existing[workflow.ID] = true
	}
	return uniqueID(base, existing)
}

func Projects() []Project {
	return NewStore().Projects()
}

func ProjectByID(projectID string) (Project, bool) {
	return NewStore().ProjectByID(projectID)
}

func ProjectConfigSet(projectID string, kind string) []ProjectCopy {
	copies, _ := NewStore().ProjectConfigSet(projectID, kind)
	return copies
}

func Templates(kind string) []TemplateItem {
	items, ok := NewStore().Templates(kind)
	if !ok {
		return nil
	}
	return items
}

func Workflows() []WorkflowSummary {
	return mockWorkflows()
}

func WorkflowByID(workflowID string) (WorkflowGraph, bool) {
	graph, ok := btdWorkflowGraphs()[workflowID]
	if ok {
		return graph, true
	}
	return WorkflowGraph{}, false
}

func ModelRoutes() []ModelRoute {
	config := codexrouter.DefaultConfig()
	routes := make([]ModelRoute, 0, len(config.Routes))
	for _, route := range config.Routes {
		routes = append(routes, ModelRoute{
			ID:       modelProxyRouteID(route.ID),
			Client:   "Codex Responses",
			Source:   route.ID,
			Target:   route.Model,
			Provider: modelProxyProvider(route),
			Endpoint: modelProxyEndpoint(route),
			Auth:     modelProxyAuth(route),
			Status:   "ready",
		})
	}
	return routes
}

func ResolveModelRoute(client string, model string) (ModelRouteResolution, bool) {
	normalizedClient := strings.ToLower(client)
	normalizedModel := strings.ToLower(model)

	switch normalizedClient {
	case "codex", "codex responses":
		for _, route := range codexrouter.DefaultConfig().Routes {
			if strings.ToLower(route.ID) != normalizedModel {
				continue
			}
			return ModelRouteResolution{
				RouteID:     modelProxyRouteID(route.ID),
				Client:      "Codex Responses",
				SourceModel: model,
				TargetModel: route.Model,
				Provider:    modelProxyProvider(route),
				Endpoint:    modelProxyEndpoint(route),
				Auth:        modelProxyAuth(route),
				Passthrough: route.API == "responses" && route.AuthMode == "codex_openai",
			}, true
		}
	}

	return ModelRouteResolution{}, false
}

func modelProxyRouteID(modelID string) string {
	replacer := strings.NewReplacer(".", "", "-", "-")
	return "codex-" + replacer.Replace(modelID)
}

func modelProxyProvider(route codexrouter.Route) string {
	if route.AuthMode == "codex_openai" {
		return "ChatGPT Subscription"
	}
	return route.Provider
}

func modelProxyEndpoint(route codexrouter.Route) string {
	if route.API == "responses" {
		return strings.TrimRight(route.BaseURL, "/") + "/responses"
	}
	if route.API == "chat_completions" {
		return strings.TrimRight(route.BaseURL, "/") + "/chat/completions"
	}
	return route.BaseURL
}

func modelProxyAuth(route codexrouter.Route) string {
	switch route.AuthMode {
	case "codex_openai":
		return "Codex bearer"
	case "api_key":
		return route.APIKeyEnv
	default:
		return route.AuthMode
	}
}

func TemplateBootstrapData() BootstrapData {
	library := hydrateTemplateLibraryFromFiles(TemplateLibrary{
		Agents:    btdAgentTemplates(),
		Rules:     btdRuleTemplates(),
		Skills:    btdSkillTemplates(),
		Workflows: btdWorkflowTemplates(),
	})
	return BootstrapData{
		TemplateLibrary:   library,
		Projects:          []Project{},
		ProjectConfigSets: map[string][]ProjectCopy{},
	}
}

func btdAgentTemplates() []TemplateItem {
	type agentSpec struct {
		id        string
		summary   string
		model     string
		effort    string
		skills    []string
		tools     []string
		mcp       []string
		content   string
		updatedAt string
	}

	specs := []agentSpec{
		{
			id:        "sisyphus",
			summary:   "主编排者，负责复杂任务分析、拆解、多 agent 协调与最终综合。",
			model:     "gpt-5.4",
			effort:    "high",
			skills:    []string{"dev-workflow", "skill-standard", "review-feedback"},
			tools:     []string{"shell", "rg", "git", "task-dispatch"},
			mcp:       []string{"codegraph"},
			content:   "Phase 0-5 orchestration: 接收需求、分析模块、拆解角色任务、协调执行、综合结果并交付。",
			updatedAt: "2026-06-20 10:00",
		},
		{
			id:        "prometheus",
			summary:   "战略规划角色，负责方案设计、架构决策、选型评估，开始实现前使用。",
			model:     "gpt-5.5",
			effort:    "high",
			skills:    []string{"dev-workflow", "skill-standard", "pmconf-pattern"},
			tools:     []string{"rg", "codegraph", "read-only-shell"},
			mcp:       []string{"codegraph"},
			content:   "Clarify -> read skills -> inspect project context -> compare 2-3 approaches -> hand off implementation plan.",
			updatedAt: "2026-06-20 10:02",
		},
		{
			id:        "hephaestus",
			summary:   "深度工匠，负责端到端 feature 实现、多文件重构和完整功能开发。",
			model:     "gpt-5.4",
			effort:    "high",
			skills:    []string{"coding-rules", "testing", "high-risk-api", "pmconf-pattern", "cross-config", "cross-client"},
			tools:     []string{"shell", "apply_patch", "rg", "git"},
			mcp:       []string{"codegraph"},
			content:   "Read required skills, explore references with codegraph/rg, implement scoped changes, then run build and tests.",
			updatedAt: "2026-06-20 10:04",
		},
		{
			id:        "quick",
			summary:   "快速修改角色，处理单文件修改、小 bug 修复和编译错误修复。",
			model:     "deepseek-v4-flash",
			effort:    "low",
			skills:    []string{"coding-rules", "testing"},
			tools:     []string{"shell", "apply_patch", "rg"},
			mcp:       []string{"codegraph"},
			content:   "Locate quickly, make the smallest safe edit, verify build; if scope grows, recommend hephaestus.",
			updatedAt: "2026-06-20 10:06",
		},
		{
			id:        "oracle",
			summary:   "架构分析与复杂调试角色，负责深度代码追踪、复杂 bug 诊断和影响评估。",
			model:     "gpt-5.5",
			effort:    "high",
			skills:    []string{"coding-rules", "testing", "review-feedback"},
			tools:     []string{"rg", "git", "codegraph"},
			mcp:       []string{"codegraph"},
			content:   "Evidence first: gather logs and call graph, form hypotheses, verify root cause, report file:line and impact.",
			updatedAt: "2026-06-20 10:08",
		},
		{
			id:        "debugger",
			summary:   "日志优先 Bug 排查角色，低成本快速定位简单或中等 bug。",
			model:     "gpt-5.4-mini",
			effort:    "medium",
			skills:    []string{"coding-rules", "testing", "cross-social", "cross-gate", "cross-client"},
			tools:     []string{"rg", "git"},
			mcp:       []string{"codegraph"},
			content:   "Use logs and stack traces first, keep grep/read budget small, then conclude or escalate to oracle.",
			updatedAt: "2026-06-20 10:10",
		},
		{
			id:        "librarian",
			summary:   "文档查询角色，负责查 API 用法、框架文档和第三方库使用方式。",
			model:     "deepseek-v4-flash",
			effort:    "medium",
			skills:    []string{"skill-standard", "cross-config", "cross-client"},
			tools:     []string{"rg", "web", "context7"},
			mcp:       []string{"codegraph"},
			content:   "Check project-local docs first, then external docs when needed; return code examples, parameters, and caveats.",
			updatedAt: "2026-06-20 10:12",
		},
		{
			id:        "worker",
			summary:   "任务执行工人，接收明确子任务并完成函数、样板代码或重复性修改。",
			model:     "deepseek-v4-pro",
			effort:    "high",
			skills:    []string{"coding-rules", "testing", "quest-system", "pmconf-pattern"},
			tools:     []string{"shell", "apply_patch", "rg"},
			mcp:       []string{"codegraph"},
			content:   "Execute bounded instructions, follow local patterns, verify the assigned slice, and ask when context is insufficient.",
			updatedAt: "2026-06-20 10:14",
		},
		{
			id:        "reviewer-logic",
			summary:   "逻辑正确性审核角色，聚焦空指针、边界条件、并发安全、事务完整性和错误处理。",
			model:     "deepseek-v4-pro",
			effort:    "high",
			skills:    []string{"coding-rules", "testing", "review-feedback"},
			tools:     []string{"rg", "git", "codegraph"},
			mcp:       []string{"codegraph"},
			content:   "Review only logic correctness. Trace callers, inspect state transitions, and rank concrete findings by severity.",
			updatedAt: "2026-06-20 10:16",
		},
		{
			id:        "reviewer-perf",
			summary:   "性能审核角色，聚焦 N+1、goroutine 泄漏、内存分配、锁粒度和缓存缺失。",
			model:     "deepseek-v4-pro",
			effort:    "medium",
			skills:    []string{"coding-rules", "testing", "pmconf-pattern"},
			tools:     []string{"rg", "git", "codegraph"},
			mcp:       []string{"codegraph"},
			content:   "Review hot paths, loops, allocations, actor calls, locks, and config lookup patterns.",
			updatedAt: "2026-06-20 10:18",
		},
		{
			id:        "reviewer-security",
			summary:   "安全审核角色，从攻击者视角检查越权、输入验证、耗尽、泄露、注入和重放风险。",
			model:     "deepseek-v4-pro",
			effort:    "high",
			skills:    []string{"high-risk-api", "coding-rules", "review-feedback"},
			tools:     []string{"rg", "git", "codegraph"},
			mcp:       []string{"codegraph"},
			content:   "Trace data flow from inputs through auth, economy, persistence, and side effects; report exploitable risks only.",
			updatedAt: "2026-06-20 10:20",
		},
		{
			id:        "gatekeeper",
			summary:   "提交门禁角色，提交前扫描 diff 并生成高风险清单，必要时要求用户确认。",
			model:     "deepseek-v4-flash",
			effort:    "medium",
			skills:    []string{"coding-rules", "high-risk-api", "review-feedback"},
			tools:     []string{"git", "rg"},
			mcp:       []string{"codegraph"},
			content:   "Scan staged or working-tree diff for destructive, auth, currency, goroutine, lock, and ignored-error risks.",
			updatedAt: "2026-06-20 10:22",
		},
	}

	items := make([]TemplateItem, 0, len(specs))
	for _, spec := range specs {
		items = append(items, TemplateItem{
			ID:            spec.id,
			Kind:          "agent",
			Slug:          spec.id,
			Name:          spec.id,
			Version:       1,
			Summary:       spec.summary,
			UpdatedAt:     spec.updatedAt,
			Status:        "ready",
			ModelTier:     spec.model,
			RulesCount:    4,
			SkillsCount:   len(spec.skills),
			RelatedRules:  []string{"00-routing", "01-communication", "02-safety", "03-project-model"},
			RelatedSkills: append([]string(nil), spec.skills...),
			Tools:         append([]string(nil), spec.tools...),
			MCP:           append([]string(nil), spec.mcp...),
			SourcePaths: []string{
				"templates/agents/claude/go-" + spec.id + ".md",
				"templates/agents/codex/go-" + spec.id + ".toml",
				".claude/agents/" + spec.id + ".md",
				".codex/agents/" + spec.id + ".toml",
			},
			Content:         spec.content,
			CodexProjection: fmt.Sprintf("name = %q\nmodel = %q\nmodel_reasoning_effort = %q", spec.id, spec.model, spec.effort),
			ClaudeSource:    "templates/agents/claude/go-" + spec.id + ".md",
		})
	}
	return items
}

func btdRuleTemplates() []TemplateItem {
	specs := []struct {
		id        string
		summary   string
		content   string
		updatedAt string
	}{
		{
			id:        "00-routing",
			summary:   "定义 --feat、--mod、--bug、--rev、--design 等工作流入口与角色激活规则。",
			content:   "多步流程必须按阶段推进；复杂任务先拆解，代码审核可并行派发 reviewer，高风险改动走 high-risk-api。",
			updatedAt: "2026-06-20 10:24",
		},
		{
			id:        "01-communication",
			summary:   "定义中文沟通、何时提问、何时直接做，以及简洁输出格式。",
			content:   "始终中文回复；需求明确且范围可验证时直接执行；代码位置使用 file.go:123；避免无意义过渡句。",
			updatedAt: "2026-06-20 10:25",
		},
		{
			id:        "02-safety",
			summary:   "定义 Git、文件、验证和不可逆操作的安全边界。",
			content:   "不 force push、不 reset --hard、不覆盖未知修改；修改代码后必须 go build；删除数据等不可逆操作要先确认。",
			updatedAt: "2026-06-20 10:26",
		},
		{
			id:        "03-project-model",
			summary:   "定义 btd-game-server 的 Actor 模型、xbean 事务、配置层和 Manager 模式约束。",
			content:   "玩家状态只能在自己的 actor 内修改；数据变更必须走 DoTransaction；配置使用 WithContext；go 命令带 actor_id_uint64 tag。",
			updatedAt: "2026-06-20 10:27",
		},
	}

	items := make([]TemplateItem, 0, len(specs))
	for _, spec := range specs {
		path := btdRuleTemplatePath(spec.id)
		items = append(items, TemplateItem{
			ID:              spec.id,
			Kind:            "rule",
			Slug:            spec.id,
			Name:            spec.id,
			Version:         1,
			Summary:         spec.summary,
			Entry:           path,
			Files:           []string{path},
			UpdatedAt:       spec.updatedAt,
			Status:          "active",
			Source:          "Copied rule markdown",
			SourcePaths:     []string{path, ".claude/rules/" + spec.id + ".md"},
			Content:         spec.content,
			CodexProjection: fmt.Sprintf("[rules.%s]\nsource = %q", strings.TrimPrefix(spec.id, "0"), path),
			ClaudeSource:    path,
		})
	}
	return items
}

func btdSkillTemplates() []TemplateItem {
	specs := []struct {
		id               string
		summary          string
		content          string
		applicableAgents []string
		updatedAt        string
	}{
		{
			id:               "coding-rules",
			summary:          "Go 编码规范，覆盖质量原则、import、命名、错误、日志、并发、性能和复用。",
			content:          "写代码前先找现有实现；热路径关注分配和循环；错误码匹配业务语义；禁止循环内 Info/Error。",
			applicableAgents: []string{"hephaestus", "quick", "worker", "reviewer-logic", "reviewer-perf", "reviewer-security", "gatekeeper"},
			updatedAt:        "2026-06-20 10:30",
		},
		{
			id:               "cross-client",
			summary:          "客户端跨端协作手册，说明协议对接、联调和常见跨端问题排查。",
			content:          "新增服务端接口后检查 Req/Resp/Notify/ErrorCode 对齐，排查客户端收不到响应或数据不一致。",
			applicableAgents: []string{"hephaestus", "debugger", "librarian"},
			updatedAt:        "2026-06-20 10:31",
		},
		{
			id:               "cross-config",
			summary:          "btd-config 跨仓配置协作手册，说明 proto、rawdata、gen 文件和配置注册边界。",
			content:          "proto/config 变更时停下等待用户生成；gen 文件不可手改；新增配置表需在 setting.yaml 注册。",
			applicableAgents: []string{"hephaestus", "librarian"},
			updatedAt:        "2026-06-20 10:32",
		},
		{
			id:               "cross-gate",
			summary:          "Gate 服务跨服务协作手册，覆盖仓库结构、通信协议、关键调试文件和常见问题。",
			content:          "涉及 gate 通信时检查协议边界、关键文件和跨服务调用链，优先按调试指引定位。",
			applicableAgents: []string{"debugger", "oracle", "worker"},
			updatedAt:        "2026-06-20 10:33",
		},
		{
			id:               "cross-social",
			summary:          "Social 服务跨服务协作手册，覆盖职责边界、通信方式、调试指引和常见跨服务 Bug。",
			content:          "涉及 social 通信时先确认职责边界和通信方式，再按调试指引定位跨服务问题。",
			applicableAgents: []string{"debugger", "oracle", "worker"},
			updatedAt:        "2026-06-20 10:34",
		},
		{
			id:               "dev-workflow",
			summary:          "新功能开发流程，定义九步流程、铁律、Agent 分工和方案文件要求。",
			content:          "需求澄清、阅读上下文、设计方案、实现、测试、审核、文档和完成状态必须形成可追踪流程。",
			applicableAgents: []string{"sisyphus", "prometheus", "hephaestus"},
			updatedAt:        "2026-06-20 10:35",
		},
		{
			id:               "high-risk-api",
			summary:          "高风险 API 管控，分为报备级、确认级和自动审核级。",
			content:          "货币、GM、cron、删除数据、鉴权等高风险行为按级别报备或暂停确认，完成后触发 reviewer。",
			applicableAgents: []string{"hephaestus", "reviewer-security", "gatekeeper"},
			updatedAt:        "2026-06-20 10:36",
		},
		{
			id:               "pmconf-pattern",
			summary:          "pmconf 配置预处理模式，说明何时预处理、标准四步写法和参考实现。",
			content:          "配置加载/热更时预处理一次，业务侧 O(1) 查询；不要发明新模式，优先参考已有 postLoad 实现。",
			applicableAgents: []string{"hephaestus", "worker", "reviewer-perf", "prometheus"},
			updatedAt:        "2026-06-20 10:37",
		},
		{
			id:               "quest-system",
			summary:          "任务系统手册，覆盖架构、核心接口、数据结构、扩展点、配置依赖和测试方法。",
			content:          "新增任务目标、触发点或任务类型时，检查 counter/handler 注册、配置同步、通知客户端和 QuestPool 容量。",
			applicableAgents: []string{"worker", "hephaestus", "reviewer-logic"},
			updatedAt:        "2026-06-20 10:38",
		},
		{
			id:               "review-feedback",
			summary:          "审核反馈动态纳入规范，用于把重复或高风险审核发现沉淀为规则。",
			content:          "只纳入重复出现或高风险模式；规则需具体可执行，并标注负责检查的 reviewer 角色。",
			applicableAgents: []string{"sisyphus", "reviewer-logic", "reviewer-security", "gatekeeper"},
			updatedAt:        "2026-06-20 10:39",
		},
		{
			id:               "skill-standard",
			summary:          "Skill 创建规范，定义定位、标准结构、编写原则、创建时机和触发规则。",
			content:          "Skill 应按模块沉淀稳定知识，结构包含架构、接口、数据、扩展、配置、踩坑、测试和检查清单。",
			applicableAgents: []string{"sisyphus", "prometheus", "librarian"},
			updatedAt:        "2026-06-20 10:40",
		},
		{
			id:               "testing",
			summary:          "测试编写规范，覆盖必须写测试的场景、固定命令、mock、gomonkey 和断言规则。",
			content:          "修复 bug 必须先写复现测试；Go 测试带 actor_id_uint64；gomonkey 需 -gcflags=all=-l 防止内联。",
			applicableAgents: []string{"hephaestus", "quick", "worker", "reviewer-logic", "reviewer-perf"},
			updatedAt:        "2026-06-20 10:41",
		},
	}

	items := make([]TemplateItem, 0, len(specs))
	for _, spec := range specs {
		path := btdSkillTemplatePath(spec.id)
		items = append(items, TemplateItem{
			ID:               spec.id,
			Kind:             "skill",
			Slug:             spec.id,
			Name:             spec.id,
			Version:          1,
			Summary:          spec.summary,
			Entry:            path,
			Files:            []string{path},
			UpdatedAt:        spec.updatedAt,
			Status:           "ready",
			Source:           "Copied skill markdown",
			ApplicableAgents: append([]string(nil), spec.applicableAgents...),
			SourcePaths:      []string{path, ".claude/skills/" + spec.id + ".md"},
			Content:          spec.content,
			ClaudeSource:     path,
		})
	}
	return items
}

func btdRuleTemplatePath(id string) string {
	switch id {
	case "00-routing":
		return "templates/rules/go-00-routing.md"
	case "02-safety":
		return "templates/rules/go-02-safety.md"
	case "03-project-model":
		return "templates/rules/go-03-project-model.md"
	default:
		return "templates/rules/" + id + ".md"
	}
}

func btdSkillTemplatePath(id string) string {
	switch id {
	case "dev-workflow", "coding-rules", "testing", "pmconf-pattern", "quest-system", "cross-config":
		return "templates/skills/go-" + id + ".md"
	default:
		return "templates/skills/" + id + ".md"
	}
}

func btdWorkflowTemplates() []TemplateItem {
	specs := btdWorkflowSpecs()
	items := make([]TemplateItem, 0, len(specs))
	for _, spec := range specs {
		items = append(items, TemplateItem{
			ID:          spec.id,
			Kind:        "workflow",
			Slug:        spec.id,
			Name:        spec.name,
			Version:     1,
			Summary:     spec.summary,
			Entry:       btdWorkflowTemplatePath(spec.id),
			Files:       []string{btdWorkflowTemplatePath(spec.id), btdWorkflowGraphTemplatePath(spec.id)},
			UpdatedAt:   spec.updatedAt,
			Status:      "ready",
			Source:      "Expanded workflow markdown",
			SourcePaths: []string{btdWorkflowTemplatePath(spec.id), btdWorkflowGraphTemplatePath(spec.id), "templates/rules/go-00-routing.md", ".claude/rules/go-00-routing.md"},
			Content:     spec.content,
		})
	}
	return items
}

func btdWorkflowTemplatePath(id string) string {
	switch id {
	case "feature-development", "modify-existing", "bugfix", "code-review", "refactor":
		return "templates/workflows/go-" + id + ".md"
	default:
		return "templates/workflows/" + id + ".md"
	}
}

func btdWorkflowGraphTemplatePath(id string) string {
	return strings.TrimSuffix(btdWorkflowTemplatePath(id), ".md") + ".graph.json"
}

type btdWorkflowSpec struct {
	id        string
	name      string
	trigger   string
	owner     string
	summary   string
	content   string
	tags      []string
	status    string
	updatedAt string
}

func btdWorkflowSpecs() []btdWorkflowSpec {
	return []btdWorkflowSpec{
		{
			id:        "feature-development",
			name:      "--feat 新功能开发",
			trigger:   "--feat",
			owner:     "sisyphus",
			summary:   "全新功能或较大模块开发，加载 dev-workflow 并按九步流程推进。",
			content:   "步骤 1 前澄清需求边界；实现阶段加载 coding-rules；写测试时遵循先失败、再实现、再通过。",
			tags:      []string{"routing", "feature", "sequence"},
			status:    "ready",
			updatedAt: "2026-06-20 11:00",
		},
		{
			id:        "modify-existing",
			name:      "--mod 功能调整",
			trigger:   "--mod",
			owner:     "hephaestus",
			summary:   "已有功能调整，先摸清影响范围，再按单文件或多文件路径执行。",
			content:   "codegraph/Grep 定位影响范围；读模块 skill；多文件交给 hephaestus，单文件交给 quick；最后 build 和相关 test。",
			tags:      []string{"routing", "modify", "verification"},
			status:    "ready",
			updatedAt: "2026-06-20 11:01",
		},
		{
			id:        "bugfix",
			name:      "--bug Bug 排查修复",
			trigger:   "--bug",
			owner:     "debugger",
			summary:   "报错、异常或 crash 排查，先定位根因再修复，禁止猜测性修改。",
			content:   "debugger 日志优先排查；复杂或跨模块升级 oracle；定位根因后加载 coding-rules 修复；用复现路径和 build 验证。",
			tags:      []string{"routing", "bugfix", "root-cause"},
			status:    "ready",
			updatedAt: "2026-06-20 11:02",
		},
		{
			id:        "code-review",
			name:      "--rev 代码审核",
			trigger:   "--rev",
			owner:     "sisyphus",
			summary:   "当前改动审核，logic、perf、security 三个 reviewer 并行后汇总去重。",
			content:   "并行派发 reviewer-logic、reviewer-perf、reviewer-security，再合并 findings 并按严重度分级。",
			tags:      []string{"routing", "review", "parallel"},
			status:    "ready",
			updatedAt: "2026-06-20 11:03",
		},
		{
			id:        "design",
			name:      "--design 方案设计",
			trigger:   "--design",
			owner:     "prometheus",
			summary:   "只出方案不写代码，适合架构决策、技术选型和复杂实现前置设计。",
			content:   "先问 2-3 个澄清问题；prometheus 读 skill 和 codegraph 摸底；提出方案对比、推荐方案并落盘 docs/dev-plans。",
			tags:      []string{"routing", "design", "planning"},
			status:    "ready",
			updatedAt: "2026-06-20 11:04",
		},
		{
			id:        "research",
			name:      "--ask 理解/调研",
			trigger:   "--ask",
			owner:     "oracle",
			summary:   "只读不改，用于代码理解、调用链追踪和外部文档调研。",
			content:   "代码理解走 oracle + codegraph；外部文档走 librarian + context7；输出结论、关键路径和参考代码位置。",
			tags:      []string{"routing", "research", "read-only"},
			status:    "ready",
			updatedAt: "2026-06-20 11:05",
		},
		{
			id:        "commit-gate",
			name:      "--commit 提交检查",
			trigger:   "--commit",
			owner:     "gatekeeper",
			summary:   "提交前门禁，扫描 git diff 并生成风险清单，高风险逐项确认。",
			content:   "gatekeeper 扫描 diff；有高风险项则逐项向用户确认；全部确认后 commit，不自动 push。",
			tags:      []string{"routing", "commit", "gate"},
			status:    "ready",
			updatedAt: "2026-06-20 11:06",
		},
		{
			id:        "refactor",
			name:      "--refactor 重构",
			trigger:   "--refactor",
			owner:     "prometheus",
			summary:   "大范围结构调整但外部行为不变，按影响面分析、分步方案和分阶段验证推进。",
			content:   "codegraph_impact 分析影响面；prometheus 设计可编译分步方案；hephaestus 分阶段实现；3 reviewer 并行审核。",
			tags:      []string{"routing", "refactor", "staged"},
			status:    "ready",
			updatedAt: "2026-06-20 11:07",
		},
		{
			id:        "lark-integration",
			name:      "--lark 飞书操作",
			trigger:   "--lark",
			owner:     "librarian",
			summary:   "飞书文档、消息、多维表格等操作入口，路由到具体 lark-* skill。",
			content:   "查飞书文档、发消息或操作多维表格时触发 feishu skill；--feat 阅读飞书需求文档时也可自动触发。",
			tags:      []string{"routing", "lark", "integration"},
			status:    "ready",
			updatedAt: "2026-06-20 11:08",
		},
	}
}

func btdProjectConfigCopies(library TemplateLibrary) []ProjectCopy {
	copies := make([]ProjectCopy, 0, len(library.Agents)+len(library.Rules)+len(library.Skills)+len(library.Workflows))
	for index, item := range library.Agents {
		copies = append(copies, projectCopyFromTemplate(item, index, btdCopyStatus(item.ID), btdCopyDiff(item.ID)))
	}
	for index, item := range library.Rules {
		copies = append(copies, projectCopyFromTemplate(item, index, btdCopyStatus(item.ID), btdCopyDiff(item.ID)))
	}
	for index, item := range library.Skills {
		copies = append(copies, projectCopyFromTemplate(item, index, btdCopyStatus(item.ID), btdCopyDiff(item.ID)))
	}
	for index, item := range library.Workflows {
		copies = append(copies, projectCopyFromTemplate(item, index, btdCopyStatus(item.ID), btdCopyDiff(item.ID)))
	}
	return copies
}

func projectCopyFromTemplate(item TemplateItem, index int, status string, diff string) ProjectCopy {
	path := item.Entry
	if path == "" && len(item.SourcePaths) > 0 {
		path = item.SourcePaths[0]
	}
	copy := ProjectCopy{
		ID:           "proj_" + item.Kind + "_btd_" + strings.ReplaceAll(item.ID, "-", "_"),
		Kind:         item.Kind,
		Name:         item.Name,
		LocalVersion: 1 + index%3,
		SyncMode:     "manual",
		Status:       status,
		Path:         path,
		Diff:         diff,
	}
	if status != "detached" {
		copy.Origin = &Origin{
			TemplateID:  item.ID,
			BaseVersion: item.Version,
			BaseHash:    contentHash(item.ID, item.Version),
		}
	}
	return copy
}

func btdCopyStatus(templateID string) string {
	switch templateID {
	case "worker", "00-routing", "feature-development", "code-review":
		return "template_updated"
	case "quick", "03-project-model", "quest-system", "modify-existing", "bugfix":
		return "project_modified"
	case "high-risk-api", "refactor":
		return "diverged"
	case "lark-integration":
		return "detached"
	default:
		return "synced"
	}
}

func btdCopyDiff(templateID string) string {
	switch templateID {
	case "worker":
		return "- project: model_reasoning_effort = \"medium\"\n+ template: model_reasoning_effort = \"high\""
	case "00-routing":
		return "+ template: expanded --rev reviewer routing and workflow phase checkpoints."
	case "feature-development":
		return "+ template: align --feat workflow with latest dev-workflow and testing rules."
	case "code-review":
		return "+ template: route review through logic / perf / security reviewer fan-out."
	case "quick":
		return "+ project: tightened quick role to single-file fixes only."
	case "03-project-model":
		return "+ project: actor index rebuild note added for local btd-game-server development."
	case "quest-system":
		return "+ project: added local quest pool capacity checklist."
	case "high-risk-api":
		return "- template: generic high-risk checklist\n+ project: payment rollback and GM currency checklist"
	case "modify-existing":
		return "+ project: local module-impact checklist added before implementation."
	case "bugfix":
		return "+ project: local log capture path added for btd runtime crashes."
	case "refactor":
		return "- template: generic staged refactor\n+ project: extra actor impact review and reviewer fan-out"
	case "lark-integration":
		return "Detached because this project currently handles Feishu operations outside the repo workflow."
	default:
		return "No content changes. Project copy is synced with the Template Library baseline."
	}
}

func mockWorkflows() []WorkflowSummary {
	specs := btdWorkflowSpecs()
	graphs := btdWorkflowGraphs()
	workflows := make([]WorkflowSummary, 0, len(specs))
	for _, spec := range specs {
		graph := graphs[spec.id]
		workflows = append(workflows, WorkflowSummary{
			ID:        spec.id,
			Name:      spec.name,
			Status:    spec.status,
			UpdatedAt: spec.updatedAt,
			Owner:     spec.owner,
			Trigger:   spec.trigger,
			Tags:      append([]string(nil), spec.tags...),
			Summary:   spec.summary,
			NodeCount: len(graph.Nodes),
			EdgeCount: len(graph.Edges),
		})
	}
	return workflows
}

func btdWorkflowGraphs() map[string]WorkflowGraph {
	graphs := make(map[string]WorkflowGraph)
	for _, spec := range btdWorkflowSpecs() {
		graphs[spec.id] = workflowGraphFromRoutingSpec(spec)
	}
	return graphs
}

func workflowGraphFromRoutingSpec(spec btdWorkflowSpec) WorkflowGraph {
	nodes := []WorkflowNode{
		{ID: "trigger", Type: "input", Category: "event", Label: spec.trigger, Agent: "-", Detail: "User message starts with " + spec.trigger + " and the rest becomes workflow input.", X: 48, Y: 210},
		{ID: "route", Type: "condition", Category: "condition", Label: "routing match", Agent: "sisyphus", Detail: "Match the command section in .claude/rules/go-00-routing.md.", X: 300, Y: 210},
		{ID: "owner", Type: "agent", Category: "action", Label: spec.owner, Agent: spec.owner, Detail: spec.summary, X: 560, Y: 210},
		{ID: "skills", Type: "transform", Category: "data", Label: "load skills", Agent: "skill resolver", Detail: spec.content, X: 820, Y: 210},
		{ID: "verify", Type: "human_approval", Category: "human", Label: "checkpoint", Agent: "owner", Detail: "Each phase reports progress; risky or ambiguous work waits for confirmation.", X: 1080, Y: 210},
		{ID: "output", Type: "output", Category: "data", Label: "deliver", Agent: spec.owner, Detail: "Return result, evidence, and next action according to the workflow section.", X: 1340, Y: 210},
	}
	edges := []WorkflowEdge{
		{From: "trigger", To: "route", Label: "command"},
		{From: "route", To: "owner", Label: "matched"},
		{From: "owner", To: "skills", Label: "context"},
		{From: "skills", To: "verify", Label: "steps"},
		{From: "verify", To: "output", Label: "approved"},
	}
	if spec.id == "code-review" || spec.id == "refactor" {
		nodes = append(nodes,
			WorkflowNode{ID: "logic", Type: "agent", Category: "action", Label: "reviewer-logic", Agent: "reviewer-logic", Detail: "Check control flow, state, transaction, nil, and boundary risks.", X: 820, Y: 70},
			WorkflowNode{ID: "perf", Type: "agent", Category: "action", Label: "reviewer-perf", Agent: "reviewer-perf", Detail: "Check hot paths, allocations, loops, locks, and goroutine risks.", X: 820, Y: 350},
			WorkflowNode{ID: "security", Type: "agent", Category: "action", Label: "reviewer-security", Agent: "reviewer-security", Detail: "Check permissions, input validation, leakage, replay, and economy risks.", X: 1080, Y: 350},
			WorkflowNode{ID: "join", Type: "join", Category: "data", Label: "merge findings", Agent: "sisyphus", Detail: "Deduplicate findings and sort by severity.", X: 1080, Y: 70},
		)
		edges = append(edges,
			WorkflowEdge{From: "owner", To: "logic", Label: "diff"},
			WorkflowEdge{From: "owner", To: "perf", Label: "diff"},
			WorkflowEdge{From: "owner", To: "security", Label: "diff"},
			WorkflowEdge{From: "logic", To: "join", Label: "findings"},
			WorkflowEdge{From: "perf", To: "join", Label: "findings"},
			WorkflowEdge{From: "security", To: "join", Label: "findings"},
			WorkflowEdge{From: "join", To: "verify", Label: "report"},
		)
	}
	return WorkflowGraph{
		ID:    spec.id,
		Name:  spec.name,
		Nodes: nodes,
		Edges: edges,
	}
}

func collectionKind(kind string) (string, bool) {
	switch strings.ToLower(strings.TrimSpace(kind)) {
	case "agents", "agent":
		return "agents", true
	case "rules", "rule":
		return "rules", true
	case "skills", "skill":
		return "skills", true
	case "workflows", "workflow":
		return "workflows", true
	default:
		return "", false
	}
}

func singularKind(kind string) (string, bool) {
	collection, ok := collectionKind(kind)
	if !ok {
		return "", false
	}
	return strings.TrimSuffix(collection, "s"), true
}

func slugify(value string) string {
	value = strings.ToLower(strings.TrimSpace(value))
	var builder strings.Builder
	lastDash := false
	for _, r := range value {
		isAlphaNum := (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9')
		if isAlphaNum {
			builder.WriteRune(r)
			lastDash = false
			continue
		}
		if !lastDash && builder.Len() > 0 {
			builder.WriteRune('-')
			lastDash = true
		}
	}
	slug := strings.Trim(builder.String(), "-")
	if slug == "" {
		return "item"
	}
	return slug
}

func uniqueID(base string, existing map[string]bool) string {
	if base == "" {
		base = "item"
	}
	id := base
	for suffix := 2; existing[id]; suffix++ {
		id = fmt.Sprintf("%s-%d", base, suffix)
	}
	return id
}

func uniqueTemplateID(items []TemplateItem, base string) string {
	existing := make(map[string]bool, len(items))
	for _, item := range items {
		existing[item.ID] = true
	}
	return uniqueID(base, existing)
}

func baseName(path string) string {
	trimmed := strings.Trim(strings.TrimSpace(path), `\/`)
	if trimmed == "" {
		return ""
	}
	parts := strings.FieldsFunc(trimmed, func(r rune) bool {
		return r == '\\' || r == '/'
	})
	return parts[len(parts)-1]
}

func defaultString(value string, fallback string) string {
	if strings.TrimSpace(value) == "" {
		return fallback
	}
	return strings.TrimSpace(value)
}

func nowStamp() string {
	return time.Now().Format("2006-01-02 15:04")
}

func contentHash(seed string, version int) string {
	return fmt.Sprintf("sha256:%s-v%d", slugify(seed), version)
}

func cloneBootstrap(data BootstrapData) BootstrapData {
	return BootstrapData{
		TemplateLibrary: TemplateLibrary{
			Agents:    cloneTemplateItems(data.TemplateLibrary.Agents),
			Rules:     cloneTemplateItems(data.TemplateLibrary.Rules),
			Skills:    cloneTemplateItems(data.TemplateLibrary.Skills),
			Workflows: cloneTemplateItems(data.TemplateLibrary.Workflows),
		},
		Projects:          cloneProjects(data.Projects),
		ProjectConfigSets: cloneProjectConfigSets(data.ProjectConfigSets),
	}
}

func cloneProjects(projects []Project) []Project {
	cloned := make([]Project, len(projects))
	copy(cloned, projects)
	return cloned
}

func cloneProjectConfigSets(configSets map[string][]ProjectCopy) map[string][]ProjectCopy {
	cloned := make(map[string][]ProjectCopy, len(configSets))
	for projectID, copies := range configSets {
		cloned[projectID] = cloneProjectCopies(copies)
	}
	return cloned
}

func cloneProjectCopies(copies []ProjectCopy) []ProjectCopy {
	cloned := make([]ProjectCopy, len(copies))
	for index, copy := range copies {
		cloned[index] = cloneProjectCopy(copy)
	}
	return cloned
}

func cloneProjectCopy(copy ProjectCopy) ProjectCopy {
	if copy.Origin != nil {
		origin := *copy.Origin
		copy.Origin = &origin
	}
	return copy
}

func cloneTemplateItems(items []TemplateItem) []TemplateItem {
	cloned := make([]TemplateItem, len(items))
	for index, item := range items {
		cloned[index] = cloneTemplateItem(item)
	}
	return cloned
}

func cloneTemplateItem(item TemplateItem) TemplateItem {
	item.Files = append([]string(nil), item.Files...)
	item.ApplicableAgents = append([]string(nil), item.ApplicableAgents...)
	item.RelatedRules = append([]string(nil), item.RelatedRules...)
	item.RelatedSkills = append([]string(nil), item.RelatedSkills...)
	item.Tools = append([]string(nil), item.Tools...)
	item.MCP = append([]string(nil), item.MCP...)
	item.SourcePaths = append([]string(nil), item.SourcePaths...)
	return item
}

func cloneWorkflowSummaries(workflows []WorkflowSummary) []WorkflowSummary {
	cloned := make([]WorkflowSummary, len(workflows))
	for index, workflow := range workflows {
		cloned[index] = workflow
		cloned[index].Tags = append([]string(nil), workflow.Tags...)
	}
	return cloned
}

func cloneWorkflowGraph(graph WorkflowGraph) WorkflowGraph {
	graph.Nodes = append([]WorkflowNode(nil), graph.Nodes...)
	graph.Edges = append([]WorkflowEdge(nil), graph.Edges...)
	return graph
}

func cloneWorkflowGraphMap(graphs map[string]WorkflowGraph) map[string]WorkflowGraph {
	cloned := make(map[string]WorkflowGraph, len(graphs))
	for id, graph := range graphs {
		cloned[id] = cloneWorkflowGraph(graph)
	}
	return cloned
}
