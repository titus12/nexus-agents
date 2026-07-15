package knowledgebase

import "testing"

func TestParseFrontmatterWithTags(t *testing.T) {
	input := "---\ntype: Domain\ntitle: Go Actor System\ndescription: Actor rules.\nresource: KnowledgeBase/domains/actor\ntags: [go, actor, server]\ndepends_on: [KnowledgeBase/project/routing.md]\nsee_also: [./routing.md]\nstatus: stable\nowner: gameplay-team\ntimestamp: 2026-07-07T00:00:00+08:00\n---\n\n# Body\nText"
	fm, body, ok := ParseFrontmatter(input)
	if !ok {
		t.Fatal("expected frontmatter")
	}
	if fm.Type != "Domain" || fm.Title != "Go Actor System" || fm.Resource != "KnowledgeBase/domains/actor" {
		t.Fatalf("unexpected frontmatter: %#v", fm)
	}
	if len(fm.Tags) != 3 || fm.Tags[0] != "go" || fm.Tags[2] != "server" {
		t.Fatalf("unexpected tags: %#v", fm.Tags)
	}
	if len(fm.DependsOn) != 1 || len(fm.SeeAlso) != 1 || fm.Status != "stable" || fm.Owner != "gameplay-team" {
		t.Fatalf("unexpected relationship metadata: %#v", fm)
	}
	if body != "# Body\nText" {
		t.Fatalf("unexpected body %q", body)
	}
}

func TestParseFrontmatterMissingBlock(t *testing.T) {
	fm, body, ok := ParseFrontmatter("# Title\nNo metadata")
	if ok {
		t.Fatal("expected no frontmatter")
	}
	if fm.Type != "" {
		t.Fatalf("expected empty frontmatter, got %#v", fm)
	}
	if body != "# Title\nNo metadata" {
		t.Fatalf("body should be unchanged, got %q", body)
	}
}

func TestParseFrontmatterRoutingAliasesAndKeywords(t *testing.T) {
	input := "---\ntype: Routing\ntitle: Behaviour Tree Routing\ndescription: Routes behaviour tree work.\nresource: KnowledgeBase/domains/behaviour_tree/routing.md\ntags: [btd-client, routing]\nrouting:\n  aliases:\n    zh: [行为树, 战斗 AI]\n    en: [behaviour tree, BonsaiBT]\n    pairs:\n      - zh: 行为树\n        en: behaviour tree\n      - zh: BonsaiBT\n        en: BonsaiBT\n  keywords:\n    zh: [黑板, 选择器]\n    en: [blackboard, selector]\ntimestamp: 2026-07-07T00:00:00+08:00\n---\n\n# Body"
	fm, _, ok := ParseFrontmatter(input)
	if !ok {
		t.Fatal("expected frontmatter")
	}
	if len(fm.Routing.Aliases.ZH) != 2 || fm.Routing.Aliases.ZH[0] != "行为树" {
		t.Fatalf("unexpected zh aliases: %#v", fm.Routing.Aliases.ZH)
	}
	if len(fm.Routing.Aliases.EN) != 2 || fm.Routing.Aliases.EN[1] != "BonsaiBT" {
		t.Fatalf("unexpected en aliases: %#v", fm.Routing.Aliases.EN)
	}
	if len(fm.Routing.Aliases.Pairs) != 2 || fm.Routing.Aliases.Pairs[0].ZH != "行为树" || fm.Routing.Aliases.Pairs[0].EN != "behaviour tree" {
		t.Fatalf("unexpected alias pairs: %#v", fm.Routing.Aliases.Pairs)
	}
	if len(fm.Routing.Keywords.ZH) != 2 || fm.Routing.Keywords.EN[0] != "blackboard" {
		t.Fatalf("unexpected keywords: %#v", fm.Routing.Keywords)
	}
}
