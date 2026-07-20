package knowledgesync

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestProfileRoundTripAndSafetyRules(t *testing.T) {
	profile := DefaultProfile()
	profile.Scan.Rules = []ScanRule{
		{Pattern: `internal\**`, Action: "include", Category: "code", Reason: "core"},
		{Pattern: "docs/**", Action: "exclude", Category: "docs"},
	}
	data, err := SerializeProfile(profile)
	if err != nil {
		t.Fatal(err)
	}
	parsed, err := ParseProfile(data)
	if err != nil {
		t.Fatal(err)
	}
	if parsed.Scan.Rules[0].Pattern != "internal/**" {
		t.Fatalf("normalized pattern = %q", parsed.Scan.Rules[0].Pattern)
	}
	if len(EffectiveRules(parsed)) <= len(parsed.Scan.Rules) {
		t.Fatal("hard safety rules were not appended")
	}
	if parsed.KnowledgeGraph.Provider != TestedKnowledgeGraphProvider ||
		parsed.KnowledgeGraph.Version != TestedGBrainVersion ||
		parsed.KnowledgeGraph.Engine != "pglite" {
		t.Fatalf("knowledge graph settings = %#v", parsed.KnowledgeGraph)
	}
	if !IsHardExcluded("services/auth/.env.production") || !IsHardExcluded("web/node_modules/vue/index.js") {
		t.Fatal("expected sensitive and dependency paths to be hard excluded")
	}
}

func TestProfileRejectsUnsafePathsAndModes(t *testing.T) {
	for _, pattern := range []string{"../secret", "C:/secret", "/etc/passwd"} {
		profile := DefaultProfile()
		profile.Scan.Rules = []ScanRule{{Pattern: pattern, Action: "include"}}
		if _, err := SerializeProfile(profile); err == nil {
			t.Fatalf("expected %q to be rejected", pattern)
		}
	}
	profile := DefaultProfile()
	profile.Knowledge.UpdateMode = "automatic"
	if _, err := SerializeProfile(profile); err == nil {
		t.Fatal("automatic update mode should be rejected")
	}
	profile = DefaultProfile()
	profile.KnowledgeGraph.Transport = "http"
	if _, err := SerializeProfile(profile); err == nil {
		t.Fatal("HTTP transport should be rejected in the local PGLite release")
	}
	profile = DefaultProfile()
	profile.KnowledgeGraph.Version = "latest"
	if _, err := SerializeProfile(profile); err == nil {
		t.Fatal("an unpinned GBrain version should be rejected")
	}
}

func TestLoadAndSaveProfile(t *testing.T) {
	root := t.TempDir()
	if _, err := LoadProfile(root); !errors.Is(err, ErrProfileNotFound) {
		t.Fatalf("missing profile error = %v", err)
	}
	profile := DefaultProfile()
	profile.Ownership.Owners = []string{"team-b", "team-a", "team-a"}
	if err := SaveProfile(root, profile); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(ProfileRelativePath)))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(data), "team-a") {
		t.Fatalf("saved profile missing owner:\n%s", data)
	}
	loaded, err := LoadProfile(root)
	if err != nil {
		t.Fatal(err)
	}
	if len(loaded.Ownership.Owners) != 2 || loaded.Ownership.Owners[0] != "team-a" {
		t.Fatalf("owners = %#v", loaded.Ownership.Owners)
	}
}

func TestRepositoryKnowledgeProfilesMatchCurrentSchema(t *testing.T) {
	for _, relative := range []string{
		filepath.Join("..", "..", "KnowledgeBase", "Setting.yaml"),
		filepath.Join("..", "..", "templates", "KnowledgeBase", "Setting.yaml"),
	} {
		data, err := os.ReadFile(relative)
		if err != nil {
			t.Fatalf("read %s: %v", relative, err)
		}
		profile, err := ParseProfile(data)
		if err != nil {
			t.Fatalf("parse %s: %v", relative, err)
		}
		if !profile.KnowledgeGraph.Enabled || profile.KnowledgeGraph.Version != TestedGBrainVersion {
			t.Fatalf("knowledge graph settings in %s = %#v", relative, profile.KnowledgeGraph)
		}
	}
}
