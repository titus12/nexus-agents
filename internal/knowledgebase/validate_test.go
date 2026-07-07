package knowledgebase

import (
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"
)

func TestValidateBundleReportsMissingMetadataAndBrokenLinks(t *testing.T) {
	root := t.TempDir()
	kb := filepath.Join(root, filepath.FromSlash(DefaultRoot))
	writeTestFile(t, filepath.Join(kb, "index.md"), "---\ntype: Index\ntitle: Root\ndescription: Root.\nresource: design/KnowledgeBase/index.md\ntags: [index]\ntimestamp: 2026-07-07T00:00:00+08:00\n---\n\n[Missing](./missing.md)")
	writeTestFile(t, filepath.Join(kb, "domains", "actor", "routing.md"), "# Routing\nNo metadata")

	report, err := Validate(root)
	if err != nil {
		t.Fatal(err)
	}
	if report.Summary.Documents != 2 {
		t.Fatalf("expected 2 docs, got %#v", report.Summary)
	}
	if !hasIssue(report.Issues, "missing_frontmatter") {
		t.Fatalf("expected missing_frontmatter issue: %#v", report.Issues)
	}
	if !hasIssue(report.Issues, "broken_link") {
		t.Fatalf("expected broken_link issue: %#v", report.Issues)
	}
}

func TestValidateReportsOKFQualityGateIssues(t *testing.T) {
	root := t.TempDir()
	kb := filepath.Join(root, filepath.FromSlash(DefaultRoot))
	writeTestFile(t, filepath.Join(kb, "index.md"), okfDoc("Index", "Root", "design/KnowledgeBase/index.md", "# Root\n\n[Project routing](./project/routing.md)\n[Guide](./domains/ui/guide.md)"))
	writeTestFile(t, filepath.Join(kb, "log.md"), okfDoc("Log", "Log", "design/KnowledgeBase/log.md", "# Log\n\nInitial knowledge log entry."))
	writeTestFile(t, filepath.Join(kb, "project", "routing.md"), okfDoc("Routing", "Project Routing", "design/KnowledgeBase/project/routing.md", "# Routing\n\nUI tasks load `design/KnowledgeBase/domains/ui/routing.md`."))
	writeTestFile(t, filepath.Join(kb, "domains", "ui", "README.md"), okfDoc("Domain", "UI", "design/KnowledgeBase/domains/ui/README.md", "# UI\n\nUI domain overview links to [routing](./routing.md)."))
	writeTestFile(t, filepath.Join(kb, "domains", "ui", "routing.md"), okfDoc("Routing", "UI Routing", "design/KnowledgeBase/domains/ui/routing.md", "# UI Routing\n\nLoad `design/KnowledgeBase/domains/ui/guide.md`."))
	writeTestFile(t, filepath.Join(kb, "domains", "ui", "guide.md"), "---\ntype: Strange\ntitle: UI\nresource: design/KnowledgeBase/domains/ui/not-guide.md\ntags: []\ntimestamp: not-a-time\ndepends_on: [design/KnowledgeBase/domains/ui/missing.md]\n---\n\nTODO")
	writeTestFile(t, filepath.Join(kb, "domains", "ui", "orphan.md"), okfDoc("Guide", "Orphan", "design/KnowledgeBase/domains/ui/orphan.md", "# Orphan\n\nThis document is not linked by routing."))

	report, err := Validate(root)
	if err != nil {
		t.Fatal(err)
	}
	for _, code := range []string{"unknown_type", "resource_mismatch", "invalid_timestamp", "thin_document", "placeholder_content", "broken_frontmatter_ref", "duplicate_title", "orphan_document"} {
		if !hasIssue(report.Issues, code) {
			t.Fatalf("expected %s issue, got %#v", code, report.Issues)
		}
	}
}

func TestValidateDomainRoutingAliases(t *testing.T) {
	root := t.TempDir()
	kb := filepath.Join(root, filepath.FromSlash(DefaultRoot))
	writeTestFile(t, filepath.Join(kb, "index.md"), okfDoc("Index", "Root", "design/KnowledgeBase/index.md", "# Root\n\n[Project routing](./project/routing.md)"))
	writeTestFile(t, filepath.Join(kb, "log.md"), okfDoc("Log", "Log", "design/KnowledgeBase/log.md", "# Log\n\nInitial knowledge log entry."))
	writeTestFile(t, filepath.Join(kb, "project", "routing.md"), okfDoc("Routing", "Project Routing", "design/KnowledgeBase/project/routing.md", "# Routing\n\nUse domain routing."))
	writeTestFile(t, filepath.Join(kb, "domains", "behaviour_tree", "routing.md"), "---\ntype: Routing\ntitle: Behaviour Tree Routing\ndescription: Routes behaviour tree work.\nresource: design/KnowledgeBase/domains/behaviour_tree/routing.md\ntags: [routing, behaviour-tree]\nrouting:\n  aliases:\n    zh: [行为树]\n    en: [behaviour tree, BonsaiBT]\n  keywords:\n    zh: [黑板]\n    en: [blackboard]\ntimestamp: 2026-07-07T00:00:00+08:00\n---\n\n# Routing\n\nLoad `design/KnowledgeBase/domains/behaviour_tree/README.md`.")
	writeTestFile(t, filepath.Join(kb, "domains", "behaviour_tree", "README.md"), okfDoc("Domain", "Behaviour Tree", "design/KnowledgeBase/domains/behaviour_tree/README.md", "# Behaviour Tree"))
	writeTestFile(t, filepath.Join(kb, "domains", "network", "routing.md"), "---\ntype: Routing\ntitle: Network Routing\ndescription: Routes network work.\nresource: design/KnowledgeBase/domains/network/routing.md\ntags: [routing, network]\nrouting:\n  aliases:\n    zh: [网络]\n  keywords:\n    en: [protocol]\ntimestamp: 2026-07-07T00:00:00+08:00\n---\n\n# Routing")
	writeTestFile(t, filepath.Join(kb, "domains", "gameplay", "routing.md"), "---\ntype: Routing\ntitle: Gameplay Routing\ndescription: Routes gameplay work.\nresource: design/KnowledgeBase/domains/gameplay/routing.md\ntags: [routing, gameplay]\nrouting:\n  aliases:\n    zh: [系统]\n    en: [behaviour tree]\ntimestamp: 2026-07-07T00:00:00+08:00\n---\n\n# Routing")

	report, err := Validate(root)
	if err != nil {
		t.Fatal(err)
	}
	if hasIssueForPath(report.Issues, "missing_domain_aliases", "design/KnowledgeBase/domains/behaviour_tree/routing.md") {
		t.Fatalf("behaviour_tree has aliases and should not report missing_domain_aliases: %#v", report.Issues)
	}
	for _, code := range []string{"missing_bilingual_aliases", "duplicate_domain_alias", "broad_domain_alias"} {
		if !hasIssue(report.Issues, code) {
			t.Fatalf("expected %s issue, got %#v", code, report.Issues)
		}
	}
}

func TestValidateReportsMojibakeContent(t *testing.T) {
	root := t.TempDir()
	kb := filepath.Join(root, filepath.FromSlash(DefaultRoot))
	writeTestFile(t, filepath.Join(kb, "index.md"), "---\ntype: Index\ntitle: Root\ndescription: Root.\nresource: design/KnowledgeBase/index.md\ntags: [index]\ntimestamp: 2026-07-07T00:00:00+08:00\n---\n\n# Root\n\nThis text contains garbled Chinese like 鐞涘奔璐熼弽 and 閹存ɑ鏋 AI.")
	writeTestFile(t, filepath.Join(kb, "log.md"), okfDoc("Log", "Log", "design/KnowledgeBase/log.md", "# Log\n\nInitial knowledge log entry."))

	report, err := Validate(root)
	if err != nil {
		t.Fatal(err)
	}
	if !hasIssue(report.Issues, "mojibake_content") {
		t.Fatalf("expected mojibake_content issue, got %#v", report.Issues)
	}
}

func TestValidateAllowsMojibakeExamplesInsideCode(t *testing.T) {
	root := t.TempDir()
	kb := filepath.Join(root, filepath.FromSlash(DefaultRoot))
	writeTestFile(t, filepath.Join(kb, "index.md"), "---\ntype: Index\ntitle: Root\ndescription: Root.\nresource: design/KnowledgeBase/index.md\ntags: [index]\ntimestamp: 2026-07-07T00:00:00+08:00\n---\n\n# Root\n\nUse examples like `鈥?` only inside code spans when documenting encoding checks.\n\n```text\n鐞涘奔璐熼弽\n閹存ɑ鏋\n```")
	writeTestFile(t, filepath.Join(kb, "log.md"), okfDoc("Log", "Log", "design/KnowledgeBase/log.md", "# Log\n\nInitial knowledge log entry."))

	report, err := Validate(root)
	if err != nil {
		t.Fatal(err)
	}
	if hasIssue(report.Issues, "mojibake_content") {
		t.Fatalf("code examples should not trigger mojibake_content: %#v", report.Issues)
	}
}

func TestMaintenanceDetectsDuplicateChineseHardRules(t *testing.T) {
	root := t.TempDir()
	kb := filepath.Join(root, filepath.FromSlash(DefaultRoot))
	rule := "- 必须通过 ViewModel 传递界面状态，禁止在 View 中直接访问网络和业务服务。"
	writeTestFile(t, filepath.Join(kb, "index.md"), okfDoc("Index", "Root", "design/KnowledgeBase/index.md", "# Root\n\n[Rules](./domains/ui/rules.md)\n[More](./domains/ui/more-rules.md)"))
	writeTestFile(t, filepath.Join(kb, "log.md"), okfDoc("Log", "Log", "design/KnowledgeBase/log.md", "# Log\n\nInitial knowledge log entry."))
	writeTestFile(t, filepath.Join(kb, "project", "routing.md"), okfDoc("Routing", "Project Routing", "design/KnowledgeBase/project/routing.md", "# Routing\n\nLoad `design/KnowledgeBase/domains/ui/routing.md`."))
	writeTestFile(t, filepath.Join(kb, "domains", "ui", "rules.md"), okfDoc("CodingRules", "Rules", "design/KnowledgeBase/domains/ui/rules.md", rule))
	writeTestFile(t, filepath.Join(kb, "domains", "ui", "more-rules.md"), okfDoc("CodingRules", "More Rules", "design/KnowledgeBase/domains/ui/more-rules.md", rule))

	report, err := Maintenance(root)
	if err != nil {
		t.Fatal(err)
	}
	if len(report.DuplicateRules) != 1 {
		t.Fatalf("expected duplicate Chinese hard rule, got %#v", report.DuplicateRules)
	}
}

func TestMaintenanceReportFlagsLargeDocs(t *testing.T) {
	root := t.TempDir()
	kb := filepath.Join(root, filepath.FromSlash(DefaultRoot))
	largeBody := make([]byte, 130000)
	for i := range largeBody {
		largeBody[i] = 'x'
	}
	writeTestFile(t, filepath.Join(kb, "index.md"), "---\ntype: Index\ntitle: Root\ndescription: Root.\nresource: design/KnowledgeBase/index.md\ntags: [index]\ntimestamp: 2026-07-07T00:00:00+08:00\n---\n\n"+string(largeBody))
	writeTestFile(t, filepath.Join(kb, "log.md"), "---\ntype: Log\ntitle: Log\ndescription: Changes.\nresource: design/KnowledgeBase/log.md\ntags: [log]\ntimestamp: 2026-07-07T00:00:00+08:00\n---\n")

	report, err := Maintenance(root)
	if err != nil {
		t.Fatal(err)
	}
	if len(report.LargeDocuments) != 1 {
		t.Fatalf("expected one large document, got %#v", report.LargeDocuments)
	}
}

func TestKnowledgeResponsesMarshalEmptySlicesAsArrays(t *testing.T) {
	root := t.TempDir()

	bundle, err := ScanBundle(root)
	if err != nil {
		t.Fatal(err)
	}
	assertJSONDoesNotContainNull(t, bundle, "bundle")

	validation, err := Validate(root)
	if err != nil {
		t.Fatal(err)
	}
	assertJSONDoesNotContainNull(t, validation, "validation")

	maintenance, err := Maintenance(root)
	if err != nil {
		t.Fatal(err)
	}
	assertJSONDoesNotContainNull(t, maintenance, "maintenance")

	tree, err := BuildRenderTree(root)
	if err != nil {
		t.Fatal(err)
	}
	assertJSONDoesNotContainNull(t, tree, "render tree")

	route, err := PreviewRoute(root, "unknown")
	if err != nil {
		t.Fatal(err)
	}
	assertJSONDoesNotContainNull(t, route, "route preview")
}

func assertJSONDoesNotContainNull(t *testing.T, value any, label string) {
	t.Helper()
	data, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(data), ":null") {
		t.Fatalf("%s should marshal empty slices as arrays, got %s", label, data)
	}
}

func hasIssue(issues []ValidationIssue, code string) bool {
	for _, issue := range issues {
		if issue.Code == code {
			return true
		}
	}
	return false
}

func hasIssueForPath(issues []ValidationIssue, code string, path string) bool {
	for _, issue := range issues {
		if issue.Code == code && issue.Path == path {
			return true
		}
	}
	return false
}
