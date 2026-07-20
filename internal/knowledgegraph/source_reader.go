package knowledgegraph

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

type loadedSourceManifest struct {
	root     string
	manifest SourceManifest
	bySlug   map[string]SourceDocumentManifest
}

func (s *Service) LoadSearchDocuments(projectIDs []string, hits []GraphSearchHit) ([]SearchDocument, error) {
	manifests := map[string]loadedSourceManifest{}
	for _, projectID := range uniqueSortedPaths(projectIDs) {
		root := s.exportRoot(projectID)
		data, err := os.ReadFile(filepath.Join(root, "manifest.json"))
		if os.IsNotExist(err) {
			continue
		}
		if err != nil {
			return nil, fmt.Errorf("read GBrain source manifest for %s: %w", projectID, err)
		}
		var manifest SourceManifest
		if err := json.Unmarshal(data, &manifest); err != nil {
			return nil, fmt.Errorf("decode GBrain source manifest for %s: %w", projectID, err)
		}
		bySlug := make(map[string]SourceDocumentManifest, len(manifest.Documents))
		for _, document := range manifest.Documents {
			bySlug[normalizeSearchSlug(document.Slug)] = document
		}
		loaded := loadedSourceManifest{root: root, manifest: manifest, bySlug: bySlug}
		if manifest.SourceID != "" {
			manifests[manifest.SourceID] = loaded
		}
		if manifest.ProviderSourceID != "" {
			manifests[manifest.ProviderSourceID] = loaded
		}
	}

	result := make([]SearchDocument, 0, len(hits))
	seen := map[string]bool{}
	for _, hit := range hits {
		if stale, _ := hit.Metadata["stale"].(bool); stale {
			continue
		}
		loaded, ok := manifests[strings.TrimSpace(hit.SourceID)]
		if !ok && len(manifests) == 1 {
			for _, candidate := range manifests {
				loaded = candidate
				ok = true
				break
			}
		}
		if !ok {
			continue
		}
		slug := normalizeSearchSlug(hit.Path)
		document, ok := loaded.bySlug[slug]
		if !ok {
			continue
		}
		key := loaded.manifest.SourceID + "::" + slug
		if seen[key] {
			continue
		}
		contentPath, err := safeExportDocumentPath(loaded.root, document.ExportPath)
		if err != nil {
			return nil, err
		}
		content, err := os.ReadFile(contentPath)
		if err != nil {
			return nil, fmt.Errorf("read GBrain source document %s: %w", document.ExportPath, err)
		}
		seen[key] = true
		result = append(result, SearchDocument{
			ProjectID:        loaded.manifest.ProjectID,
			SourceID:         loaded.manifest.SourceID,
			ProviderSourceID: loaded.manifest.ProviderSourceID,
			Revision:         loaded.manifest.Revision,
			Slug:             document.Slug,
			CanonicalPath:    document.CanonicalPath,
			ExportPath:       document.ExportPath,
			Title:            document.Title,
			Content:          string(content),
			Snippet:          hit.Snippet,
			Score:            hit.Score,
			Metadata:         cloneSearchMetadata(hit.Metadata),
		})
	}
	return result, nil
}

func normalizeSearchSlug(value string) string {
	value = filepath.ToSlash(strings.TrimSpace(value))
	value = strings.TrimPrefix(value, "./")
	value = strings.TrimSuffix(value, ".md")
	return value
}

func safeExportDocumentPath(root, relative string) (string, error) {
	root = filepath.Clean(root)
	target := filepath.Clean(filepath.Join(root, filepath.FromSlash(relative)))
	rel, err := filepath.Rel(root, target)
	if err != nil {
		return "", err
	}
	if rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", fmt.Errorf("GBrain source document escapes export root: %s", relative)
	}
	return target, nil
}

func cloneSearchMetadata(values map[string]any) map[string]any {
	if len(values) == 0 {
		return nil
	}
	result := make(map[string]any, len(values))
	for key, value := range values {
		result[key] = value
	}
	return result
}
