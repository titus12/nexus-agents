# GBrain + PGLite Knowledge Graph Implementation Plan

Date: 2026-07-19

Status: Approved; P1, P2, and P3 implementation complete; P3 cutover gate remains open

Branch baseline: `feat-kb` / `601aaae9e4cfe6076ae118bbf6058d90e135e16f`

## Implementation progress

P1 completed on 2026-07-19:

- added the provider contract plus Noop and Fake providers;
- added the `knowledgeGraph` profile schema and validation;
- pinned GBrain `0.42.62.0` by installing GitHub commit
  `f72de97943eb9dc1292a80f85d19db7e311855dc` because this version is not
  published in the npm registry;
- added Bun detection and Nexus-managed GBrain installation paths;
- added PGLite initialization and migration commands;
- added one long-lived stdio MCP process with initialization, bounded health checks,
  hidden Windows launch, clean shutdown, bounded restart backoff, and redacted logs;
- verified Nexus still starts when Bun and GBrain are not installed;
- kept all retrieval and SQLite FTS5 behavior unchanged.

P2 completed on 2026-07-19:

- exports only approved `KnowledgeBase/project/**/*.md` files;
- creates deterministic `project.md`, `domains/`, `features/`, `entities/`,
  `relations/`, and `manifest.json` source layouts;
- represents the current Nexus repository as 1 project document, 5 Domain
  documents, and 15 feature documents;
- rewrites approved Markdown links to stable GBrain slugs;
- rejects likely secrets, unsafe evidence paths, duplicate slugs, and oversized
  documents;
- maps canonical `project:<id>` IDs to deterministic GBrain-safe Source IDs;
- registers one GBrain Source per project and serializes writes through the
  single PGLite-owning stdio process;
- uses `put_page` for idempotent upserts and `delete_page` for deletion and
  rename reconciliation;
- rebuilds all pages when the provider Source registry has been recreated;
- stores graph state and run history separately under
  `%USERPROFILE%\.nexus\knowledge-graph\<project-id>`;
- queues asynchronous sync after Proposal apply without rolling back approved
  Markdown when GBrain fails;
- exposes project graph status, run, sync, and rebuild APIs.

P3 Shadow Search is implemented. SQLite FTS5 remains authoritative and GBrain
results are recorded only for comparison. The isolated verifier
`scripts/verify_p3_shadow_isolated.ps1` rebuilds from Approved Markdown, checks
idempotency, runs the fixed queries, and performs 200 sequential comparisons
without touching the developer's active Nexus/GBrain processes.

## 1. Goal

Upgrade Nexus from a project-local Markdown knowledge retriever into a local-first,
cross-project knowledge system with:

- approved Markdown as the canonical knowledge source;
- GBrain + PGLite as the final knowledge retrieval and graph engine;
- CodeGraph as the code-fact and impact-analysis engine;
- OpenWiki as a candidate-document generator;
- Nexus Proposal as the only path into formal knowledge;
- Nexus Context Pack as the unified AI-facing output.

The final state removes the current in-memory SQLite FTS5 search implementation.
SQLite FTS5 remains only during a bounded Shadow Search migration.

## 2. Final architecture

```text
Git repositories and optional external documents
                    |
                    +--> OpenWiki --> Nexus Proposal --> Review
                    |                                  |
                    +--> CodeGraph                     v
                                           Approved KnowledgeBase Markdown
                                                        |
                                                        v
                                              Nexus GBrain Exporter
                                                        |
                                                        v
                                              GBrain stdio MCP process
                                                        |
                                                        v
                                                Embedded PGLite DB
                                                        |
                                  +---------------------+--------------------+
                                  |                                          |
                                  v                                          v
                         Hybrid knowledge search                    Typed graph traversal
                                  |                                          |
                                  +---------------------+--------------------+
                                                        |
                                                        v
                                               Nexus Context Pack
```

## 3. Non-negotiable boundaries

1. `KnowledgeBase/**/*.md` remains the canonical, Git-managed source of truth.
2. Pending, rejected, or stale Proposals must never enter GBrain.
3. GBrain data is derived and must be fully rebuildable from approved Markdown.
4. GBrain must not write directly to project KnowledgeBase files.
5. GBrain does not replace CodeGraph for callers, callees, routes, symbols, or tests.
6. OpenWiki output must continue through Proposal validation and review.
7. GBrain failure must not roll back an already-applied KnowledgeBase Proposal.
8. No PostgreSQL deployment is required for the first local implementation.
9. GBrain is installed as a pinned managed tool; its source is not copied into Nexus.
10. SQLite FTS5 is removed only after Shadow Search acceptance passes.

## 4. Initial deployment choice

### Local implementation

```text
Nexus Go process
    -> one managed GBrain Bun child process
    -> stdio MCP
    -> PGLite
    -> %USERPROFILE%\.gbrain\brain.db
```

Rules:

- run exactly one GBrain process for the PGLite database;
- reuse that process for health, import, sync, query, and graph operations;
- do not start a new CLI process for every query;
- serialize schema migration and write operations;
- apply request timeouts and restart the process after protocol failure;
- pin a tested GBrain version or Git commit.

### Future team implementation

The provider interface must permit later migration to:

```text
Nexus -> GBrain HTTP MCP -> PostgreSQL + pgvector
```

PGLite-to-PostgreSQL migration is not part of the initial implementation.

## 5. Planned packages

```text
internal/knowledgegraph/
├── provider.go
├── types.go
├── service.go
├── state.go
├── exporter.go
├── sync.go
├── search.go
├── graph.go
├── context_pack.go
├── fallback.go
└── gbrain/
    ├── process.go
    ├── mcp.go
    ├── protocol.go
    ├── health.go
    ├── sync.go
    ├── search.go
    └── graph.go
```

The top-level package is named `knowledgegraph`, not `gbrain`, so Nexus remains
independent of one implementation.

## 6. Provider contract

```go
type KnowledgeGraphProvider interface {
    Start(ctx context.Context) error
    Stop(ctx context.Context) error
    Health(ctx context.Context) (GraphHealth, error)

    SyncSource(
        ctx context.Context,
        source GraphSource,
    ) (GraphSyncResult, error)

    RemoveSource(
        ctx context.Context,
        sourceID string,
    ) error

    Search(
        ctx context.Context,
        query GraphSearchQuery,
    ) (GraphSearchResult, error)

    Traverse(
        ctx context.Context,
        query GraphTraversalQuery,
    ) (GraphTraversalResult, error)

    Synthesize(
        ctx context.Context,
        query GraphSynthesisQuery,
    ) (GraphSynthesisResult, error)

    FindGaps(
        ctx context.Context,
        scope GraphGapScope,
    ) ([]KnowledgeGap, error)
}
```

Required implementations:

- `GBrainProvider`;
- `NoopProvider`;
- `FakeProvider` for deterministic tests.

## 7. Configuration changes

Extend `KnowledgeBase/Setting.yaml`:

```yaml
knowledgeGraph:
  enabled: true
  provider: gbrain
  version: "<pinned-version-or-git-sha>"
  brain: nexus-development
  sourceId: project:nexus-agents

  engine: pglite
  transport: stdio

  sync:
    onProposalApplied: true
    committedChangesOnly: true
    retryMinutes: 5
    maxRetries: 5

  export:
    includeDomains: true
    includeFeatures: true
    includeCodeFacts: false
    includeExternalEvidence: false

  query:
    timeoutSeconds: 10
    maxResults: 20
    maxGraphDepth: 3

  synthesis:
    enabled: false
    automatic: false

  gaps:
    enabled: false
    createProposal: true
```

Secrets remain environment-only:

```text
GBRAIN_EMBEDDING_API_KEY
GBRAIN_EMBEDDING_PROVIDER
GBRAIN_DATABASE_URL       # future PostgreSQL only
```

`ParseProfile`, `SerializeProfile`, `ValidateProfile`, the template profile, API
types, and web types must be updated together.

## 8. P1: managed installation and process lifecycle

### Scope

Add GBrain to the Nexus infrastructure catalog and implement the stdio MCP process
manager.

### Work

1. Add a GBrain infrastructure definition.
2. Detect Bun availability.
3. Install a pinned GBrain release or Git SHA into a Nexus-managed tool directory.
4. Initialize PGLite when no GBrain database exists.
5. Run schema migration before serving queries.
6. Start one hidden GBrain stdio child process.
7. Complete MCP initialization and capability negotiation.
8. Implement bounded health checks.
9. Capture stdout/stderr without allowing untrusted output to become instructions.
10. Restart after protocol failure with bounded backoff.
11. Stop the child process cleanly during Nexus shutdown.

### Files

```text
internal/catalog/infrastructure.go
internal/catalog/infrastructure_gbrain_test.go
internal/knowledgegraph/provider.go
internal/knowledgegraph/types.go
internal/knowledgegraph/gbrain/process.go
internal/knowledgegraph/gbrain/mcp.go
internal/knowledgegraph/gbrain/health.go
cmd/nexus-agents/main.go
```

### Acceptance

- Nexus starts when GBrain is not installed.
- The UI reports `not_installed`, `unhealthy`, `starting`, or `ready`.
- GBrain installation uses a pinned version.
- Only one PGLite-owning GBrain process is active.
- A GBrain crash does not crash Nexus.
- A process restart does not lose indexed knowledge.
- No search behavior changes in P1.

## 9. P2: approved KnowledgeBase export and sync

### Scope

Export approved project knowledge into deterministic GBrain source directories.

### Source layout

```text
%USERPROFILE%\.nexus\gbrain-sources\<project-id>\
├── project.md
├── domains\
├── features\
├── entities\
├── relations\
└── manifest.json
```

### Export rules

- input is only the current approved `KnowledgeBase` tree;
- preserve project ID, branch, Git revision, document path, source paths, and hash;
- convert Markdown links into stable GBrain-compatible links;
- generate deterministic page slugs;
- never include Proposal JSON or OpenWiki workspaces;
- never include secrets or raw external snapshots;
- repeated export with unchanged input must be byte-stable.

### Source identity

```text
brain: nexus-development
source: project:<project-id>
```

### Sync trigger

After a Proposal applies successfully:

```text
Apply Proposal
-> Validate KnowledgeBase
-> Export KnowledgeBase
-> Queue Graph Sync
-> Return apply success
```

Graph sync runs asynchronously. Failure sets graph status to `degraded` and queues
a retry; it does not roll back the Proposal.

### State

```text
%USERPROFILE%\.nexus\knowledge-graph\<project-id>\state.json
%USERPROFILE%\.nexus\knowledge-graph\<project-id>\runs\*.json
```

Required state:

```json
{
  "projectId": "nexus-agents",
  "sourceId": "project:nexus-agents",
  "status": "ready",
  "lastSyncedRevision": "601aaae...",
  "lastSourceHash": "sha256:...",
  "documents": 20,
  "lastSuccessfulAt": "..."
}
```

### Files

```text
internal/knowledgegraph/exporter.go
internal/knowledgegraph/sync.go
internal/knowledgegraph/state.go
internal/knowledgegraph/gbrain/sync.go
internal/knowledgesync/service.go
internal/httpapi/server.go
```

### Acceptance

- current Nexus KB exports to one source;
- 5 Domains and 15 feature documents are represented;
- repeated sync creates no duplicate pages;
- rename and deletion are reconciled;
- pending/rejected Proposals are absent;
- deleting the PGLite database and rebuilding restores the same source;
- sync failure leaves formal Markdown unchanged.

## 10. P3: Shadow Search

Implementation status: complete. Current-repository isolated acceptance passed
with 21 documents, five fixed queries, 200 sequential comparisons, zero
timeouts, zero duplicate pages, and no process restarts. The removal gate below
is intentionally still open because it requires at least three repositories,
100 documents, and 30 fixed acceptance queries.

### P3.2 Project Group source scopes

Implementation status: complete.

- every Nexus Project keeps exactly one deterministic GBrain Source;
- `ProjectGroup` is a Nexus-local collection of Project IDs and does not change
  canonical Markdown or Source identity;
- one Project may belong to multiple groups without duplicate ingestion;
- import can select existing groups or create a new group;
- project membership can be edited after import;
- Shadow Search supports `project`, `group`, and `all` scopes;
- group/all retrieval resolves explicit Source IDs and uses per-source ranking
  followed by reciprocal-rank fusion;
- result identity remains `sourceId + slug`, so equal slugs in different
  projects are not treated as duplicates.

The isolated verifier creates a second repository fixture, rebuilds two
independent 21-document Sources, adds both projects to one group, and verifies
multi-source retrieval with zero duplicate pages.

### Scope

Run existing SQLite FTS5 retrieval and GBrain retrieval for the same query, while
continuing to return the existing FTS5 result to the user.

### Shadow behavior

```text
Query
├── current FTS5 retrieval -> user response
└── GBrain retrieval       -> comparison log only
```

Do not merge results during Shadow Search.

### Comparison metrics

- matched project;
- matched Domain;
- matched document paths;
- required-document precision;
- missing expected documents;
- duplicated documents;
- query latency;
- GBrain timeout rate;
- process restart count;
- token count of produced context.

### Fixed acceptance queries

At minimum:

1. 修改模型路由会影响哪些功能和入口？
2. 知识库如何扫描仓库、检索上下文并加载给 AI？
3. 模板初始化和同步有什么区别？
4. Workflow Run 如何进入评估？
5. 哪些功能依赖 Session-Id？
6. 哪些项目使用公会数据？
7. 修改 GuildMember 会影响哪些服务？

The first five queries validate the current repository. The final two become active
after the first business repositories are onboarded.

### Acceptance gate for removing FTS5

- at least 3 real repositories;
- at least 100 approved KB documents;
- at least 30 fixed acceptance queries;
- GBrain project/Domain accuracy is not lower than FTS5;
- expected-document precision is not lower than FTS5;
- 200 sequential queries without a stuck child process;
- P95 local query latency is acceptable for interactive use;
- duplicate page rate is zero;
- index rebuild succeeds from approved Markdown only.

## 11. P4: retrieval cutover and FTS5 removal

### Cutover

Replace the retrieval implementation with:

```text
Domain alias match
-> GBrain search scoped by project/source/domain
-> GBrain graph context
-> context budget packing
```

Nexus performs query understanding once and passes the normalized query to GBrain.
Do not run separate query-rewrite calls for multiple engines.

### Fallback

If GBrain is unavailable:

```text
Domain alias
-> Domain index.md
-> explicitly linked feature documents
```

The response must include:

```json
{
  "degraded": true,
  "retrieval": "domain-routing-only",
  "reason": "GBrain unavailable"
}
```

This fallback is not a second full-text engine.

### Delete after acceptance

```text
internal/knowledgebase/search_index.go
internal/knowledgebase/search_index_test.go
```

Remove `modernc.org/sqlite` and dependencies used only by the FTS5 search path.

### Preserve

- KnowledgeBase scan;
- Frontmatter parsing;
- validation;
- render tree and document rendering;
- Domain aliases;
- Proposal;
- export;
- token-budgeted Context Pack.

## 12. P5: business entities and graph relations

### Graph schema

Initial entity types:

```text
Project
Repository
Domain
Feature
Service
APIEndpoint
DataEntity
BusinessEntity
Document
ExternalDocument
CodeSymbol
```

Initial relation types:

```text
contains
implements
exposes
reads
writes
calls
depends_on
owned_by
documented_by
implemented_by
affects
related_to
same_as
evidence_from
```

### Stable IDs

```text
project:nexus-agents
domain:nexus-agents:knowledgebase
feature:nexus-agents:knowledge-sync
business:guild
data:livetopia:guild-member
api:nexus-agents:POST:/api/projects/{id}/knowledge/initialize-preview
```

### Knowledge frontmatter

Extend the frontmatter model with structured graph metadata:

```yaml
graph:
  entities:
    - id: business:guild
      type: BusinessEntity
      name: 公会
      aliases: [Guild, Club]

  relations:
    - source: feature:guild-management
      type: reads
      target: data:livetopia:guild-member
      evidence:
        - internal/guild/service.go
```

Replace the lightweight frontmatter parser with `yaml.v3` structured decoding while
remaining backward compatible with current files.

### CodeGraph bridge

Do not import the full CodeGraph graph into GBrain.

Export only selected evidence links:

```text
Feature -> implemented_by -> CodeSymbol
APIEndpoint -> handled_by -> CodeSymbol
DataEntity -> read_by -> CodeSymbol
DataEntity -> written_by -> CodeSymbol
```

CodeGraph remains authoritative for live code traversal.

## 13. P6: unified Context Pack, APIs, UI, and gaps

### Context Pack sources

```text
GBrain approved document results
GBrain typed relation paths
CodeGraph exact code facts
Nexus validation and provenance
```

### Authority order

```text
1. CodeGraph exact code facts
2. Approved KnowledgeBase statements
3. Explicit GBrain typed relations
4. GBrain synthesis or inference
5. External supporting evidence
```

### New APIs

```text
POST /api/knowledge/search
POST /api/knowledge/context-pack
POST /api/knowledge/graph/traverse
GET  /api/knowledge/entities/{id}
GET  /api/knowledge/gaps
GET  /api/knowledge/graph/status

POST /api/projects/{id}/knowledge/graph/sync
POST /api/projects/{id}/knowledge/graph/rebuild
GET  /api/projects/{id}/knowledge/graph/status
```

### UI

Add:

- cross-project search;
- entity detail;
- graph-path visualization;
- project and relation filters;
- evidence panel;
- sync status;
- gap review.

### Gap lifecycle

```text
GBrain gap
-> Nexus verifies Git and CodeGraph evidence
-> Nexus creates Knowledge Proposal
-> human review
-> approved Markdown update
-> GBrain resync
```

GBrain never edits canonical Markdown directly.

## 14. Testing strategy

### Unit tests

- config validation;
- deterministic export;
- source manifest hashing;
- stdio MCP framing;
- process timeout/restart;
- sync idempotency;
- delete and rename reconciliation;
- degraded fallback;
- graph schema validation;
- stable entity IDs;
- Context Pack authority ordering.

### Integration tests

- fake MCP process;
- real GBrain/PGLite opt-in tests;
- Proposal applied -> graph sync;
- Proposal rejected -> no graph sync;
- corrupted DB -> rebuild;
- process crash -> restart;
- 200-query stability run.

### Full verification

```text
go test ./...
go vet ./...
cd web && npm run test:unit && npm run build
powershell -File scripts/verify_all.ps1
git diff --check
```

## 15. Commit boundaries

Recommended implementation commits:

1. `feat: add knowledge graph provider contract`
2. `feat: manage gbrain pglite process`
3. `feat: export approved knowledge to gbrain sources`
4. `feat: sync applied knowledge proposals to gbrain`
5. `test: add gbrain shadow retrieval`
6. `feat: switch knowledge retrieval to gbrain`
7. `refactor: remove sqlite fts5 retrieval`
8. `feat: add business entity graph schema`
9. `feat: bridge codegraph evidence into knowledge graph`
10. `feat: add cross-project context pack and graph UI`
11. `feat: add knowledge gap proposal loop`

Do not combine installation, sync, cutover, FTS5 deletion, entity graph, and UI into
one commit.

## 16. Explicitly out of scope for the first implementation

- PostgreSQL deployment;
- multi-user permissions;
- remote HTTP MCP;
- automatic GBrain synthesis on every query;
- automatic acceptance of inferred relations;
- importing all code symbols into GBrain;
- scanning pending OpenWiki workspaces;
- direct GBrain writes to project Markdown;
- deleting SQLite FTS5 before Shadow Search acceptance.

## 17. Definition of done

The second stage is complete when:

1. GBrain is installed and managed by Nexus.
2. PGLite requires no separate database deployment.
3. Approved KB documents sync incrementally and idempotently.
4. Three real repositories are indexed as separate sources in one Brain.
5. Cross-project search and graph traversal return evidence-backed results.
6. CodeGraph facts can be attached to business features and entities.
7. Context Pack contains project, Domain, relation path, code facts, and revision.
8. Knowledge gaps create reviewable Nexus Proposals.
9. The GBrain database can be deleted and fully rebuilt.
10. GBrain failure degrades to Domain Routing without breaking Nexus.
11. Shadow Search acceptance passes.
12. SQLite FTS5 code and dependencies are removed.
