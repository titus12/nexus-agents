package main

import (
	"encoding/json"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"

	"nexus-agents/internal/codexrouter"
	"nexus-agents/internal/httpapi"
)

func main() {
	addr := os.Getenv("NEXUS_ADDR")
	if addr == "" {
		addr = ":8766"
	}

	setupLogger()

	router := codexrouter.NewService(codexrouter.DefaultConfig())
	writeCatalogFile(router)

	log.Printf("nexus-agents listening on %s", addr)
	if err := http.ListenAndServe(addr, httpapi.NewServerWithCodexRouter(router)); err != nil {
		log.Fatal(err)
	}
}

// setupLogger 把日志同时写到 stderr 和 ~/.codex/nexus-agents.log，
// 方便在 Codex Desktop 出问题时回查代理侧的请求记录。
func setupLogger() {
	home, err := os.UserHomeDir()
	if err != nil {
		return
	}
	logPath := filepath.Join(home, ".codex", "nexus-agents.log")
	f, err := os.OpenFile(logPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644)
	if err != nil {
		return
	}
	log.SetOutput(io.MultiWriter(os.Stderr, f))
	log.SetFlags(log.Ldate | log.Ltime | log.Lmicroseconds)
	log.Printf("log file: %s", logPath)
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
	// 先取消只读（如果存在），写完再恢复只读，防止 Codex 覆盖
	_ = os.Chmod(dest, 0644)
	if err := os.WriteFile(dest, data, 0444); err != nil {
		log.Printf("catalog: write %s error: %v", dest, err)
		return
	}
	log.Printf("catalog: wrote %s (%d models, read-only)", dest, len(catalog["models"].([]map[string]any)))
}
