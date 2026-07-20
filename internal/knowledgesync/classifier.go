package knowledgesync

import (
	"path"
	"strings"
)

type ChangeClass string

const (
	ChangeKnowledgeOnly ChangeClass = "knowledge_only"
	ChangeDocsContract  ChangeClass = "docs_contract"
	ChangeStableCode    ChangeClass = "stable_code"
	ChangeTestsOnly     ChangeClass = "tests_only"
	ChangeGenerated     ChangeClass = "generated_dependency"
	ChangeHighRisk      ChangeClass = "unknown_high_risk"
)

type ClassifiedChanges struct {
	Class   ChangeClass `json:"class"`
	Changes []GitChange `json:"changes"`
	Reasons []string    `json:"reasons"`
}

func ClassifyChanges(changes []GitChange) ClassifiedChanges {
	result := ClassifiedChanges{Class: ChangeGenerated, Changes: changes, Reasons: []string{}}
	hasKB, hasDocs, hasCode, hasTests, hasUnknown := false, false, false, false, false
	for _, change := range changes {
		file := strings.ToLower(normalizeRelativePath(change.Path))
		switch {
		case strings.HasPrefix(file, "knowledgebase/"):
			hasKB = true
		case IsHardExcluded(file) || isGeneratedOrDependency(file):
			result.Reasons = append(result.Reasons, change.Path+" is generated, dependency, or safety-excluded")
		case isContractOrDoc(file):
			hasDocs = true
			result.Reasons = append(result.Reasons, change.Path+" changes repository documentation or a contract")
		case isTestPath(file):
			hasTests = true
		case isStableCodeBoundary(file):
			hasCode = true
			result.Reasons = append(result.Reasons, change.Path+" may change a stable code boundary")
		default:
			hasUnknown = true
			result.Reasons = append(result.Reasons, change.Path+" requires conservative review")
		}
	}
	switch {
	case hasCode:
		result.Class = ChangeStableCode
	case hasDocs:
		result.Class = ChangeDocsContract
	case hasUnknown:
		result.Class = ChangeHighRisk
	case hasTests && !hasKB:
		result.Class = ChangeTestsOnly
	case hasKB:
		result.Class = ChangeKnowledgeOnly
	default:
		result.Class = ChangeGenerated
	}
	result.Reasons = uniqueSorted(result.Reasons)
	return result
}

func isContractOrDoc(file string) bool {
	ext := path.Ext(file)
	if ext == ".md" || ext == ".mdx" || ext == ".rst" || ext == ".adoc" {
		return true
	}
	for _, marker := range []string{"openapi", "swagger", "asyncapi", "schema", "proto", "adr", "contract"} {
		if strings.Contains(file, marker) {
			return true
		}
	}
	return ext == ".graphql" || ext == ".gql"
}

func isTestPath(file string) bool {
	base := path.Base(file)
	return strings.Contains(file, "/test/") || strings.Contains(file, "/tests/") ||
		strings.HasSuffix(base, "_test.go") || strings.Contains(base, ".test.") ||
		strings.Contains(base, ".spec.") || strings.HasSuffix(base, "tests.cs")
}

func isGeneratedOrDependency(file string) bool {
	return strings.Contains(file, ".generated.") || strings.HasSuffix(file, ".lock") ||
		path.Base(file) == "go.sum" || path.Base(file) == "package-lock.json" ||
		strings.Contains(file, "/generated/")
}

func isStableCodeBoundary(file string) bool {
	ext := path.Ext(file)
	switch ext {
	case ".go", ".ts", ".tsx", ".js", ".jsx", ".cs", ".java", ".kt", ".py", ".rs", ".cpp", ".c", ".h":
	default:
		return false
	}
	for _, marker := range []string{"route", "router", "controller", "handler", "api", "model", "entity", "schema", "event", "repository", "service", "config"} {
		if strings.Contains(file, marker) {
			return true
		}
	}
	return true
}

type CodeGraphImpact struct {
	Routes  []string `json:"routes"`
	Callers []string `json:"callers"`
	Callees []string `json:"callees"`
	Tests   []string `json:"tests"`
}

type CodeGraphClient interface {
	Impact(projectRoot string, changedPaths []string, depth int) (CodeGraphImpact, error)
	Summary(projectRoot string) (string, error)
}
