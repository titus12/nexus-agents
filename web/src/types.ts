export type TemplateKind = "agents" | "rules" | "skills" | "workflows";
export type ProjectCopyKind = "agent" | "rule" | "skill" | "workflow";
export type SyncStatus = "synced" | "project_modified" | "template_updated" | "diverged" | "detached";

export type TemplateItem = {
  id: string;
  kind: ProjectCopyKind;
  slug: string;
  name: string;
  version: number;
  summary: string;
  entry?: string;
  files?: string[];
  updatedAt: string;
  status: string;
  modelTier?: string;
  rulesCount?: number;
  skillsCount?: number;
  source?: string;
  applicableAgents?: string[];
  relatedRules?: string[];
  relatedSkills?: string[];
  tools?: string[];
  mcp?: string[];
  sourcePaths?: string[];
  content?: string;
  codexProjection?: string;
  claudeSource?: string;
};

export type TemplateInput = {
  name?: string;
  slug?: string;
  summary?: string;
  entry?: string;
  files?: string[];
  status?: string;
};

export type TemplateLibrary = {
  agents: TemplateItem[];
  rules: TemplateItem[];
  skills: TemplateItem[];
  workflows: TemplateItem[];
};

export type ConfigSummary = {
  agents: number;
  rules: number;
  skills: number;
  workflows: number;
};

export type KnowledgeSummary = {
  exists: boolean;
  root: string;
  documents: number;
  domains: number;
  workflows: number;
  issues: number;
  errors: number;
  warnings: number;
  lastModified?: string;
  lastExported?: string;
};

export type Project = {
  id: string;
  name: string;
  path: string;
  projectType: TemplateProjectType;
  status: "ready" | "draft" | string;
  updatedAt: string;
  configSummary: ConfigSummary;
  repoKey?: string;
  localPath?: string;
  localConfigPath?: string;
  localConfigIgnored?: boolean;
  knowledgeSummary?: KnowledgeSummary;
};

export type KnowledgeFrontmatter = {
  type?: string;
  title?: string;
  description?: string;
  resource?: string;
  tags?: string[];
  dependsOn?: string[];
  seeAlso?: string[];
  status?: string;
  owner?: string;
  timestamp?: string;
  routing?: {
    aliases?: {
      values?: string[];
      zh?: string[];
      en?: string[];
      pairs?: Array<{ zh?: string; en?: string }>;
    };
    keywords?: {
      values?: string[];
      zh?: string[];
      en?: string[];
    };
  };
  raw?: string;
};

export type KnowledgeDocument = {
  path: string;
  name: string;
  directory: string;
  frontmatter: KnowledgeFrontmatter;
  links: Array<{ text: string; target: string; line: number }>;
  sizeBytes: number;
  modifiedAt: string;
  reserved: boolean;
};

export type KnowledgeIssue = {
  severity: "error" | "warning" | string;
  code: string;
  path: string;
  line?: number;
  message: string;
};

export type KnowledgeValidationReport = {
  root: string;
  summary: KnowledgeSummary;
  issues: KnowledgeIssue[];
  checkedAt: string;
};

export type KnowledgeMaintenanceReport = {
  root: string;
  summary: KnowledgeSummary;
  issues: KnowledgeIssue[];
  staleDocuments: KnowledgeDocument[];
  largeDocuments: KnowledgeDocument[];
  duplicateRules: Array<{ text: string; paths: string[] }>;
  suggestedActions: string[];
  checkedAt: string;
};

export type KnowledgeRoutePreview = {
  task: string;
  matchedDomain?: string;
  requiredFiles: string[];
  optionalFiles: string[];
  reason: string;
  missingFiles: string[];
  routingDocuments: string[];
  matches?: KnowledgeContextItem[];
  terms?: string[];
  tokenBudget?: KnowledgeTokenBudget;
  loadedKnowledgeMarkdown?: string;
};

export type KnowledgeTokenBudget = {
  maxTokens: number;
  usedTokens: number;
};

export type KnowledgeContextItem = {
  projectId?: string;
  sourceId?: string;
  revision?: string;
  path: string;
  title: string;
  type?: string;
  domain?: string;
  heading?: string;
  startLine?: number;
  endLine?: number;
  score: number;
  tokens: number;
  required: boolean;
  reasons: string[];
  snippet?: string;
};

export type KnowledgeRetrievalResult = {
  query: string;
  mode: string;
  engine?: "gbrain" | "fts5" | string;
  scope?: "project" | "group" | string;
  projectIds?: string[];
  normalizedQuery?: string;
  sources?: Array<{
    projectId: string;
    sourceId: string;
    path: string;
    title?: string;
    revision?: string;
    score: number;
    snippet?: string;
  }>;
  degraded?: boolean;
  fallbackReason?: string;
  matchedDomain?: string;
  matchedAlias?: {
    alias?: string;
    pairedAlias?: string;
    domain?: string;
    source?: string;
  };
  confidence: number;
  terms: string[];
  required: KnowledgeContextItem[];
  optional: KnowledgeContextItem[];
  related: KnowledgeContextItem[];
  missingFiles: string[];
  omitted: Array<{ path: string; reason: string }>;
  routingDocuments: string[];
  loadedKnowledgeMarkdown: string;
  tokenBudget: KnowledgeTokenBudget;
  reason: string;
};

export type KnowledgeRenderNode = {
  path: string;
  title: string;
  type?: string;
  kind?: "directory" | "document" | string;
  indexDocument?: string;
  children?: KnowledgeRenderNode[];
};

export type KnowledgeRenderTree = {
  root: string;
  nodes: KnowledgeRenderNode[];
};

export type KnowledgeRenderedDocument = {
  path: string;
  title: string;
  frontmatter: KnowledgeFrontmatter;
  html: string;
  raw: string;
};

export type KnowledgeExportManifest = {
  projectId: string;
  projectRoot: string;
  sourceRoot: string;
  exportRoot: string;
  documentCount: number;
  sourceHash: string;
  exportedAt: string;
  summary: KnowledgeSummary;
};

export type KnowledgeExportData = {
  manifest: KnowledgeExportManifest;
  tree: KnowledgeRenderTree;
  validation: KnowledgeValidationReport;
  maintenance: KnowledgeMaintenanceReport;
};

export type ProjectInput = {
  name: string;
  path: string;
  projectType: TemplateProjectType;
  groupIds?: string[];
};

export type CodeGraphSetup = {
  projectPath: string;
  indexPath?: string;
  version?: string;
  initialized: boolean;
  action: "init" | "sync" | string;
};

export type ProjectGroup = {
  id: string;
  name: string;
  projectIds: string[];
};

export type ProjectGroupInput = {
  name: string;
  projectIds?: string[];
};

export type TemplateProjectType = "general" | "go" | "unity" | "dotnet";

export type TemplateInitializationInput = {
  targetPath: string;
  projectType: TemplateProjectType;
};

export type TemplateInitializationWrite = {
  relativePath: string;
  sourcePath?: string;
  action: "create" | "update" | "unchanged" | "conflict" | "protected";
  reason?: string;
};

export type TemplateInitializationSummary = {
  create: number;
  update: number;
  unchanged: number;
  conflict: number;
  protected: number;
};

export type TemplateInitializationPreview = {
  planId: string;
  targetPath: string;
  projectType: TemplateProjectType;
  summary: TemplateInitializationSummary;
  writes: TemplateInitializationWrite[];
};

export type TemplateInitializationResult = {
  targetPath: string;
  projectType: TemplateProjectType;
  summary: TemplateInitializationSummary;
  writes: TemplateInitializationWrite[];
};

export type LocalDirectoryEntry = {
  name: string;
  path: string;
};

export type LocalDirectoriesResponse = {
  path: string;
  parent?: string;
  roots: LocalDirectoryEntry[];
  shortcuts: LocalDirectoryEntry[];
  entries: LocalDirectoryEntry[];
};

export type LocalDirectoryPickerResponse = {
  path: string;
  selected: boolean;
};

export type Origin = {
  templateId: string;
  baseVersion: number;
  baseHash: string;
};

export type ProjectCopy = {
  id: string;
  kind: ProjectCopyKind;
  name: string;
  origin: Origin | null;
  localVersion: number;
  syncMode: "manual";
  status: SyncStatus;
  path: string;
  diff: string;
  content?: string;
  sourcePaths?: string[];
};

export type ProjectConfigSet = Record<string, ProjectCopy[]>;

export type ProjectRescanResult = {
  project: Project;
  copies: ProjectCopy[];
};

export type ProjectTemplateSyncResult = ProjectRescanResult & {
  overwritten: number;
  created: number;
  skipped: number;
};

export type BootstrapData = {
  templateLibrary: TemplateLibrary;
  projects: Project[];
  projectGroups: ProjectGroup[];
  projectConfigSets: ProjectConfigSet;
};

export type WorkflowSummary = {
  id: string;
  name: string;
  status: string;
  updatedAt: string;
  owner: string;
  trigger: string;
  tags: string[];
  summary: string;
  nodeCount: number;
  edgeCount: number;
};

export type WorkflowInput = {
  name?: string;
  status?: string;
  trigger?: string;
  summary?: string;
};

export type WorkflowNode = {
  id: string;
  type: string;
  category: string;
  label: string;
  agent: string;
  detail: string;
  x: number;
  y: number;
};

export type WorkflowEdge = {
  from: string;
  to: string;
  label: string;
};

export type WorkflowGraph = {
  id: string;
  name: string;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
};

export type ProjectWorkflowCreateResult = {
  copy: ProjectCopy;
  graph: WorkflowGraph;
};

export type ModelRoute = {
  id: string;
  client: string;
  source: string;
  target: string;
  provider: string;
  endpoint: string;
  auth: string;
  status: string;
};

export type ModelRouteResolution = {
  routeId: string;
  client: string;
  sourceModel: string;
  targetModel: string;
  provider: string;
  endpoint: string;
  auth: string;
  passthrough: boolean;
};

export type InfrastructureItem = {
  id: "rtk" | "codegraph" | "openwiki" | "gbrain" | "repomix" | string;
  name: string;
  kind: "token_proxy" | "code_context" | "knowledge_compiler" | "knowledge_graph" | "context_pack" | string;
  status: "ready" | "missing" | "not_installed" | "unhealthy" | "unknown" | string;
  summary: string;
  description: string;
  githubUrl: string;
  installCommand: string;
  commonCommands: string[];
  source: "built_in" | "third_party" | string;
  installable: boolean;
  version?: string;
  executablePath?: string;
  lastCheckedAt?: string;
  output?: string;
};

export type TaskRun = {
  id: string;
  projectId: string;
  workflowTemplateId?: string;
  workflowCopyId?: string;
  workflowType: string;
  taskTitle?: string;
  submittedStatus: string;
  startedAt?: string;
  endedAt?: string;
  durationMs?: number;
  evaluationStatus: string;
  context?: Record<string, unknown>;
  metrics?: Record<string, unknown>;
  evidence?: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
};

export type TaskRunInput = {
  projectId: string;
  sessionId?: string;
  workflowTemplateId?: string;
  workflowCopyId?: string;
  workflowType: string;
  taskTitle?: string;
  submittedStatus: string;
  startedAt?: string;
  endedAt?: string;
  durationMs?: number;
  context?: Record<string, unknown>;
  metrics?: Record<string, unknown>;
  evidence?: Record<string, unknown>;
};

export type WorkflowRunSubmitOptions = {
  projectId?: string;
  sessionId?: string;
  workflowCopyId?: string;
  workflowTemplateId?: string;
  workflowId?: string;
  workflowName?: string;
  workflowSummary?: string;
  workflowTrigger?: string;
  graph?: WorkflowGraph | null;
  context?: {
    sessionId?: string;
    model?: string;
    rules?: string[];
    skills?: string[];
    tools?: string[];
    agent?: string;
  };
  metrics?: Record<string, unknown>;
  evidence?: Record<string, unknown>;
  submittedStatus?: "success" | "partial_success" | "failed" | "cancelled";
  taskTitle?: string;
  startedAt?: string;
  endedAt?: string;
};

export type Evaluation = {
  id: string;
  runId: string;
  rubricId: string;
  rubricVersion: string;
  evaluationLevel: number;
  finalStatus: string;
  overallScore: number;
  confidence: number;
  scores: Record<string, unknown>;
  analysis: Record<string, unknown>;
  modelJudgements?: Record<string, unknown>;
  modelPolicy?: Record<string, unknown>;
  createdAt: string;
  latestReviewStatus?: string;
  latestReviewScore?: number;
  latestReviewComment?: string;
};

export type EvaluationReviewInput = {
  reviewer?: string;
  overrideStatus?: string;
  overrideScore?: number;
  review?: Record<string, unknown>;
};

export type EvaluationSummary = {
  totalRuns: number;
  evaluatedRuns: number;
  pendingRuns: number;
  workflowMetrics: WorkflowEvaluationStat[];
  topIssues: { issue: string; count: number }[];
  componentStats: Record<string, { sampleCount: number; averageScore: number }>;
  dimensionStats: EvaluationDimensionStat[];
};

export type WorkflowEvaluationStat = {
  workflowTemplateId: string;
  workflowType: string;
  sampleCount: number;
  averageScore: number;
  successRate: number;
};

export type EvaluationDimensionStat = {
  dimension: "agent" | "model" | "rules" | string;
  name: string;
  sampleCount: number;
  averageScore: number;
  successRate: number;
};

export type LearningCase = {
  id: string;
  runId: string;
  evaluationId: string;
  caseType: string;
  projectId: string;
  workflowTemplateId?: string;
  workflowType: string;
  title: string;
  summary: string;
  path: string[];
  components: Record<string, unknown>;
  scores: Record<string, unknown>;
  tags: string[];
  retentionClass: string;
  createdAt: string;
};

export type LearningCaseHit = {
  case: LearningCase;
  similarity: number;
};


export type EvaluationProjectHealth = {
  projectId: string;
  totalRuns: number;
  successRate: number;
  averageScore: number;
  failedCount: number;
  pendingCount: number;
  proposalCount: number;
};

export type EvaluationProjectsResponse = {
  projects: EvaluationProjectHealth[];
};

export type EvaluationProposal = {
  id: string;
  projectId: string;
  sourceRunId: string;
  sourceEvaluationId: string;
  target: string;
  action: string;
  reason: string;
  severity: string;
  status: string;
  arbiterModel?: string;
  escalationModel?: string;
  needsEscalation?: boolean;
  escalationReasons?: string[];
  highRiskWorkflow?: boolean;
  failedTask?: boolean;
  createdAt: string;
  reviewedAt?: string;
  reviewNote?: string;
};

export type EvaluationProposalsResponse = {
  items: EvaluationProposal[];
};

export type EvaluationProposalReviewInput = {
  status: "approved" | "rejected" | "later" | "pending" | string;
  reviewNote?: string;
};

export type TokenUsageRoleRollup = {
  requestCount: number;
  inputTokens: number;
  cachedInputTokens: number;
  outputTokens: number;
  totalTokens: number;
  cacheHitRate: number;
  outputInputRatio: number;
  models: Record<string, number>;
};

export type WorkflowTokenUsage = {
  workflowRunId: string;
  requestCount: number;
  inputTokens: number;
  cachedInputTokens: number;
  outputTokens: number;
  totalTokens: number;
  cacheHitRate: number;
  outputInputRatio: number;
  roles: Record<string, TokenUsageRoleRollup>;
  updatedAt?: string;
};

export type RouteMetricRoleRollup = {
  requestCount: number;
  successCount: number;
  errorCount: number;
  errorRate: number;
  durationMs: number;
  averageRequestDurationMs: number;
  requestBytes: number;
  toolCount: number;
  inputItemCount: number;
  toolCallCount: number;
  models: Record<string, number>;
};

export type WorkflowRouteMetrics = {
  workflowRunId: string;
  requestCount: number;
  successCount: number;
  errorCount: number;
  errorRate: number;
  durationMs: number;
  averageRequestDurationMs: number;
  requestBytes: number;
  toolCount: number;
  inputItemCount: number;
  toolCallCount: number;
  roles: Record<string, RouteMetricRoleRollup>;
  updatedAt?: string;
};

export type StatisticsTaskItem = {
  runId: string;
  evaluationId?: string;
  projectId: string;
  taskTitle: string;
  workflowType: string;
  status: string;
  score: number;
  confidence: number;
  agent?: string;
  model?: string;
  rules?: string[];
  arbiterModel?: string;
  escalationModel?: string;
  needsEscalation?: boolean;
  escalationReasons?: string[];
  highRiskWorkflow?: boolean;
  failedTask?: boolean;
  tokenUsage?: WorkflowTokenUsage;
  routeMetrics?: WorkflowRouteMetrics;
  durationMs?: number;
  createdAt: string;
};

export type StatisticsTasksResponse = {
  items: StatisticsTaskItem[];
};

export type EvaluationEvidenceRow = {
  runId: string;
  taskRun?: TaskRun;
  evaluation?: Evaluation;
  statistics?: StatisticsTaskItem;
};

export type WorkflowRunRecord = {
  id: string;
  projectId: string;
  workflowTemplateId: string;
  workflowCopyID?: string;
  workflowType: string;
  taskTitle?: string;
  status: "running" | "completed" | "failed" | string;
  startedAt: string;
  endedAt?: string;
  durationMs?: number;
  context?: Record<string, unknown>;
  metrics?: Record<string, unknown>;
  evidence?: Record<string, unknown>;
  taskRunId?: string;
};

export type WorkflowRunStartInput = {
  projectId: string;
  workflowTemplateId?: string;
  workflowCopyId?: string;
  workflowType: string;
  taskTitle?: string;
  context?: Record<string, unknown>;
  metrics?: Record<string, unknown>;
  evidence?: Record<string, unknown>;
};

export type WorkflowRunFinishInput = {
  submittedStatus?: string;
  context?: Record<string, unknown>;
  metrics?: Record<string, unknown>;
  evidence?: Record<string, unknown>;
};

export type KnowledgeScanRule = {
  pattern: string;
  action: "include" | "exclude";
  category?: string;
  priority?: string;
  reason?: string;
  confidence?: number;
  hard?: boolean;
};

export type KnowledgeSyncProfile = {
  version: number;
  knowledge: {
    root: string;
    language: string;
    updateMode: "proposal";
    defaultBranch: string;
  };
  discovery: {
    strategy: string;
    generatedFromRevision: string;
    generatedAt: string;
    reviewed: boolean;
    reviewedBy: string;
  };
  scan: {
    source: "git-tracked";
    rules: KnowledgeScanRule[];
    limits: { maxFileSizeKB: number; maxFilesPerRun: number };
  };
  instructions: { requiredTopics: string[]; additional: string };
  codeGraph: {
    enabled: boolean;
    impactDepth: number;
    includeCallers: boolean;
    includeCallees: boolean;
    includeRelatedTests: boolean;
  };
  openWiki: { enabled: boolean; version: string };
  knowledgeGraph: {
    enabled: boolean;
    provider: "gbrain" | string;
    version: string;
    brain: string;
    sourceId: string;
    engine: "pglite" | string;
    transport: "stdio" | string;
    sync: {
      onProposalApplied: boolean;
      committedChangesOnly: boolean;
      retryMinutes: number;
      maxRetries: number;
    };
    export: {
      includeDomains: boolean;
      includeFeatures: boolean;
      includeCodeFacts: boolean;
      includeExternalEvidence: boolean;
    };
    query: {
      timeoutSeconds: number;
      maxResults: number;
      maxGraphDepth: number;
      shadowEnabled: boolean;
    };
    synthesis: { enabled: boolean; automatic: boolean };
    gaps: { enabled: boolean; createProposal: boolean };
  };
  schedule: { enabled: boolean; intervalMinutes: number; committedChangesOnly: boolean };
  ownership: { owners: string[]; requireApproval: boolean };
};

export type KnowledgeInventory = {
  revision: string;
  branch: string;
  trackedFiles: number;
  files: Array<{ path: string; category: string; size?: number }>;
  directoryStats: Array<{ path: string; fileCount: number; extensions: Record<string, number> }>;
  manifests: string[];
  readmes: string[];
  existingKnowledge: string[];
  extensions: Record<string, number>;
  codeGraphSummary?: string;
  truncated: boolean;
};

export type KnowledgeDiscoveryProposal = {
  revision: string;
  rules: KnowledgeScanRule[];
  requiredTopics: string[];
  uncertain: Array<{ path: string; reason: string; confidence: number }>;
  warnings: string[];
  aiRefined: boolean;
  inventory: KnowledgeInventory;
};

export type KnowledgeDiscoveryResponse = {
  proposal: KnowledgeDiscoveryProposal;
  profile: KnowledgeSyncProfile;
};

export type KnowledgeSyncState = {
  projectId: string;
  projectRoot: string;
  branch: string;
  lastProcessedCommit: string;
  lastKnowledgeHash: string;
  lastCheckedAt: string;
  lastSuccessfulAt: string;
  status: string;
  pendingProposalId?: string;
  compilerVersion: string;
  profileHash: string;
  lastError?: string;
};

export type KnowledgeSyncRun = {
  id: string;
  projectId: string;
  kind: string;
  status: string;
  stage?: string;
  stageMessage?: string;
  progress?: number;
  branch: string;
  baseRevision?: string;
  targetRevision: string;
  changeClass?: string;
  reasonCode?: string;
  reason?: string;
  nextAction?: string;
  proposalId?: string;
  warnings: string[];
  error?: string;
  startedAt: string;
  updatedAt?: string;
  endedAt?: string;
};

export type KnowledgeProposalChange = {
  path: string;
  action: string;
  before?: string;
  after?: string;
  managedBy?: string;
  selected: boolean;
};

export type KnowledgeProposal = {
  id: string;
  projectId: string;
  projectRoot: string;
  branch: string;
  baseRevision?: string;
  targetRevision: string;
  profileHash: string;
  compilerVersion: string;
  status: string;
  changes: KnowledgeProposalChange[];
  evidence: Array<{ kind: string; source: string; summary: string; paths: string[]; revision?: string }>;
  validation: { valid: boolean; errors: string[]; warnings: string[] };
  warnings: string[];
  createdAt: string;
  resolvedAt?: string;
};

export type KnowledgeSyncResult = {
  state: KnowledgeSyncState;
  run: KnowledgeSyncRun;
  proposal?: KnowledgeProposal;
  message: string;
};

export type KnowledgeSyncProfileResponse = {
  exists: boolean;
  profile: KnowledgeSyncProfile;
};

export type KnowledgeGraphSyncState = {
  projectId: string;
  projectRoot: string;
  sourceId: string;
  providerSourceId: string;
  status: "disabled" | "uninitialized" | "pending" | "syncing" | "ready" | "degraded" | string;
  branch?: string;
  lastSyncedRevision?: string;
  pendingRevision?: string;
  lastSourceHash?: string;
  documents: number;
  documentHashes: Record<string, string>;
  lastAttemptAt?: string;
  lastSuccessfulAt?: string;
  lastError?: string;
};

export type KnowledgeGraphSyncRun = {
  id: string;
  projectId: string;
  sourceId: string;
  providerSourceId: string;
  status: string;
  revision?: string;
  sourceHash?: string;
  documents: number;
  created: number;
  updated: number;
  deleted: number;
  unchanged: number;
  attempt: number;
  error?: string;
  startedAt: string;
  endedAt?: string;
};

export type KnowledgeGraphSyncResult = {
  sourceId: string;
  providerSourceId?: string;
  documents: number;
  created: number;
  updated: number;
  deleted: number;
  unchanged: number;
};

export type KnowledgeGraphSyncResponse = {
  result: KnowledgeGraphSyncResult;
  state: KnowledgeGraphSyncState;
};

export type KnowledgeGraphSearchHit = {
  id: string;
  sourceId?: string;
  path?: string;
  title?: string;
  snippet?: string;
  score: number;
  metadata?: Record<string, unknown>;
};

export type KnowledgeGraphShadowBaseline = {
  matchedProject: boolean;
  matchedDomain?: string;
  sourceIds?: string[];
  paths: string[];
  requiredPaths: string[];
  scopedPaths?: string[];
  scopedRequiredPaths?: string[];
  missingPaths: string[];
  latencyMs: number;
  tokenCount: number;
};

export type KnowledgeGraphShadowRun = {
  id: string;
  projectId: string;
  sourceId: string;
  sourceIds?: string[];
  providerSourceId: string;
  scope?: "project" | "group" | "all" | string;
  groupId?: string;
  status: "running" | "ready" | "degraded" | string;
  query: string;
  normalizedQuery: string;
  fts5: KnowledgeGraphShadowBaseline;
  gbrain: {
    matchedProject: boolean;
    matchedDomain?: string;
    paths: string[];
    scopedPaths?: string[];
    hits: KnowledgeGraphSearchHit[];
    duplicatePaths: string[];
    latencyMs: number;
    timedOut: boolean;
    processRestarts: number;
    error?: string;
  };
  comparison: {
    domainMatched: boolean;
    pathOverlap: string[];
    requiredMatchedPaths: string[];
    missingExpectedPaths: string[];
    duplicateDocuments: string[];
    requiredDocumentPrecision: number;
    expectedDocumentCoverage: number;
  };
  startedAt: string;
  endedAt: string;
};

export type KnowledgeGraphShadowSummary = {
  projectId: string;
  runs: number;
  succeeded: number;
  degraded: number;
  timeouts: number;
  timeoutRate: number;
  domainMatchRate: number;
  averageRequiredPrecision: number;
  averageExpectedCoverage: number;
  duplicatePageRate: number;
  fts5P95LatencyMs: number;
  gbrainP95LatencyMs: number;
  maxObservedRestartCount: number;
};

export type KnowledgeGraphShadowSearchResponse = {
  retrieval: KnowledgeRetrievalResult;
  shadow: KnowledgeGraphShadowRun;
};
