package knowledgesync

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io/fs"
	"log"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"nexus-agents/internal/knowledgebase"
	"nexus-agents/internal/wikicompiler"
)

var ErrProjectSyncBusy = errors.New("knowledge synchronization is already running for this project")

type ServiceOptions struct {
	Store      *StateStore
	Git        GitRunner
	Compiler   wikicompiler.Provider
	Discovery  DiscoveryClient
	CodeGraph  CodeGraphClient
	External   ExternalSourceClient
	ExportRoot func(projectID string) string
}

type Service struct {
	store      *StateStore
	git        GitRunner
	compiler   wikicompiler.Provider
	discovery  DiscoveryClient
	codeGraph  CodeGraphClient
	external   ExternalSourceClient
	exportRoot func(projectID string) string
	mu         sync.Mutex
	running    map[string]bool
}

type ProjectRequest struct {
	ProjectID   string `json:"projectId"`
	ProjectRoot string `json:"projectRoot"`
}

type InitializeRequest struct {
	ProjectRequest
	Mode               string              `json:"mode"`
	ExternalReferences []ExternalReference `json:"externalReferences"`
}

type SyncResult struct {
	State    KnowledgeSyncState `json:"state"`
	Run      SyncRun            `json:"run"`
	Proposal *KnowledgeProposal `json:"proposal,omitempty"`
	Message  string             `json:"message"`
}

func NewService(options ServiceOptions) *Service {
	if options.Store == nil {
		options.Store = NewStateStore("")
	}
	if options.Git == nil {
		options.Git = ExecGitRunner{}
	}
	if options.Compiler == nil {
		options.Compiler = wikicompiler.NewOpenWiki()
	}
	if options.Discovery == nil {
		options.Discovery = NewEnvironmentDiscoveryClient()
	}
	if options.External == nil {
		options.External = NewEnvironmentExternalSourceClient()
	}
	if options.CodeGraph == nil {
		options.CodeGraph = NewCodeGraphCLI()
	}
	return &Service{
		store: options.Store, git: options.Git, compiler: options.Compiler,
		discovery: options.Discovery, codeGraph: options.CodeGraph, external: options.External,
		exportRoot: options.ExportRoot, running: map[string]bool{},
	}
}

func (s *Service) Store() *StateStore {
	return s.store
}

func (s *Service) Profile(projectRoot string) (Profile, error) {
	return LoadProfile(projectRoot)
}

func (s *Service) SaveProfile(projectRoot string, profile Profile) error {
	return SaveProfile(projectRoot, profile)
}

func (s *Service) DiscoverPolicy(ctx context.Context, request ProjectRequest) (ScanPolicyProposal, error) {
	if err := validateProjectRequest(request); err != nil {
		return ScanPolicyProposal{}, err
	}
	started := time.Now()
	proposal, err := DiscoverPolicy(ctx, request.ProjectRoot, s.git, s.discovery, s.codeGraph)
	if err != nil {
		log.Printf("[knowledge-sync] discovery project=%s status=failed duration_ms=%d error=%q", request.ProjectID, time.Since(started).Milliseconds(), boundedText(err.Error(), 500))
		return ScanPolicyProposal{}, err
	}
	log.Printf("[knowledge-sync] discovery project=%s status=succeeded duration_ms=%d revision=%s files=%d rules=%d ai_refined=%t warnings=%d", request.ProjectID, time.Since(started).Milliseconds(), proposal.Revision, proposal.Inventory.TrackedFiles, len(proposal.Rules), proposal.AIRefined, len(proposal.Warnings))
	return proposal, nil
}

func (s *Service) Status(projectID string) (KnowledgeSyncState, error) {
	return s.store.LoadState(projectID)
}

func (s *Service) Runs(projectID string) ([]SyncRun, error) {
	return s.store.ListRuns(projectID)
}

func (s *Service) Proposals(projectID string) ([]KnowledgeProposal, error) {
	return s.store.ListProposals(projectID)
}

func (s *Service) Proposal(projectID, proposalID string) (KnowledgeProposal, error) {
	return s.store.LoadProposal(projectID, proposalID)
}

func (s *Service) AdoptExisting(ctx context.Context, request ProjectRequest) (SyncResult, error) {
	release, err := s.acquire(request.ProjectID)
	if err != nil {
		return SyncResult{}, err
	}
	defer release()
	if err := validateProjectRequest(request); err != nil {
		return SyncResult{}, err
	}
	gitState, err := ReadGitState(ctx, s.git, request.ProjectRoot)
	if err != nil {
		return SyncResult{}, err
	}
	profile, err := LoadProfile(request.ProjectRoot)
	if err != nil {
		return SyncResult{}, err
	}
	validation, err := knowledgebase.Validate(request.ProjectRoot)
	if err != nil {
		return SyncResult{}, err
	}
	hash, err := knowledgeHash(request.ProjectRoot)
	if err != nil {
		return SyncResult{}, err
	}
	profileHash, _ := ProfileHash(profile)
	now := time.Now().Format(time.RFC3339)
	state := KnowledgeSyncState{
		ProjectID: request.ProjectID, ProjectRoot: filepath.Clean(request.ProjectRoot),
		Branch: gitState.Branch, LastProcessedCommit: gitState.Head, LastKnowledgeHash: hash,
		LastCheckedAt: now, LastSuccessfulAt: now, Status: StatusReady,
		CompilerVersion: profile.OpenWiki.Version, ProfileHash: profileHash,
	}
	run := SyncRun{
		ID: newRunID(request.ProjectID, "adopt"), ProjectID: request.ProjectID, Kind: "adopt",
		Status: "succeeded", Branch: gitState.Branch, TargetRevision: gitState.Head,
		Warnings: validationWarnings(validation), StartedAt: now, EndedAt: now,
	}
	if err := s.store.SaveState(state); err != nil {
		return SyncResult{}, err
	}
	if err := s.store.SaveRun(run); err != nil {
		return SyncResult{}, err
	}
	_ = s.export(request.ProjectID, request.ProjectRoot)
	return SyncResult{State: state, Run: run, Message: "Existing knowledge base adopted without file changes."}, nil
}

func (s *Service) InitializePreview(ctx context.Context, request InitializeRequest) (SyncResult, error) {
	return s.compilePreview(ctx, request, false)
}

func (s *Service) CheckAndEnrich(ctx context.Context, request InitializeRequest) (SyncResult, error) {
	return s.compilePreview(ctx, request, true)
}

func (s *Service) compilePreview(ctx context.Context, request InitializeRequest, update bool) (result SyncResult, returnErr error) {
	release, err := s.acquire(request.ProjectID)
	if err != nil {
		return SyncResult{}, err
	}
	defer release()
	if err := validateProjectRequest(request.ProjectRequest); err != nil {
		return SyncResult{}, err
	}
	if len(request.ExternalReferences) > MaxExternalReferences {
		return SyncResult{}, fmt.Errorf("external references exceed %d", MaxExternalReferences)
	}
	profile, err := LoadProfile(request.ProjectRoot)
	if err != nil {
		return SyncResult{}, err
	}
	gitState, err := ReadGitState(ctx, s.git, request.ProjectRoot)
	if err != nil {
		return SyncResult{}, err
	}
	inventory, err := BuildInventory(ctx, request.ProjectRoot, s.git, s.codeGraph, profile.Scan.Limits)
	if err != nil {
		return SyncResult{}, err
	}
	allPaths := make([]string, 0, len(inventory.Files))
	for _, file := range inventory.Files {
		allPaths = append(allPaths, file.Path)
	}
	manifest := IncludedPaths(profile, allPaths)
	started := time.Now()
	run := SyncRun{
		ID:        newRunID(request.ProjectID, map[bool]string{true: "enrich", false: "initialize"}[update]),
		ProjectID: request.ProjectID, Branch: gitState.Branch, TargetRevision: gitState.Head,
		Status: "running", StartedAt: started.Format(time.RFC3339), Warnings: []string{},
	}
	if update {
		run.Kind = "enrich"
	} else {
		run.Kind = "initialize"
	}
	state, _ := s.store.LoadState(request.ProjectID)
	var impact CodeGraphImpact
	impactAvailable := false
	if update && profile.CodeGraph.Enabled && s.codeGraph != nil && state.LastProcessedCommit != "" && state.LastProcessedCommit != gitState.Head {
		if changes, diffErr := DiffCommitted(ctx, s.git, request.ProjectRoot, state.LastProcessedCommit, gitState.Head); diffErr == nil {
			changedPaths := make([]string, 0, len(changes))
			for _, change := range changes {
				changedPaths = append(changedPaths, change.Path)
			}
			if resolvedImpact, impactErr := s.codeGraph.Impact(request.ProjectRoot, changedPaths, profile.CodeGraph.ImpactDepth); impactErr == nil {
				impact = resolvedImpact
				impactAvailable = true
			} else {
				run.Warnings = append(run.Warnings, "CodeGraph impact analysis failed: "+boundedText(impactErr.Error(), 500))
			}
		}
	}
	state.ProjectID = request.ProjectID
	state.ProjectRoot = filepath.Clean(request.ProjectRoot)
	state.Branch = gitState.Branch
	state.LastCheckedAt = run.StartedAt
	state.LastError = ""
	state.Status = StatusChecking
	_ = s.store.SaveState(state)
	_ = s.store.SaveRun(run)
	log.Printf("[knowledge-sync] compile project=%s run=%s kind=%s status=started branch=%s revision=%s selected_files=%d", request.ProjectID, run.ID, run.Kind, gitState.Branch, gitState.Head, len(manifest))
	defer func() {
		if returnErr != nil {
			run.Status = "failed"
			run.Error = boundedText(returnErr.Error(), 2000)
			run.EndedAt = time.Now().Format(time.RFC3339)
			state.Status = StatusFailed
			state.LastError = run.Error
			_ = s.store.SaveRun(run)
			_ = s.store.SaveState(state)
			log.Printf("[knowledge-sync] compile project=%s run=%s kind=%s status=failed duration_ms=%d error=%q", request.ProjectID, run.ID, run.Kind, time.Since(started).Milliseconds(), run.Error)
		}
	}()

	externalRoot := s.store.projectPath(request.ProjectID, "runs", safeID(run.ID), "external")
	snapshots, err := ProjectExternalReferences(ctx, externalRoot, request.ExternalReferences, s.external)
	if err != nil && len(request.ExternalReferences) > 0 {
		return SyncResult{}, err
	}
	defer RemoveExternalSnapshots(externalRoot)
	compilerExternal := make([]wikicompiler.ExternalSource, 0, len(snapshots))
	for _, snapshot := range snapshots {
		if snapshot.Status != "ready" {
			run.Warnings = append(run.Warnings, snapshot.Warnings...)
			continue
		}
		compilerExternal = append(compilerExternal, wikicompiler.ExternalSource{
			Title: snapshot.Title, SourceURL: snapshot.SourceURL, LocalPath: snapshot.LocalPath, Revision: snapshot.Revision,
		})
	}
	compileInput := wikicompiler.CompileInput{
		ProjectID: request.ProjectID, RunID: run.ID, ProjectRoot: request.ProjectRoot,
		Revision: gitState.Head, DataRoot: s.store.Root, KnowledgeRoot: profile.Knowledge.Root,
		Language: profile.Knowledge.Language, RequiredTopics: profile.Instructions.RequiredTopics,
		AdditionalInstructions: profile.Instructions.Additional, ScanManifest: manifest,
		ExternalSources: compilerExternal,
	}
	if update {
		compileInput.ExistingKnowledge, err = readExistingKnowledge(request.ProjectRoot)
		if err != nil {
			return SyncResult{}, err
		}
	}
	var generated wikicompiler.GeneratedBundle
	if update {
		generated, err = s.compiler.Update(ctx, compileInput)
	} else {
		generated, err = s.compiler.Initialize(ctx, compileInput)
	}
	if err != nil {
		return SyncResult{}, err
	}
	if err := ensureKnowledgeScaffold(request.ProjectRoot, generated.Files); err != nil {
		return SyncResult{}, err
	}
	wikicompiler.AnnotateProvenance(generated.Files, gitState.Head, generated.Version)
	validation, err := validateGeneratedBundle(request.ProjectRoot, generated.Files)
	if err != nil {
		return SyncResult{}, err
	}
	profileHash, err := ProfileHash(profile)
	if err != nil {
		return SyncResult{}, err
	}
	evidence := []ProposalEvidence{
		{Kind: "git", Source: request.ProjectRoot, Summary: fmt.Sprintf("%d tracked files selected for compilation", len(manifest)), Paths: manifest, Revision: gitState.Head},
	}
	if profile.CodeGraph.Enabled && s.codeGraph != nil {
		evidence = append(evidence, ProposalEvidence{Kind: "codegraph", Source: "CodeGraph", Summary: inventory.CodeGraph, Revision: gitState.Head})
	}
	if impactAvailable {
		evidence = append(evidence, ProposalEvidence{
			Kind: "codegraph-impact", Source: "CodeGraph",
			Summary:  fmt.Sprintf("routes=%d callers=%d callees=%d relatedTests=%d", len(impact.Routes), len(impact.Callers), len(impact.Callees), len(impact.Tests)),
			Paths:    append(append(append(append([]string{}, impact.Routes...), impact.Callers...), impact.Callees...), impact.Tests...),
			Revision: gitState.Head,
		})
	}
	for _, snapshot := range snapshots {
		evidence = append(evidence, ProposalEvidence{Kind: "external", Source: snapshot.SourceURL, Summary: "Initialization-only auxiliary evidence: " + snapshot.Title, Revision: snapshot.Revision})
	}
	proposal, err := BuildProposal(
		request.ProjectID, request.ProjectRoot, gitState.Branch, state.LastProcessedCommit, gitState.Head,
		profileHash, generated.Version, generated.Files, evidence, validation,
	)
	if err != nil {
		return SyncResult{}, err
	}
	proposal.Warnings = append(proposal.Warnings, generated.Warnings...)
	proposal.Warnings = append(proposal.Warnings, run.Warnings...)
	if err := s.store.SaveProposal(proposal); err != nil {
		return SyncResult{}, err
	}
	now := time.Now().Format(time.RFC3339)
	run.Status = "succeeded"
	run.ProposalID = proposal.ID
	run.Warnings = proposal.Warnings
	run.EndedAt = now
	state.Status = StatusProposalPending
	state.PendingProposalID = proposal.ID
	state.CompilerVersion = generated.Version
	state.ProfileHash = profileHash
	state.LastCheckedAt = now
	state.LastError = ""
	if err := s.store.SaveRun(run); err != nil {
		return SyncResult{}, err
	}
	if err := s.store.SaveState(state); err != nil {
		return SyncResult{}, err
	}
	log.Printf("[knowledge-sync] compile project=%s run=%s kind=%s status=succeeded duration_ms=%d proposal=%s changes=%d validation_errors=%d validation_warnings=%d warnings=%d", request.ProjectID, run.ID, run.Kind, time.Since(started).Milliseconds(), proposal.ID, len(proposal.Changes), len(proposal.Validation.Errors), len(proposal.Validation.Warnings), len(proposal.Warnings))
	return SyncResult{State: state, Run: run, Proposal: &proposal, Message: "Knowledge proposal generated; project files are unchanged until approval."}, nil
}

func (s *Service) CheckUpdates(ctx context.Context, request ProjectRequest) (SyncResult, error) {
	release, err := s.acquire(request.ProjectID)
	if err != nil {
		return SyncResult{}, err
	}
	defer release()
	if err := validateProjectRequest(request); err != nil {
		return SyncResult{}, err
	}
	state, err := s.store.LoadState(request.ProjectID)
	if err != nil {
		return SyncResult{}, err
	}
	gitState, err := ReadGitState(ctx, s.git, request.ProjectRoot)
	if err != nil {
		return SyncResult{}, err
	}
	now := time.Now().Format(time.RFC3339)
	run := SyncRun{
		ID: newRunID(request.ProjectID, "check"), ProjectID: request.ProjectID, Kind: "check",
		Branch: gitState.Branch, BaseRevision: state.LastProcessedCommit, TargetRevision: gitState.Head,
		Status: "succeeded", StartedAt: now, EndedAt: now, Warnings: []string{},
	}
	if state.PendingProposalID != "" {
		state.Status = StatusProposalPending
		state.LastCheckedAt = now
		_ = s.store.SaveState(state)
		_ = s.store.SaveRun(run)
		return SyncResult{State: state, Run: run, Message: "A knowledge Proposal is already pending; scheduled regeneration was skipped."}, nil
	}
	if state.LastProcessedCommit == gitState.Head && state.PendingProposalID == "" {
		state.Status = StatusUpToDate
		state.LastCheckedAt = now
		_ = s.store.SaveState(state)
		_ = s.store.SaveRun(run)
		return SyncResult{State: state, Run: run, Message: "HEAD is unchanged; OpenWiki and model calls were skipped."}, nil
	}
	changes, diffErr := DiffCommitted(ctx, s.git, request.ProjectRoot, state.LastProcessedCommit, gitState.Head)
	if diffErr != nil && !errors.Is(diffErr, ErrGitBaseMissing) {
		return SyncResult{}, diffErr
	}
	if errors.Is(diffErr, ErrGitBaseMissing) {
		run.ChangeClass = ChangeHighRisk
		run.Warnings = append(run.Warnings, "The previous base revision is unavailable; use Check and enrich for a full comparison proposal.")
		state.Status = StatusUpdateAvailable
		state.LastCheckedAt = now
		_ = s.store.SaveRun(run)
		_ = s.store.SaveState(state)
		return SyncResult{State: state, Run: run, Message: "Git history changed; a full enrichment proposal is required."}, nil
	}
	classified := ClassifyChanges(changes)
	run.ChangeClass = classified.Class
	switch classified.Class {
	case ChangeKnowledgeOnly:
		report, err := knowledgebase.Validate(request.ProjectRoot)
		if err != nil {
			return SyncResult{}, err
		}
		_ = s.export(request.ProjectID, request.ProjectRoot)
		state.LastProcessedCommit = gitState.Head
		state.LastSuccessfulAt = now
		state.LastKnowledgeHash, _ = knowledgeHash(request.ProjectRoot)
		state.Status = StatusUpToDate
		run.Warnings = validationWarnings(report)
		run.MessageFallback()
		_ = s.store.SaveRun(run)
		_ = s.store.SaveState(state)
		return SyncResult{State: state, Run: run, Message: "Only approved KB files changed; validation and export were refreshed without OpenWiki."}, nil
	case ChangeTestsOnly, ChangeGenerated:
		state.LastProcessedCommit = gitState.Head
		state.LastSuccessfulAt = now
		state.Status = StatusUpToDate
		_ = s.store.SaveRun(run)
		_ = s.store.SaveState(state)
		return SyncResult{State: state, Run: run, Message: "No stable knowledge impact was detected; OpenWiki was skipped."}, nil
	default:
		state.Status = StatusUpdateAvailable
		state.LastCheckedAt = now
		_ = s.store.SaveRun(run)
		_ = s.store.SaveState(state)
		return SyncResult{State: state, Run: run, Message: "Relevant committed changes detected; run Check and enrich to generate a proposal."}, nil
	}
}

func (r *SyncRun) MessageFallback() {}

func (s *Service) ApplyProposal(ctx context.Context, request ProjectRequest, proposalID string, input ApplyProposalInput) (SyncResult, error) {
	started := time.Now()
	log.Printf("[knowledge-sync] apply project=%s proposal=%s status=started selected_paths=%d", request.ProjectID, proposalID, len(input.Paths))
	release, err := s.acquire(request.ProjectID)
	if err != nil {
		log.Printf("[knowledge-sync] apply project=%s proposal=%s status=failed duration_ms=%d error=%q", request.ProjectID, proposalID, time.Since(started).Milliseconds(), boundedText(err.Error(), 500))
		return SyncResult{}, err
	}
	defer release()
	proposal, err := s.store.LoadProposal(request.ProjectID, proposalID)
	if err != nil {
		return SyncResult{}, err
	}
	state, _ := s.store.LoadState(request.ProjectID)
	state.Status = StatusApplying
	_ = s.store.SaveState(state)
	applied, err := ApplyProposal(ctx, s.git, s.store, request.ProjectRoot, proposal, input)
	if err != nil {
		state.Status = StatusFailed
		state.LastError = err.Error()
		_ = s.store.SaveState(state)
		log.Printf("[knowledge-sync] apply project=%s proposal=%s status=failed duration_ms=%d error=%q", request.ProjectID, proposalID, time.Since(started).Milliseconds(), boundedText(err.Error(), 500))
		return SyncResult{}, err
	}
	report, err := knowledgebase.Validate(request.ProjectRoot)
	if err != nil {
		return SyncResult{}, err
	}
	_ = s.export(request.ProjectID, request.ProjectRoot)
	now := time.Now().Format(time.RFC3339)
	state.ProjectID = request.ProjectID
	state.ProjectRoot = filepath.Clean(request.ProjectRoot)
	state.Branch = applied.Branch
	state.LastProcessedCommit = applied.TargetRevision
	state.LastKnowledgeHash, _ = knowledgeHash(request.ProjectRoot)
	state.LastSuccessfulAt = now
	state.LastCheckedAt = now
	state.Status = StatusReady
	state.PendingProposalID = ""
	state.LastError = ""
	if err := s.store.SaveState(state); err != nil {
		return SyncResult{}, err
	}
	run := SyncRun{
		ID: newRunID(request.ProjectID, "apply"), ProjectID: request.ProjectID, Kind: "apply",
		Status: "succeeded", Branch: applied.Branch, BaseRevision: applied.BaseRevision,
		TargetRevision: applied.TargetRevision, ProposalID: applied.ID,
		Warnings: validationWarnings(report), StartedAt: now, EndedAt: now,
	}
	_ = s.store.SaveRun(run)
	log.Printf("[knowledge-sync] apply project=%s proposal=%s status=succeeded duration_ms=%d applied_paths=%d validation_warnings=%d", request.ProjectID, proposalID, time.Since(started).Milliseconds(), selectedProposalChangeCount(applied.Changes), len(run.Warnings))
	return SyncResult{State: state, Run: run, Proposal: &applied, Message: "Selected knowledge files were applied locally; review and commit them through the normal project PR flow."}, nil
}

func (s *Service) RejectProposal(request ProjectRequest, proposalID string) (SyncResult, error) {
	release, err := s.acquire(request.ProjectID)
	if err != nil {
		return SyncResult{}, err
	}
	defer release()
	proposal, err := s.store.LoadProposal(request.ProjectID, proposalID)
	if err != nil {
		return SyncResult{}, err
	}
	if proposal.Status != ProposalPending {
		return SyncResult{}, fmt.Errorf("proposal is not pending")
	}
	proposal.Status = ProposalRejected
	proposal.ResolvedAt = time.Now().Format(time.RFC3339)
	if err := s.store.SaveProposal(proposal); err != nil {
		return SyncResult{}, err
	}
	state, _ := s.store.LoadState(request.ProjectID)
	state.PendingProposalID = ""
	state.Status = StatusReady
	state.LastCheckedAt = time.Now().Format(time.RFC3339)
	if err := s.store.SaveState(state); err != nil {
		return SyncResult{}, err
	}
	run := SyncRun{
		ID: newRunID(request.ProjectID, "reject"), ProjectID: request.ProjectID, Kind: "reject",
		Status: "succeeded", ProposalID: proposal.ID, TargetRevision: proposal.TargetRevision,
		StartedAt: state.LastCheckedAt, EndedAt: state.LastCheckedAt, Warnings: []string{},
	}
	_ = s.store.SaveRun(run)
	log.Printf("[knowledge-sync] reject project=%s proposal=%s status=succeeded", request.ProjectID, proposalID)
	return SyncResult{State: state, Run: run, Proposal: &proposal, Message: "Proposal rejected; no project files were changed."}, nil
}

func selectedProposalChangeCount(changes []ProposalChange) int {
	count := 0
	for _, change := range changes {
		if change.Selected {
			count++
		}
	}
	return count
}

func (s *Service) acquire(projectID string) (func(), error) {
	projectID = strings.TrimSpace(projectID)
	if projectID == "" {
		return nil, fmt.Errorf("project id is empty")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.running[projectID] {
		return nil, ErrProjectSyncBusy
	}
	s.running[projectID] = true
	return func() {
		s.mu.Lock()
		delete(s.running, projectID)
		s.mu.Unlock()
	}, nil
}

func (s *Service) export(projectID, projectRoot string) error {
	if s.exportRoot == nil {
		return nil
	}
	_, err := knowledgebase.ExportProjectKnowledge(projectID, projectRoot, s.exportRoot(projectID))
	return err
}

func validateProjectRequest(request ProjectRequest) error {
	if strings.TrimSpace(request.ProjectID) == "" {
		return fmt.Errorf("project id is empty")
	}
	info, err := os.Stat(request.ProjectRoot)
	if err != nil {
		return err
	}
	if !info.IsDir() {
		return fmt.Errorf("project root is not a directory")
	}
	return nil
}

func readExistingKnowledge(projectRoot string) (map[string][]byte, error) {
	root := filepath.Join(projectRoot, filepath.FromSlash(DefaultKnowledgeRoot))
	files := map[string][]byte{}
	err := filepath.WalkDir(root, func(filePath string, entry fs.DirEntry, walkErr error) error {
		if os.IsNotExist(walkErr) {
			return nil
		}
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() || strings.ToLower(filepath.Ext(entry.Name())) != ".md" {
			return nil
		}
		relative, err := filepath.Rel(projectRoot, filePath)
		if err != nil {
			return err
		}
		data, err := os.ReadFile(filePath)
		if err != nil {
			return err
		}
		files[filepath.ToSlash(relative)] = data
		return nil
	})
	if os.IsNotExist(err) {
		return files, nil
	}
	return files, err
}

func validateGeneratedBundle(projectRoot string, generated map[string][]byte) (ProposalValidation, error) {
	tempRoot, err := os.MkdirTemp("", "nexus-kb-validation-*")
	if err != nil {
		return ProposalValidation{}, err
	}
	defer os.RemoveAll(tempRoot)
	sourceKnowledge := filepath.Join(projectRoot, knowledgebase.DefaultRoot)
	targetKnowledge := filepath.Join(tempRoot, knowledgebase.DefaultRoot)
	if err := copyDirectory(sourceKnowledge, targetKnowledge); err != nil {
		return ProposalValidation{}, err
	}
	if err := os.MkdirAll(targetKnowledge, 0o755); err != nil {
		return ProposalValidation{}, err
	}
	generatedPaths := make(map[string]bool, len(generated))
	for relative := range generated {
		generatedPaths[normalizeRelativePath(relative)] = true
	}
	projectKnowledge := filepath.Join(targetKnowledge, "project")
	if err := filepath.WalkDir(projectKnowledge, func(filePath string, entry fs.DirEntry, walkErr error) error {
		if os.IsNotExist(walkErr) {
			return nil
		}
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() || strings.ToLower(filepath.Ext(entry.Name())) != ".md" {
			return nil
		}
		relative, err := filepath.Rel(tempRoot, filePath)
		if err != nil {
			return err
		}
		relative = filepath.ToSlash(relative)
		if generatedPaths[relative] || !isFlatGeneratedProjectPath(relative) {
			return nil
		}
		data, err := os.ReadFile(filePath)
		if err != nil {
			return err
		}
		frontmatter, _, _ := knowledgebase.ParseFrontmatter(string(data))
		if frontmatter.ManagedBy == "openwiki" {
			return os.Remove(filePath)
		}
		return nil
	}); err != nil {
		return ProposalValidation{}, err
	}
	for relative, content := range map[string]string{
		"KnowledgeBase/index.md": "# Knowledge Base\n",
		"KnowledgeBase/log.md":   "# Knowledge Log\n",
	} {
		target, _ := SafeProjectPath(tempRoot, relative)
		if _, err := os.Stat(target); os.IsNotExist(err) {
			if err := os.WriteFile(target, []byte(content), 0o644); err != nil {
				return ProposalValidation{}, err
			}
		}
	}
	for relative, data := range generated {
		if !isWritableKnowledgePath(relative) || relative == ProfileRelativePath {
			return ProposalValidation{}, fmt.Errorf("invalid generated path %s", relative)
		}
		target, err := SafeProjectPath(tempRoot, relative)
		if err != nil {
			return ProposalValidation{}, err
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return ProposalValidation{}, err
		}
		if err := os.WriteFile(target, data, 0o644); err != nil {
			return ProposalValidation{}, err
		}
	}
	report, err := knowledgebase.Validate(tempRoot)
	if err != nil {
		return ProposalValidation{}, err
	}
	validation := ProposalValidation{Valid: report.Summary.Errors == 0, Errors: []string{}, Warnings: []string{}}
	for _, issue := range report.Issues {
		message := issue.Path + ": " + issue.Message
		if issue.Severity == "error" {
			validation.Errors = append(validation.Errors, message)
		} else {
			validation.Warnings = append(validation.Warnings, message)
		}
	}
	return validation, nil
}

func ensureKnowledgeScaffold(projectRoot string, generated map[string][]byte) error {
	if generated == nil {
		return fmt.Errorf("generated knowledge bundle is nil")
	}
	scaffold := map[string]string{
		"KnowledgeBase/index.md": "# Knowledge Base\n\n- [Project knowledge](project/index.md) - Generated and reviewed project-specific knowledge.\n",
		"KnowledgeBase/log.md":   "# Knowledge Log\n\n- Knowledge sync initialized through a reviewed Nexus Proposal.\n",
	}
	for relative, content := range scaffold {
		if _, exists := generated[relative]; exists {
			continue
		}
		target, err := SafeProjectPath(projectRoot, relative)
		if err != nil {
			return err
		}
		if _, err := os.Stat(target); err == nil {
			continue
		} else if !os.IsNotExist(err) {
			return err
		}
		generated[relative] = []byte(content)
	}
	return nil
}

func copyDirectory(source, destination string) error {
	return filepath.WalkDir(source, func(filePath string, entry fs.DirEntry, walkErr error) error {
		if os.IsNotExist(walkErr) {
			return nil
		}
		if walkErr != nil {
			return walkErr
		}
		relative, err := filepath.Rel(source, filePath)
		if err != nil {
			return err
		}
		target := filepath.Join(destination, relative)
		if entry.IsDir() {
			return os.MkdirAll(target, 0o755)
		}
		data, err := os.ReadFile(filePath)
		if err != nil {
			return err
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return err
		}
		return os.WriteFile(target, data, 0o644)
	})
}

func validationWarnings(report knowledgebase.ValidationReport) []string {
	var warnings []string
	for _, issue := range report.Issues {
		warnings = append(warnings, issue.Severity+": "+issue.Path+": "+issue.Message)
	}
	return warnings
}

func knowledgeHash(projectRoot string) (string, error) {
	files, err := readExistingKnowledge(projectRoot)
	if err != nil {
		return "", err
	}
	keys := sortedMapKeys(files)
	hash := sha256.New()
	for _, key := range keys {
		hash.Write([]byte(key))
		hash.Write([]byte{0})
		hash.Write(files[key])
		hash.Write([]byte{0})
	}
	return "sha256:" + hex.EncodeToString(hash.Sum(nil)), nil
}
