package knowledgebase

import (
	"database/sql"
	"strings"
	"testing"

	_ "modernc.org/sqlite"
)

func TestSQLiteFTS5SupportsBM25AndSnippet(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	defer db.Close()

	if _, err := db.Exec(`CREATE VIRTUAL TABLE kb_sections USING fts5(section_id UNINDEXED, path, title, heading, tags, body)`); err != nil {
		t.Fatalf("create fts5 table: %v", err)
	}

	rows := []struct {
		id      string
		path    string
		title   string
		heading string
		tags    string
		body    string
	}{
		{
			id:      "ui-routing",
			path:    "KnowledgeBase/domains/ui/routing.md",
			title:   "UI Routing",
			heading: "Popup and ViewModel tasks",
			tags:    "ui popup viewmodel 界面 弹窗",
			body:    "新增活动奖励弹窗 UI 时，先读取 ViewModel、Prefab、数据流和弹窗编码规则。",
		},
		{
			id:      "network",
			path:    "KnowledgeBase/domains/network/README.md",
			title:   "Network",
			heading: "Protocol",
			tags:    "network protocol socket 网络 协议",
			body:    "网络协议和 socket 连接规则。",
		},
	}
	for _, row := range rows {
		if _, err := db.Exec(`INSERT INTO kb_sections(section_id, path, title, heading, tags, body) VALUES (?, ?, ?, ?, ?, ?)`, row.id, row.path, row.title, row.heading, row.tags, row.body); err != nil {
			t.Fatalf("insert %s: %v", row.id, err)
		}
	}

	var sectionID string
	var rank float64
	var snippet string
	err = db.QueryRow(`
		SELECT section_id,
		       bm25(kb_sections) AS rank,
		       snippet(kb_sections, 5, '<mark>', '</mark>', '...', 24) AS snippet
		FROM kb_sections
		WHERE kb_sections MATCH ?
		ORDER BY rank
		LIMIT 1
	`, "ui OR viewmodel OR 弹窗").Scan(&sectionID, &rank, &snippet)
	if err != nil {
		t.Fatalf("query fts5: %v", err)
	}
	if sectionID != "ui-routing" {
		t.Fatalf("expected ui-routing as top hit, got %s rank=%f snippet=%q", sectionID, rank, snippet)
	}
	if !strings.Contains(snippet, "<mark>") {
		t.Fatalf("expected highlighted snippet, got %q", snippet)
	}

	err = db.QueryRow(`
		SELECT section_id,
		       snippet(kb_sections, 5, '<mark>', '</mark>', '...', 12) AS snippet
		FROM kb_sections
		WHERE kb_sections MATCH ?
		ORDER BY bm25(kb_sections)
		LIMIT 1
	`, "弹窗").Scan(&sectionID, &snippet)
	if err != nil {
		t.Fatalf("query chinese fts5 term: %v", err)
	}
	if sectionID != "ui-routing" {
		t.Fatalf("expected chinese term to find ui-routing, got %s snippet=%q", sectionID, snippet)
	}
	if !strings.Contains(snippet, "弹窗") {
		t.Fatalf("expected chinese snippet to contain 弹窗, got %q", snippet)
	}
}
