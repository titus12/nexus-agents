package knowledgegraph

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"nexus-agents/internal/knowledgebase"
)

const (
	SourceExportSchemaVersion = 1
	approvedKnowledgePrefix   = "KnowledgeBase/project/"
	maxExportDocumentBytes    = 1024 * 1024
)

var (
	markdownLinkPattern = regexp.MustCompile(`\[([^\]]+)\]\(([^)]+)\)`)
	privateKeyPattern   = regexp.MustCompile(`(?i)-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----`)
	tokenPattern        = regexp.MustCompile(`(?i)\b(?:sk-[a-z0-9_-]{20,}|gh[opusr]_[a-z0-9]{20,}|AKIA[0-9A-Z]{16})\b`)
)

type ExportRequest struct {
	ProjectID        string
	ProjectRoot      string
	SourceID         string
	ProviderSourceID string
	Branch           string
	Revision         string
	ExportRoot       string
	IncludeDomains   bool
	IncludeFeatures  bool
}

type SourceDocumentManifest struct {
	ID            string   `json:"id"`
	Slug          string   `json:"slug"`
	CanonicalPath string   `json:"canonicalPath"`
	ExportPath    string   `json:"exportPath"`
	Title         string   `json:"title"`
	Hash          string   `json:"hash"`
	SizeBytes     int      `json:"sizeBytes"`
	SourcePaths   []string `json:"sourcePaths"`
}

type SourceManifest struct {
	SchemaVersion    int                      `json:"schemaVersion"`
	ProjectID        string                   `json:"projectId"`
	SourceID         string                   `json:"sourceId"`
	ProviderSourceID string                   `json:"providerSourceId"`
	Branch           string                   `json:"branch"`
	Revision         string                   `json:"revision"`
	SourceHash       string                   `json:"sourceHash"`
	DocumentCount    int                      `json:"documentCount"`
	Documents        []SourceDocumentManifest `json:"documents"`
}

type SourceExport struct {
	Root      string
	Manifest  SourceManifest
	Documents []GraphDocument
}

func ExportApprovedKnowledge(request ExportRequest) (SourceExport, error) {
	if strings.TrimSpace(request.ProjectID) == "" {
		return SourceExport{}, fmt.Errorf("knowledge graph export project id is empty")
	}
	if strings.TrimSpace(request.ProjectRoot) == "" {
		return SourceExport{}, fmt.Errorf("knowledge graph export project root is empty")
	}
	if strings.TrimSpace(request.SourceID) == "" {
		return SourceExport{}, fmt.Errorf("knowledge graph export source id is empty")
	}
	if strings.TrimSpace(request.ProviderSourceID) == "" {
		return SourceExport{}, fmt.Errorf("knowledge graph export provider source id is empty")
	}
	if strings.TrimSpace(request.ExportRoot) == "" {
		return SourceExport{}, fmt.Errorf("knowledge graph export root is empty")
	}

	validation, err := knowledgebase.Validate(request.ProjectRoot)
	if err != nil {
		return SourceExport{}, err
	}
	if validation.Summary.Errors > 0 {
		return SourceExport{}, fmt.Errorf("approved KnowledgeBase has %d validation errors", validation.Summary.Errors)
	}
	bundle, err := knowledgebase.ScanBundle(request.ProjectRoot)
	if err != nil {
		return SourceExport{}, err
	}

	documents := make([]knowledgebase.Document, 0, len(bundle.Documents))
	slugByCanonicalPath := map[string]string{}
	exportPathByCanonicalPath := map[string]string{}
	canonicalPathBySlug := map[string]string{}
	for _, document := range bundle.Documents {
		canonicalPath := filepath.ToSlash(filepath.Clean(document.Path))
		if !strings.HasPrefix(canonicalPath, approvedKnowledgePrefix) {
			continue
		}
		exportPath, slug, kind, ok := exportedDocumentPath(canonicalPath)
		if !ok {
			continue
		}
		if kind == "domain" && !request.IncludeDomains {
			continue
		}
		if kind == "feature" && !request.IncludeFeatures {
			continue
		}
		if _, duplicate := slugByCanonicalPath[canonicalPath]; duplicate {
			return SourceExport{}, fmt.Errorf("duplicate approved knowledge path %s", canonicalPath)
		}
		if existing, duplicate := canonicalPathBySlug[slug]; duplicate {
			return SourceExport{}, fmt.Errorf("approved knowledge paths %s and %s map to duplicate slug %s", existing, canonicalPath, slug)
		}
		documents = append(documents, document)
		slugByCanonicalPath[canonicalPath] = slug
		exportPathByCanonicalPath[canonicalPath] = exportPath
		canonicalPathBySlug[slug] = canonicalPath
	}
	sort.Slice(documents, func(i, j int) bool { return documents[i].Path < documents[j].Path })
	if len(documents) == 0 {
		return SourceExport{}, fmt.Errorf("approved KnowledgeBase contains no project Markdown documents")
	}

	graphDocuments := make([]GraphDocument, 0, len(documents))
	manifestDocuments := make([]SourceDocumentManifest, 0, len(documents))
	for _, document := range documents {
		canonicalPath := filepath.ToSlash(filepath.Clean(document.Path))
		content := normalizeMarkdown(document.Raw)
		if len([]byte(content)) > maxExportDocumentBytes {
			return SourceExport{}, fmt.Errorf("approved knowledge document exceeds 1 MiB: %s", canonicalPath)
		}
		if containsLikelySecret(content) {
			return SourceExport{}, fmt.Errorf("approved knowledge contains a likely secret: %s", canonicalPath)
		}
		content = rewriteApprovedLinks(canonicalPath, content, slugByCanonicalPath)
		hash := hashBytes([]byte(content))
		title := strings.TrimSpace(document.Frontmatter.Title)
		if title == "" {
			title = strings.TrimSuffix(document.Name, filepath.Ext(document.Name))
		}
		sourcePaths, err := normalizeSourcePaths(document.Frontmatter.SourcePaths)
		if err != nil {
			return SourceExport{}, fmt.Errorf("%s: %w", canonicalPath, err)
		}
		exportPath := exportPathByCanonicalPath[canonicalPath]
		slug := slugByCanonicalPath[canonicalPath]
		graphDocuments = append(graphDocuments, GraphDocument{
			ID:         request.ProviderSourceID + ":" + slug,
			Slug:       slug,
			Path:       exportPath,
			SourcePath: canonicalPath,
			Title:      title,
			Content:    content,
			Hash:       hash,
			Metadata: map[string]string{
				"projectId": request.ProjectID,
				"sourceId":  request.SourceID,
				"revision":  request.Revision,
			},
		})
		manifestDocuments = append(manifestDocuments, SourceDocumentManifest{
			ID:            request.ProviderSourceID + ":" + slug,
			Slug:          slug,
			CanonicalPath: canonicalPath,
			ExportPath:    exportPath,
			Title:         title,
			Hash:          hash,
			SizeBytes:     len([]byte(content)),
			SourcePaths:   sourcePaths,
		})
	}

	sourceHash := hashManifestDocuments(manifestDocuments)
	manifest := SourceManifest{
		SchemaVersion:    SourceExportSchemaVersion,
		ProjectID:        request.ProjectID,
		SourceID:         request.SourceID,
		ProviderSourceID: request.ProviderSourceID,
		Branch:           strings.TrimSpace(request.Branch),
		Revision:         strings.TrimSpace(request.Revision),
		SourceHash:       sourceHash,
		DocumentCount:    len(manifestDocuments),
		Documents:        manifestDocuments,
	}
	if err := writeSourceExport(request.ExportRoot, manifest, graphDocuments); err != nil {
		return SourceExport{}, err
	}
	return SourceExport{Root: request.ExportRoot, Manifest: manifest, Documents: graphDocuments}, nil
}

func exportedDocumentPath(canonicalPath string) (string, string, string, bool) {
	relative := strings.TrimPrefix(filepath.ToSlash(filepath.Clean(canonicalPath)), approvedKnowledgePrefix)
	if relative == "index.md" {
		return "project.md", "project", "project", true
	}
	parts := strings.Split(relative, "/")
	if len(parts) >= 3 && parts[0] == "domains" {
		domain := safeSlugSegment(parts[1])
		name := strings.TrimSuffix(parts[len(parts)-1], filepath.Ext(parts[len(parts)-1]))
		if name == "index" && len(parts) == 3 {
			exportPath := path.Join("domains", domain+".md")
			return exportPath, strings.TrimSuffix(exportPath, ".md"), "domain", true
		}
		featureSegments := append([]string(nil), parts[2:]...)
		featureSegments[len(featureSegments)-1] = name
		for index := range featureSegments {
			featureSegments[index] = safeSlugSegment(featureSegments[index])
		}
		exportPath := path.Join(append([]string{"features", domain}, featureSegments...)...) + ".md"
		return exportPath, strings.TrimSuffix(exportPath, ".md"), "feature", true
	}
	cleaned := strings.TrimSuffix(relative, filepath.Ext(relative))
	segments := strings.Split(cleaned, "/")
	for index := range segments {
		segments[index] = safeSlugSegment(segments[index])
	}
	exportPath := path.Join("documents", path.Join(segments...)+".md")
	return exportPath, strings.TrimSuffix(exportPath, ".md"), "document", true
}

func rewriteApprovedLinks(canonicalPath, content string, slugByCanonicalPath map[string]string) string {
	return markdownLinkPattern.ReplaceAllStringFunc(content, func(value string) string {
		match := markdownLinkPattern.FindStringSubmatch(value)
		if len(match) != 3 {
			return value
		}
		target := strings.TrimSpace(match[2])
		lower := strings.ToLower(target)
		if target == "" || strings.HasPrefix(target, "#") ||
			strings.HasPrefix(lower, "http://") || strings.HasPrefix(lower, "https://") ||
			strings.HasPrefix(lower, "mailto:") {
			return value
		}
		pathPart := target
		fragment := ""
		if index := strings.Index(pathPart, "#"); index >= 0 {
			fragment = pathPart[index:]
			pathPart = pathPart[:index]
		}
		pathPart = strings.TrimSpace(strings.ReplaceAll(pathPart, "\\", "/"))
		var resolved string
		if strings.HasPrefix(pathPart, "KnowledgeBase/") {
			resolved = path.Clean(pathPart)
		} else {
			resolved = path.Clean(path.Join(path.Dir(canonicalPath), pathPart))
		}
		slug, ok := slugByCanonicalPath[resolved]
		if !ok {
			return value
		}
		return "[" + match[1] + "](" + slug + fragment + ")"
	})
}

func writeSourceExport(exportRoot string, manifest SourceManifest, documents []GraphDocument) error {
	parent := filepath.Dir(exportRoot)
	if err := os.MkdirAll(parent, 0o755); err != nil {
		return err
	}
	stage, err := os.MkdirTemp(parent, "."+filepath.Base(exportRoot)+".tmp-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(stage)
	for _, directory := range []string{"domains", "features", "entities", "relations"} {
		if err := os.MkdirAll(filepath.Join(stage, directory), 0o755); err != nil {
			return err
		}
	}
	for _, document := range documents {
		target := filepath.Join(stage, filepath.FromSlash(document.Path))
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return err
		}
		if err := os.WriteFile(target, []byte(document.Content), 0o644); err != nil {
			return err
		}
	}
	data, err := json.MarshalIndent(manifest, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	if err := os.WriteFile(filepath.Join(stage, "manifest.json"), data, 0o644); err != nil {
		return err
	}
	return replaceDirectory(stage, exportRoot)
}

func replaceDirectory(stage, target string) error {
	backup := target + ".backup"
	if err := os.RemoveAll(backup); err != nil {
		return err
	}
	hadTarget := false
	if info, err := os.Stat(target); err == nil {
		if !info.IsDir() {
			return fmt.Errorf("knowledge graph export target is not a directory: %s", target)
		}
		hadTarget = true
		if err := os.Rename(target, backup); err != nil {
			return err
		}
	} else if !os.IsNotExist(err) {
		return err
	}
	if err := os.Rename(stage, target); err != nil {
		if hadTarget {
			_ = os.Rename(backup, target)
		}
		return err
	}
	if hadTarget {
		_ = os.RemoveAll(backup)
	}
	return nil
}

func normalizeMarkdown(value string) string {
	value = strings.ReplaceAll(value, "\r\n", "\n")
	value = strings.ReplaceAll(value, "\r", "\n")
	value = strings.TrimRight(value, "\n") + "\n"
	return value
}

func containsLikelySecret(value string) bool {
	return privateKeyPattern.MatchString(value) || tokenPattern.MatchString(value)
}

func normalizeSourcePaths(values []string) ([]string, error) {
	seen := map[string]bool{}
	normalized := make([]string, 0, len(values))
	for _, value := range values {
		value = strings.TrimSpace(strings.ReplaceAll(value, "\\", "/"))
		if value == "" {
			continue
		}
		cleaned := path.Clean(value)
		if filepath.IsAbs(value) || strings.HasPrefix(value, "/") ||
			cleaned == ".." || strings.HasPrefix(cleaned, "../") {
			return nil, fmt.Errorf("sourcePaths contains an unsafe path %q", value)
		}
		if !seen[cleaned] {
			seen[cleaned] = true
			normalized = append(normalized, cleaned)
		}
	}
	sort.Strings(normalized)
	return normalized, nil
}

func hashManifestDocuments(documents []SourceDocumentManifest) string {
	hash := sha256.New()
	for _, document := range documents {
		hash.Write([]byte(document.Slug))
		hash.Write([]byte{0})
		hash.Write([]byte(document.Hash))
		hash.Write([]byte{0})
	}
	return "sha256:" + hex.EncodeToString(hash.Sum(nil))
}

func hashBytes(value []byte) string {
	sum := sha256.Sum256(value)
	return "sha256:" + hex.EncodeToString(sum[:])
}

func safeSlugSegment(value string) string {
	original := value
	value = strings.ToLower(strings.TrimSpace(value))
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
		sum := sha256.Sum256([]byte(original))
		return "item-" + hex.EncodeToString(sum[:4])
	}
	return value
}
