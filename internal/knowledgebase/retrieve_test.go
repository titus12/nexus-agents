package knowledgebase

import (
	"path/filepath"
	"strings"
	"testing"
)

func TestBuildKnowledgeSectionsSplitsMarkdownHeadings(t *testing.T) {
	root := t.TempDir()
	kb := filepath.Join(root, filepath.FromSlash(DefaultRoot))
	writeTestFile(t, filepath.Join(kb, "index.md"), okfDoc("Index", "Root", "design/KnowledgeBase/index.md", "# Root\n\nIntro\n\n## UI\n\nPopup rules"))
	bundle, err := ScanBundle(root)
	if err != nil {
		t.Fatal(err)
	}
	sections := BuildKnowledgeSections(bundle)
	if len(sections) != 2 {
		t.Fatalf("expected two sections, got %#v", sections)
	}
	if sections[1].Heading != "UI" || sections[1].StartLine == 0 || sections[1].Tokens == 0 {
		t.Fatalf("unexpected UI section: %#v", sections[1])
	}
}

func TestRetrieveUsesFTS5OKFRoutingAndTokenBudget(t *testing.T) {
	root := t.TempDir()
	kb := filepath.Join(root, filepath.FromSlash(DefaultRoot))
	writeTestFile(t, filepath.Join(kb, "index.md"), okfDoc("Index", "Root", "design/KnowledgeBase/index.md", "# Root\n\n[Project routing](./project/routing.md)"))
	writeTestFile(t, filepath.Join(kb, "log.md"), okfDoc("Log", "Log", "design/KnowledgeBase/log.md", "# Log\n\nInitial knowledge log entry."))
	writeTestFile(t, filepath.Join(kb, "project", "routing.md"), okfDoc("Routing", "Project Routing", "design/KnowledgeBase/project/routing.md", "# Routing\n\nUI or popup tasks read `design/KnowledgeBase/domains/ui/routing.md`. Network tasks read `design/KnowledgeBase/domains/network/README.md`."))
	writeTestFile(t, filepath.Join(kb, "domains", "ui", "routing.md"), "---\ntype: Routing\ntitle: UI Routing\ndescription: Test.\nresource: design/KnowledgeBase/domains/ui/routing.md\ntags: [test, ui]\nrouting:\n  aliases:\n    zh: [弹窗]\n    en: [UI, popup, ViewModel]\ntimestamp: 2026-07-07T00:00:00+08:00\n---\n\n# UI Routing\n\nRequired:\n- design/KnowledgeBase/domains/ui/coding_rules.md\n- design/KnowledgeBase/domains/ui/data_flow_rules.md\n- design/KnowledgeBase/domains/ui/missing.md")
	writeTestFile(t, filepath.Join(kb, "domains", "ui", "coding_rules.md"), okfDoc("CodingRules", "UI Coding Rules", "design/KnowledgeBase/domains/ui/coding_rules.md", "# Coding\n\n## ViewModel\n\n新增活动奖励弹窗 UI 必须通过 ViewModel 管理状态，Prefab 绑定只处理显示。"))
	writeTestFile(t, filepath.Join(kb, "domains", "ui", "data_flow_rules.md"), okfDoc("Guide", "UI Data Flow", "design/KnowledgeBase/domains/ui/data_flow_rules.md", "# Data Flow\n\n弹窗数据流从 model 到 ViewModel 再到 panel。"))
	writeTestFile(t, filepath.Join(kb, "domains", "network", "README.md"), okfDoc("Domain", "Network", "design/KnowledgeBase/domains/network/README.md", "# Network\n\nProtocol socket rules."))

	result, err := Retrieve(root, "新增活动奖励弹窗 UI ViewModel", RetrieveOptions{Mode: RetrieveModeRouting, Limit: 8, MaxTokens: 1200})
	if err != nil {
		t.Fatal(err)
	}
	if result.MatchedDomain != "ui" {
		t.Fatalf("expected ui domain, got %#v", result)
	}
	if !hasContextPath(result.Required, "design/KnowledgeBase/domains/ui/routing.md") || !hasContextPath(result.Required, "design/KnowledgeBase/domains/ui/coding_rules.md") {
		t.Fatalf("expected ui routing and coding rules in required: %#v", result.Required)
	}
	if hasContextPath(result.Required, "design/KnowledgeBase/domains/network/README.md") {
		t.Fatalf("network should not be required for UI query: %#v", result.Required)
	}
	if !containsString(result.MissingFiles, "design/KnowledgeBase/domains/ui/missing.md") {
		t.Fatalf("expected missing routing target: %#v", result.MissingFiles)
	}
	if result.TokenBudget.UsedTokens > result.TokenBudget.MaxTokens {
		t.Fatalf("token budget exceeded: %#v", result.TokenBudget)
	}
	if !strings.Contains(result.LoadedKnowledgeMarkdown, "Loaded Knowledge") {
		t.Fatalf("missing loaded knowledge markdown: %q", result.LoadedKnowledgeMarkdown)
	}
}

func TestRetrieveChineseBehaviourTreePrefersBehaviourTreeDomain(t *testing.T) {
	root := t.TempDir()
	kb := filepath.Join(root, filepath.FromSlash(DefaultRoot))
	writeTestFile(t, filepath.Join(kb, "index.md"), okfDoc("Index", "Root", "design/KnowledgeBase/index.md", "# Root\n\n[Project routing](./project/routing.md)"))
	writeTestFile(t, filepath.Join(kb, "log.md"), okfDoc("Log", "Log", "design/KnowledgeBase/log.md", "# Log\n\nInitial knowledge log entry."))
	writeTestFile(t, filepath.Join(kb, "project", "routing.md"), okfDoc("Routing", "Project Routing", "design/KnowledgeBase/project/routing.md", "# Routing\n\nUI tasks read `design/KnowledgeBase/domains/ui/routing.md`. Behaviour tree tasks read `design/KnowledgeBase/domains/behaviour_tree/routing.md`."))
	writeTestFile(t, filepath.Join(kb, "domains", "ui", "routing.md"), okfDoc("Routing", "UI Routing", "design/KnowledgeBase/domains/ui/routing.md", "# UI Routing\n\nPopup and ViewModel tasks read UI files."))
	writeTestFile(t, filepath.Join(kb, "domains", "ui", "coding_rules.md"), okfDoc("CodingRules", "UI Coding Rules", "design/KnowledgeBase/domains/ui/coding_rules.md", "# Coding\n\nUI popup viewmodel panel rules."))
	writeTestFile(t, filepath.Join(kb, "domains", "behaviour_tree", "routing.md"), "---\ntype: Routing\ntitle: Behaviour Tree Routing\ndescription: Test.\nresource: design/KnowledgeBase/domains/behaviour_tree/routing.md\ntags: [test, behaviour-tree]\nrouting:\n  aliases:\n    zh: [行为树]\n    en: [behaviour tree, BonsaiBT]\n    pairs:\n      - zh: 行为树\n        en: behaviour tree\ntimestamp: 2026-07-07T00:00:00+08:00\n---\n\n# Behaviour Tree Routing\n\n行为树 and BonsaiBT tasks read `design/KnowledgeBase/domains/behaviour_tree/README.md`.")
	writeTestFile(t, filepath.Join(kb, "domains", "behaviour_tree", "README.md"), okfDoc("Domain", "Behaviour Tree", "design/KnowledgeBase/domains/behaviour_tree/README.md", "# Behaviour Tree\n\n行为树 BonsaiBT battle AI rules."))

	result, err := Retrieve(root, "行为树", RetrieveOptions{Mode: RetrieveModeRouting, Limit: 8, MaxTokens: 1200})
	if err != nil {
		t.Fatal(err)
	}
	if result.MatchedDomain != "behaviour_tree" {
		t.Fatalf("expected behaviour_tree domain, got %#v", result)
	}
	if !hasContextPath(result.Required, "design/KnowledgeBase/domains/behaviour_tree/routing.md") {
		t.Fatalf("expected behaviour tree routing in required: %#v", result.Required)
	}
	if hasContextPath(result.Required, "design/KnowledgeBase/project/routing.md") || hasContextPath(result.Required, "design/KnowledgeBase/domains/ui/routing.md") || hasContextPath(result.Required, "design/KnowledgeBase/domains/ui/coding_rules.md") {
		t.Fatalf("unrelated project/ui files should not be required for behaviour tree: %#v", result.Required)
	}
}

func TestRetrieveUsesOKFRoutingAliasesBeforeFallbackSynonyms(t *testing.T) {
	root := t.TempDir()
	kb := filepath.Join(root, filepath.FromSlash(DefaultRoot))
	writeTestFile(t, filepath.Join(kb, "index.md"), okfDoc("Index", "Root", "design/KnowledgeBase/index.md", "# Root\n\n[Project routing](./project/routing.md)"))
	writeTestFile(t, filepath.Join(kb, "log.md"), okfDoc("Log", "Log", "design/KnowledgeBase/log.md", "# Log\n\nInitial knowledge log entry."))
	writeTestFile(t, filepath.Join(kb, "project", "routing.md"), okfDoc("Routing", "Project Routing", "design/KnowledgeBase/project/routing.md", "# Routing\n\nUse domain routing."))
	writeTestFile(t, filepath.Join(kb, "domains", "behaviour_tree", "routing.md"), "---\ntype: Routing\ntitle: Behaviour Tree Routing\ndescription: Routes behaviour tree work.\nresource: design/KnowledgeBase/domains/behaviour_tree/routing.md\ntags: [routing, behaviour-tree]\nrouting:\n  aliases:\n    zh: [怪物卡住]\n    en: [monster stuck]\n    pairs:\n      - zh: 怪物卡住\n        en: monster stuck\n  keywords:\n    zh: [移动]\n    en: [movement]\ntimestamp: 2026-07-07T00:00:00+08:00\n---\n\n# Behaviour Tree Routing\n\n怪物卡住 and movement tasks read `design/KnowledgeBase/domains/behaviour_tree/README.md`.")
	writeTestFile(t, filepath.Join(kb, "domains", "behaviour_tree", "README.md"), okfDoc("Domain", "Behaviour Tree", "design/KnowledgeBase/domains/behaviour_tree/README.md", "# Behaviour Tree\n\nMonster movement and AI rules."))
	writeTestFile(t, filepath.Join(kb, "domains", "ui", "routing.md"), okfDoc("Routing", "UI Routing", "design/KnowledgeBase/domains/ui/routing.md", "# UI Routing\n\nPopup panel UI rules."))

	result, err := Retrieve(root, "怪物卡住", RetrieveOptions{Mode: RetrieveModeRouting, Limit: 8, MaxTokens: 1200})
	if err != nil {
		t.Fatal(err)
	}
	if result.MatchedDomain != "behaviour_tree" {
		t.Fatalf("expected alias-defined behaviour_tree domain, got %#v", result)
	}
	if result.MatchedAlias.Alias != "怪物卡住" || result.MatchedAlias.PairedAlias != "monster stuck" {
		t.Fatalf("expected matched alias metadata, got %#v", result.MatchedAlias)
	}
	if hasContextPath(result.Required, "design/KnowledgeBase/domains/ui/routing.md") {
		t.Fatalf("dynamic alias should scope search away from UI: %#v", result.Required)
	}
}

func TestRetrieveUnknownQueryKeepsContextSmall(t *testing.T) {
	root := t.TempDir()
	kb := filepath.Join(root, filepath.FromSlash(DefaultRoot))
	writeTestFile(t, filepath.Join(kb, "index.md"), okfDoc("Index", "Root", "design/KnowledgeBase/index.md", "# Root\n\n[Project routing](./project/routing.md)"))
	writeTestFile(t, filepath.Join(kb, "project", "routing.md"), okfDoc("Routing", "Project Routing", "design/KnowledgeBase/project/routing.md", "# Routing\n\nRead domain routing before implementation."))
	result, err := Retrieve(root, "优化一下", RetrieveOptions{Mode: RetrieveModeRouting, MaxTokens: 500})
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Required) > 1 {
		t.Fatalf("unknown query should keep context small, got %#v", result.Required)
	}
	if result.TokenBudget.UsedTokens > 500 {
		t.Fatalf("token budget exceeded: %#v", result.TokenBudget)
	}
}

func hasContextPath(items []KnowledgeContextItem, path string) bool {
	for _, item := range items {
		if item.Path == path {
			return true
		}
	}
	return false
}
