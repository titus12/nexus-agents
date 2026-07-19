package wikicompiler

import "context"

type Provider interface {
	Initialize(ctx context.Context, input CompileInput) (GeneratedBundle, error)
	Update(ctx context.Context, input CompileInput) (GeneratedBundle, error)
}

type CompileInput struct {
	ProjectID              string
	RunID                  string
	ProjectRoot            string
	Revision               string
	DataRoot               string
	KnowledgeRoot          string
	Language               string
	RequiredTopics         []string
	AdditionalInstructions string
	ScanManifest           []string
	ExistingKnowledge      map[string][]byte
	ExternalSources        []ExternalSource
	Environment            map[string]string
}

type ExternalSource struct {
	Title     string
	SourceURL string
	LocalPath string
	Revision  string
}

type GeneratedBundle struct {
	Files         map[string][]byte `json:"-"`
	Paths         []string          `json:"paths"`
	Compiler      string            `json:"compiler"`
	Version       string            `json:"version"`
	WorkspaceRoot string            `json:"workspaceRoot"`
	Output        string            `json:"output,omitempty"`
	Warnings      []string          `json:"warnings"`
}
