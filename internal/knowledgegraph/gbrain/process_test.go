package gbrain

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"nexus-agents/internal/knowledgegraph"
)

func TestProcessManagerStartsOneProcessAndStopsCleanly(t *testing.T) {
	options := helperProcessOptions(t, false)
	manager := NewProcessManager(options)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	if err := manager.Start(ctx); err != nil {
		t.Fatal(err)
	}
	first := manager.Snapshot()
	if first.Status != knowledgegraph.GraphStatusReady || first.ProcessID == 0 {
		t.Fatalf("first health = %#v", first)
	}
	if err := manager.Start(ctx); err != nil {
		t.Fatal(err)
	}
	second := manager.Snapshot()
	if second.ProcessID != first.ProcessID {
		t.Fatalf("started a second process: first=%d second=%d", first.ProcessID, second.ProcessID)
	}
	health, err := manager.Health(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if health.Status != knowledgegraph.GraphStatusReady || len(health.Capabilities) != 2 {
		t.Fatalf("health = %#v", health)
	}

	stopCtx, stopCancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer stopCancel()
	if err := manager.Stop(stopCtx); err != nil {
		t.Fatal(err)
	}
	if got := manager.Snapshot().Status; got != knowledgegraph.GraphStatusStopped {
		t.Fatalf("status after stop = %q", got)
	}
}

func TestProcessManagerRestartsAfterCrash(t *testing.T) {
	options := helperProcessOptions(t, true)
	options.MaxRestarts = 2
	options.RestartBackoff = 10 * time.Millisecond
	manager := NewProcessManager(options)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if err := manager.Start(ctx); err != nil {
		t.Fatal(err)
	}

	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if manager.Snapshot().RestartCount >= 1 {
			stopCtx, stopCancel := context.WithTimeout(context.Background(), time.Second)
			defer stopCancel()
			_ = manager.Stop(stopCtx)
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("process was not restarted: %#v", manager.Snapshot())
}

func TestProcessManagerReportsMissingExecutable(t *testing.T) {
	manager := NewProcessManager(ProcessOptions{
		ExecutablePath: "definitely-missing-gbrain-for-nexus-test",
		HomeDir:        t.TempDir(),
		SkipInitialize: true,
	})
	err := manager.Start(context.Background())
	if err == nil {
		t.Fatal("expected missing executable")
	}
	if got := manager.Snapshot().Status; got != knowledgegraph.GraphStatusNotInstalled {
		t.Fatalf("status = %q, err = %v", got, err)
	}
}

func TestProcessManagerRedactsSensitiveEnvironmentValues(t *testing.T) {
	manager := NewProcessManager(ProcessOptions{
		HomeDir: t.TempDir(),
		Environment: map[string]string{
			"GBRAIN_EMBEDDING_API_KEY": "sensitive-test-value",
		},
	})
	output := manager.safeOutput("provider failed with sensitive-test-value")
	if strings.Contains(output, "sensitive-test-value") || !strings.Contains(output, "[REDACTED]") {
		t.Fatalf("output was not redacted: %q", output)
	}
}

func TestProcessManagerPrependsManagedExecutableDirectoryToPath(t *testing.T) {
	executable := filepath.Join(t.TempDir(), "bin", "gbrain.cmd")
	manager := NewProcessManager(ProcessOptions{
		ExecutablePath: executable,
		HomeDir:        t.TempDir(),
	})
	var processPath string
	for _, entry := range manager.environment() {
		if strings.HasPrefix(strings.ToUpper(entry), "PATH=") {
			processPath = entry[len("PATH="):]
			break
		}
	}
	if processPath == "" || !strings.EqualFold(strings.Split(processPath, string(os.PathListSeparator))[0], filepath.Dir(executable)) {
		t.Fatalf("PATH = %q", processPath)
	}
}

func TestProcessManagerInitializesAndMigratesPGLite(t *testing.T) {
	home := t.TempDir()
	recordPath := filepath.Join(home, "commands.log")
	databasePath := filepath.Join(home, "brain.db")
	options := helperProcessOptions(t, false)
	options.HomeDir = home
	options.DatabasePath = databasePath
	options.SkipInitialize = false
	options.Environment["NEXUS_GBRAIN_HELPER_RECORD"] = recordPath

	ctx, cancel := context.WithCancel(context.Background())
	first := NewProcessManager(options)
	if err := first.Start(ctx); err != nil {
		cancel()
		t.Fatal(err)
	}
	stopCtx, stopCancel := context.WithTimeout(context.Background(), time.Second)
	if err := first.Stop(stopCtx); err != nil {
		stopCancel()
		cancel()
		t.Fatal(err)
	}
	stopCancel()

	second := NewProcessManager(options)
	if err := second.Start(ctx); err != nil {
		cancel()
		t.Fatal(err)
	}
	stopCtx, stopCancel = context.WithTimeout(context.Background(), time.Second)
	if err := second.Stop(stopCtx); err != nil {
		stopCancel()
		cancel()
		t.Fatal(err)
	}
	stopCancel()
	cancel()

	data, err := os.ReadFile(recordPath)
	if err != nil {
		t.Fatal(err)
	}
	record := string(data)
	if !strings.Contains(record, "init --pglite --non-interactive --no-embedding --skip-embed-check --path "+databasePath+" --json") {
		t.Fatalf("missing PGLite init command:\n%s", record)
	}
	if !strings.Contains(record, "apply-migrations --yes --non-interactive") {
		t.Fatalf("missing migration command:\n%s", record)
	}
}

func TestExistingConfigPathSupportsGBrainNestedHomeLayout(t *testing.T) {
	home := t.TempDir()
	nested := filepath.Join(home, ".gbrain", "config.json")
	if err := os.MkdirAll(filepath.Dir(nested), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(nested, []byte("{}\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if got := existingConfigPath(home); got != nested {
		t.Fatalf("config path = %q, want %q", got, nested)
	}
}

func TestProcessManagerRestartsWithOneChangedSource(t *testing.T) {
	options := helperProcessOptions(t, false)
	manager := NewProcessManager(options)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if err := manager.Start(ctx); err != nil {
		t.Fatal(err)
	}
	firstPID := manager.Snapshot().ProcessID
	restartCtx, restartCancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer restartCancel()
	if err := manager.RestartWithEnvironment(restartCtx, map[string]string{"GBRAIN_SOURCE": "project-sample"}); err != nil {
		t.Fatal(err)
	}
	secondPID := manager.Snapshot().ProcessID
	if firstPID == 0 || secondPID == 0 || firstPID == secondPID {
		t.Fatalf("first pid=%d second pid=%d", firstPID, secondPID)
	}
	if err := manager.RestartWithEnvironment(restartCtx, map[string]string{"GBRAIN_SOURCE": "project-sample"}); err != nil {
		t.Fatal(err)
	}
	if manager.Snapshot().ProcessID != secondPID {
		t.Fatal("unchanged source restarted the GBrain process")
	}
	stopCtx, stopCancel := context.WithTimeout(context.Background(), time.Second)
	defer stopCancel()
	if err := manager.Stop(stopCtx); err != nil {
		t.Fatal(err)
	}
}

func helperProcessOptions(t *testing.T, exitAfterInitialize bool) ProcessOptions {
	t.Helper()
	home := t.TempDir()
	return ProcessOptions{
		ExecutablePath:    os.Args[0],
		CommandPrefixArgs: []string{"-test.run=TestGBrainHelperProcess", "--"},
		ServeArgs:         []string{"serve"},
		HomeDir:           home,
		SkipInitialize:    true,
		StartupTimeout:    2 * time.Second,
		RequestTimeout:    time.Second,
		StopTimeout:       time.Second,
		RestartBackoff:    10 * time.Millisecond,
		MaxRestarts:       2,
		Environment: map[string]string{
			"NEXUS_GBRAIN_HELPER":            "1",
			"NEXUS_GBRAIN_HELPER_EXIT_AFTER": fmt.Sprintf("%t", exitAfterInitialize),
			"NEXUS_GBRAIN_HELPER_SOURCES":    filepath.Join(home, "sources.txt"),
		},
	}
}

func TestGBrainHelperProcess(t *testing.T) {
	if os.Getenv("NEXUS_GBRAIN_HELPER") != "1" {
		return
	}
	separator := -1
	for index, arg := range os.Args {
		if arg == "--" {
			separator = index
			break
		}
	}
	if separator < 0 || separator+1 >= len(os.Args) {
		os.Exit(2)
	}
	args := os.Args[separator+1:]
	if recordPath := os.Getenv("NEXUS_GBRAIN_HELPER_RECORD"); recordPath != "" {
		file, err := os.OpenFile(recordPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
		if err != nil {
			os.Exit(4)
		}
		_, _ = fmt.Fprintln(file, strings.Join(args, " "))
		_ = file.Close()
	}
	if args[0] == "--version" {
		fmt.Println("gbrain 0.42.62.0")
		os.Exit(0)
	}
	if args[0] != "serve" {
		if args[0] == "init" {
			home := os.Getenv("GBRAIN_HOME")
			if home == "" {
				os.Exit(5)
			}
			if err := os.MkdirAll(home, 0o755); err != nil {
				os.Exit(6)
			}
			if err := os.WriteFile(filepath.Join(home, "config.json"), []byte("{}\n"), 0o644); err != nil {
				os.Exit(7)
			}
		}
		fmt.Println(`{"ok":true}`)
		os.Exit(0)
	}

	reader := bufio.NewScanner(os.Stdin)
	writer := bufio.NewWriter(os.Stdout)
	for reader.Scan() {
		var request map[string]any
		if err := json.Unmarshal(reader.Bytes(), &request); err != nil {
			os.Exit(3)
		}
		method, _ := request["method"].(string)
		id, hasID := request["id"]
		if !hasID {
			continue
		}
		var result any
		switch method {
		case "initialize":
			result = map[string]any{
				"protocolVersion": mcpProtocolVersion,
				"capabilities":    map[string]any{"tools": map[string]any{}},
				"serverInfo":      map[string]any{"name": "gbrain-helper", "version": "test"},
			}
		case "tools/list":
			result = map[string]any{"tools": []map[string]any{{"name": "search"}, {"name": "graph_traverse"}}}
		case "tools/call":
			params, _ := request["params"].(map[string]any)
			toolName, _ := params["name"].(string)
			arguments, _ := params["arguments"].(map[string]any)
			appendHelperRecord("tool " + toolName + " source=" + os.Getenv("GBRAIN_SOURCE") + " slug=" + fmt.Sprint(arguments["slug"]))
			payload := helperToolResult(toolName, arguments)
			encoded, _ := json.Marshal(payload)
			result = map[string]any{
				"content": []map[string]any{{"type": "text", "text": string(encoded)}},
			}
		default:
			result = map[string]any{}
		}
		response, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": id, "result": result})
		_, _ = writer.Write(append(response, '\n'))
		_ = writer.Flush()
		if method == "tools/list" && strings.EqualFold(os.Getenv("NEXUS_GBRAIN_HELPER_EXIT_AFTER"), "true") {
			os.Exit(9)
		}
	}
	os.Exit(0)
}

func helperToolResult(name string, arguments map[string]any) any {
	switch name {
	case "sources_list":
		sources := []map[string]any{}
		if data, err := os.ReadFile(os.Getenv("NEXUS_GBRAIN_HELPER_SOURCES")); err == nil {
			for _, id := range strings.Fields(string(data)) {
				sources = append(sources, map[string]any{"id": id, "name": id})
			}
		}
		return map[string]any{"sources": sources}
	case "sources_add":
		id := fmt.Sprint(arguments["id"])
		if id != "" {
			file, err := os.OpenFile(os.Getenv("NEXUS_GBRAIN_HELPER_SOURCES"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
			if err == nil {
				_, _ = fmt.Fprintln(file, id)
				_ = file.Close()
			}
		}
		return map[string]any{"ok": true, "source": arguments}
	case "put_page", "delete_page", "sources_remove":
		return map[string]any{"ok": true}
	case "search":
		return []map[string]any{
			{
				"slug": "features/model-routing/proxy-model-families", "page_id": 10,
				"title": "Proxy model families", "type": "Guide",
				"chunk_text": "The proxy routes model families.", "chunk_source": "compiled_truth",
				"chunk_id": 20, "chunk_index": 0, "score": 0.98,
				"source_id": os.Getenv("GBRAIN_SOURCE"), "stale": false,
			},
		}
	default:
		return map[string]any{}
	}
}

func appendHelperRecord(line string) {
	recordPath := os.Getenv("NEXUS_GBRAIN_HELPER_RECORD")
	if recordPath == "" {
		return
	}
	file, err := os.OpenFile(recordPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return
	}
	_, _ = fmt.Fprintln(file, line)
	_ = file.Close()
}
