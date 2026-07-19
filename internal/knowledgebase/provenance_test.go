package knowledgebase

import "testing"

func TestParseKnowledgeProvenanceFields(t *testing.T) {
	fm, _, ok := ParseFrontmatter("---\ntype: Project\ntitle: Auth\nmanagedBy: openwiki\nsourcePaths: [internal/auth/**, api/openapi.yaml]\nsourceRevision: abc123\ngeneratedBy: openwiki@0.2.0\n---\nbody")
	if !ok {
		t.Fatal("frontmatter was not parsed")
	}
	if fm.ManagedBy != "openwiki" || fm.SourceRevision != "abc123" || len(fm.SourcePaths) != 2 {
		t.Fatalf("frontmatter = %#v", fm)
	}
}
