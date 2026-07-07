package knowledgebase

import (
	"database/sql"
	"fmt"
	"sort"

	_ "modernc.org/sqlite"
)

type SectionSearchHit struct {
	SectionID string
	Path      string
	Rank      float64
	Snippet   string
}

type SectionSearchIndex struct {
	db *sql.DB
}

func NewSectionSearchIndex(sections []KnowledgeSection) (*SectionSearchIndex, error) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		return nil, err
	}
	index := &SectionSearchIndex{db: db}
	if err := index.init(sections); err != nil {
		_ = db.Close()
		return nil, err
	}
	return index, nil
}

func (i *SectionSearchIndex) Close() error {
	if i == nil || i.db == nil {
		return nil
	}
	return i.db.Close()
}

func (i *SectionSearchIndex) init(sections []KnowledgeSection) error {
	if _, err := i.db.Exec(`CREATE VIRTUAL TABLE kb_sections USING fts5(section_id UNINDEXED, path, title, heading, tags, body)`); err != nil {
		return fmt.Errorf("create fts5 table: %w", err)
	}
	tx, err := i.db.Begin()
	if err != nil {
		return err
	}
	stmt, err := tx.Prepare(`INSERT INTO kb_sections(section_id, path, title, heading, tags, body) VALUES (?, ?, ?, ?, ?, ?)`)
	if err != nil {
		_ = tx.Rollback()
		return err
	}
	defer stmt.Close()
	for _, section := range sections {
		if _, err := stmt.Exec(section.ID, section.Path, section.Title, section.Heading, stringsJoin(section.Tags, " "), section.Body); err != nil {
			_ = tx.Rollback()
			return err
		}
	}
	return tx.Commit()
}

func (i *SectionSearchIndex) Search(query string, limit int) ([]SectionSearchHit, error) {
	if limit <= 0 {
		limit = 20
	}
	ftsQuery := buildFTSQuery(query)
	if ftsQuery == "" {
		return []SectionSearchHit{}, nil
	}
	rows, err := i.db.Query(`
		SELECT section_id,
		       path,
		       bm25(kb_sections) AS rank,
		       snippet(kb_sections, 5, '<mark>', '</mark>', '...', 48) AS snippet
		FROM kb_sections
		WHERE kb_sections MATCH ?
		ORDER BY rank
		LIMIT ?
	`, ftsQuery, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var hits []SectionSearchHit
	for rows.Next() {
		var hit SectionSearchHit
		if err := rows.Scan(&hit.SectionID, &hit.Path, &hit.Rank, &hit.Snippet); err != nil {
			return nil, err
		}
		hits = append(hits, hit)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	sort.SliceStable(hits, func(a, b int) bool { return hits[a].Rank < hits[b].Rank })
	return hits, nil
}

func stringsJoin(values []string, sep string) string {
	if len(values) == 0 {
		return ""
	}
	out := values[0]
	for _, value := range values[1:] {
		out += sep + value
	}
	return out
}
