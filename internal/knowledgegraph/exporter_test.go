package knowledgegraph

import (
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
)

func TestExportApprovedKnowledgeIsDeterministicAndDomainScoped(t *testing.T) {
	projectRoot, err := filepath.Abs(filepath.Join("..", ".."))
	if err != nil {
		t.Fatal(err)
	}
	exportRoot := filepath.Join(t.TempDir(), "nexus-agents")
	request := ExportRequest{
		ProjectID: "nexus-agents", ProjectRoot: projectRoot,
		SourceID: "project:nexus-agents", ProviderSourceID: "project-nexus-agents",
		Branch: "feat-kb", Revision: "601aaae9", ExportRoot: exportRoot,
		IncludeDomains: true, IncludeFeatures: true,
	}
	first, err := ExportApprovedKnowledge(request)
	if err != nil {
		t.Fatal(err)
	}
	firstFiles := readExportFiles(t, exportRoot)
	second, err := ExportApprovedKnowledge(request)
	if err != nil {
		t.Fatal(err)
	}
	secondFiles := readExportFiles(t, exportRoot)

	if first.Manifest.SourceHash != second.Manifest.SourceHash || !reflect.DeepEqual(firstFiles, secondFiles) {
		t.Fatal("repeated export was not byte-stable")
	}
	domains := 0
	features := 0
	for _, document := range first.Manifest.Documents {
		switch {
		case strings.HasPrefix(document.ExportPath, "domains/"):
			domains++
		case strings.HasPrefix(document.ExportPath, "features/"):
			features++
		}
		if strings.Contains(document.CanonicalPath, "proposals") ||
			document.CanonicalPath == "KnowledgeBase/Setting.yaml" ||
			document.CanonicalPath == "KnowledgeBase/log.md" {
			t.Fatalf("non-approved source entered export: %#v", document)
		}
	}
	if domains != 5 || features != 15 {
		t.Fatalf("domains=%d features=%d manifest=%#v", domains, features, first.Manifest.Documents)
	}
	if _, err := os.Stat(filepath.Join(exportRoot, "project.md")); err != nil {
		t.Fatalf("project.md missing: %v", err)
	}
	knowledgeDomain, err := os.ReadFile(filepath.Join(exportRoot, "domains", "knowledgebase.md"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(knowledgeDomain), "(features/knowledgebase/repository-scanning-and-indexing)") {
		t.Fatalf("approved link was not rewritten to a stable slug:\n%s", knowledgeDomain)
	}
}

func TestStableProviderSourceIDMeetsGBrainConstraints(t *testing.T) {
	for _, input := range []string{
		"project:nexus-agents",
		"Project:Very Long Repository Name That Exceeds Thirty Two Characters",
		"公会项目",
	} {
		value := StableProviderSourceID(input)
		if value == "" || len(value) > 32 {
			t.Fatalf("%q -> %q", input, value)
		}
		for _, char := range value {
			if !((char >= 'a' && char <= 'z') || (char >= '0' && char <= '9') || char == '-') {
				t.Fatalf("%q contains invalid character %q", value, char)
			}
		}
		if value != StableProviderSourceID(input) {
			t.Fatalf("source id is not deterministic for %q", input)
		}
	}
}

func readExportFiles(t *testing.T, root string) map[string]string {
	t.Helper()
	files := map[string]string{}
	var paths []string
	err := filepath.WalkDir(root, func(filePath string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() {
			return nil
		}
		relative, err := filepath.Rel(root, filePath)
		if err != nil {
			return err
		}
		paths = append(paths, filepath.ToSlash(relative))
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	sort.Strings(paths)
	for _, relative := range paths {
		data, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(relative)))
		if err != nil {
			t.Fatal(err)
		}
		files[relative] = string(data)
	}
	return files
}
