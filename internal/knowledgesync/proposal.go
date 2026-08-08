package knowledgesync

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"nexus-agents/internal/knowledgebase"
)

type ProposalStatus string

const (
	ProposalPending      ProposalStatus = "pending"
	ProposalApplied      ProposalStatus = "applied"
	ProposalRejected     ProposalStatus = "rejected"
	ProposalStale        ProposalStatus = "stale"
	ProposalInvalid      ProposalStatus = "invalid"
	maxProposalFiles                    = 1000
	maxProposalBytes                    = 8 * 1024 * 1024
	maxProposalFileBytes                = 1024 * 1024
)

type KnowledgeProposal struct {
	ID              string             `json:"id"`
	ProjectID       string             `json:"projectId"`
	ProjectRoot     string             `json:"projectRoot"`
	Branch          string             `json:"branch"`
	BaseRevision    string             `json:"baseRevision,omitempty"`
	TargetRevision  string             `json:"targetRevision"`
	ProfileHash     string             `json:"profileHash"`
	CompilerVersion string             `json:"compilerVersion"`
	Status          ProposalStatus     `json:"status"`
	Changes         []ProposalChange   `json:"changes"`
	Evidence        []ProposalEvidence `json:"evidence"`
	Validation      ProposalValidation `json:"validation"`
	Warnings        []string           `json:"warnings"`
	CreatedAt       string             `json:"createdAt"`
	ResolvedAt      string             `json:"resolvedAt,omitempty"`
}

type ProposalChange struct {
	Path      string `json:"path"`
	Action    string `json:"action"`
	Before    string `json:"before,omitempty"`
	After     string `json:"after,omitempty"`
	ManagedBy string `json:"managedBy,omitempty"`
	Selected  bool   `json:"selected"`
}

type ProposalEvidence struct {
	Kind     string   `json:"kind"`
	Source   string   `json:"source"`
	Summary  string   `json:"summary"`
	Paths    []string `json:"paths"`
	Revision string   `json:"revision,omitempty"`
}

type ProposalValidation struct {
	Valid    bool     `json:"valid"`
	Errors   []string `json:"errors"`
	Warnings []string `json:"warnings"`
}

type ApplyProposalInput struct {
	Paths []string `json:"paths"`
}

func BuildProposal(projectID, projectRoot, branch, baseRevision, targetRevision, profileHash, compilerVersion string, generated map[string][]byte, evidence []ProposalEvidence, validation ProposalValidation) (KnowledgeProposal, error) {
	if strings.TrimSpace(targetRevision) == "" {
		return KnowledgeProposal{}, fmt.Errorf("target revision is empty")
	}
	changes, err := diffGeneratedKnowledge(projectRoot, generated)
	if err != nil {
		return KnowledgeProposal{}, err
	}
	id := DeterministicProposalID(projectID, branch, baseRevision, targetRevision, profileHash, compilerVersion)
	if validation.Errors == nil {
		validation.Errors = []string{}
	}
	if validation.Warnings == nil {
		validation.Warnings = []string{}
	}
	return KnowledgeProposal{
		ID: id, ProjectID: projectID, ProjectRoot: filepath.Clean(projectRoot), Branch: branch,
		BaseRevision: baseRevision, TargetRevision: targetRevision, ProfileHash: profileHash,
		CompilerVersion: compilerVersion, Status: ProposalPending, Changes: changes,
		Evidence: evidence, Validation: validation, Warnings: []string{},
		CreatedAt: time.Now().Format(time.RFC3339),
	}, nil
}

func DeterministicProposalID(projectID, branch, baseRevision, targetRevision, profileHash, compilerVersion string) string {
	value := strings.Join([]string{projectID, branch, baseRevision, targetRevision, profileHash, compilerVersion}, "\x00")
	sum := sha256.Sum256([]byte(value))
	return "kbp-" + hex.EncodeToString(sum[:12])
}

func diffGeneratedKnowledge(projectRoot string, generated map[string][]byte) ([]ProposalChange, error) {
	if len(generated) > maxProposalFiles {
		return nil, fmt.Errorf("compiler output exceeds the %d file proposal limit", maxProposalFiles)
	}
	totalBytes := 0
	paths := make([]string, 0, len(generated))
	normalizedGenerated := make(map[string][]byte, len(generated))
	for original, data := range generated {
		relative := normalizeRelativePath(original)
		if !isWritableKnowledgePath(relative) {
			return nil, fmt.Errorf("compiler output is outside project knowledge: %s", relative)
		}
		if _, duplicate := normalizedGenerated[relative]; duplicate {
			return nil, fmt.Errorf("compiler output has duplicate normalized path %s", relative)
		}
		if len(data) > maxProposalFileBytes {
			return nil, fmt.Errorf("compiler output file exceeds 1 MiB: %s", relative)
		}
		totalBytes += len(data)
		if totalBytes > maxProposalBytes {
			return nil, fmt.Errorf("compiler output exceeds the 8 MiB proposal limit")
		}
		normalizedGenerated[relative] = data
		paths = append(paths, relative)
	}
	sort.Strings(paths)
	var changes []ProposalChange
	for _, relative := range paths {
		target, err := SafeProjectPath(projectRoot, relative)
		if err != nil {
			return nil, err
		}
		before, err := os.ReadFile(target)
		if err != nil && !os.IsNotExist(err) {
			return nil, err
		}
		if len(before) > maxProposalFileBytes {
			return nil, fmt.Errorf("existing knowledge file exceeds 1 MiB and cannot be proposed safely: %s", relative)
		}
		after := normalizedGenerated[relative]
		if string(before) == string(after) {
			continue
		}
		action := "update"
		if os.IsNotExist(err) {
			action = "create"
		}
		managedBy := "openwiki"
		selected := true
		if len(before) > 0 {
			if isNexusKnowledgeResolver(relative) {
				managedBy = "nexus"
			} else {
				frontmatter, _, _ := knowledgebase.ParseFrontmatter(string(before))
				if frontmatter.ManagedBy == "" || frontmatter.ManagedBy == "human" {
					managedBy = "human"
					selected = false
				} else {
					managedBy = frontmatter.ManagedBy
				}
			}
		}
		changes = append(changes, ProposalChange{
			Path: relative, Action: action, Before: string(before), After: string(after), ManagedBy: managedBy, Selected: selected,
		})
	}
	existingRoot := filepath.Join(projectRoot, filepath.FromSlash(DefaultKnowledgeRoot))
	walkErr := filepath.WalkDir(existingRoot, func(filePath string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() || strings.ToLower(filepath.Ext(entry.Name())) != ".md" {
			return nil
		}
		relative, err := filepath.Rel(projectRoot, filePath)
		if err != nil {
			return nil
		}
		relative = filepath.ToSlash(relative)
		if _, generatedNow := normalizedGenerated[relative]; generatedNow {
			return nil
		}
		before, err := os.ReadFile(filePath)
		if err != nil {
			return nil
		}
		frontmatter, _, _ := knowledgebase.ParseFrontmatter(string(before))
		if frontmatter.ManagedBy != "openwiki" {
			return nil
		}
		changes = append(changes, ProposalChange{
			Path: relative, Action: "deprecate", Before: string(before), ManagedBy: "openwiki",
			Selected: isFlatGeneratedProjectPath(relative),
		})
		return nil
	})
	if walkErr != nil && !os.IsNotExist(walkErr) {
		return nil, walkErr
	}
	sort.Slice(changes, func(i, j int) bool { return changes[i].Path < changes[j].Path })
	for index := 1; index < len(changes); index++ {
		if changes[index-1].Path == changes[index].Path {
			return nil, fmt.Errorf("duplicate proposal destination %s", changes[index].Path)
		}
	}
	return changes, nil
}

func isNexusKnowledgeResolver(relative string) bool {
	relative = normalizeRelativePath(relative)
	return relative == "KnowledgeBase/index.md" ||
		relative == "KnowledgeBase/log.md" ||
		relative == DefaultKnowledgeRoot+"/index.md"
}

func isFlatGeneratedProjectPath(relative string) bool {
	relative = normalizeRelativePath(relative)
	prefix := DefaultKnowledgeRoot + "/"
	if !strings.HasPrefix(relative, prefix) || relative == DefaultKnowledgeRoot+"/index.md" {
		return false
	}
	rest := strings.TrimPrefix(relative, prefix)
	return !strings.Contains(rest, "/")
}

func (s *StateStore) SaveProposal(proposal KnowledgeProposal) error {
	if strings.TrimSpace(proposal.ID) == "" {
		return fmt.Errorf("proposal id is empty")
	}
	target := s.projectPath(proposal.ProjectID, "proposals", safeID(proposal.ID)+".json")
	if err := atomicWriteJSON(target, proposal); err != nil {
		return err
	}
	trimKnowledgeProposals(filepath.Dir(target), maxRetainedKnowledgeRecords)
	return nil
}

func (s *StateStore) LoadProposal(projectID, proposalID string) (KnowledgeProposal, error) {
	var proposal KnowledgeProposal
	err := readJSONFile(s.projectPath(projectID, "proposals", safeID(proposalID)+".json"), &proposal)
	return proposal, err
}

func (s *StateStore) ListProposals(projectID string) ([]KnowledgeProposal, error) {
	dir := s.projectPath(projectID, "proposals")
	entries, err := os.ReadDir(dir)
	if os.IsNotExist(err) {
		return []KnowledgeProposal{}, nil
	}
	if err != nil {
		return nil, err
	}
	var proposals []KnowledgeProposal
	for _, entry := range entries {
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".json" {
			continue
		}
		var proposal KnowledgeProposal
		if err := readJSONFile(filepath.Join(dir, entry.Name()), &proposal); err == nil {
			proposals = append(proposals, proposal)
		}
	}
	sort.Slice(proposals, func(i, j int) bool { return proposals[i].CreatedAt > proposals[j].CreatedAt })
	if len(proposals) > maxRetainedKnowledgeRecords {
		trimKnowledgeProposals(dir, maxRetainedKnowledgeRecords)
		proposals = proposals[:maxRetainedKnowledgeRecords]
	}
	return proposals, nil
}

func trimKnowledgeProposals(directory string, limit int) {
	if limit <= 0 {
		return
	}
	entries, err := os.ReadDir(directory)
	if err != nil {
		return
	}
	type record struct {
		path      string
		createdAt time.Time
		modTime   time.Time
	}
	records := make([]record, 0, len(entries))
	for _, entry := range entries {
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".json" {
			continue
		}
		info, infoErr := entry.Info()
		if infoErr != nil {
			continue
		}
		var proposal KnowledgeProposal
		if readErr := readJSONFile(filepath.Join(directory, entry.Name()), &proposal); readErr != nil {
			continue
		}
		createdAt, parseErr := time.Parse(time.RFC3339, proposal.CreatedAt)
		if parseErr != nil {
			createdAt = info.ModTime()
		}
		records = append(records, record{
			path: filepath.Join(directory, entry.Name()), createdAt: createdAt, modTime: info.ModTime(),
		})
	}
	sort.Slice(records, func(i, j int) bool {
		if records[i].createdAt.Equal(records[j].createdAt) {
			return records[i].modTime.After(records[j].modTime)
		}
		return records[i].createdAt.After(records[j].createdAt)
	})
	for _, stale := range records[minimumInt(limit, len(records)):] {
		_ = os.Remove(stale.path)
	}
}

func ApplyProposal(ctx context.Context, runner GitRunner, store *StateStore, projectRoot string, proposal KnowledgeProposal, input ApplyProposalInput) (KnowledgeProposal, error) {
	if proposal.Status != ProposalPending {
		return proposal, fmt.Errorf("proposal is not pending")
	}
	if !proposal.Validation.Valid || len(proposal.Validation.Errors) > 0 {
		return proposal, fmt.Errorf("proposal validation failed and cannot be applied")
	}
	absoluteRoot, err := filepath.Abs(projectRoot)
	if err != nil {
		return proposal, err
	}
	expectedRoot, err := filepath.Abs(proposal.ProjectRoot)
	if err != nil || !strings.EqualFold(filepath.Clean(absoluteRoot), filepath.Clean(expectedRoot)) {
		return proposal, fmt.Errorf("proposal project path no longer matches")
	}
	gitState, err := ReadGitState(ctx, runner, projectRoot)
	if err != nil {
		return proposal, err
	}
	if gitState.Head != proposal.TargetRevision {
		proposal.Status = ProposalStale
		proposal.ResolvedAt = time.Now().Format(time.RFC3339)
		_ = store.SaveProposal(proposal)
		return proposal, fmt.Errorf("proposal is stale: current HEAD %s does not match %s", gitState.Head, proposal.TargetRevision)
	}
	selected := map[string]bool{}
	for _, relative := range input.Paths {
		selected[normalizeRelativePath(relative)] = true
	}
	if input.Paths != nil && len(input.Paths) == 0 {
		return proposal, fmt.Errorf("no proposal paths were selected")
	}
	applyAll := input.Paths == nil
	type fileBackup struct {
		target  string
		existed bool
		data    []byte
		mode    os.FileMode
	}
	var backups []fileBackup
	for index := range proposal.Changes {
		change := &proposal.Changes[index]
		change.Selected = applyAll || selected[change.Path]
		if !change.Selected {
			continue
		}
		if !isWritableKnowledgePath(change.Path) {
			return proposal, fmt.Errorf("proposal contains protected destination %s", change.Path)
		}
		target, err := SafeProjectPath(projectRoot, change.Path)
		if err != nil {
			return proposal, err
		}
		backup := fileBackup{target: target, mode: 0o644}
		if info, statErr := os.Stat(target); statErr == nil {
			backup.existed = true
			backup.mode = info.Mode().Perm()
			backup.data, err = os.ReadFile(target)
			if err != nil {
				return proposal, err
			}
		} else if !os.IsNotExist(statErr) {
			return proposal, statErr
		}
		backups = append(backups, backup)
	}
	rollback := func() {
		for _, backup := range backups {
			if backup.existed {
				_ = atomicWriteFile(backup.target, backup.data, backup.mode)
			} else {
				_ = os.Remove(backup.target)
			}
		}
	}
	for index := range proposal.Changes {
		change := &proposal.Changes[index]
		if !change.Selected {
			continue
		}
		target, err := SafeProjectPath(projectRoot, change.Path)
		if err != nil {
			rollback()
			return proposal, err
		}
		switch change.Action {
		case "create", "update", "move":
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				rollback()
				return proposal, err
			}
			if err := atomicWriteFile(target, []byte(change.After), 0o644); err != nil {
				rollback()
				return proposal, err
			}
		case "delete", "deprecate":
			if err := os.Remove(target); err != nil && !os.IsNotExist(err) {
				rollback()
				return proposal, err
			}
		default:
			rollback()
			return proposal, fmt.Errorf("unsupported proposal action %q", change.Action)
		}
	}
	proposal.Status = ProposalApplied
	proposal.ResolvedAt = time.Now().Format(time.RFC3339)
	if err := store.SaveProposal(proposal); err != nil {
		return proposal, err
	}
	return proposal, nil
}

func isWritableKnowledgePath(relative string) bool {
	relative = normalizeRelativePath(relative)
	return relative == ProfileRelativePath ||
		relative == path.Join(knowledgebase.DefaultRoot, "index.md") ||
		relative == path.Join(knowledgebase.DefaultRoot, "log.md") ||
		relative == DefaultKnowledgeRoot ||
		strings.HasPrefix(relative, DefaultKnowledgeRoot+"/")
}
