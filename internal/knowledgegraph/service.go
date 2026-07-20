package knowledgegraph

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

type SyncRequest struct {
	ProjectID       string
	ProjectRoot     string
	SourceID        string
	Branch          string
	Revision        string
	Force           bool
	RetryDelay      time.Duration
	MaxRetries      int
	Attempt         int
	IncludeDomains  bool
	IncludeFeatures bool
}

type SyncCoordinator interface {
	QueueSync(request SyncRequest) error
	SyncNow(ctx context.Context, request SyncRequest) (GraphSyncResult, error)
	State(projectID string) (SyncState, error)
	Runs(projectID string) ([]SyncRun, error)
}

type ServiceOptions struct {
	Provider        KnowledgeGraphProvider
	Store           *GraphStateStore
	ExportRoot      func(projectID string) string
	Exporter        func(request ExportRequest) (SourceExport, error)
	QueueSize       int
	ShadowQueueSize int
	Logf            func(format string, args ...any)
}

type Service struct {
	provider   KnowledgeGraphProvider
	store      *GraphStateStore
	exportRoot func(projectID string) string
	exporter   func(request ExportRequest) (SourceExport, error)
	logf       func(format string, args ...any)

	queue       chan SyncRequest
	shadowQueue chan ShadowSearchRequest
	startMu     sync.Mutex
	started     bool
	context     context.Context
	cancel      context.CancelFunc
	syncMu      sync.Mutex
	workerWG    sync.WaitGroup
}

func NewService(options ServiceOptions) *Service {
	if options.Provider == nil {
		options.Provider = NoopProvider{}
	}
	if options.Store == nil {
		options.Store = NewGraphStateStore("")
	}
	if options.ExportRoot == nil {
		options.ExportRoot = DefaultSourceExportRoot
	}
	if options.Exporter == nil {
		options.Exporter = ExportApprovedKnowledge
	}
	if options.QueueSize <= 0 {
		options.QueueSize = 64
	}
	if options.ShadowQueueSize <= 0 {
		options.ShadowQueueSize = 128
	}
	if options.Logf == nil {
		options.Logf = log.Printf
	}
	return &Service{
		provider: options.Provider, store: options.Store, exportRoot: options.ExportRoot,
		exporter: options.Exporter, logf: options.Logf,
		queue:       make(chan SyncRequest, options.QueueSize),
		shadowQueue: make(chan ShadowSearchRequest, options.ShadowQueueSize),
	}
}

func (s *Service) Start(ctx context.Context) error {
	s.startMu.Lock()
	if s.started {
		s.startMu.Unlock()
		return nil
	}
	s.context, s.cancel = context.WithCancel(ctx)
	s.started = true
	serviceContext := s.context
	s.startMu.Unlock()
	s.workerWG.Add(2)
	go func() {
		defer s.workerWG.Done()
		s.runQueue(serviceContext)
	}()
	go func() {
		defer s.workerWG.Done()
		s.runShadowQueue(serviceContext)
	}()
	return s.provider.Start(serviceContext)
}

func (s *Service) Stop(ctx context.Context) error {
	s.startMu.Lock()
	if s.cancel != nil {
		s.cancel()
	}
	s.started = false
	s.context = nil
	s.cancel = nil
	s.startMu.Unlock()
	workerDone := make(chan struct{})
	go func() {
		s.workerWG.Wait()
		close(workerDone)
	}()
	select {
	case <-workerDone:
	case <-ctx.Done():
		return ctx.Err()
	}
	return s.provider.Stop(ctx)
}

func (s *Service) QueueSync(request SyncRequest) error {
	if err := validateSyncRequest(request); err != nil {
		return err
	}
	state, err := s.store.Load(request.ProjectID)
	if err != nil {
		return err
	}
	state.ProjectID = request.ProjectID
	state.ProjectRoot = filepath.Clean(request.ProjectRoot)
	state.SourceID = request.SourceID
	state.ProviderSourceID = StableProviderSourceID(request.SourceID)
	state.Status = SyncStatusPending
	state.Branch = request.Branch
	state.PendingRevision = request.Revision
	state.LastError = ""
	if err := s.store.Save(state); err != nil {
		return err
	}
	select {
	case s.queue <- request:
		return nil
	default:
		err := fmt.Errorf("knowledge graph sync queue is full")
		state.Status = SyncStatusDegraded
		state.LastError = err.Error()
		_ = s.store.Save(state)
		return err
	}
}

func (s *Service) SyncNow(ctx context.Context, request SyncRequest) (result GraphSyncResult, returnErr error) {
	if err := validateSyncRequest(request); err != nil {
		return GraphSyncResult{}, err
	}
	s.syncMu.Lock()
	defer s.syncMu.Unlock()

	started := time.Now()
	now := started.UTC().Format(time.RFC3339)
	providerSourceID := StableProviderSourceID(request.SourceID)
	state, err := s.store.Load(request.ProjectID)
	if err != nil {
		return GraphSyncResult{}, err
	}
	previousSourceID := state.SourceID
	previousProviderSourceID := state.ProviderSourceID
	if request.Attempt > 0 &&
		((state.PendingRevision != "" && state.PendingRevision != request.Revision) ||
			(state.Status == SyncStatusReady && state.LastSyncedRevision != "" && state.LastSyncedRevision != request.Revision)) {
		supersedingRevision := state.PendingRevision
		if supersedingRevision == "" {
			supersedingRevision = state.LastSyncedRevision
		}
		s.logf("[knowledge-graph] retry project=%s revision=%s status=skipped superseded_by=%s",
			request.ProjectID, request.Revision, supersedingRevision)
		return GraphSyncResult{
			SourceID: state.SourceID, ProviderSourceID: state.ProviderSourceID,
			Documents: state.Documents, Unchanged: state.Documents,
		}, nil
	}
	previousHashes := copyStringMap(state.DocumentHashes)
	if previousSourceID != "" &&
		(previousSourceID != request.SourceID || previousProviderSourceID != providerSourceID) {
		previousHashes = map[string]string{}
	}
	state.ProjectID = request.ProjectID
	state.ProjectRoot = filepath.Clean(request.ProjectRoot)
	state.SourceID = request.SourceID
	state.ProviderSourceID = providerSourceID
	state.Status = SyncStatusSyncing
	state.Branch = request.Branch
	state.PendingRevision = request.Revision
	state.LastAttemptAt = now
	state.LastError = ""
	if err := s.store.Save(state); err != nil {
		return GraphSyncResult{}, err
	}
	run := SyncRun{
		ID: newGraphRunID(request.ProjectID), ProjectID: request.ProjectID,
		SourceID: request.SourceID, ProviderSourceID: providerSourceID,
		Status: SyncStatusSyncing, Revision: request.Revision, Attempt: request.Attempt,
		StartedAt: now,
	}
	if err := s.store.SaveRun(run); err != nil {
		return GraphSyncResult{}, err
	}
	defer func() {
		if returnErr == nil {
			return
		}
		ended := time.Now().UTC().Format(time.RFC3339)
		state.Status = SyncStatusDegraded
		state.LastError = boundedGraphError(returnErr)
		state.LastAttemptAt = ended
		_ = s.store.Save(state)
		run.Status = SyncStatusDegraded
		run.Error = state.LastError
		run.EndedAt = ended
		_ = s.store.SaveRun(run)
		s.logf("[knowledge-graph] sync project=%s source=%s status=degraded attempt=%d duration_ms=%d error=%q",
			request.ProjectID, request.SourceID, request.Attempt, time.Since(started).Milliseconds(), state.LastError)
	}()

	exported, err := s.exporter(ExportRequest{
		ProjectID: request.ProjectID, ProjectRoot: request.ProjectRoot,
		SourceID: request.SourceID, ProviderSourceID: providerSourceID,
		Branch: request.Branch, Revision: request.Revision,
		ExportRoot:     s.exportRoot(request.ProjectID),
		IncludeDomains: request.IncludeDomains, IncludeFeatures: request.IncludeFeatures,
	})
	if err != nil {
		return GraphSyncResult{}, err
	}
	currentHashes := make(map[string]string, len(exported.Documents))
	changedDocuments := make([]GraphDocument, 0, len(exported.Documents))
	for _, document := range exported.Documents {
		currentHashes[document.Slug] = document.Hash
		previousHash, existed := previousHashes[document.Slug]
		if request.Force || !existed || previousHash != document.Hash {
			changedDocuments = append(changedDocuments, document)
		}
	}
	deleteSlugs := make([]string, 0)
	for slug := range previousHashes {
		if _, exists := currentHashes[slug]; !exists {
			deleteSlugs = append(deleteSlugs, slug)
		}
	}
	sort.Strings(deleteSlugs)

	created, updated, unchanged := compareDocumentHashes(previousHashes, currentHashes, request.Force)
	result = GraphSyncResult{
		SourceID: request.SourceID, ProviderSourceID: providerSourceID,
		Documents: len(exported.Documents), Created: created, Updated: updated,
		Deleted: len(deleteSlugs), Unchanged: unchanged,
	}
	providerResult, err := s.provider.SyncSource(ctx, GraphSource{
		ID: request.SourceID, ProviderSourceID: providerSourceID,
		ProjectID: request.ProjectID, Root: exported.Root, Revision: request.Revision,
		Documents: changedDocuments, AllDocuments: exported.Documents, DeleteSlugs: deleteSlugs,
	})
	if err != nil {
		return GraphSyncResult{}, err
	}
	if providerResult.ProviderSourceID != "" && providerResult.ProviderSourceID != providerSourceID {
		return GraphSyncResult{}, fmt.Errorf("provider source mismatch: got %s, require %s", providerResult.ProviderSourceID, providerSourceID)
	}
	if previousSourceID != "" && previousSourceID != request.SourceID {
		if err := s.provider.RemoveSource(ctx, previousSourceID); err != nil {
			return GraphSyncResult{}, fmt.Errorf("remove previous knowledge graph source %s: %w", previousSourceID, err)
		}
	}

	ended := time.Now().UTC().Format(time.RFC3339)
	state.Status = SyncStatusReady
	state.LastSyncedRevision = request.Revision
	state.PendingRevision = ""
	state.LastSourceHash = exported.Manifest.SourceHash
	state.Documents = len(exported.Documents)
	state.DocumentHashes = currentHashes
	state.LastAttemptAt = ended
	state.LastSuccessfulAt = ended
	state.LastError = ""
	if err := s.store.Save(state); err != nil {
		return GraphSyncResult{}, err
	}
	run.Status = SyncStatusReady
	run.SourceHash = exported.Manifest.SourceHash
	run.Documents = result.Documents
	run.Created = result.Created
	run.Updated = result.Updated
	run.Deleted = result.Deleted
	run.Unchanged = result.Unchanged
	run.EndedAt = ended
	if err := s.store.SaveRun(run); err != nil {
		return GraphSyncResult{}, err
	}
	s.logf("[knowledge-graph] sync project=%s source=%s provider_source=%s status=ready documents=%d created=%d updated=%d deleted=%d unchanged=%d duration_ms=%d",
		request.ProjectID, request.SourceID, providerSourceID, result.Documents, result.Created,
		result.Updated, result.Deleted, result.Unchanged, time.Since(started).Milliseconds())
	return result, nil
}

func (s *Service) State(projectID string) (SyncState, error) {
	return s.store.Load(projectID)
}

func (s *Service) Runs(projectID string) ([]SyncRun, error) {
	return s.store.Runs(projectID)
}

func (s *Service) runQueue(ctx context.Context) {
	for {
		select {
		case <-ctx.Done():
			return
		case request := <-s.queue:
			_, err := s.SyncNow(ctx, request)
			if err == nil || request.Attempt >= request.MaxRetries {
				continue
			}
			next := request
			next.Attempt++
			delay := request.RetryDelay
			if delay <= 0 {
				delay = 5 * time.Minute
			}
			go func() {
				timer := time.NewTimer(delay)
				defer timer.Stop()
				select {
				case <-ctx.Done():
					return
				case <-timer.C:
					select {
					case <-ctx.Done():
					case s.queue <- next:
					}
				}
			}()
		}
	}
}

func DefaultSourceExportRoot(projectID string) string {
	base := strings.TrimSpace(os.Getenv("NEXUS_GBRAIN_SOURCE_DIR"))
	if base == "" {
		if home, err := os.UserHomeDir(); err == nil {
			base = filepath.Join(home, ".nexus", "gbrain-sources")
		} else {
			base = filepath.Join(".nexus-agents", "gbrain-sources")
		}
	}
	return filepath.Join(base, safeGraphID(projectID))
}

func StableProviderSourceID(sourceID string) string {
	value := strings.ToLower(strings.TrimSpace(sourceID))
	var builder strings.Builder
	lastDash := false
	for _, char := range value {
		if (char >= 'a' && char <= 'z') || (char >= '0' && char <= '9') {
			builder.WriteRune(char)
			lastDash = false
			continue
		}
		if !lastDash && builder.Len() > 0 {
			builder.WriteByte('-')
			lastDash = true
		}
	}
	value = strings.Trim(builder.String(), "-")
	if value == "" {
		sum := sha256.Sum256([]byte(sourceID))
		value = "source-" + hex.EncodeToString(sum[:4])
	}
	if len(value) <= 32 {
		return value
	}
	sum := sha256.Sum256([]byte(sourceID))
	suffix := hex.EncodeToString(sum[:4])
	return strings.Trim(value[:23], "-") + "-" + suffix
}

func validateSyncRequest(request SyncRequest) error {
	if strings.TrimSpace(request.ProjectID) == "" {
		return fmt.Errorf("knowledge graph sync project id is empty")
	}
	if strings.TrimSpace(request.ProjectRoot) == "" {
		return fmt.Errorf("knowledge graph sync project root is empty")
	}
	if strings.TrimSpace(request.SourceID) == "" {
		return fmt.Errorf("knowledge graph sync source id is empty")
	}
	info, err := os.Stat(request.ProjectRoot)
	if err != nil {
		return err
	}
	if !info.IsDir() {
		return fmt.Errorf("knowledge graph sync project root is not a directory")
	}
	return nil
}

func compareDocumentHashes(previous, current map[string]string, force bool) (created, updated, unchanged int) {
	for slug, currentHash := range current {
		previousHash, existed := previous[slug]
		switch {
		case !existed:
			created++
		case force || previousHash != currentHash:
			updated++
		default:
			unchanged++
		}
	}
	return
}

func copyStringMap(values map[string]string) map[string]string {
	copied := make(map[string]string, len(values))
	for key, value := range values {
		copied[key] = value
	}
	return copied
}

func boundedGraphError(err error) string {
	value := strings.TrimSpace(err.Error())
	if len(value) > 2000 {
		return value[:2000]
	}
	return value
}
