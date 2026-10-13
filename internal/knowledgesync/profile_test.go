package knowledgesync

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
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

func TestScanExtensionsDefaultNormalizeAndValidation(t *testing.T) {
	profile := DefaultProfile()
	if got := EffectiveExtensions(profile); len(got) != 2 || got[0] != "cs" || got[1] != "go" {
		t.Fatalf("default extensions = %#v", got)
	}
	profile.Scan.Extensions = []string{".CS", " go ", "go", "md"}
	data, err := SerializeProfile(profile)
	if err != nil {
		t.Fatal(err)
	}
	parsed, err := ParseProfile(data)
	if err != nil {
		t.Fatal(err)
	}
	if got := parsed.Scan.Extensions; len(got) != 3 || got[0] != "cs" || got[1] != "go" || got[2] != "md" {
		t.Fatalf("normalized extensions = %#v", got)
	}
	for _, invalid := range [][]string{{"c++"}, {"../x"}, {"a b"}} {
		profile := DefaultProfile()
		profile.Scan.Extensions = invalid
		if _, err := SerializeProfile(profile); err == nil {
			t.Fatalf("expected %q to be rejected", invalid)
		}
	}
	if !ExtensionAllowed(parsed.Scan.Extensions, ".cs") || !ExtensionAllowed(parsed.Scan.Extensions, "CS") {
		t.Fatal("ExtensionAllowed should match normalized extensions")
	}
	if ExtensionAllowed(parsed.Scan.Extensions, ".md.cs") || ExtensionAllowed(parsed.Scan.Extensions, "") {
		t.Fatal("ExtensionAllowed must reject unknown or empty extensions")
	}
}

func TestScanExtensionsAbsentFallsBackToCodeDefault(t *testing.T) {
	data, err := SerializeProfile(DefaultProfile())
	if err != nil {
		t.Fatal(err)
	}
	var raw map[string]interface{}
	if err := yaml.Unmarshal(data, &raw); err != nil {
		t.Fatal(err)
	}
	scan := raw["scan"].(map[string]interface{})
	delete(scan, "extensions")
	stripped, err := yaml.Marshal(raw)
	if err != nil {
		t.Fatal(err)
	}
	parsed, err := ParseProfile(stripped)
	if err != nil {
		t.Fatalf("legacy profile without scan.extensions must load: %v", err)
	}
	if got := parsed.Scan.Extensions; len(got) != 2 || got[0] != "cs" || got[1] != "go" {
		t.Fatalf("fallback extensions = %#v", got)
	}
}

func TestLimitManifestKeepsHigherPriority(t *testing.T) {
	profile := DefaultProfile()
	profile.Scan.Rules = []ScanRule{
		{Pattern: "core/**", Action: "include", Priority: "high"},
		{Pattern: "extras/**", Action: "include", Priority: "low"},
	}
	paths := []string{"extras/b.go", "core/b.go", "extras/a.go", "core/a.go"}
	kept, dropped := LimitManifest(profile, paths, 2)
	if dropped != 2 || len(kept) != 2 {
		t.Fatalf("kept/dropped = %#v / %d", kept, dropped)
	}
	if kept[0] != "core/a.go" || kept[1] != "core/b.go" {
		t.Fatalf("priority order = %#v", kept)
	}
	kept, dropped = LimitManifest(profile, paths, 10)
	if dropped != 0 || len(kept) != 4 {
		t.Fatalf("under-limit keep = %#v dropped=%d", kept, dropped)
	}
}

func TestProfileWarningsDetectsConflictsAndShadowing(t *testing.T) {
	profile := DefaultProfile()
	profile.Scan.Rules = []ScanRule{
		{Pattern: "Packages/**", Action: "exclude"},
		{Pattern: "Packages/**", Action: "include"},
		{Pattern: "Packages/com.acme/sdk/Client.cs", Action: "include"},
		{Pattern: "docs/**", Action: "exclude"},
		{Pattern: "src/**", Action: "include"},
	}
	warnings := ProfileWarnings(profile)
	var conflicts, shadows int
	for _, warning := range warnings {
		if strings.Contains(warning, "both include and exclude") {
			conflicts++
		}
		if strings.Contains(warning, "shadowed by exclude") && strings.Contains(warning, "Packages/com.acme/sdk/Client.cs") {
			shadows++
		}
		if strings.Contains(warning, "src/**") {
			t.Fatalf("healthy include flagged: %q", warning)
		}
	}
	if conflicts != 1 || shadows != 1 {
		t.Fatalf("warnings = %#v", warnings)
	}
}
