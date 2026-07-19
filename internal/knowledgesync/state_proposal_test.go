package knowledgesync

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

func TestStateAndProposalSurviveStoreRecreation(t *testing.T) {
	root := t.TempDir()
	store := NewStateStore(root)
	state := KnowledgeSyncState{ProjectID: "project-1", Status: StatusProposalPending, PendingProposalID: "p1"}
	if err := store.SaveState(state); err != nil {
		t.Fatal(err)
	}
	reloaded, err := NewStateStore(root).LoadState("project-1")
	if err != nil {
		t.Fatal(err)
	}
	if reloaded.PendingProposalID != "p1" {
		t.Fatalf("state = %#v", reloaded)
	}
}

func TestProposalApplyWritesOnlyProjectKnowledgeAndRejectsStaleHead(t *testing.T) {
	projectRoot := initTestRepository(t)
	writeRepoFile(t, projectRoot, "README.md", "# repo\n")
	runGit(t, projectRoot, "add", ".")
	runGit(t, projectRoot, "commit", "-m", "initial")
	state, err := ReadGitState(context.Background(), ExecGitRunner{}, projectRoot)
	if err != nil {
		t.Fatal(err)
	}
	store := NewStateStore(t.TempDir())
	proposal, err := BuildProposal("project", projectRoot, state.Branch, "", state.Head, "sha256:test", "0.2.0", map[string][]byte{
		"KnowledgeBase/project/index.md": []byte("# Project knowledge\n"),
	}, nil, ProposalValidation{Valid: true})
	if err != nil {
		t.Fatal(err)
	}
	if err := store.SaveProposal(proposal); err != nil {
		t.Fatal(err)
	}
	applied, err := ApplyProposal(context.Background(), ExecGitRunner{}, store, projectRoot, proposal, ApplyProposalInput{})
	if err != nil {
		t.Fatal(err)
	}
	if applied.Status != ProposalApplied {
		t.Fatalf("status = %s", applied.Status)
	}
	if _, err := os.Stat(filepath.Join(projectRoot, "KnowledgeBase", "project", "index.md")); err != nil {
		t.Fatal(err)
	}

	writeRepoFile(t, projectRoot, "README.md", "# changed\n")
	runGit(t, projectRoot, "add", ".")
	runGit(t, projectRoot, "commit", "-m", "change")
	proposal.Status = ProposalPending
	if _, err := ApplyProposal(context.Background(), ExecGitRunner{}, store, projectRoot, proposal, ApplyProposalInput{}); err == nil {
		t.Fatal("expected stale proposal rejection")
	}
}

func TestProposalProtectsHumanPagesAndSuggestsOpenWikiDeprecation(t *testing.T) {
	projectRoot := initTestRepository(t)
	writeRepoFile(t, projectRoot, "README.md", "# repo\n")
	writeRepoFile(t, projectRoot, "KnowledgeBase/project/human.md", "---\ntype: Guide\ntitle: Human\nmanagedBy: human\n---\nHuman-owned content.\n")
	writeRepoFile(t, projectRoot, "KnowledgeBase/project/stale.md", "---\ntype: Guide\ntitle: Stale\nmanagedBy: openwiki\n---\nOld generated content.\n")
	runGit(t, projectRoot, "add", ".")
	runGit(t, projectRoot, "commit", "-m", "initial")
	state, err := ReadGitState(context.Background(), ExecGitRunner{}, projectRoot)
	if err != nil {
		t.Fatal(err)
	}
	proposal, err := BuildProposal("project", projectRoot, state.Branch, "", state.Head, "sha256:test", "0.2.0", map[string][]byte{
		"KnowledgeBase/project/human.md": []byte("---\ntype: Guide\ntitle: Human\nmanagedBy: openwiki\n---\nGenerated replacement.\n"),
	}, nil, ProposalValidation{Valid: true})
	if err != nil {
		t.Fatal(err)
	}
	changes := map[string]ProposalChange{}
	for _, change := range proposal.Changes {
		changes[change.Path] = change
	}
	if changes["KnowledgeBase/project/human.md"].Selected {
		t.Fatal("human-managed page should not be selected by default")
	}
	stale := changes["KnowledgeBase/project/stale.md"]
	if stale.Action != "deprecate" || !stale.Selected {
		t.Fatalf("stale generated page = %#v", stale)
	}
}

func TestProposalRejectsInvalidOrExplicitlyEmptySelection(t *testing.T) {
	projectRoot := initTestRepository(t)
	writeRepoFile(t, projectRoot, "README.md", "# repo\n")
	runGit(t, projectRoot, "add", ".")
	runGit(t, projectRoot, "commit", "-m", "initial")
	state, _ := ReadGitState(context.Background(), ExecGitRunner{}, projectRoot)
	store := NewStateStore(t.TempDir())
	proposal, err := BuildProposal("project", projectRoot, state.Branch, "", state.Head, "sha256:test", "0.2.0", map[string][]byte{
		"KnowledgeBase/project/index.md": []byte("# Project\n"),
	}, nil, ProposalValidation{Valid: false, Errors: []string{"invalid"}})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := ApplyProposal(context.Background(), ExecGitRunner{}, store, projectRoot, proposal, ApplyProposalInput{}); err == nil {
		t.Fatal("invalid proposal should not apply")
	}
	proposal.Validation = ProposalValidation{Valid: true}
	if _, err := ApplyProposal(context.Background(), ExecGitRunner{}, store, projectRoot, proposal, ApplyProposalInput{Paths: []string{}}); err == nil {
		t.Fatal("explicitly empty selection should not apply all files")
	}
}
