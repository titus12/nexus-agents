# OpenWiki Knowledge Synchronization Design

## Goal

Allow a developer to import a local Git repository into Nexus, have Nexus propose
an AI-assisted knowledge scan policy, initialize or enrich the repository's
existing KnowledgeBase, and later detect committed Git changes and propose
incremental KnowledgeBase updates.

The resulting project knowledge must remain:

- stored with the repository it describes;
- reviewable through normal Git diffs and pull requests;
- grounded primarily in current source, configuration, contracts, and CodeGraph;
- protected from direct, unreviewed OpenWiki writes;
- usable by the existing Nexus retrieval, routing, rendering, and export paths.

## Product decision

The ownership model is:

| Artifact | Authoritative location | Ownership |
|---|---|---|
| Project scan and generation policy | `<project>/KnowledgeBase/Setting.yaml` | Project team |
| Stable project knowledge | `<project>/KnowledgeBase/project/**` | Project team |
| Portable OKF framework | Nexus `templates/KnowledgeBase/framework/**` | Nexus |
| OpenWiki native output | Nexus-managed temporary workspace | Derived, disposable |
| Sync state, runs, and proposals | Nexus user data directory | Derived local state |
| Code facts | CodeGraph and current source | Derived from source |
| Optional Feishu initialization input | Per-run temporary source snapshot | Auxiliary only |
| Future GBrain index | Nexus/GBrain data directory | Rebuildable index |

Project knowledge follows the project repository. Nexus coordinates generation,
validation, proposals, and retrieval, but does not become the authoritative
storage location for project facts.

## Source-of-truth order

Knowledge generation and conflict resolution use this precedence:

1. Current source code, configuration, schemas, generated contracts, and
   CodeGraph facts.
2. Repository-owned ADRs, API contracts, architecture documents, and existing
   approved KnowledgeBase pages.
3. Developer-supplied Feishu documents used during initialization.
4. AI inference.

Feishu content may explain business intent or design history. It must not override
the current implementation or be presented as implemented behavior when the
source does not support it. AI-only conclusions remain proposal findings until a
human accepts them.

## Repository layout

Each participating repository uses:

```text
KnowledgeBase/
  Setting.yaml
  index.md
  log.md
  framework/              # Nexus-provided shared framework; ignored locally
  project/                # Project-owned and committed
    index.md
    domains/
      <domain>/
        index.md
        ...
```

`KnowledgeBase/Setting.yaml` is project-owned and committed. It is not an OKF
concept document and is excluded from Markdown scanning and rendering.

The template initialization ignore block must preserve:

```gitignore
KnowledgeBase/*
!KnowledgeBase/Setting.yaml
!KnowledgeBase/project/
!KnowledgeBase/project/**
```

Template synchronization must treat both `KnowledgeBase/Setting.yaml` and
`KnowledgeBase/project/**` as protected project content.

## Setting.yaml contract

The initial schema is:

```yaml
version: 1

knowledge:
  root: KnowledgeBase/project
  language: zh-CN
  updateMode: proposal
  defaultBranch: develop

discovery:
  strategy: ai-assisted
  generatedFromRevision: ""
  generatedAt: ""
  reviewed: false
  reviewedBy: ""

scan:
  source: git-tracked
  rules: []
  limits:
    maxFileSizeKB: 512
    maxFilesPerRun: 3000

instructions:
  requiredTopics:
    - service-responsibility
    - integration-boundaries
    - data-ownership
    - verification-paths
  additional: ""

codeGraph:
  enabled: true
  impactDepth: 2
  includeCallers: true
  includeCallees: true
  includeRelatedTests: true

openWiki:
  enabled: true
  version: "0.2.0"

schedule:
  enabled: false
  intervalMinutes: 30
  committedChangesOnly: true

ownership:
  owners: []
  requireApproval: true
```

Each scan rule uses:

```yaml
- pattern: internal/**
  action: include
  category: code
  priority: high
  reason: Core Go implementation.
```

Allowed actions are `include` and `exclude`. Exclusion wins when rules overlap.
Patterns are project-relative and use Git-ignore-style slash normalization.

## Hard safety exclusions

Nexus applies a non-overridable safety policy before AI-generated or
project-authored scan rules:

```text
.git/**
.env
.env.*
**/*.pem
**/*.key
**/credentials*
**/secrets/**
**/node_modules/**
**/vendor/**
**/dist/**
**/build/**
**/target/**
**/bin/**
**/obj/**
**/.cache/**
**/coverage/**
```

Nexus also rejects absolute paths, `..` traversal, files above the configured
size limit, and binary files unless a future explicit parser supports them.
Secrets are not passed to OpenWiki or an external model.

## AI-assisted scan-policy initialization

### Inventory input

Nexus builds a deterministic repository inventory from:

- `git ls-files`;
- current branch and HEAD commit;
- directory tree with bounded depth;
- per-directory file counts and extension summaries;
- repository manifests such as `go.mod`, `package.json`, `*.csproj`,
  `pyproject.toml`, `pom.xml`, and API/schema files;
- root README and directory-local README metadata;
- `.gitignore`;
- existing KnowledgeBase summary;
- CodeGraph language, module, route, and entry-point summaries when available.

The first pass sends metadata, not full repository contents, to the model.
Ambiguous directories may receive a bounded second pass containing a README,
file-name sample, representative file headers, and CodeGraph summaries.

### Structured model output

The model returns JSON matching a Nexus-owned schema:

```text
ScanPolicyProposal
  revision
  rules[]
  requiredTopics[]
  uncertain[]
  warnings[]
```

The model does not write YAML. Nexus validates the JSON and serializes the
approved result to `KnowledgeBase/Setting.yaml`.

### Review gate

The initialization UI shows included, excluded, and uncertain paths with
reasons and confidence. The developer must resolve uncertain paths and approve
the policy before Nexus writes `Setting.yaml` or starts OpenWiki.

If an existing `Setting.yaml` exists, Nexus offers:

- use the existing policy;
- compare it with a newly generated proposal;
- regenerate after explicit confirmation.

It never silently replaces a team-owned policy.

## Existing-KB behavior

When `KnowledgeBase/project/**` already contains knowledge, Nexus offers:

### Adopt existing

- scan and validate the current bundle;
- record the current Git revision and knowledge hash;
- rebuild the existing Nexus export/search state;
- do not call OpenWiki;
- do not modify files.

### Check and enrich

- use the current KnowledgeBase as the generation baseline;
- compare it with repository documents and CodeGraph facts;
- ask OpenWiki to update missing or stale areas;
- return a proposal rather than overwriting the existing bundle.

Human-managed documents are suggestion-only. A future extension field
`managedBy` defaults to `human` when absent.

## OpenWiki execution model

OpenWiki code mode normally writes an `openwiki/` directory, maintains OpenWiki
blocks in root `AGENTS.md` and `CLAUDE.md`, and can install a scheduled CI
workflow. Nexus must not run that native workflow directly in the developer's
active worktree.

Nexus uses an isolated workspace:

```text
~/.nexus/knowledge-sync/<project-id>/
  state.json
  runs/<run-id>/
  proposals/<proposal-id>/
  workspaces/<run-id>/repository/
```

For each run Nexus:

1. Creates an isolated Git worktree or safe repository snapshot at the target
   revision.
2. Copies the approved `KnowledgeBase/project/**` bundle into staging
   `openwiki/**`, applying Nexus-to-OpenWiki OKF normalization.
3. Generates staging `openwiki/INSTRUCTIONS.md` from the framework contract,
   `Setting.yaml`, scan manifest, and run-specific auxiliary references.
4. Adds temporary Feishu Markdown projections only when the initialization
   request supplied them.
5. Runs the pinned OpenWiki command:

   ```text
   openwiki code --update --print
   ```

6. Ignores OpenWiki changes to staging `AGENTS.md`, `CLAUDE.md`, and CI files.
7. Reads only the generated `openwiki/**` bundle.
8. Converts it to the Nexus repository profile.
9. Validates it and creates a proposal.
10. Removes or expires the isolated workspace after retaining run evidence.

Nexus supplies provider/model settings through the subprocess environment.
Secrets are never written to `Setting.yaml`, proposal files, or project files.

## OKF normalization

OpenWiki and Nexus both use OKF-style Markdown but have different repository
profiles. The adapter must at least normalize:

- OpenWiki root `index.md` `okf_version` metadata to the Nexus reserved-index
  rule;
- concept `resource` paths from `openwiki/**` to
  `KnowledgeBase/project/**`;
- parent index links after path translation;
- extension fields supported by OpenWiki but not yet typed by Nexus;
- reserved files and generated instructions that must not enter the project KB.

The converted output must pass the existing `knowledgebase.Validate` and
structure validator before a proposal can be applied.

## Feishu initialization references

Feishu is optional and initialization-only in this scope.

The developer supplies links in the initialization request. Nexus fetches and
converts them to temporary Markdown, marks them as auxiliary evidence, and
removes the content after the run. URLs and credentials are not written to
`Setting.yaml`.

OpenWiki receives a source-precedence instruction that:

- code and current repository contracts define implemented behavior;
- Feishu may explain intent and background;
- unimplemented Feishu plans must be labeled as design intent or omitted;
- conflicts become proposal warnings.

Automatic Feishu refresh is explicitly out of scope.

## Initial knowledge generation

For a project without a KB:

1. Build the repository inventory.
2. Generate and approve `Setting.yaml`.
3. Initialize the Nexus KnowledgeBase framework if missing.
4. Ensure CodeGraph is ready when enabled.
5. Run OpenWiki in the isolated workspace.
6. Normalize and validate the generated bundle.
7. Present create/update actions and evidence as a proposal.
8. Apply selected files only after user approval.
9. Rescan, export, and make the approved KB available to retrieval.
10. Record the processed Git revision and knowledge hash.

The initial generation should be domain-first. The first pass identifies
service responsibilities and candidate domains; later passes may generate
domain pages in bounded batches instead of sending the complete repository to
one unbounded model call.

## Incremental Git maintenance

### Lightweight polling

The scheduler checks:

- repository availability;
- current branch;
- current HEAD;
- last processed commit.

When HEAD is unchanged, no CodeGraph or OpenWiki work runs.

Scheduled runs use committed changes only. A manual action may later offer an
explicit preview of uncommitted changes, but automatic maintenance never writes
knowledge from an uncommitted working tree.

### Change calculation

When HEAD changes:

```text
git diff --name-status <last-processed>..<current-head>
```

Nexus classifies changes:

| Change class | Action |
|---|---|
| KnowledgeBase-only | Validate, export, and reindex; do not run OpenWiki |
| Formatting/test-only with no stable knowledge impact | Sync CodeGraph only |
| Repository docs, ADR, API, schema, or contract changes | OpenWiki incremental run |
| Route, public type, configuration ownership, event, persistence, or service-boundary changes | CodeGraph impact plus OpenWiki |
| Generated/dependency/build output | Ignore |
| Missing or rewritten base commit | Fall back to full comparison proposal |

CodeGraph expands changed code by the configured bounded impact radius. Nexus
also maps changed source paths to KB pages using `sourcePaths`,
`sourceRevision`, document links, and routing metadata.

### Proposal-only result

Incremental work produces a `KnowledgeProposal` containing:

- base and target commits;
- create/update/move/deprecate actions;
- before/after content;
- source and CodeGraph evidence;
- validation results;
- confidence and warnings;
- a deterministic proposal identity derived from project, revisions, settings
  hash, and compiler version.

Pending proposals are excluded from normal AI retrieval.

## Runtime state

Local state is separate from the team-owned policy:

```text
KnowledgeSyncState
  projectId
  branch
  lastProcessedCommit
  lastKnowledgeHash
  lastCheckedAt
  lastSuccessfulAt
  status
  pendingProposalId
  compilerVersion
  profileHash
```

Supported states:

```text
uninitialized
ready
checking
update_available
proposal_pending
applying
up_to_date
failed
```

Only one run per project may execute at a time. Runs are debounced and survive
process restart through persisted state.

## Team maintenance

The MVP uses Git rather than a separate collaborative editor:

1. Developers generate or apply a proposal locally.
2. The resulting `KnowledgeBase/Setting.yaml` and
   `KnowledgeBase/project/**` changes are committed through a normal project
   branch and pull request.
3. `CODEOWNERS` routes domain knowledge to the owning team.
4. Other developers pull the merged KB and their local Nexus instances rebuild
   indexes from the shared Git state.

Project knowledge lives with the service repository. Cross-project business
knowledge should live in a separate team knowledge repository imported into
Nexus as a knowledge-only project rather than being copied into every service.

A future team bot may run the same synchronization service against the default
branch and open KB update pull requests. That mode is out of the first
implementation slice.

## API surface

Extend `/api/projects/{id}/knowledge` with:

```text
GET  /sync-profile
PUT  /sync-profile
GET  /sync-status
POST /discovery-preview
POST /initialize-preview
POST /check-updates
GET  /runs
GET  /proposals
GET  /proposals/{proposalId}
POST /proposals/{proposalId}/apply
POST /proposals/{proposalId}/reject
```

Manual and scheduled checks call the same backend service.

## User interface

Add a `同步` tab to the existing Project Knowledge Base page.

It shows:

- repository branch, HEAD, and last processed commit;
- detected project types and file-category counts;
- current policy and schedule;
- initialization/adoption state;
- last run and failure details;
- pending proposals;
- actions for discovery preview, initialize, check updates, apply, and reject.

Initialization choices are:

```text
No KB:
  Generate scan policy → Review → Initialize KB

Existing KB:
  Adopt existing
  Check and enrich
```

The file-scope preview explains why each pattern is included or excluded and
requires the user to resolve ambiguous directories before proceeding.

## Failure and safety rules

- OpenWiki never runs against the active developer worktree.
- OpenWiki never writes the formal KB directly.
- A failed compiler, conversion, or validation run leaves project files
  unchanged.
- Applying a proposal verifies that the current HEAD still matches the proposal
  target or requires regeneration.
- Human-managed pages are not silently overwritten.
- Project-relative paths are validated before reading or writing.
- External source snapshots and model credentials are not committed.
- Scheduled runs do not include uncommitted changes.
- No Git commit, push, branch, or pull request is created by the MVP.

## Acceptance criteria

1. Importing a repository without a KB can produce a reviewed
   `Setting.yaml` and an initial KB proposal.
2. Importing a repository with an existing KB can adopt it without modifications
   or produce an incremental enrichment proposal.
3. No Git change results in no model or OpenWiki invocation.
4. KB-only changes rebuild validation/export state without running OpenWiki.
5. Relevant code or documentation changes produce a bounded proposal tied to
   the exact Git revisions.
6. Applying a proposal writes only selected `KnowledgeBase/project/**` files and
   the approved `Setting.yaml`.
7. OpenWiki-generated `openwiki/`, `AGENTS.md`, `CLAUDE.md`, or CI files never
   enter the target repository.
8. Feishu references are auxiliary, temporary, and initialization-only.
9. Existing validation, rendering, retrieval, and export continue to work on the
   approved result.
10. Restarting Nexus preserves sync state and pending proposal metadata.

## Deferred scope

- GBrain integration and cross-project semantic graph.
- Team-hosted synchronization bot and automatic pull requests.
- Automatic Feishu refresh.
- Automatic application of low-risk proposals.
- Uncommitted working-tree maintenance.
- Runtime traces and data-lineage sources.

