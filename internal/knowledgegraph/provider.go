package knowledgegraph

import (
	"context"
	"errors"
	"sync"
	"time"
)

var (
	ErrProviderDisabled     = errors.New("knowledge graph provider is disabled")
	ErrProviderUnavailable  = errors.New("knowledge graph provider is unavailable")
	ErrOperationUnavailable = errors.New("knowledge graph operation is not available in the current implementation phase")
)

type KnowledgeGraphProvider interface {
	Start(ctx context.Context) error
	Stop(ctx context.Context) error
	Health(ctx context.Context) (GraphHealth, error)
	SyncSource(ctx context.Context, source GraphSource) (GraphSyncResult, error)
	RemoveSource(ctx context.Context, sourceID string) error
	Search(ctx context.Context, query GraphSearchQuery) (GraphSearchResult, error)
	Traverse(ctx context.Context, query GraphTraversalQuery) (GraphTraversalResult, error)
	Synthesize(ctx context.Context, query GraphSynthesisQuery) (GraphSynthesisResult, error)
	FindGaps(ctx context.Context, scope GraphGapScope) ([]KnowledgeGap, error)
}

type NoopProvider struct{}

func (NoopProvider) Start(context.Context) error { return nil }
func (NoopProvider) Stop(context.Context) error  { return nil }
func (NoopProvider) Health(context.Context) (GraphHealth, error) {
	return GraphHealth{
		Provider: "noop", Status: GraphStatusDisabled, LastCheckedAt: time.Now().UTC(),
	}, nil
}
func (NoopProvider) SyncSource(context.Context, GraphSource) (GraphSyncResult, error) {
	return GraphSyncResult{}, ErrProviderDisabled
}
func (NoopProvider) RemoveSource(context.Context, string) error {
	return ErrProviderDisabled
}
func (NoopProvider) Search(context.Context, GraphSearchQuery) (GraphSearchResult, error) {
	return GraphSearchResult{}, ErrProviderDisabled
}
func (NoopProvider) Traverse(context.Context, GraphTraversalQuery) (GraphTraversalResult, error) {
	return GraphTraversalResult{}, ErrProviderDisabled
}
func (NoopProvider) Synthesize(context.Context, GraphSynthesisQuery) (GraphSynthesisResult, error) {
	return GraphSynthesisResult{}, ErrProviderDisabled
}
func (NoopProvider) FindGaps(context.Context, GraphGapScope) ([]KnowledgeGap, error) {
	return nil, ErrProviderDisabled
}

type FakeProvider struct {
	mu sync.Mutex

	HealthResult    GraphHealth
	HealthError     error
	SyncResult      GraphSyncResult
	SyncError       error
	SearchResult    GraphSearchResult
	SearchError     error
	TraversalResult GraphTraversalResult
	TraversalError  error
	SynthesisResult GraphSynthesisResult
	SynthesisError  error
	GapsResult      []KnowledgeGap
	GapsError       error

	StartCalls       int
	StopCalls        int
	SyncedSources    []GraphSource
	RemovedSourceIDs []string
	SearchQueries    []GraphSearchQuery
	TraversalQueries []GraphTraversalQuery
	SynthesisQueries []GraphSynthesisQuery
	GapScopes        []GraphGapScope
}

func (f *FakeProvider) Start(context.Context) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.StartCalls++
	return nil
}

func (f *FakeProvider) Stop(context.Context) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.StopCalls++
	return nil
}

func (f *FakeProvider) Health(context.Context) (GraphHealth, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.HealthResult, f.HealthError
}

func (f *FakeProvider) SyncSource(_ context.Context, source GraphSource) (GraphSyncResult, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.SyncedSources = append(f.SyncedSources, source)
	return f.SyncResult, f.SyncError
}

func (f *FakeProvider) RemoveSource(_ context.Context, sourceID string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.RemovedSourceIDs = append(f.RemovedSourceIDs, sourceID)
	return f.SyncError
}

func (f *FakeProvider) Search(_ context.Context, query GraphSearchQuery) (GraphSearchResult, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.SearchQueries = append(f.SearchQueries, query)
	return f.SearchResult, f.SearchError
}

func (f *FakeProvider) Traverse(_ context.Context, query GraphTraversalQuery) (GraphTraversalResult, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.TraversalQueries = append(f.TraversalQueries, query)
	return f.TraversalResult, f.TraversalError
}

func (f *FakeProvider) Synthesize(_ context.Context, query GraphSynthesisQuery) (GraphSynthesisResult, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.SynthesisQueries = append(f.SynthesisQueries, query)
	return f.SynthesisResult, f.SynthesisError
}

func (f *FakeProvider) FindGaps(_ context.Context, scope GraphGapScope) ([]KnowledgeGap, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.GapScopes = append(f.GapScopes, scope)
	return append([]KnowledgeGap(nil), f.GapsResult...), f.GapsError
}
