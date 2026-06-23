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
		filePath   string
		endpoint   string
		useStore   bool
		storePath  string
	)
	flags.StringVar(&filePath, "file", "", "Path to a JSON TaskRunInput payload file. Use - for stdin.")
	flags.StringVar(&endpoint, "endpoint", taskrunsubmit.DefaultEndpoint, "HTTP endpoint for task run submission.")
	flags.BoolVar(&useStore, "use-store", false, "Submit directly to a local evaluation store instead of HTTP.")
	flags.StringVar(&storePath, "store", "", "Optional evaluation store path used with --use-store.")
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

	submitter := taskrunsubmit.Submitter{Endpoint: endpoint}
	if useStore {
		store, err := openEvaluationStore(storePath)
		if err != nil {
			return err
		}
		defer store.Close()
		submitter.Store = store
	}

	run, err := submitter.SubmitWorkflowResult(input)
	if err != nil {
		return err
	}
	encoded, err := json.MarshalIndent(run, "", "  ")
	if err != nil {
		return fmt.Errorf("encode created task run: %w", err)
	}
	_, _ = fmt.Fprintln(os.Stdout, string(encoded))
	return nil
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
