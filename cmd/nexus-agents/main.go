package main

import (
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"nexus-agents/internal/codexrouter"
	"nexus-agents/internal/httpapi"
)

func main() {
	addr := os.Getenv("NEXUS_ADDR")
	if addr == "" {
		addr = ":8766"
	}

	cleanup, err := setupLogger()
	if err != nil {
		log.Fatalf("setup logger: %v", err)
	}
	defer cleanup()

	router := codexrouter.NewService(codexrouter.DefaultConfig())
	writeCatalogFile(router)

	log.Printf("nexus-agents listening on %s", addr)
	if err := http.ListenAndServe(addr, httpapi.NewServerWithCodexRouter(router)); err != nil {
		log.Fatal(err)
	}
}

func setupLogger() (func(), error) {
	baseDir, err := resolveLogBaseDir()
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(baseDir, 0755); err != nil {
		return nil, fmt.Errorf("create log dir %s: %w", baseDir, err)
	}
	if err := pruneOldLogDirs(baseDir, 7, time.Now()); err != nil {
		return nil, err
	}

	writer, path, err := newHourlyLogWriter(baseDir, time.Now)
	if err != nil {
		return nil, err
	}
	log.SetOutput(io.MultiWriter(os.Stderr, writer))
	log.SetFlags(log.Ldate | log.Ltime | log.Lmicroseconds)
	log.Printf("log file: %s", path)
	return writer.Close, nil
}

func resolveLogBaseDir() (string, error) {
	if v := strings.TrimSpace(os.Getenv("NEXUS_LOG_DIR")); v != "" {
		return v, nil
	}
	cwd, err := os.Getwd()
	if err != nil {
		return "", fmt.Errorf("get working directory: %w", err)
	}
	return filepath.Join(cwd, ".logs"), nil
}

type hourlyLogWriter struct {
	baseDir    string
	now        func() time.Time
	mu         sync.Mutex
	currentKey string
	current    *os.File
}

func newHourlyLogWriter(baseDir string, now func() time.Time) (*hourlyLogWriter, string, error) {
	w := &hourlyLogWriter{baseDir: baseDir, now: now}
	path, err := w.rotateLocked(now())
	if err != nil {
		return nil, "", err
	}
	return w, path, nil
}

func (w *hourlyLogWriter) Write(p []byte) (int, error) {
	w.mu.Lock()
	defer w.mu.Unlock()

	if _, err := w.ensureCurrentLocked(); err != nil {
		return 0, err
	}
	return w.current.Write(p)
}

func (w *hourlyLogWriter) Close() {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.current != nil {
		_ = w.current.Close()
		w.current = nil
	}
}

func (w *hourlyLogWriter) ensureCurrentLocked() (string, error) {
	now := w.now()
	key := hourlyKey(now)
	if w.current != nil && key == w.currentKey {
		return logFilePath(w.baseDir, now), nil
	}
	return w.rotateLocked(now)
}

func (w *hourlyLogWriter) rotateLocked(now time.Time) (string, error) {
	if w.current != nil {
		_ = w.current.Close()
		w.current = nil
	}
	dir := filepath.Join(w.baseDir, now.Format("2006-01-02"))
	if err := os.MkdirAll(dir, 0755); err != nil {
		return "", fmt.Errorf("create hourly log dir %s: %w", dir, err)
	}
	path := logFilePath(w.baseDir, now)
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644)
	if err != nil {
		return "", fmt.Errorf("open log file %s: %w", path, err)
	}
	w.current = f
	w.currentKey = hourlyKey(now)
	return path, nil
}

func hourlyKey(t time.Time) string {
	return t.Format("2006-01-02-15")
}

func logFilePath(baseDir string, t time.Time) string {
	return filepath.Join(baseDir, t.Format("2006-01-02"), fmt.Sprintf("%02d.log", t.Hour()))
}

func pruneOldLogDirs(baseDir string, keepDays int, now time.Time) error {
	entries, err := os.ReadDir(baseDir)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return fmt.Errorf("read log base dir %s: %w", baseDir, err)
	}

	cutoff := now.AddDate(0, 0, -(keepDays - 1))
	cutoffDay := time.Date(cutoff.Year(), cutoff.Month(), cutoff.Day(), 0, 0, 0, 0, cutoff.Location())

	var errs []string
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		day, err := time.Parse("2006-01-02", entry.Name())
		if err != nil {
			continue
		}
		if day.Before(cutoffDay) {
			fullPath := filepath.Join(baseDir, entry.Name())
			if err := os.RemoveAll(fullPath); err != nil {
				errs = append(errs, fmt.Sprintf("remove %s: %v", fullPath, err))
			}
		}
	}
	if len(errs) > 0 {
		sort.Strings(errs)
		return fmt.Errorf("prune old logs: %s", strings.Join(errs, "; "))
	}
	return nil
}

// writeCatalogFile writes the Codex model catalog to the local .codex directory
// so that model_catalog_json in config.toml can point to a local file path.
// The file is set read-only after writing to prevent Codex from overwriting it.
func writeCatalogFile(router *codexrouter.Service) {
	home, err := os.UserHomeDir()
	if err != nil {
		log.Printf("catalog: cannot determine home dir: %v", err)
		return
	}
	dest := filepath.Join(home, ".codex", "nexus-model-catalog.json")
	catalog := router.ModelCatalog()
	data, err := json.MarshalIndent(catalog, "", "  ")
	if err != nil {
		log.Printf("catalog: marshal error: %v", err)
		return
	}
	_ = os.Chmod(dest, 0644)
	if err := os.WriteFile(dest, data, 0444); err != nil {
		log.Printf("catalog: write %s error: %v", dest, err)
		return
	}
	log.Printf("catalog: wrote %s (%d models, read-only)", dest, len(catalog["models"].([]map[string]any)))
}
