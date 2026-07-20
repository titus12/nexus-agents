package knowledgegraph

import (
	"context"
	"errors"
	"fmt"
	"log"
	"math"
	"path"
	"sort"
	"strings"
	"time"
)

const (
	defaultShadowSearchLimit   = 20
	defaultShadowSearchTimeout = 10 * time.Second
)

type ShadowSearchBaseline struct {
	MatchedProject      bool     `json:"matchedProject"`
	MatchedDomain       string   `json:"matchedDomain,omitempty"`
	SourceIDs           []string `json:"sourceIds,omitempty"`
	Paths               []string `json:"paths"`
	RequiredPaths       []string `json:"requiredPaths"`
	ScopedPaths         []string `json:"scopedPaths,omitempty"`
	ScopedRequiredPaths []string `json:"scopedRequiredPaths,omitempty"`
	MissingPaths        []string `json:"missingPaths"`
	LatencyMS           int64    `json:"latencyMs"`
	TokenCount          int      `json:"tokenCount"`
}

type ShadowSearchRequest struct {
	ProjectID     string               `json:"projectId"`
	ProjectRoot   string               `json:"projectRoot"`
	SourceID      string               `json:"sourceId"`
	SourceIDs     []string             `json:"sourceIds,omitempty"`
	Scope         string               `json:"scope,omitempty"`
	GroupID       string               `json:"groupId,omitempty"`
	Query         string               `json:"query"`
	SearchQuery   string               `json:"searchQuery,omitempty"`
	Limit         int                  `json:"limit"`
	Timeout       time.Duration        `json:"-"`
	ExpectedPaths []string             `json:"expectedPaths,omitempty"`
	FTS5          ShadowSearchBaseline `json:"fts5"`
}

type ShadowSearchEngineResult struct {
	MatchedProject  bool             `json:"matchedProject"`
	MatchedDomain   string           `json:"matchedDomain,omitempty"`
	Paths           []string         `json:"paths"`
	ScopedPaths     []string         `json:"scopedPaths,omitempty"`
	Hits            []GraphSearchHit `json:"hits"`
	DuplicatePaths  []string         `json:"duplicatePaths"`
	LatencyMS       int64            `json:"latencyMs"`
	TimedOut        bool             `json:"timedOut"`
	ProcessRestarts int              `json:"processRestarts"`
	Error           string           `json:"error,omitempty"`
}

type ShadowSearchComparison struct {
	DomainMatched             bool     `json:"domainMatched"`
	PathOverlap               []string `json:"pathOverlap"`
	RequiredMatchedPaths      []string `json:"requiredMatchedPaths"`
	MissingExpectedPaths      []string `json:"missingExpectedPaths"`
	DuplicateDocuments        []string `json:"duplicateDocuments"`
	RequiredDocumentPrecision float64  `json:"requiredDocumentPrecision"`
	ExpectedDocumentCoverage  float64  `json:"expectedDocumentCoverage"`
}

type ShadowSearchRun struct {
	ID               string                   `json:"id"`
	ProjectID        string                   `json:"projectId"`
	SourceID         string                   `json:"sourceId"`
	SourceIDs        []string                 `json:"sourceIds,omitempty"`
	ProviderSourceID string                   `json:"providerSourceId"`
	Scope            string                   `json:"scope,omitempty"`
	GroupID          string                   `json:"groupId,omitempty"`
	Status           string                   `json:"status"`
	Query            string                   `json:"query"`
	NormalizedQuery  string                   `json:"normalizedQuery"`
	FTS5             ShadowSearchBaseline     `json:"fts5"`
	GBrain           ShadowSearchEngineResult `json:"gbrain"`
	Comparison       ShadowSearchComparison   `json:"comparison"`
	StartedAt        string                   `json:"startedAt"`
	EndedAt          string                   `json:"endedAt"`
}

type ShadowSearchSummary struct {
	ProjectID                string  `json:"projectId"`
	Runs                     int     `json:"runs"`
	Succeeded                int     `json:"succeeded"`
	Degraded                 int     `json:"degraded"`
	Timeouts                 int     `json:"timeouts"`
	TimeoutRate              float64 `json:"timeoutRate"`
	DomainMatchRate          float64 `json:"domainMatchRate"`
	AverageRequiredPrecision float64 `json:"averageRequiredPrecision"`
	AverageExpectedCoverage  float64 `json:"averageExpectedCoverage"`
	DuplicatePageRate        float64 `json:"duplicatePageRate"`
	FTS5P95LatencyMS         int64   `json:"fts5P95LatencyMs"`
	GBrainP95LatencyMS       int64   `json:"gbrainP95LatencyMs"`
	MaxObservedRestartCount  int     `json:"maxObservedRestartCount"`
}

type ShadowSearchCoordinator interface {
	QueueShadowSearch(request ShadowSearchRequest) error
	ShadowSearchNow(ctx context.Context, request ShadowSearchRequest) (ShadowSearchRun, error)
	ShadowSearchRuns(projectID string, limit int) ([]ShadowSearchRun, error)
	ShadowSearchSummary(projectID string) (ShadowSearchSummary, error)
}

func (s *Service) QueueShadowSearch(request ShadowSearchRequest) error {
	if err := validateShadowSearchRequest(request); err != nil {
		return err
	}
	select {
	case s.shadowQueue <- request:
		return nil
	default:
		return fmt.Errorf("knowledge graph shadow search queue is full")
	}
}

func (s *Service) ShadowSearchNow(ctx context.Context, request ShadowSearchRequest) (run ShadowSearchRun, returnErr error) {
	if err := validateShadowSearchRequest(request); err != nil {
		return ShadowSearchRun{}, err
	}
	request = normalizeShadowSearchRequest(request)
	started := time.Now()
	run = ShadowSearchRun{
		ID:               newShadowSearchRunID(request.ProjectID),
		ProjectID:        request.ProjectID,
		SourceID:         request.SourceID,
		SourceIDs:        append([]string(nil), request.SourceIDs...),
		ProviderSourceID: StableProviderSourceID(request.SourceID),
		Scope:            request.Scope,
		GroupID:          request.GroupID,
		Status:           "running",
		Query:            request.Query,
		NormalizedQuery:  request.SearchQuery,
		FTS5:             normalizeShadowBaseline(request.FTS5),
		StartedAt:        started.UTC().Format(time.RFC3339Nano),
	}

	beforeHealth, _ := s.provider.Health(context.Background())
	searchContext, cancel := context.WithTimeout(ctx, request.Timeout)
	searchStarted := time.Now()
	result, err := s.provider.Search(searchContext, GraphSearchQuery{
		Query: request.SearchQuery, SourceIDs: request.SourceIDs, Limit: request.Limit,
	})
	cancel()
	run.GBrain.LatencyMS = time.Since(searchStarted).Milliseconds()
	afterHealth, _ := s.provider.Health(context.Background())
	run.GBrain.ProcessRestarts = max(0, afterHealth.RestartCount-beforeHealth.RestartCount)
	if err != nil {
		run.Status = "degraded"
		run.GBrain.TimedOut = errors.Is(err, context.DeadlineExceeded) || errors.Is(searchContext.Err(), context.DeadlineExceeded)
		run.GBrain.Error = boundedGraphError(err)
		returnErr = err
	} else {
		run.Status = "ready"
		run.GBrain.Hits = append([]GraphSearchHit(nil), result.Hits...)
		run.GBrain.Paths, run.GBrain.DuplicatePaths = graphHitPaths(result.Hits)
		run.GBrain.ScopedPaths = graphHitScopedPaths(result.Hits)
		run.GBrain.MatchedProject = len(run.GBrain.Paths) > 0
		run.GBrain.MatchedDomain = inferGraphDomain(result.Hits)
		run.Comparison = compareShadowSearch(run.FTS5, run.GBrain, request.ExpectedPaths)
	}
	run.EndedAt = time.Now().UTC().Format(time.RFC3339Nano)
	if saveErr := s.store.SaveShadowRun(run); saveErr != nil && returnErr == nil {
		returnErr = saveErr
	}
	s.logf("[knowledge-graph] shadow-search project=%s source=%s status=%s query=%q normalized_query=%q fts5_domain=%s gbrain_domain=%s domain_match=%t fts5_paths=%d gbrain_paths=%d overlap=%d required_precision=%.3f expected_coverage=%.3f fts5_ms=%d gbrain_ms=%d timeout=%t restarts=%d duplicates=%d error=%q",
		run.ProjectID, run.SourceID, run.Status, run.Query, run.NormalizedQuery, run.FTS5.MatchedDomain, run.GBrain.MatchedDomain,
		run.Comparison.DomainMatched, len(run.FTS5.Paths), len(run.GBrain.Paths), len(run.Comparison.PathOverlap),
		run.Comparison.RequiredDocumentPrecision, run.Comparison.ExpectedDocumentCoverage,
		run.FTS5.LatencyMS, run.GBrain.LatencyMS, run.GBrain.TimedOut, run.GBrain.ProcessRestarts,
		len(run.Comparison.DuplicateDocuments), run.GBrain.Error)
	return run, returnErr
}

func (s *Service) ShadowSearchRuns(projectID string, limit int) ([]ShadowSearchRun, error) {
	return s.store.ShadowRuns(projectID, limit)
}

func (s *Service) ShadowSearchSummary(projectID string) (ShadowSearchSummary, error) {
	runs, err := s.store.ShadowRuns(projectID, 500)
	if err != nil {
		return ShadowSearchSummary{}, err
	}
	summary := ShadowSearchSummary{ProjectID: projectID, Runs: len(runs)}
	ftsLatencies := make([]int64, 0, len(runs))
	graphLatencies := make([]int64, 0, len(runs))
	domainMatches := 0
	duplicateRuns := 0
	for _, run := range runs {
		if run.Status == "ready" {
			summary.Succeeded++
		} else {
			summary.Degraded++
		}
		if run.GBrain.TimedOut {
			summary.Timeouts++
		}
		if run.Comparison.DomainMatched {
			domainMatches++
		}
		if len(run.Comparison.DuplicateDocuments) > 0 {
			duplicateRuns++
		}
		summary.AverageRequiredPrecision += run.Comparison.RequiredDocumentPrecision
		summary.AverageExpectedCoverage += run.Comparison.ExpectedDocumentCoverage
		ftsLatencies = append(ftsLatencies, run.FTS5.LatencyMS)
		graphLatencies = append(graphLatencies, run.GBrain.LatencyMS)
		summary.MaxObservedRestartCount = max(summary.MaxObservedRestartCount, run.GBrain.ProcessRestarts)
	}
	if summary.Runs > 0 {
		total := float64(summary.Runs)
		summary.TimeoutRate = float64(summary.Timeouts) / total
		summary.DomainMatchRate = float64(domainMatches) / total
		summary.AverageRequiredPrecision /= total
		summary.AverageExpectedCoverage /= total
		summary.DuplicatePageRate = float64(duplicateRuns) / total
	}
	summary.FTS5P95LatencyMS = percentile95(ftsLatencies)
	summary.GBrainP95LatencyMS = percentile95(graphLatencies)
	return summary, nil
}

func (s *Service) runShadowQueue(ctx context.Context) {
	for {
		select {
		case <-ctx.Done():
			return
		case request := <-s.shadowQueue:
			if _, err := s.ShadowSearchNow(ctx, request); err != nil {
				log.Printf("[knowledge-graph] shadow-search project=%s status=degraded error=%q", request.ProjectID, boundedGraphError(err))
			}
		}
	}
}

func validateShadowSearchRequest(request ShadowSearchRequest) error {
	if strings.TrimSpace(request.ProjectID) == "" {
		return fmt.Errorf("shadow search project id is empty")
	}
	if strings.TrimSpace(request.SourceID) == "" {
		if len(request.SourceIDs) == 0 {
			return fmt.Errorf("shadow search source id is empty")
		}
	}
	if strings.TrimSpace(request.Query) == "" {
		return fmt.Errorf("shadow search query is empty")
	}
	return nil
}

func normalizeShadowSearchRequest(request ShadowSearchRequest) ShadowSearchRequest {
	request.ProjectID = strings.TrimSpace(request.ProjectID)
	request.SourceID = strings.TrimSpace(request.SourceID)
	request.SourceIDs = uniqueSortedPaths(append(append([]string(nil), request.SourceIDs...), request.SourceID))
	if request.SourceID == "" && len(request.SourceIDs) > 0 {
		request.SourceID = request.SourceIDs[0]
	}
	request.Scope = strings.ToLower(strings.TrimSpace(request.Scope))
	if request.Scope == "" {
		request.Scope = "project"
	}
	request.GroupID = strings.TrimSpace(request.GroupID)
	request.Query = strings.TrimSpace(request.Query)
	request.SearchQuery = strings.TrimSpace(request.SearchQuery)
	if request.SearchQuery == "" {
		request.SearchQuery = request.Query
	}
	if request.Limit <= 0 {
		request.Limit = defaultShadowSearchLimit
	}
	if request.Timeout <= 0 {
		request.Timeout = defaultShadowSearchTimeout
	}
	request.ExpectedPaths = uniqueSortedPaths(request.ExpectedPaths)
	return request
}

func normalizeShadowBaseline(baseline ShadowSearchBaseline) ShadowSearchBaseline {
	baseline.MatchedDomain = strings.TrimSpace(baseline.MatchedDomain)
	baseline.Paths = uniqueSortedPaths(baseline.Paths)
	baseline.SourceIDs = uniqueSortedPaths(baseline.SourceIDs)
	baseline.RequiredPaths = uniqueSortedPaths(baseline.RequiredPaths)
	baseline.ScopedPaths = uniqueSortedPaths(baseline.ScopedPaths)
	baseline.ScopedRequiredPaths = uniqueSortedPaths(baseline.ScopedRequiredPaths)
	baseline.MissingPaths = uniqueSortedPaths(baseline.MissingPaths)
	baseline.MatchedProject = baseline.MatchedProject || len(baseline.Paths) > 0
	return baseline
}

func graphHitPaths(hits []GraphSearchHit) ([]string, []string) {
	seen := map[string]bool{}
	duplicates := map[string]bool{}
	paths := make([]string, 0, len(hits))
	for _, hit := range hits {
		value := normalizeGraphComparablePath(hit.Path)
		if value == "" {
			continue
		}
		sourceID := strings.TrimSpace(hit.SourceID)
		key := sourceID + "::" + value
		if seen[key] {
			duplicates[key] = true
			continue
		}
		seen[key] = true
		paths = append(paths, value)
	}
	sort.Strings(paths)
	return paths, sortedMapKeys(duplicates)
}

func graphHitScopedPaths(hits []GraphSearchHit) []string {
	values := make([]string, 0, len(hits))
	for _, hit := range hits {
		sourceID := strings.TrimSpace(hit.SourceID)
		pathValue := normalizeGraphComparablePath(hit.Path)
		if sourceID == "" || pathValue == "" {
			continue
		}
		values = append(values, sourceID+"::"+pathValue)
	}
	return uniqueSortedPaths(values)
}

func compareShadowSearch(fts ShadowSearchBaseline, graph ShadowSearchEngineResult, expectedPaths []string) ShadowSearchComparison {
	ftsPaths := comparablePathSet(fts.Paths)
	graphPaths := comparablePathSet(graph.Paths)
	required := comparablePathSet(fts.RequiredPaths)
	if len(fts.ScopedPaths) > 0 {
		ftsPaths = comparableScopedPathSet(fts.ScopedPaths)
		graphPaths = comparableScopedPathSet(graph.ScopedPaths)
		required = comparableScopedPathSet(fts.ScopedRequiredPaths)
	}
	expected := comparablePathSet(expectedPaths)
	if len(fts.ScopedPaths) > 0 {
		expected = comparableExpectedPathSet(expectedPaths, fts.SourceIDs)
	}
	if len(expected) == 0 {
		expected = required
	}
	comparison := ShadowSearchComparison{
		DomainMatched:        sameDomain(fts.MatchedDomain, graph.MatchedDomain),
		PathOverlap:          intersectPathSets(ftsPaths, graphPaths),
		RequiredMatchedPaths: intersectPathSets(required, graphPaths),
		MissingExpectedPaths: subtractPathSets(expected, graphPaths),
		DuplicateDocuments:   append([]string(nil), graph.DuplicatePaths...),
	}
	if len(graphPaths) > 0 {
		comparison.RequiredDocumentPrecision = float64(len(comparison.RequiredMatchedPaths)) / float64(len(graphPaths))
	}
	if len(expected) > 0 {
		comparison.ExpectedDocumentCoverage = float64(len(expected)-len(comparison.MissingExpectedPaths)) / float64(len(expected))
	} else {
		comparison.ExpectedDocumentCoverage = 1
	}
	return comparison
}

func comparableScopedPathSet(values []string) map[string]bool {
	result := map[string]bool{}
	for _, value := range values {
		sourceID, pathValue, ok := strings.Cut(value, "::")
		if !ok {
			continue
		}
		sourceID = strings.TrimSpace(sourceID)
		pathValue = normalizeGraphComparablePath(pathValue)
		if sourceID != "" && pathValue != "" {
			result[sourceID+"::"+pathValue] = true
		}
	}
	return result
}

func comparableExpectedPathSet(values, sourceIDs []string) map[string]bool {
	if len(values) == 0 {
		return map[string]bool{}
	}
	result := map[string]bool{}
	for _, value := range values {
		if strings.Contains(value, "::") {
			for key := range comparableScopedPathSet([]string{value}) {
				result[key] = true
			}
			continue
		}
		normalized := normalizeGraphComparablePath(value)
		for _, sourceID := range sourceIDs {
			sourceID = strings.TrimSpace(sourceID)
			if sourceID != "" && normalized != "" {
				result[sourceID+"::"+normalized] = true
			}
		}
	}
	return result
}

func comparablePathSet(values []string) map[string]bool {
	result := map[string]bool{}
	for _, value := range values {
		if normalized := normalizeGraphComparablePath(value); normalized != "" {
			result[normalized] = true
		}
	}
	return result
}

func normalizeGraphComparablePath(value string) string {
	value = strings.TrimSpace(strings.ReplaceAll(value, "\\", "/"))
	value = strings.TrimPrefix(value, "./")
	value = strings.TrimPrefix(value, "KnowledgeBase/")
	value = strings.TrimSuffix(value, ".md")
	if value == "project/index" || value == "index" {
		return "project"
	}
	parts := strings.Split(value, "/")
	if len(parts) >= 4 && parts[0] == "project" && parts[1] == "domains" {
		domain := safeSlugSegment(parts[2])
		if len(parts) == 4 && parts[3] == "index" {
			return path.Join("domains", domain)
		}
		feature := append([]string{"features", domain}, parts[3:]...)
		if feature[len(feature)-1] == "index" {
			feature = feature[:len(feature)-1]
		}
		for index := 2; index < len(feature); index++ {
			feature[index] = safeSlugSegment(feature[index])
		}
		return path.Join(feature...)
	}
	if len(parts) >= 2 && parts[0] == "project" {
		document := append([]string{"documents"}, parts[1:]...)
		for index := 1; index < len(document); index++ {
			document[index] = safeSlugSegment(document[index])
		}
		return path.Join(document...)
	}
	return strings.Trim(path.Clean(value), "/.")
}

func inferGraphDomain(hits []GraphSearchHit) string {
	scores := map[string]float64{}
	for _, hit := range hits {
		value := normalizeGraphComparablePath(hit.Path)
		parts := strings.Split(value, "/")
		if len(parts) >= 2 && (parts[0] == "domains" || parts[0] == "features") {
			score := hit.Score
			if score <= 0 {
				score = 1
			}
			if parts[0] == "domains" && len(parts) == 2 {
				score += 1
			}
			scores[parts[1]] += score
		}
	}
	bestDomain := ""
	bestScore := float64(0)
	for domain, score := range scores {
		if score > bestScore || (score == bestScore && (bestDomain == "" || domain < bestDomain)) {
			bestDomain = domain
			bestScore = score
		}
	}
	return bestDomain
}

func sameDomain(left, right string) bool {
	left = strings.TrimSpace(left)
	right = strings.TrimSpace(right)
	if left == "" || right == "" {
		return left == right
	}
	left = safeSlugSegment(left)
	right = safeSlugSegment(right)
	return left == right
}

func uniqueSortedPaths(values []string) []string {
	seen := map[string]bool{}
	for _, value := range values {
		value = strings.TrimSpace(strings.ReplaceAll(value, "\\", "/"))
		if value != "" {
			seen[value] = true
		}
	}
	return sortedMapKeys(seen)
}

func sortedMapKeys(values map[string]bool) []string {
	result := make([]string, 0, len(values))
	for value := range values {
		result = append(result, value)
	}
	sort.Strings(result)
	return result
}

func intersectPathSets(left, right map[string]bool) []string {
	result := map[string]bool{}
	for value := range left {
		if right[value] {
			result[value] = true
		}
	}
	return sortedMapKeys(result)
}

func subtractPathSets(left, right map[string]bool) []string {
	result := map[string]bool{}
	for value := range left {
		if !right[value] {
			result[value] = true
		}
	}
	return sortedMapKeys(result)
}

func percentile95(values []int64) int64 {
	if len(values) == 0 {
		return 0
	}
	sorted := append([]int64(nil), values...)
	sort.Slice(sorted, func(i, j int) bool { return sorted[i] < sorted[j] })
	index := int(math.Ceil(float64(len(sorted))*0.95)) - 1
	if index < 0 {
		index = 0
	}
	return sorted[index]
}

func newShadowSearchRunID(projectID string) string {
	return "shadow-search-" + safeGraphID(projectID) + "-" + time.Now().UTC().Format("20060102T150405.000000000Z")
}
