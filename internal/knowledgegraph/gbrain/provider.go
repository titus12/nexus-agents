package gbrain

import (
	"context"
	"fmt"
	"sort"
	"strings"

	"nexus-agents/internal/knowledgegraph"
)

type Provider struct {
	process    *ProcessManager
	searchGate chan struct{}
}

func NewProvider(options ProcessOptions) *Provider {
	return &Provider{
		process:    NewProcessManager(options),
		searchGate: make(chan struct{}, 1),
	}
}

func (p *Provider) Start(ctx context.Context) error {
	return p.process.Start(ctx)
}

func (p *Provider) Stop(ctx context.Context) error {
	return p.process.Stop(ctx)
}

func (p *Provider) Health(ctx context.Context) (knowledgegraph.GraphHealth, error) {
	return p.process.Health(ctx)
}

type searchResult struct {
	Slug        string  `json:"slug"`
	PageID      int64   `json:"page_id"`
	Title       string  `json:"title"`
	Type        string  `json:"type"`
	ChunkText   string  `json:"chunk_text"`
	ChunkSource string  `json:"chunk_source"`
	ChunkID     int64   `json:"chunk_id"`
	ChunkIndex  int     `json:"chunk_index"`
	Score       float64 `json:"score"`
	SourceID    string  `json:"source_id"`
	Stale       bool    `json:"stale"`
}

func (p *Provider) Search(ctx context.Context, query knowledgegraph.GraphSearchQuery) (knowledgegraph.GraphSearchResult, error) {
	query.Query = strings.TrimSpace(query.Query)
	if query.Query == "" {
		return knowledgegraph.GraphSearchResult{}, fmt.Errorf("GBrain search query is empty")
	}
	if query.Limit <= 0 {
		query.Limit = 20
	}

	if err := p.acquireOperation(ctx); err != nil {
		return knowledgegraph.GraphSearchResult{}, err
	}
	defer p.releaseOperation()

	sourceIDs := uniqueSourceIDs(query.SourceIDs)
	if len(sourceIDs) == 0 {
		sourceIDs = []string{""}
	}
	hits := make([]knowledgegraph.GraphSearchHit, 0, query.Limit)
	for _, sourceID := range sourceIDs {
		providerSourceID := ""
		if sourceID != "" {
			providerSourceID = knowledgegraph.StableProviderSourceID(sourceID)
			if err := p.process.RestartWithEnvironment(ctx, map[string]string{"GBRAIN_SOURCE": providerSourceID}); err != nil {
				return knowledgegraph.GraphSearchResult{}, unavailable("switch search source to "+providerSourceID, err)
			}
		}
		client, err := p.process.Client()
		if err != nil {
			return knowledgegraph.GraphSearchResult{}, unavailable("get MCP client", err)
		}
		var results []searchResult
		if err := client.CallTool(ctx, "search", map[string]any{
			"query": query.Query,
			"limit": query.Limit,
			"mode":  "balanced",
		}, &results); err != nil {
			return knowledgegraph.GraphSearchResult{}, unavailable("search", err)
		}
		for rank, result := range results {
			slug := strings.TrimSpace(result.Slug)
			if slug == "" {
				continue
			}
			resultSourceID := sourceID
			if resultSourceID == "" {
				resultSourceID = strings.TrimSpace(result.SourceID)
			}
			score := result.Score
			if len(sourceIDs) > 1 {
				score = 1.0 / float64(60+rank+1)
			}
			hits = append(hits, knowledgegraph.GraphSearchHit{
				ID:       resultSourceID + ":" + slug,
				SourceID: resultSourceID,
				Path:     slug,
				Title:    result.Title,
				Snippet:  result.ChunkText,
				Score:    score,
				Metadata: map[string]any{
					"providerSourceId": providerSourceID,
					"pageId":           result.PageID,
					"type":             result.Type,
					"chunkId":          result.ChunkID,
					"chunkIndex":       result.ChunkIndex,
					"chunkSource":      result.ChunkSource,
					"stale":            result.Stale,
					"providerScore":    result.Score,
					"sourceRank":       rank + 1,
				},
			})
		}
	}
	sort.SliceStable(hits, func(i, j int) bool {
		if hits[i].Score != hits[j].Score {
			return hits[i].Score > hits[j].Score
		}
		if hits[i].SourceID != hits[j].SourceID {
			return hits[i].SourceID < hits[j].SourceID
		}
		return hits[i].Path < hits[j].Path
	})
	if len(hits) > query.Limit {
		hits = hits[:query.Limit]
	}
	return knowledgegraph.GraphSearchResult{Query: query.Query, Hits: hits}, nil
}

func (p *Provider) acquireOperation(ctx context.Context) error {
	select {
	case p.searchGate <- struct{}{}:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func (p *Provider) releaseOperation() {
	<-p.searchGate
}

func uniqueSourceIDs(values []string) []string {
	seen := map[string]bool{}
	result := make([]string, 0, len(values))
	for _, value := range values {
		value = strings.TrimSpace(value)
		if value == "" || seen[value] {
			continue
		}
		seen[value] = true
		result = append(result, value)
	}
	return result
}

func (p *Provider) Traverse(context.Context, knowledgegraph.GraphTraversalQuery) (knowledgegraph.GraphTraversalResult, error) {
	return knowledgegraph.GraphTraversalResult{}, knowledgegraph.ErrOperationUnavailable
}

func (p *Provider) Synthesize(context.Context, knowledgegraph.GraphSynthesisQuery) (knowledgegraph.GraphSynthesisResult, error) {
	return knowledgegraph.GraphSynthesisResult{}, knowledgegraph.ErrOperationUnavailable
}

func (p *Provider) FindGaps(context.Context, knowledgegraph.GraphGapScope) ([]knowledgegraph.KnowledgeGap, error) {
	return nil, knowledgegraph.ErrOperationUnavailable
}

var _ knowledgegraph.KnowledgeGraphProvider = (*Provider)(nil)
