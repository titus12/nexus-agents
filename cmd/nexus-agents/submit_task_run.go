package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"strings"

	"nexus-agents/internal/catalog"
	"nexus-agents/internal/taskrunsubmit"
)

func runSubmitTaskRun(args []string) error {
	flags := flag.NewFlagSet("submit-task-run", flag.ContinueOnError)
	flags.SetOutput(io.Discard)

	var (
		filePath  string
		endpoint  string
		useStore  bool
		storePath string
		sessionID string
	)
	flags.StringVar(&filePath, "file", "", "Path to a JSON TaskRunInput payload file. Use - for stdin.")
	flags.StringVar(&endpoint, "endpoint", taskrunsubmit.DefaultEndpoint, "HTTP endpoint for task run submission.")
	flags.BoolVar(&useStore, "use-store", false, "Submit directly to a local evaluation store instead of HTTP.")
	flags.StringVar(&storePath, "store", "", "Optional evaluation store path used with --use-store.")
	flags.StringVar(&sessionID, "session-id", "", "Codex Session-Id used for server-owned token and route metric attribution.")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if strings.TrimSpace(filePath) == "" {
		return fmt.Errorf("submit-task-run requires --file <payload.json> or --file - for stdin")
	}

	data, err := readSubmitPayload(filePath)
	if err != nil {
		return err
	}
	var input catalog.TaskRunInput
	if err := json.Unmarshal(data, &input); err != nil {
		return fmt.Errorf("decode task run payload: %w", err)
	}

	if strings.TrimSpace(sessionID) == "" {
		sessionID = strings.TrimSpace(input.SessionID)
	}
	if strings.TrimSpace(sessionID) == "" {
		if value, ok := input.Context["sessionId"].(string); ok {
			sessionID = strings.TrimSpace(value)
		}
	}
	submitter := taskrunsubmit.Submitter{Endpoint: endpoint, SessionID: sessionID}
	var sessionStore *catalog.ActiveWorkflowSessionStore
	if useStore {
		store, err := openEvaluationStore(storePath)
		if err != nil {
			return err
		}
		defer store.Close()
		if strings.TrimSpace(sessionID) != "" {
			sessionStore, _ = catalog.NewDefaultActiveWorkflowSessionStore()
			input = enrichTaskRunInputFromSession(input, sessionID, sessionStore, store)
		}
		submitter.Store = store
	}
	if sessionStore == nil {
		sessionStore, _ = catalog.NewDefaultActiveWorkflowSessionStore()
	}

	run, err := submitter.SubmitWorkflowResult(input)
	if err != nil {
		return err
	}
	if sessionStore != nil {
		if sessionID := strings.TrimSpace(input.SessionID); sessionID != "" {
			_ = sessionStore.Complete(sessionID)
		} else if sessionID, ok := input.Context["sessionId"].(string); ok && strings.TrimSpace(sessionID) != "" {
			_ = sessionStore.Complete(sessionID)
		}
	}
	encoded, err := json.MarshalIndent(run, "", "  ")
	if err != nil {
		return fmt.Errorf("encode created task run: %w", err)
	}
	_, _ = fmt.Fprintln(os.Stdout, string(encoded))
	return nil
}

func enrichTaskRunInputFromSession(input catalog.TaskRunInput, sessionID string, sessionStore *catalog.ActiveWorkflowSessionStore, lookup taskrunsubmit.TokenUsageLookup) catalog.TaskRunInput {
	metrics := map[string]any{}
	for key, value := range input.Metrics {
		if key == "tokenUsage" || key == "routeMetrics" {
			continue
		}
		metrics[key] = value
	}
	if sessionStore == nil || lookup == nil || strings.TrimSpace(sessionID) == "" {
		input.Metrics = metrics
		return input
	}
	session, ok, err := sessionStore.Lookup(sessionID)
	if err != nil || !ok || strings.TrimSpace(session.WorkflowRunID) == "" {
		input.Metrics = metrics
		return input
	}
	workflowRunID := strings.TrimSpace(session.WorkflowRunID)
	if usage, ok, err := lookup.TokenUsageForWorkflowRun(workflowRunID); err == nil && ok {
		metrics["tokenUsage"] = usage
	}
	if routeMetrics, ok, err := lookup.RouteMetricsForWorkflowRun(workflowRunID); err == nil && ok {
		metrics["routeMetrics"] = routeMetrics
	}
	input.Metrics = metrics
	return input
}

func readSubmitPayload(filePath string) ([]byte, error) {
	if strings.TrimSpace(filePath) == "-" {
		data, err := io.ReadAll(os.Stdin)
		if err != nil {
			return nil, fmt.Errorf("read stdin payload: %w", err)
		}
		return data, nil
	}
	data, err := os.ReadFile(filePath)
	if err != nil {
		return nil, fmt.Errorf("read payload file %s: %w", filePath, err)
	}
	return data, nil
}

func openEvaluationStore(storePath string) (*catalog.EvaluationStore, error) {
	if strings.TrimSpace(storePath) == "" {
		return catalog.NewDefaultEvaluationStore()
	}
	return catalog.NewEvaluationStore(storePath)
}
