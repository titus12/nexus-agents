package knowledgebase

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
)

func ExportProjectKnowledge(projectID string, projectRoot string, exportRoot string) (ExportManifest, error) {
	bundle, err := ScanBundle(projectRoot)
	if err != nil {
		return ExportManifest{}, err
	}
	if err := os.RemoveAll(exportRoot); err != nil {
		return ExportManifest{}, err
	}
	if err := os.MkdirAll(filepath.Join(exportRoot, "docs"), 0o755); err != nil {
		return ExportManifest{}, err
	}
	tree, err := BuildRenderTree(projectRoot)
	if err != nil {
		return ExportManifest{}, err
	}
	validation, err := Validate(projectRoot)
	if err != nil {
		return ExportManifest{}, err
	}
	maintenance, err := Maintenance(projectRoot)
	if err != nil {
		return ExportManifest{}, err
	}
	hash := sourceHash(bundle)
	manifest := ExportManifest{
		ProjectID:     projectID,
		ProjectRoot:   filepath.ToSlash(projectRoot),
		SourceRoot:    DefaultRoot,
		ExportRoot:    filepath.ToSlash(exportRoot),
		DocumentCount: len(bundle.Documents),
		SourceHash:    "sha256:" + hash,
		ExportedAt:    nowStamp(),
		Summary:       validation.Summary,
	}
	manifest.Summary.LastExported = manifest.ExportedAt
	for _, doc := range bundle.Documents {
		rendered, err := RenderDocument(projectRoot, doc.Path)
		if err != nil {
			return ExportManifest{}, err
		}
		if err := writeJSON(filepath.Join(exportRoot, "docs", exportDocName(doc.Path)+".json"), rendered); err != nil {
			return ExportManifest{}, err
		}
	}
	if err := writeJSON(filepath.Join(exportRoot, "manifest.json"), manifest); err != nil {
		return ExportManifest{}, err
	}
	if err := writeJSON(filepath.Join(exportRoot, "tree.json"), tree); err != nil {
		return ExportManifest{}, err
	}
	if err := writeJSON(filepath.Join(exportRoot, "validation.json"), validation); err != nil {
		return ExportManifest{}, err
	}
	if err := writeJSON(filepath.Join(exportRoot, "maintenance.json"), maintenance); err != nil {
		return ExportManifest{}, err
	}
	return manifest, nil
}

func ReadExport(exportRoot string) (ExportData, error) {
	var data ExportData
	if err := readJSON(filepath.Join(exportRoot, "manifest.json"), &data.Manifest); err != nil {
		return data, err
	}
	_ = readJSON(filepath.Join(exportRoot, "tree.json"), &data.Tree)
	_ = readJSON(filepath.Join(exportRoot, "validation.json"), &data.Validation)
	_ = readJSON(filepath.Join(exportRoot, "maintenance.json"), &data.Maintenance)
	return normalizeExportData(data), nil
}

func ReadFreshExport(projectID string, projectRoot string, exportRoot string) (ExportData, error) {
	bundle, err := ScanBundle(projectRoot)
	if err != nil {
		return ExportData{}, err
	}
	expectedHash := "sha256:" + sourceHash(bundle)
	data, err := ReadExport(exportRoot)
	if err != nil || data.Manifest.SourceHash != expectedHash || data.Manifest.ProjectRoot != filepath.ToSlash(projectRoot) {
		manifest, exportErr := ExportProjectKnowledge(projectID, projectRoot, exportRoot)
		if exportErr != nil {
			return ExportData{}, exportErr
		}
		data, err = ReadExport(exportRoot)
		if err != nil {
			return ExportData{}, err
		}
		data.Manifest = manifest
	}
	return normalizeExportData(data), nil
}

func ReadExportDocument(exportRoot string, relPath string) (RenderedDocument, error) {
	var doc RenderedDocument
	err := readJSON(filepath.Join(exportRoot, "docs", exportDocName(relPath)+".json"), &doc)
	return doc, err
}

func exportDocName(path string) string {
	replacer := strings.NewReplacer("/", "_", "\\", "_", ":", "_")
	return replacer.Replace(path)
}

func sourceHash(bundle Bundle) string {
	hash := sha256.New()
	for _, doc := range bundle.Documents {
		hash.Write([]byte(doc.Path))
		hash.Write([]byte{0})
		hash.Write([]byte(doc.Raw))
		hash.Write([]byte{0})
	}
	return hex.EncodeToString(hash.Sum(nil))
}

func writeJSON(path string, value any) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, data, 0o644)
}

func readJSON(path string, value any) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	return json.Unmarshal(data, value)
}
