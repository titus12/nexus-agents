package gbrain

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"sync"
	"time"

	"nexus-agents/internal/knowledgegraph"
)

const (
	TestedVersion = "0.42.62.0"
	TestedCommit  = "f72de97943eb9dc1292a80f85d19db7e311855dc"
)

var (
	ErrNotInstalled    = errors.New("GBrain is not installed")
	ErrVersionMismatch = errors.New("GBrain version does not match the Nexus pin")
)

type ProcessOptions struct {
	ExecutablePath    string
	CommandPrefixArgs []string
	ServeArgs         []string
	HomeDir           string
	DatabasePath      string
	Environment       map[string]string
	StartupTimeout    time.Duration
	RequestTimeout    time.Duration
	StopTimeout       time.Duration
	RestartBackoff    time.Duration
	MaxRestarts       int
	SkipInitialize    bool
	Logf              func(format string, args ...any)
}

func DefaultProcessOptions() ProcessOptions {
	home, _ := os.UserHomeDir()
	gbrainHome := filepath.Join(home, ".nexus", "gbrain")
	return ProcessOptions{
		ExecutablePath: defaultExecutablePath(home),
		ServeArgs:      []string{"serve"},
		HomeDir:        gbrainHome,
		DatabasePath:   filepath.Join(gbrainHome, "brain.db"),
		StartupTimeout: 20 * time.Second,
		RequestTimeout: 10 * time.Second,
		StopTimeout:    5 * time.Second,
		RestartBackoff: 500 * time.Millisecond,
		MaxRestarts:    5,
	}
}

type ProcessManager struct {
	options ProcessOptions

	lifecycleMu     sync.Mutex
	mu              sync.Mutex
	status          knowledgegraph.GraphStatus
	lastError       string
	lastCheckedAt   time.Time
	capabilities    []string
	processID       int
	restartCount    int
	restartAttempts int
	prepared        bool
	stopping        bool
	lifetimeContext context.Context
	runContext      context.Context
	cancel          context.CancelFunc
	command         *exec.Cmd
	client          *MCPClient
	processDone     chan struct{}
	stderr          *boundedBuffer
}

func NewProcessManager(options ProcessOptions) *ProcessManager {
	defaults := DefaultProcessOptions()
	if strings.TrimSpace(options.ExecutablePath) == "" {
		options.ExecutablePath = defaults.ExecutablePath
	}
	if len(options.ServeArgs) == 0 {
		options.ServeArgs = defaults.ServeArgs
	}
	if strings.TrimSpace(options.HomeDir) == "" {
		options.HomeDir = defaults.HomeDir
	}
	if strings.TrimSpace(options.DatabasePath) == "" {
		options.DatabasePath = filepath.Join(options.HomeDir, "brain.db")
	}
	if options.StartupTimeout <= 0 {
		options.StartupTimeout = defaults.StartupTimeout
	}
	if options.RequestTimeout <= 0 {
		options.RequestTimeout = defaults.RequestTimeout
	}
	if options.StopTimeout <= 0 {
		options.StopTimeout = defaults.StopTimeout
	}
	if options.RestartBackoff <= 0 {
		options.RestartBackoff = defaults.RestartBackoff
	}
	if options.MaxRestarts <= 0 {
		options.MaxRestarts = defaults.MaxRestarts
	}
	if options.Environment == nil {
		options.Environment = map[string]string{}
	}
	return &ProcessManager{options: options, status: knowledgegraph.GraphStatusStopped}
}

func (m *ProcessManager) Start(ctx context.Context) error {
	m.lifecycleMu.Lock()
	defer m.lifecycleMu.Unlock()
	m.lifetimeContext = ctx
	return m.startLocked(ctx, true)
}

func (m *ProcessManager) startLocked(ctx context.Context, resetRestarts bool) error {
	m.mu.Lock()
	if m.status == knowledgegraph.GraphStatusReady || m.status == knowledgegraph.GraphStatusStarting {
		m.mu.Unlock()
		return nil
	}
	if m.cancel != nil {
		m.cancel()
	}
	m.stopping = false
	m.status = knowledgegraph.GraphStatusStarting
	m.lastError = ""
	if resetRestarts {
		m.restartAttempts = 0
	}
	m.runContext, m.cancel = context.WithCancel(ctx)
	runContext := m.runContext
	m.mu.Unlock()

	if err := m.prepare(runContext); err != nil {
		m.setStartError(err)
		return err
	}
	if err := m.startProcess(runContext); err != nil {
		m.setStartError(err)
		m.scheduleRestartIfEligible(runContext)
		return err
	}
	return nil
}

func (m *ProcessManager) Stop(ctx context.Context) error {
	m.lifecycleMu.Lock()
	defer m.lifecycleMu.Unlock()
	err := m.stopLocked(ctx, true)
	return err
}

func (m *ProcessManager) RestartWithEnvironment(ctx context.Context, environment map[string]string) error {
	m.lifecycleMu.Lock()
	defer m.lifecycleMu.Unlock()

	m.mu.Lock()
	lifetimeContext := m.lifetimeContext
	current := make(map[string]string, len(m.options.Environment))
	for key, value := range m.options.Environment {
		current[key] = value
	}
	m.mu.Unlock()
	if lifetimeContext == nil {
		return fmt.Errorf("GBrain process manager has not been started")
	}
	unchanged := true
	for key, value := range environment {
		if current[key] != value {
			unchanged = false
			break
		}
	}
	if unchanged {
		m.mu.Lock()
		ready := m.status == knowledgegraph.GraphStatusReady && m.client != nil
		m.mu.Unlock()
		if ready {
			return nil
		}
	}
	if err := m.stopLocked(ctx, false); err != nil {
		return err
	}
	m.mu.Lock()
	for key, value := range environment {
		m.options.Environment[key] = value
	}
	m.mu.Unlock()
	return m.startLocked(lifetimeContext, false)
}

func (m *ProcessManager) stopLocked(ctx context.Context, final bool) error {
	m.mu.Lock()
	m.stopping = true
	m.status = knowledgegraph.GraphStatusStopped
	cancel := m.cancel
	client := m.client
	command := m.command
	done := m.processDone
	m.client = nil
	m.command = nil
	m.processDone = nil
	m.processID = 0
	m.capabilities = nil
	m.cancel = nil
	m.runContext = nil
	if final {
		m.lifetimeContext = nil
	}
	m.mu.Unlock()

	if client != nil {
		_ = client.Close()
	}
	if cancel != nil {
		cancel()
	}
	if command == nil || command.Process == nil || done == nil {
		return nil
	}

	timeout := m.options.StopTimeout
	timer := time.NewTimer(timeout)
	defer timer.Stop()
	select {
	case <-done:
		return nil
	case <-ctx.Done():
		_ = command.Process.Kill()
		return ctx.Err()
	case <-timer.C:
		_ = command.Process.Kill()
		select {
		case <-done:
			return nil
		case <-ctx.Done():
			return ctx.Err()
		}
	}
}

func (m *ProcessManager) Health(ctx context.Context) (knowledgegraph.GraphHealth, error) {
	m.mu.Lock()
	client := m.client
	status := m.status
	m.mu.Unlock()
	if status != knowledgegraph.GraphStatusReady || client == nil {
		health := m.snapshot()
		if health.LastError != "" {
			return health, errors.New(health.LastError)
		}
		return health, nil
	}

	requestTimeout := m.options.RequestTimeout
	checkCtx, cancel := context.WithTimeout(ctx, requestTimeout)
	defer cancel()
	tools, err := client.ListTools(checkCtx)
	now := time.Now().UTC()
	m.mu.Lock()
	m.lastCheckedAt = now
	if err != nil {
		m.status = knowledgegraph.GraphStatusUnhealthy
		m.lastError = err.Error()
		command := m.command
		m.mu.Unlock()
		if command != nil && command.Process != nil {
			_ = command.Process.Kill()
		}
		return m.snapshot(), err
	}
	m.capabilities = append([]string(nil), tools...)
	m.lastError = ""
	m.mu.Unlock()
	return m.snapshot(), nil
}

func (m *ProcessManager) Snapshot() knowledgegraph.GraphHealth {
	return m.snapshot()
}

func (m *ProcessManager) Client() (*MCPClient, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.status != knowledgegraph.GraphStatusReady || m.client == nil {
		if m.lastError != "" {
			return nil, errors.New(m.lastError)
		}
		return nil, fmt.Errorf("GBrain is %s", m.status)
	}
	return m.client, nil
}

func (m *ProcessManager) prepare(ctx context.Context) error {
	m.mu.Lock()
	if m.prepared {
		m.mu.Unlock()
		return nil
	}
	m.mu.Unlock()

	executable, err := resolveExecutable(m.options.ExecutablePath)
	if err != nil {
		return err
	}
	m.options.ExecutablePath = executable
	if err := os.MkdirAll(m.options.HomeDir, 0o755); err != nil {
		return fmt.Errorf("create GBrain home: %w", err)
	}
	versionOutput, err := m.runOneShot(ctx, "--version")
	if err != nil {
		return fmt.Errorf("check GBrain version: %w", err)
	}
	if version := parseVersion(versionOutput); version != TestedVersion {
		return fmt.Errorf("%w: found %q, require %q", ErrVersionMismatch, version, TestedVersion)
	}
	if !m.options.SkipInitialize {
		configPath := existingConfigPath(m.options.HomeDir)
		if configPath == "" {
			if _, err := m.runOneShot(
				ctx,
				"init", "--pglite", "--non-interactive", "--no-embedding",
				"--skip-embed-check", "--path", m.options.DatabasePath, "--json",
			); err != nil {
				return fmt.Errorf("initialize GBrain PGLite: %w", err)
			}
		} else {
			if _, err := m.runOneShot(ctx, "apply-migrations", "--yes", "--non-interactive"); err != nil {
				return fmt.Errorf("apply GBrain migrations: %w", err)
			}
		}
	}
	m.mu.Lock()
	m.prepared = true
	m.mu.Unlock()
	return nil
}

func existingConfigPath(homeDir string) string {
	for _, candidate := range []string{
		filepath.Join(homeDir, ".gbrain", "config.json"),
		filepath.Join(homeDir, "config.json"),
	} {
		if info, err := os.Stat(candidate); err == nil && !info.IsDir() {
			return candidate
		}
	}
	return ""
}

func (m *ProcessManager) runOneShot(ctx context.Context, args ...string) (string, error) {
	commandCtx, cancel := context.WithTimeout(ctx, m.options.StartupTimeout)
	defer cancel()
	commandArgs := append(append([]string(nil), m.options.CommandPrefixArgs...), args...)
	command := exec.CommandContext(commandCtx, m.options.ExecutablePath, commandArgs...)
	command.Dir = m.options.HomeDir
	command.Env = m.environment()
	configureHiddenProcess(command)
	output := &boundedBuffer{limit: 64 * 1024}
	command.Stdout = output
	command.Stderr = output
	err := command.Run()
	if commandCtx.Err() == context.DeadlineExceeded {
		return m.safeOutput(output.String()), fmt.Errorf("command timed out after %s", m.options.StartupTimeout)
	}
	if err != nil {
		safeOutput := m.safeOutput(output.String())
		return safeOutput, fmt.Errorf("%w: %s", err, safeOutput)
	}
	return output.String(), nil
}

func (m *ProcessManager) startProcess(runContext context.Context) error {
	commandArgs := append(append([]string(nil), m.options.CommandPrefixArgs...), m.options.ServeArgs...)
	command := exec.CommandContext(runContext, m.options.ExecutablePath, commandArgs...)
	command.Dir = m.options.HomeDir
	command.Env = m.environment()
	configureHiddenProcess(command)
	stdin, err := command.StdinPipe()
	if err != nil {
		return fmt.Errorf("open GBrain stdin: %w", err)
	}
	stdout, err := command.StdoutPipe()
	if err != nil {
		return fmt.Errorf("open GBrain stdout: %w", err)
	}
	stderr := &boundedBuffer{limit: 64 * 1024}
	command.Stderr = stderr
	if err := command.Start(); err != nil {
		return fmt.Errorf("start GBrain MCP process: %w", err)
	}

	var client *MCPClient
	client = NewMCPClient(stdout, stdin, func(err error) {
		m.handleProtocolFailure(client, err)
	})
	startCtx, cancel := context.WithTimeout(runContext, m.options.StartupTimeout)
	capabilities, initializeErr := client.Initialize(startCtx)
	cancel()
	if initializeErr != nil {
		_ = client.Close()
		_ = command.Process.Kill()
		_ = command.Wait()
		return fmt.Errorf("initialize GBrain MCP process: %w; stderr: %s", initializeErr, m.safeOutput(stderr.String()))
	}

	done := make(chan struct{})
	m.mu.Lock()
	if m.stopping {
		m.mu.Unlock()
		_ = client.Close()
		_ = command.Process.Kill()
		_ = command.Wait()
		close(done)
		return context.Canceled
	}
	m.command = command
	m.client = client
	m.processDone = done
	m.stderr = stderr
	m.processID = command.Process.Pid
	m.status = knowledgegraph.GraphStatusReady
	m.capabilities = append([]string(nil), capabilities...)
	m.lastCheckedAt = time.Now().UTC()
	m.lastError = ""
	m.mu.Unlock()

	go m.waitForExit(runContext, command, client, done, stderr)
	m.logf("GBrain MCP ready pid=%d tools=%d", command.Process.Pid, len(capabilities))
	return nil
}

func (m *ProcessManager) waitForExit(runContext context.Context, command *exec.Cmd, client *MCPClient, done chan struct{}, stderr *boundedBuffer) {
	err := command.Wait()
	_ = client.Close()
	close(done)

	m.mu.Lock()
	if m.command != command {
		m.mu.Unlock()
		return
	}
	m.command = nil
	m.client = nil
	m.processID = 0
	m.processDone = nil
	if m.stopping || runContext.Err() != nil {
		m.status = knowledgegraph.GraphStatusStopped
		m.mu.Unlock()
		return
	}
	message := "GBrain process exited"
	if err != nil {
		message += ": " + err.Error()
	}
	if output := stderr.String(); output != "" {
		message += "; stderr: " + m.safeOutput(output)
	}
	m.status = knowledgegraph.GraphStatusUnhealthy
	m.lastError = message
	m.mu.Unlock()
	m.logf("%s", message)
	m.scheduleRestartIfEligible(runContext)
}

func (m *ProcessManager) scheduleRestartIfEligible(runContext context.Context) {
	m.mu.Lock()
	if m.stopping || runContext.Err() != nil || m.restartAttempts >= m.options.MaxRestarts {
		m.mu.Unlock()
		return
	}
	m.restartAttempts++
	m.restartCount++
	attempt := m.restartAttempts
	delay := m.options.RestartBackoff
	for index := 1; index < attempt && delay < 30*time.Second; index++ {
		if delay > 15*time.Second {
			delay = 30 * time.Second
			break
		}
		delay *= 2
	}
	m.mu.Unlock()

	go func() {
		timer := time.NewTimer(delay)
		defer timer.Stop()
		select {
		case <-runContext.Done():
			return
		case <-timer.C:
		}
		m.mu.Lock()
		if m.stopping || m.command != nil {
			m.mu.Unlock()
			return
		}
		m.status = knowledgegraph.GraphStatusStarting
		m.mu.Unlock()
		if err := m.startProcess(runContext); err != nil {
			m.setStartError(err)
			m.scheduleRestartIfEligible(runContext)
		}
	}()
}

func (m *ProcessManager) setStartError(err error) {
	status := knowledgegraph.GraphStatusUnhealthy
	if errors.Is(err, ErrNotInstalled) {
		status = knowledgegraph.GraphStatusNotInstalled
	}
	m.mu.Lock()
	m.status = status
	m.lastError = err.Error()
	m.lastCheckedAt = time.Now().UTC()
	m.mu.Unlock()
}

func (m *ProcessManager) handleProtocolFailure(client *MCPClient, err error) {
	m.logf("GBrain MCP protocol ended: %v", err)
	m.mu.Lock()
	if m.client != client || m.command == nil || m.stopping {
		m.mu.Unlock()
		return
	}
	m.status = knowledgegraph.GraphStatusUnhealthy
	m.lastError = "GBrain MCP protocol failure: " + err.Error()
	command := m.command
	m.mu.Unlock()
	if command.Process != nil {
		_ = command.Process.Kill()
	}
}

func (m *ProcessManager) snapshot() knowledgegraph.GraphHealth {
	m.mu.Lock()
	defer m.mu.Unlock()
	return knowledgegraph.GraphHealth{
		Provider:      "gbrain",
		Status:        m.status,
		Version:       TestedVersion,
		Engine:        "pglite",
		Transport:     "stdio",
		ProcessID:     m.processID,
		RestartCount:  m.restartCount,
		Capabilities:  append([]string(nil), m.capabilities...),
		LastCheckedAt: m.lastCheckedAt,
		LastError:     m.lastError,
	}
}

func (m *ProcessManager) environment() []string {
	values := map[string]string{
		"GBRAIN_HOME":      m.options.HomeDir,
		"GBRAIN_NO_BANNER": "1",
	}
	if executableDir := filepath.Dir(m.options.ExecutablePath); executableDir != "" && executableDir != "." {
		values["PATH"] = executableDir + string(os.PathListSeparator) + os.Getenv("PATH")
	}
	for key, value := range m.options.Environment {
		values[key] = value
	}
	overrideKeys := make(map[string]bool, len(values))
	for key := range values {
		overrideKeys[processEnvironmentKey(key)] = true
	}
	base := os.Environ()
	environment := make([]string, 0, len(base)+len(values))
	for _, entry := range base {
		separator := strings.IndexByte(entry, '=')
		if separator <= 0 || !overrideKeys[processEnvironmentKey(entry[:separator])] {
			environment = append(environment, entry)
		}
	}
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for _, key := range keys {
		environment = append(environment, key+"="+values[key])
	}
	return environment
}

func processEnvironmentKey(key string) string {
	if runtime.GOOS == "windows" {
		return strings.ToUpper(key)
	}
	return key
}

func (m *ProcessManager) logf(format string, args ...any) {
	if m.options.Logf != nil {
		m.options.Logf(format, args...)
	}
}

func (m *ProcessManager) safeOutput(value string) string {
	for _, entry := range m.environment() {
		separator := strings.IndexByte(entry, '=')
		if separator <= 0 {
			continue
		}
		key := strings.ToUpper(entry[:separator])
		secret := entry[separator+1:]
		if len(secret) < 8 ||
			(!strings.Contains(key, "KEY") && !strings.Contains(key, "TOKEN") && !strings.Contains(key, "SECRET")) {
			continue
		}
		value = strings.ReplaceAll(value, secret, "[REDACTED]")
	}
	return value
}

func defaultExecutablePath(home string) string {
	binDir := filepath.Join(home, ".nexus", "tools", "gbrain", TestedCommit, "bin")
	for _, name := range []string{"gbrain.exe", "gbrain", "gbrain.cmd", "gbrain.ps1"} {
		candidate := filepath.Join(binDir, name)
		if info, err := os.Stat(candidate); err == nil && !info.IsDir() {
			return candidate
		}
	}
	return "gbrain"
}

func resolveExecutable(value string) (string, error) {
	value = strings.TrimSpace(value)
	if value == "" {
		return "", ErrNotInstalled
	}
	if filepath.IsAbs(value) || strings.ContainsRune(value, filepath.Separator) {
		info, err := os.Stat(value)
		if err != nil || info.IsDir() {
			return "", fmt.Errorf("%w: %s", ErrNotInstalled, value)
		}
		return value, nil
	}
	resolved, err := exec.LookPath(value)
	if err != nil {
		return "", fmt.Errorf("%w: %s", ErrNotInstalled, value)
	}
	return resolved, nil
}

func parseVersion(output string) string {
	for _, field := range strings.Fields(strings.TrimSpace(output)) {
		value := strings.TrimLeft(strings.TrimSpace(field), "vV")
		if value == "" || value[0] < '0' || value[0] > '9' {
			continue
		}
		return strings.TrimRight(value, ",;")
	}
	return ""
}

type boundedBuffer struct {
	mu     sync.Mutex
	buffer bytes.Buffer
	limit  int
}

func (b *boundedBuffer) Write(data []byte) (int, error) {
	original := len(data)
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.limit <= 0 {
		b.limit = 64 * 1024
	}
	if b.buffer.Len() < b.limit {
		remaining := b.limit - b.buffer.Len()
		if len(data) > remaining {
			data = data[:remaining]
		}
		_, _ = b.buffer.Write(data)
	}
	return original, nil
}

func (b *boundedBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return strings.TrimSpace(b.buffer.String())
}

var _ io.Writer = (*boundedBuffer)(nil)
