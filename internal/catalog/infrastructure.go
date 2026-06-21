package catalog

import (
	"archive/tar"
	"archive/zip"
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"time"
)

type InfrastructureItem struct {
	ID             string   `json:"id"`
	Name           string   `json:"name"`
	Kind           string   `json:"kind"`
	Status         string   `json:"status"`
	Summary        string   `json:"summary"`
	Description    string   `json:"description"`
	GitHubURL      string   `json:"githubUrl"`
	InstallCommand string   `json:"installCommand"`
	CommonCommands []string `json:"commonCommands"`
	Source         string   `json:"source"`
	Installable    bool     `json:"installable"`
	Version        string   `json:"version,omitempty"`
	ExecutablePath string   `json:"executablePath,omitempty"`
	LastCheckedAt  string   `json:"lastCheckedAt,omitempty"`
	Output         string   `json:"output,omitempty"`
}

type InfrastructureServiceOptions struct {
	Runner     InfrastructureCommandRunner
	RTKUpdater InfrastructureUpdater
}

type InfrastructureCommandRunner interface {
	Run(name string, args ...string) (string, error)
}

type InfrastructureCommandRunnerFunc func(name string, args ...string) (string, error)

func (fn InfrastructureCommandRunnerFunc) Run(name string, args ...string) (string, error) {
	return fn(name, args...)
}

type InfrastructureUpdater interface {
	Update() (string, error)
}

type InfrastructureUpdaterFunc func() (string, error)

func (fn InfrastructureUpdaterFunc) Update() (string, error) {
	return fn()
}

type InfrastructureService struct {
	runner            InfrastructureCommandRunner
	rtkUpdater        InfrastructureUpdater
	mu                sync.Mutex
	installedOptional map[string]bool
}

func NewInfrastructureService(options InfrastructureServiceOptions) *InfrastructureService {
	runner := options.Runner
	if runner == nil {
		runner = execCommandRunner{}
	}
	rtkUpdater := options.RTKUpdater
	if rtkUpdater == nil {
		rtkUpdater = InfrastructureUpdaterFunc(updateRTKFromGitHub)
	}
	return &InfrastructureService{
		runner:            runner,
		rtkUpdater:        rtkUpdater,
		installedOptional: map[string]bool{},
	}
}

func (s *InfrastructureService) Items() []InfrastructureItem {
	items := infrastructureDefinitions()
	for index := range items {
		items[index] = s.withLocalStatus(items[index])
	}
	s.mu.Lock()
	installedOptional := make(map[string]bool, len(s.installedOptional))
	for id, installed := range s.installedOptional {
		installedOptional[id] = installed
	}
	s.mu.Unlock()
	for _, item := range optionalInfrastructureDefinitions() {
		if !installedOptional[item.ID] {
			continue
		}
		items = append(items, s.withLocalStatus(item))
	}
	return items
}

func (s *InfrastructureService) Catalog() []InfrastructureItem {
	items := optionalInfrastructureDefinitions()
	for index := range items {
		items[index] = s.withLocalStatus(items[index])
	}
	return items
}

func (s *InfrastructureService) Check(id string) (InfrastructureItem, bool, error) {
	item, ok := infrastructureDefinition(id)
	if !ok {
		return InfrastructureItem{}, false, nil
	}
	item = s.withLocalStatus(item)
	if id == "codegraph" {
		output, err := s.runner.Run("codegraph", "upgrade", "--check")
		item.Output = strings.TrimSpace(output)
		if err != nil {
			item.Status = "unhealthy"
			item.Output = strings.TrimSpace(output + "\n" + err.Error())
		}
	}
	return item, true, nil
}

func (s *InfrastructureService) Install(id string) (InfrastructureItem, bool, error) {
	item, ok := infrastructureDefinition(id)
	if !ok {
		return InfrastructureItem{}, false, nil
	}
	command, ok := infrastructureInstallCommand(id)
	if !ok {
		item.Status = "unhealthy"
		item.Output = "install is not supported for this infrastructure item"
		return item, true, nil
	}

	output, err := s.runner.Run(command[0], command[1:]...)
	if err != nil {
		item.Status = "unhealthy"
		item.Output = strings.TrimSpace(output + "\n" + err.Error())
		return item, true, nil
	}

	item = s.withLocalStatus(item)
	item.Output = strings.TrimSpace(output + "\n" + item.Output)
	if item.Source == "third_party" {
		s.mu.Lock()
		s.installedOptional[id] = true
		s.mu.Unlock()
	}
	return item, true, nil
}

func (s *InfrastructureService) Update(id string) (InfrastructureItem, bool, error) {
	item, ok := infrastructureDefinition(id)
	if !ok {
		return InfrastructureItem{}, false, nil
	}

	switch id {
	case "rtk":
		output, err := s.rtkUpdater.Update()
		item = s.withLocalStatus(item)
		if err != nil {
			item.Status = "unhealthy"
			item.Output = strings.TrimSpace(output + "\n" + err.Error())
			return item, true, nil
		}
		item.Output = strings.TrimSpace(output)
		if version := parseVersion(output); version != "" {
			item.Version = version
			item.Status = "ready"
		}
		return item, true, nil
	}

	command, ok := infrastructureUpdateCommand(id)
	if !ok {
		return InfrastructureItem{}, false, nil
	}
	output, err := s.runner.Run(command[0], command[1:]...)
	if err != nil {
		item = s.withLocalStatus(item)
		item.Status = "unhealthy"
		item.Output = strings.TrimSpace(output + "\n" + err.Error())
		return item, true, nil
	}
	item = s.withLocalStatus(item)
	item.Output = strings.TrimSpace(output)
	return item, true, nil
}

func (s *InfrastructureService) withLocalStatus(item InfrastructureItem) InfrastructureItem {
	item.LastCheckedAt = nowStamp()
	output, err := s.runner.Run(item.ID, "--version")
	if err != nil {
		item.Status = "missing"
		item.Output = strings.TrimSpace(output + "\n" + err.Error())
		return item
	}
	if executable, err := exec.LookPath(item.ID); err == nil {
		item.ExecutablePath = executable
	}
	item.Version = parseVersion(output)
	item.Status = "ready"
	item.Output = strings.TrimSpace(output)
	return item
}

func infrastructureDefinitions() []InfrastructureItem {
	return []InfrastructureItem{
		{
			ID:             "rtk",
			Name:           "RTK",
			Kind:           "token_proxy",
			Status:         "unknown",
			Summary:        "Token-optimized CLI proxy for shell output.",
			Description:    "RTK wraps common shell commands and filters noisy output before it enters the AI context. Nexus Agents uses it to keep command feedback compact while preserving the signal needed for development work.",
			GitHubURL:      "https://github.com/rtk-ai/rtk",
			InstallCommand: "npm i -g rtk",
			CommonCommands: []string{"rtk --version", "rtk gain", "rtk gain --history", "rtk proxy <cmd>"},
			Source:         "built_in",
			Installable:    true,
		},
		{
			ID:             "codegraph",
			Name:           "Codegraph",
			Kind:           "code_context",
			Status:         "unknown",
			Summary:        "Code graph, call-chain, impact analysis, and MCP retrieval.",
			Description:    "Codegraph builds a project index that lets agents search symbols, inspect callers/callees, and estimate impact without blindly reading large folders. It is the main context-reduction layer for code understanding workflows.",
			GitHubURL:      "https://github.com/colbymchenry/codegraph",
			InstallCommand: "npm i -g @colbymchenry/codegraph",
			CommonCommands: []string{"codegraph init -i", "codegraph status", "codegraph sync", "codegraph upgrade --check", "codegraph upgrade"},
			Source:         "built_in",
			Installable:    true,
		},
	}
}

func optionalInfrastructureDefinitions() []InfrastructureItem {
	return []InfrastructureItem{
		{
			ID:             "repomix",
			Name:           "Repomix",
			Kind:           "context_pack",
			Status:         "unknown",
			Summary:        "Pack repository content into an AI-friendly context file.",
			Description:    "Repomix is a focused third-party AI infrastructure tool for turning a codebase into compact context that can be handed to coding agents or long-context model calls without manually copying folders.",
			GitHubURL:      "https://github.com/yamadashy/repomix",
			InstallCommand: "npm install -g repomix",
			CommonCommands: []string{"repomix --version", "repomix", "repomix --output repomix-output.xml"},
			Source:         "third_party",
			Installable:    true,
		},
	}
}

func infrastructureDefinition(id string) (InfrastructureItem, bool) {
	for _, items := range [][]InfrastructureItem{infrastructureDefinitions(), optionalInfrastructureDefinitions()} {
		for _, item := range items {
			if item.ID == id {
				return item, true
			}
		}
	}
	return InfrastructureItem{}, false
}

func infrastructureInstallCommand(id string) ([]string, bool) {
	commands := map[string][]string{
		"rtk":       {"npm", "install", "-g", "rtk"},
		"codegraph": {"npm", "install", "-g", "@colbymchenry/codegraph"},
		"repomix":   {"npm", "install", "-g", "repomix"},
	}
	command, ok := commands[id]
	return command, ok
}

func infrastructureUpdateCommand(id string) ([]string, bool) {
	commands := map[string][]string{
		"codegraph": {"codegraph", "upgrade"},
		"repomix":   {"npm", "update", "-g", "repomix"},
	}
	command, ok := commands[id]
	return command, ok
}

func parseVersion(output string) string {
	fields := strings.Fields(strings.TrimSpace(output))
	for _, field := range fields {
		trimmed := strings.TrimPrefix(strings.TrimSpace(field), "v")
		if trimmed == "" {
			continue
		}
		if trimmed[0] >= '0' && trimmed[0] <= '9' {
			return trimmed
		}
	}
	return ""
}

type execCommandRunner struct{}

func (execCommandRunner) Run(name string, args ...string) (string, error) {
	command := exec.Command(name, args...)
	output, err := command.CombinedOutput()
	return string(output), err
}

type githubRelease struct {
	TagName string `json:"tag_name"`
	Assets  []struct {
		Name               string `json:"name"`
		BrowserDownloadURL string `json:"browser_download_url"`
	} `json:"assets"`
}

func updateRTKFromGitHub() (string, error) {
	executablePath, err := exec.LookPath("rtk")
	if err != nil {
		return "", fmt.Errorf("find rtk executable: %w", err)
	}

	release, err := fetchRTKLatestRelease()
	if err != nil {
		return "", err
	}
	assetName, assetURL, err := selectRTKAsset(release)
	if err != nil {
		return "", err
	}
	checksumURL, err := selectChecksumAsset(release)
	if err != nil {
		return "", err
	}

	archiveData, err := downloadBytes(assetURL)
	if err != nil {
		return "", fmt.Errorf("download %s: %w", assetName, err)
	}
	checksumData, err := downloadBytes(checksumURL)
	if err != nil {
		return "", fmt.Errorf("download checksums.txt: %w", err)
	}
	if err := verifySHA256(assetName, archiveData, string(checksumData)); err != nil {
		return "", err
	}

	executableData, err := extractRTKExecutable(assetName, archiveData)
	if err != nil {
		return "", err
	}
	if err := replaceExecutable(executablePath, executableData); err != nil {
		return "", err
	}

	output, err := execCommandRunner{}.Run("rtk", "--version")
	if err != nil {
		return "", fmt.Errorf("verify updated rtk: %w", err)
	}
	return strings.Join([]string{
		"Downloaded GitHub release asset " + assetName + ".",
		"Verified checksums.txt.",
		"Replaced " + executablePath + ".",
		strings.TrimSpace(output),
	}, "\n"), nil
}

func fetchRTKLatestRelease() (githubRelease, error) {
	response, err := http.Get("https://api.github.com/repos/rtk-ai/rtk/releases/latest")
	if err != nil {
		return githubRelease{}, fmt.Errorf("fetch RTK latest release: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return githubRelease{}, fmt.Errorf("fetch RTK latest release: status %d", response.StatusCode)
	}
	var release githubRelease
	if err := json.NewDecoder(response.Body).Decode(&release); err != nil {
		return githubRelease{}, fmt.Errorf("decode RTK latest release: %w", err)
	}
	return release, nil
}

func selectRTKAsset(release githubRelease) (string, string, error) {
	osToken := runtime.GOOS
	archTokens := []string{runtime.GOARCH}
	if runtime.GOARCH == "amd64" {
		archTokens = append(archTokens, "x86_64")
	}
	if runtime.GOARCH == "arm64" {
		archTokens = append(archTokens, "aarch64")
	}
	if runtime.GOOS == "darwin" {
		osToken = "apple-darwin"
	}
	if runtime.GOOS == "windows" {
		osToken = "windows"
	}

	for _, asset := range release.Assets {
		name := strings.ToLower(asset.Name)
		if !strings.Contains(name, "rtk") || !strings.Contains(name, osToken) {
			continue
		}
		for _, archToken := range archTokens {
			if strings.Contains(name, archToken) {
				return asset.Name, asset.BrowserDownloadURL, nil
			}
		}
	}
	return "", "", fmt.Errorf("no RTK release asset for %s/%s", runtime.GOOS, runtime.GOARCH)
}

func selectChecksumAsset(release githubRelease) (string, error) {
	for _, asset := range release.Assets {
		name := strings.ToLower(asset.Name)
		if strings.Contains(name, "checksum") && strings.HasSuffix(name, ".txt") {
			return asset.BrowserDownloadURL, nil
		}
	}
	return "", fmt.Errorf("RTK release is missing checksums.txt")
}

func downloadBytes(url string) ([]byte, error) {
	client := &http.Client{Timeout: 90 * time.Second}
	response, err := client.Get(url)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("status %d", response.StatusCode)
	}
	return io.ReadAll(response.Body)
}

func verifySHA256(assetName string, data []byte, checksums string) error {
	sum := sha256.Sum256(data)
	actual := hex.EncodeToString(sum[:])
	for _, line := range strings.Split(checksums, "\n") {
		if !strings.Contains(line, assetName) {
			continue
		}
		parts := strings.Fields(line)
		if len(parts) == 0 {
			continue
		}
		if strings.EqualFold(parts[0], actual) {
			return nil
		}
		return fmt.Errorf("checksum mismatch for %s", assetName)
	}
	return fmt.Errorf("checksums.txt has no entry for %s", assetName)
}

func extractRTKExecutable(assetName string, data []byte) ([]byte, error) {
	lowerName := strings.ToLower(assetName)
	if strings.HasSuffix(lowerName, ".zip") {
		return extractRTKFromZip(data)
	}
	if strings.HasSuffix(lowerName, ".tar.gz") || strings.HasSuffix(lowerName, ".tgz") {
		return extractRTKFromTarGzip(data)
	}
	return nil, fmt.Errorf("unsupported RTK archive format: %s", assetName)
}

func extractRTKFromZip(data []byte) ([]byte, error) {
	reader, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil {
		return nil, fmt.Errorf("open zip archive: %w", err)
	}
	for _, file := range reader.File {
		if !isRTKExecutableName(file.Name) {
			continue
		}
		fileReader, err := file.Open()
		if err != nil {
			return nil, err
		}
		defer fileReader.Close()
		return io.ReadAll(fileReader)
	}
	return nil, fmt.Errorf("rtk executable not found in zip archive")
}

func extractRTKFromTarGzip(data []byte) ([]byte, error) {
	gzipReader, err := gzip.NewReader(bytes.NewReader(data))
	if err != nil {
		return nil, fmt.Errorf("open gzip archive: %w", err)
	}
	defer gzipReader.Close()
	tarReader := tar.NewReader(gzipReader)
	for {
		header, err := tarReader.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, err
		}
		if header.Typeflag != tar.TypeReg || !isRTKExecutableName(header.Name) {
			continue
		}
		return io.ReadAll(tarReader)
	}
	return nil, fmt.Errorf("rtk executable not found in tar.gz archive")
}

func isRTKExecutableName(name string) bool {
	base := strings.ToLower(filepath.Base(name))
	return base == "rtk" || base == "rtk.exe"
}

func replaceExecutable(executablePath string, data []byte) error {
	backupPath := executablePath + ".bak-nexus"
	if err := os.Remove(backupPath); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("remove old backup: %w", err)
	}
	if err := os.Rename(executablePath, backupPath); err != nil {
		return fmt.Errorf("backup current executable: %w", err)
	}
	if err := os.WriteFile(executablePath, data, 0o755); err != nil {
		_ = os.Rename(backupPath, executablePath)
		return fmt.Errorf("write updated executable: %w", err)
	}
	if err := os.Remove(backupPath); err != nil {
		return fmt.Errorf("remove backup executable: %w", err)
	}
	return nil
}
