package knowledgegraph

import "time"

type GraphStatus string

const (
	GraphStatusDisabled       GraphStatus = "disabled"
	GraphStatusNotInstalled   GraphStatus = "not_installed"
	GraphStatusNotInitialized GraphStatus = "not_initialized"
	GraphStatusStarting       GraphStatus = "starting"
	GraphStatusReady          GraphStatus = "ready"
	GraphStatusUnhealthy      GraphStatus = "unhealthy"
	GraphStatusStopped        GraphStatus = "stopped"
)

type GraphHealth struct {
	Provider      string      `json:"provider"`
	Status        GraphStatus `json:"status"`
	Version       string      `json:"version,omitempty"`
	Engine        string      `json:"engine,omitempty"`
	Transport     string      `json:"transport,omitempty"`
	ProcessID     int         `json:"processId,omitempty"`
	RestartCount  int         `json:"restartCount,omitempty"`
	Capabilities  []string    `json:"capabilities,omitempty"`
	LastCheckedAt time.Time   `json:"lastCheckedAt"`
	LastError     string      `json:"lastError,omitempty"`
}

type GraphDocument struct {
	ID         string            `json:"id"`
	Slug       string            `json:"slug"`
	Path       string            `json:"path"`
	SourcePath string            `json:"sourcePath,omitempty"`
	Title      string            `json:"title,omitempty"`
	Content    string            `json:"content"`
	Hash       string            `json:"hash,omitempty"`
	Metadata   map[string]string `json:"metadata,omitempty"`
}

type GraphSource struct {
	ID               string          `json:"id"`
	ProviderSourceID string          `json:"providerSourceId,omitempty"`
	ProjectID        string          `json:"projectId"`
	Root             string          `json:"root"`
	Revision         string          `json:"revision,omitempty"`
	Documents        []GraphDocument `json:"documents,omitempty"`
	AllDocuments     []GraphDocument `json:"allDocuments,omitempty"`
	DeleteSlugs      []string        `json:"deleteSlugs,omitempty"`
}

type GraphSyncResult struct {
	SourceID         string `json:"sourceId"`
	ProviderSourceID string `json:"providerSourceId,omitempty"`
	Documents        int    `json:"documents"`
	Created          int    `json:"created"`
	Updated          int    `json:"updated"`
	Deleted          int    `json:"deleted"`
	Unchanged        int    `json:"unchanged"`
}

type GraphSearchQuery struct {
	Query     string   `json:"query"`
	SourceIDs []string `json:"sourceIds,omitempty"`
	DomainIDs []string `json:"domainIds,omitempty"`
	Limit     int      `json:"limit,omitempty"`
}

type GraphSearchHit struct {
	ID       string         `json:"id"`
	SourceID string         `json:"sourceId,omitempty"`
	Path     string         `json:"path,omitempty"`
	Title    string         `json:"title,omitempty"`
	Snippet  string         `json:"snippet,omitempty"`
	Score    float64        `json:"score"`
	Metadata map[string]any `json:"metadata,omitempty"`
}

type GraphSearchResult struct {
	Query string           `json:"query"`
	Hits  []GraphSearchHit `json:"hits"`
}

type GraphTraversalQuery struct {
	StartIDs      []string `json:"startIds"`
	RelationTypes []string `json:"relationTypes,omitempty"`
	MaxDepth      int      `json:"maxDepth,omitempty"`
}

type GraphNode struct {
	ID         string         `json:"id"`
	Type       string         `json:"type"`
	Name       string         `json:"name,omitempty"`
	Properties map[string]any `json:"properties,omitempty"`
}

type GraphEdge struct {
	SourceID   string         `json:"sourceId"`
	Type       string         `json:"type"`
	TargetID   string         `json:"targetId"`
	Properties map[string]any `json:"properties,omitempty"`
}

type GraphTraversalResult struct {
	Nodes []GraphNode `json:"nodes"`
	Edges []GraphEdge `json:"edges"`
}

type GraphSynthesisQuery struct {
	Query     string   `json:"query"`
	SourceIDs []string `json:"sourceIds,omitempty"`
}

type GraphSynthesisResult struct {
	Content  string   `json:"content"`
	Evidence []string `json:"evidence,omitempty"`
}

type GraphGapScope struct {
	ProjectIDs []string `json:"projectIds,omitempty"`
	DomainIDs  []string `json:"domainIds,omitempty"`
}

type KnowledgeGap struct {
	ID          string   `json:"id"`
	Title       string   `json:"title"`
	Description string   `json:"description,omitempty"`
	Evidence    []string `json:"evidence,omitempty"`
	Confidence  float64  `json:"confidence,omitempty"`
}
