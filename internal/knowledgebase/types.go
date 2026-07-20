package knowledgebase

import "time"

const DefaultRoot = "KnowledgeBase"

type Summary struct {
	Exists       bool   `json:"exists"`
	Root         string `json:"root"`
	Documents    int    `json:"documents"`
	Domains      int    `json:"domains"`
	Workflows    int    `json:"workflows"`
	Issues       int    `json:"issues"`
	Errors       int    `json:"errors"`
	Warnings     int    `json:"warnings"`
	LastModified string `json:"lastModified,omitempty"`
	LastExported string `json:"lastExported,omitempty"`
}

type Bundle struct {
	ProjectRoot string     `json:"projectRoot"`
	Root        string     `json:"root"`
	Exists      bool       `json:"exists"`
	Documents   []Document `json:"documents"`
	ScannedAt   string     `json:"scannedAt"`
}

type Document struct {
	Path         string      `json:"path"`
	AbsolutePath string      `json:"-"`
	Name         string      `json:"name"`
	Directory    string      `json:"directory"`
	Frontmatter  Frontmatter `json:"frontmatter"`
	Body         string      `json:"body,omitempty"`
	Raw          string      `json:"raw,omitempty"`
	Links        []DocLink   `json:"links"`
	SizeBytes    int64       `json:"sizeBytes"`
	ModifiedAt   string      `json:"modifiedAt"`
	Reserved     bool        `json:"reserved"`
}

type Frontmatter struct {
	Type           string          `json:"type,omitempty"`
	Title          string          `json:"title,omitempty"`
	Description    string          `json:"description,omitempty"`
	Resource       string          `json:"resource,omitempty"`
	Tags           []string        `json:"tags,omitempty"`
	DependsOn      []string        `json:"dependsOn,omitempty"`
	SeeAlso        []string        `json:"seeAlso,omitempty"`
	Status         string          `json:"status,omitempty"`
	Owner          string          `json:"owner,omitempty"`
	Timestamp      string          `json:"timestamp,omitempty"`
	ManagedBy      string          `json:"managedBy,omitempty"`
	SourcePaths    []string        `json:"sourcePaths,omitempty"`
	SourceRevision string          `json:"sourceRevision,omitempty"`
	GeneratedBy    string          `json:"generatedBy,omitempty"`
	Routing        RoutingMetadata `json:"routing,omitempty"`
	Raw            string          `json:"raw,omitempty"`
}

type RoutingMetadata struct {
	Aliases  RoutingAliases  `json:"aliases,omitempty"`
	Keywords RoutingKeywords `json:"keywords,omitempty"`
}

type RoutingAliases struct {
	Values []string           `json:"values,omitempty"`
	ZH     []string           `json:"zh,omitempty"`
	EN     []string           `json:"en,omitempty"`
	Pairs  []RoutingAliasPair `json:"pairs,omitempty"`
}

type RoutingAliasPair struct {
	ZH string `json:"zh,omitempty"`
	EN string `json:"en,omitempty"`
}

type RoutingKeywords struct {
	Values []string `json:"values,omitempty"`
	ZH     []string `json:"zh,omitempty"`
	EN     []string `json:"en,omitempty"`
}

type MatchedAlias struct {
	Alias       string `json:"alias,omitempty"`
	PairedAlias string `json:"pairedAlias,omitempty"`
	Domain      string `json:"domain,omitempty"`
	Source      string `json:"source,omitempty"`
}

type DocLink struct {
	Text   string `json:"text"`
	Target string `json:"target"`
	Line   int    `json:"line"`
}

type ValidationReport struct {
	Root      string            `json:"root"`
	Summary   Summary           `json:"summary"`
	Issues    []ValidationIssue `json:"issues"`
	CheckedAt string            `json:"checkedAt"`
}

type ValidationIssue struct {
	Severity string `json:"severity"`
	Code     string `json:"code"`
	Path     string `json:"path"`
	Line     int    `json:"line,omitempty"`
	Message  string `json:"message"`
}

type MaintenanceReport struct {
	Root             string            `json:"root"`
	Summary          Summary           `json:"summary"`
	Issues           []ValidationIssue `json:"issues"`
	StaleDocuments   []Document        `json:"staleDocuments"`
	LargeDocuments   []Document        `json:"largeDocuments"`
	DuplicateRules   []DuplicateRule   `json:"duplicateRules"`
	SuggestedActions []string          `json:"suggestedActions"`
	CheckedAt        string            `json:"checkedAt"`
}

type DuplicateRule struct {
	Text  string   `json:"text"`
	Paths []string `json:"paths"`
}

type RoutePreview struct {
	Task             string                 `json:"task"`
	MatchedDomain    string                 `json:"matchedDomain,omitempty"`
	RequiredFiles    []string               `json:"requiredFiles"`
	OptionalFiles    []string               `json:"optionalFiles"`
	Reason           string                 `json:"reason"`
	MissingFiles     []string               `json:"missingFiles"`
	RoutingDocuments []string               `json:"routingDocuments"`
	Matches          []KnowledgeContextItem `json:"matches"`
	Terms            []string               `json:"terms"`
	TokenBudget      TokenBudget            `json:"tokenBudget"`
	LoadedKnowledge  string                 `json:"loadedKnowledgeMarkdown,omitempty"`
}

type RetrieveOptions struct {
	Mode         string              `json:"mode"`
	Limit        int                 `json:"limit"`
	MaxTokens    int                 `json:"maxTokens"`
	QueryRewrite QueryRewriteOptions `json:"queryRewrite,omitempty"`
}

type RetrievalResult struct {
	Query                   string                 `json:"query"`
	Mode                    string                 `json:"mode"`
	Engine                  string                 `json:"engine,omitempty"`
	Scope                   string                 `json:"scope,omitempty"`
	ProjectIDs              []string               `json:"projectIds,omitempty"`
	NormalizedQuery         string                 `json:"normalizedQuery,omitempty"`
	Sources                 []KnowledgeSource      `json:"sources,omitempty"`
	Degraded                bool                   `json:"degraded,omitempty"`
	FallbackReason          string                 `json:"fallbackReason,omitempty"`
	MatchedDomain           string                 `json:"matchedDomain,omitempty"`
	MatchedAlias            MatchedAlias           `json:"matchedAlias,omitempty"`
	Confidence              float64                `json:"confidence"`
	Terms                   []string               `json:"terms"`
	Required                []KnowledgeContextItem `json:"required"`
	Optional                []KnowledgeContextItem `json:"optional"`
	Related                 []KnowledgeContextItem `json:"related"`
	MissingFiles            []string               `json:"missingFiles"`
	Omitted                 []KnowledgeOmittedItem `json:"omitted"`
	RoutingDocuments        []string               `json:"routingDocuments"`
	LoadedKnowledgeMarkdown string                 `json:"loadedKnowledgeMarkdown"`
	QueryRewrite            QueryRewriteResult     `json:"queryRewrite,omitempty"`
	TokenBudget             TokenBudget            `json:"tokenBudget"`
	Reason                  string                 `json:"reason"`
}

type KnowledgeSource struct {
	ProjectID string  `json:"projectId"`
	SourceID  string  `json:"sourceId"`
	Path      string  `json:"path"`
	Title     string  `json:"title,omitempty"`
	Revision  string  `json:"revision,omitempty"`
	Score     float64 `json:"score"`
	Snippet   string  `json:"snippet,omitempty"`
}

type ContextPackDocument struct {
	KnowledgeSource
	Content string `json:"-"`
}

type QueryRewriteOptions struct {
	Enabled     *bool              `json:"enabled,omitempty"`
	Model       string             `json:"model,omitempty"`
	BaseURL     string             `json:"baseUrl,omitempty"`
	APIKey      string             `json:"-"`
	APIKeyEnv   string             `json:"apiKeyEnv,omitempty"`
	TimeoutMS   int                `json:"timeoutMs,omitempty"`
	MaxKeywords int                `json:"maxKeywords,omitempty"`
	Client      QueryRewriteClient `json:"-"`
}

type QueryRewriteClient interface {
	RewriteKnowledgeQuery(query string, options QueryRewriteOptions) (QueryRewriteResult, error)
}

type QueryRewriteResult struct {
	OriginalQuery string   `json:"originalQuery,omitempty"`
	EnglishQuery  string   `json:"englishQuery,omitempty"`
	Keywords      []string `json:"keywords,omitempty"`
	Model         string   `json:"model,omitempty"`
	Used          bool     `json:"used"`
	Triggered     bool     `json:"triggered,omitempty"`
	Error         string   `json:"error,omitempty"`
}

type KnowledgeContextItem struct {
	ProjectID string   `json:"projectId,omitempty"`
	SourceID  string   `json:"sourceId,omitempty"`
	Revision  string   `json:"revision,omitempty"`
	Path      string   `json:"path"`
	Title     string   `json:"title"`
	Type      string   `json:"type,omitempty"`
	Domain    string   `json:"domain,omitempty"`
	Heading   string   `json:"heading,omitempty"`
	StartLine int      `json:"startLine,omitempty"`
	EndLine   int      `json:"endLine,omitempty"`
	Score     float64  `json:"score"`
	Tokens    int      `json:"tokens"`
	Required  bool     `json:"required"`
	Reasons   []string `json:"reasons"`
	Snippet   string   `json:"snippet,omitempty"`
}

type TokenBudget struct {
	MaxTokens  int `json:"maxTokens"`
	UsedTokens int `json:"usedTokens"`
}

type KnowledgeOmittedItem struct {
	Path   string `json:"path"`
	Reason string `json:"reason"`
}

type RenderTree struct {
	Root  string       `json:"root"`
	Nodes []RenderNode `json:"nodes"`
}

type RenderNode struct {
	Path          string       `json:"path"`
	Title         string       `json:"title"`
	Type          string       `json:"type,omitempty"`
	Kind          string       `json:"kind,omitempty"`
	IndexDocument string       `json:"indexDocument,omitempty"`
	Children      []RenderNode `json:"children,omitempty"`
}

type RenderedDocument struct {
	Path        string      `json:"path"`
	Title       string      `json:"title"`
	Frontmatter Frontmatter `json:"frontmatter"`
	HTML        string      `json:"html"`
	Raw         string      `json:"raw"`
}

type ExportManifest struct {
	ProjectID     string  `json:"projectId"`
	ProjectRoot   string  `json:"projectRoot"`
	SourceRoot    string  `json:"sourceRoot"`
	ExportRoot    string  `json:"exportRoot"`
	DocumentCount int     `json:"documentCount"`
	SourceHash    string  `json:"sourceHash"`
	ExportedAt    string  `json:"exportedAt"`
	Summary       Summary `json:"summary"`
}

type ExportData struct {
	Manifest    ExportManifest    `json:"manifest"`
	Tree        RenderTree        `json:"tree"`
	Validation  ValidationReport  `json:"validation"`
	Maintenance MaintenanceReport `json:"maintenance"`
}

func nowStamp() string {
	return time.Now().Format(time.RFC3339)
}

func normalizeBundle(bundle Bundle) Bundle {
	if bundle.Documents == nil {
		bundle.Documents = []Document{}
	}
	for i := range bundle.Documents {
		bundle.Documents[i] = normalizeDocument(bundle.Documents[i])
	}
	return bundle
}

func normalizeDocument(doc Document) Document {
	if doc.Links == nil {
		doc.Links = []DocLink{}
	}
	if doc.Frontmatter.Tags == nil {
		doc.Frontmatter.Tags = []string{}
	}
	if doc.Frontmatter.DependsOn == nil {
		doc.Frontmatter.DependsOn = []string{}
	}
	if doc.Frontmatter.SeeAlso == nil {
		doc.Frontmatter.SeeAlso = []string{}
	}
	if doc.Frontmatter.SourcePaths == nil {
		doc.Frontmatter.SourcePaths = []string{}
	}
	if doc.Frontmatter.Routing.Aliases.Values == nil {
		doc.Frontmatter.Routing.Aliases.Values = []string{}
	}
	if doc.Frontmatter.Routing.Aliases.ZH == nil {
		doc.Frontmatter.Routing.Aliases.ZH = []string{}
	}
	if doc.Frontmatter.Routing.Aliases.EN == nil {
		doc.Frontmatter.Routing.Aliases.EN = []string{}
	}
	if doc.Frontmatter.Routing.Aliases.Pairs == nil {
		doc.Frontmatter.Routing.Aliases.Pairs = []RoutingAliasPair{}
	}
	if doc.Frontmatter.Routing.Keywords.Values == nil {
		doc.Frontmatter.Routing.Keywords.Values = []string{}
	}
	if doc.Frontmatter.Routing.Keywords.ZH == nil {
		doc.Frontmatter.Routing.Keywords.ZH = []string{}
	}
	if doc.Frontmatter.Routing.Keywords.EN == nil {
		doc.Frontmatter.Routing.Keywords.EN = []string{}
	}
	return doc
}

func normalizeValidationReport(report ValidationReport) ValidationReport {
	if report.Issues == nil {
		report.Issues = []ValidationIssue{}
	}
	return report
}

func normalizeMaintenanceReport(report MaintenanceReport) MaintenanceReport {
	report = MaintenanceReport{
		Root:             report.Root,
		Summary:          report.Summary,
		Issues:           report.Issues,
		StaleDocuments:   report.StaleDocuments,
		LargeDocuments:   report.LargeDocuments,
		DuplicateRules:   report.DuplicateRules,
		SuggestedActions: report.SuggestedActions,
		CheckedAt:        report.CheckedAt,
	}
	if report.Issues == nil {
		report.Issues = []ValidationIssue{}
	}
	if report.StaleDocuments == nil {
		report.StaleDocuments = []Document{}
	}
	if report.LargeDocuments == nil {
		report.LargeDocuments = []Document{}
	}
	if report.DuplicateRules == nil {
		report.DuplicateRules = []DuplicateRule{}
	}
	if report.SuggestedActions == nil {
		report.SuggestedActions = []string{}
	}
	for i := range report.StaleDocuments {
		report.StaleDocuments[i] = normalizeDocument(report.StaleDocuments[i])
	}
	for i := range report.LargeDocuments {
		report.LargeDocuments[i] = normalizeDocument(report.LargeDocuments[i])
	}
	for i := range report.DuplicateRules {
		if report.DuplicateRules[i].Paths == nil {
			report.DuplicateRules[i].Paths = []string{}
		}
	}
	return report
}

func normalizeRoutePreview(preview RoutePreview) RoutePreview {
	if preview.RequiredFiles == nil {
		preview.RequiredFiles = []string{}
	}
	if preview.OptionalFiles == nil {
		preview.OptionalFiles = []string{}
	}
	if preview.MissingFiles == nil {
		preview.MissingFiles = []string{}
	}
	if preview.RoutingDocuments == nil {
		preview.RoutingDocuments = []string{}
	}
	if preview.Matches == nil {
		preview.Matches = []KnowledgeContextItem{}
	}
	if preview.Terms == nil {
		preview.Terms = []string{}
	}
	return preview
}

func normalizeRenderTree(tree RenderTree) RenderTree {
	if tree.Nodes == nil {
		tree.Nodes = []RenderNode{}
	}
	for i := range tree.Nodes {
		tree.Nodes[i] = normalizeRenderNode(tree.Nodes[i])
	}
	return tree
}

func normalizeRenderNode(node RenderNode) RenderNode {
	if node.Children == nil {
		node.Children = []RenderNode{}
	}
	for i := range node.Children {
		node.Children[i] = normalizeRenderNode(node.Children[i])
	}
	return node
}

func normalizeExportData(data ExportData) ExportData {
	data.Tree = normalizeRenderTree(data.Tree)
	data.Validation = normalizeValidationReport(data.Validation)
	data.Maintenance = normalizeMaintenanceReport(data.Maintenance)
	return data
}
