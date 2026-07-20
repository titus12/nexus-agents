package knowledgesync

import (
	"context"
	"errors"
	"fmt"
	"log"
	"path/filepath"
	"strings"
	"time"

	"nexus-agents/internal/knowledgebase"
	"nexus-agents/internal/knowledgegraph"
)

type FindOptions struct {
	Mode      string
	Limit     int
	MaxTokens int
	Engine    string
	Scope     string
}

func (s *Service) FindKnowledge(
	ctx context.Context,
	primary ProjectRequest,
	projects []ProjectRequest,
	query string,
	options FindOptions,
) (knowledgebase.RetrievalResult, error) {
	if err := validateProjectRequest(primary); err != nil {
		return knowledgebase.RetrievalResult{}, err
	}
	query = strings.TrimSpace(query)
	if query == "" {
		return knowledgebase.RetrievalResult{}, fmt.Errorf("knowledge query is empty")
	}
	projects = normalizeFindProjects(primary, projects)
	baseline, err := knowledgebase.Retrieve(primary.ProjectRoot, query, knowledgebase.RetrieveOptions{
		Mode: options.Mode, Limit: options.Limit, MaxTokens: options.MaxTokens,
	})
	if err != nil {
		return knowledgebase.RetrievalResult{}, err
	}
	baseline.Scope = defaultFindScope(options.Scope, len(projects))
	baseline.ProjectIDs = findProjectIDs(projects)
	engine := strings.ToLower(strings.TrimSpace(options.Engine))
	if engine == "fts5" {
		baseline.Engine = "fts5"
		return baseline, nil
	}

	coordinator, ok := s.knowledgeGraph.(knowledgegraph.SearchCoordinator)
	if !ok {
		return degradedFindFallback(baseline, knowledgegraph.ErrProviderDisabled), nil
	}
	primaryProfile, err := LoadProfile(primary.ProjectRoot)
	if err != nil {
		return degradedFindFallback(baseline, err), nil
	}
	if !primaryProfile.KnowledgeGraph.Enabled {
		return degradedFindFallback(baseline, knowledgegraph.ErrProviderDisabled), nil
	}

	sourceIDs := make([]string, 0, len(projects))
	searchProjects := make([]ProjectRequest, 0, len(projects))
	for _, project := range projects {
		profile, loadErr := LoadProfile(project.ProjectRoot)
		if loadErr != nil {
			if errors.Is(loadErr, ErrProfileNotFound) {
				continue
			}
			return degradedFindFallback(baseline, loadErr), nil
		}
		if !profile.KnowledgeGraph.Enabled {
			continue
		}
		sourceIDs = append(sourceIDs, graphSourceID(profile, project.ProjectID))
		searchProjects = append(searchProjects, project)
	}
	sourceIDs = uniqueSorted(sourceIDs)
	if len(sourceIDs) == 0 {
		return degradedFindFallback(baseline, fmt.Errorf("no GBrain sources are enabled for the project scope")), nil
	}
	normalizedQuery := normalizedGraphSearchQuery(baseline)
	limit := options.Limit
	if limit <= 0 {
		limit = primaryProfile.KnowledgeGraph.Query.MaxResults
	}
	if limit <= 0 {
		limit = 20
	}
	timeout := time.Duration(primaryProfile.KnowledgeGraph.Query.TimeoutSeconds) * time.Second
	if timeout <= 0 {
		timeout = 10 * time.Second
	}
	searchContext, cancel := context.WithTimeout(ctx, timeout)
	searchResult, searchErr := coordinator.Search(searchContext, knowledgegraph.GraphSearchQuery{
		Query: normalizedQuery, SourceIDs: sourceIDs, Limit: limit,
	})
	cancel()
	if searchErr != nil {
		return degradedFindFallback(baseline, searchErr), nil
	}
	documents, err := coordinator.LoadSearchDocuments(findProjectIDs(searchProjects), searchResult.Hits)
	if err != nil {
		return degradedFindFallback(baseline, err), nil
	}
	if len(documents) == 0 {
		return degradedFindFallback(baseline, fmt.Errorf("GBrain returned no loadable approved documents")), nil
	}

	contextDocuments := make([]knowledgebase.ContextPackDocument, 0, len(documents))
	for _, document := range documents {
		contextDocuments = append(contextDocuments, knowledgebase.ContextPackDocument{
			KnowledgeSource: knowledgebase.KnowledgeSource{
				ProjectID: document.ProjectID, SourceID: document.SourceID,
				Path: document.CanonicalPath, Title: document.Title,
				Revision: document.Revision, Score: document.Score, Snippet: document.Snippet,
			},
			Content: document.Content,
		})
	}
	contextPack, sources, required, omitted, budget := knowledgebase.BuildContextPack(contextDocuments, options.MaxTokens)
	if len(sources) == 0 {
		return degradedFindFallback(baseline, fmt.Errorf("GBrain results exceeded the context budget")), nil
	}
	baseline.Engine = "gbrain"
	baseline.NormalizedQuery = normalizedQuery
	baseline.Sources = sources
	baseline.Required = required
	baseline.Optional = []knowledgebase.KnowledgeContextItem{}
	baseline.Related = []knowledgebase.KnowledgeContextItem{}
	baseline.Omitted = omitted
	baseline.LoadedKnowledgeMarkdown = contextPack
	baseline.TokenBudget = budget
	baseline.MatchedDomain = findMatchedDomain(sources, baseline.MatchedDomain)
	baseline.Reason = fmt.Sprintf("GBrain matched %d approved knowledge documents across %d project sources.", len(sources), len(sourceIDs))
	baseline.Degraded = false
	baseline.FallbackReason = ""
	log.Printf("[knowledge-find] project=%s engine=gbrain scope=%s projects=%d sources=%d query=%q normalized_query=%q documents=%d tokens=%d/%d",
		primary.ProjectID, baseline.Scope, len(projects), len(sourceIDs), query, normalizedQuery,
		len(sources), budget.UsedTokens, budget.MaxTokens)
	return baseline, nil
}

func normalizeFindProjects(primary ProjectRequest, projects []ProjectRequest) []ProjectRequest {
	result := []ProjectRequest{primary}
	seen := map[string]bool{primary.ProjectID: true}
	for _, project := range projects {
		project.ProjectID = strings.TrimSpace(project.ProjectID)
		project.ProjectRoot = filepath.Clean(strings.TrimSpace(project.ProjectRoot))
		if project.ProjectID == "" || project.ProjectRoot == "" || seen[project.ProjectID] {
			continue
		}
		seen[project.ProjectID] = true
		result = append(result, project)
	}
	return result
}

func findProjectIDs(projects []ProjectRequest) []string {
	result := make([]string, 0, len(projects))
	for _, project := range projects {
		result = append(result, project.ProjectID)
	}
	return result
}

func defaultFindScope(scope string, projectCount int) string {
	scope = strings.ToLower(strings.TrimSpace(scope))
	if scope != "" {
		return scope
	}
	if projectCount > 1 {
		return "group"
	}
	return "project"
}

func degradedFindFallback(result knowledgebase.RetrievalResult, reason error) knowledgebase.RetrievalResult {
	result.Engine = "fts5"
	result.Degraded = true
	if reason != nil {
		result.FallbackReason = reason.Error()
	}
	result.Reason = "GBrain retrieval was unavailable; Nexus returned the project-local FTS5 fallback. " + result.Reason
	log.Printf("[knowledge-find] engine=fts5 status=degraded query=%q reason=%q", result.Query, result.FallbackReason)
	return result
}

func findMatchedDomain(sources []knowledgebase.KnowledgeSource, fallback string) string {
	counts := map[string]int{}
	best := strings.TrimSpace(fallback)
	bestCount := 0
	for _, source := range sources {
		parts := strings.Split(filepath.ToSlash(source.Path), "/")
		for index, part := range parts {
			if part == "domains" && index+1 < len(parts) {
				counts[parts[index+1]]++
				if counts[parts[index+1]] > bestCount {
					best = parts[index+1]
					bestCount = counts[parts[index+1]]
				}
				break
			}
		}
	}
	return best
}
