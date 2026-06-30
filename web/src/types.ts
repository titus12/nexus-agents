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

export type Project = {
  id: string;
  name: string;
  path: string;
  status: "ready" | "draft" | string;
  updatedAt: string;
  configSummary: ConfigSummary;
  repoKey?: string;
  localPath?: string;
  localConfigPath?: string;
  localConfigIgnored?: boolean;
};

export type ProjectInput = {
  name: string;
  path: string;
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

export type BootstrapData = {
  templateLibrary: TemplateLibrary;
  projects: Project[];
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
  id: "rtk" | "codegraph" | string;
  name: string;
  kind: "token_proxy" | "code_context" | "context_pack" | string;
  status: "ready" | "missing" | "unhealthy" | "unknown" | string;
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
  workflowCopyId?: string;
  workflowTemplateId?: string;
  workflowId?: string;
  workflowName?: string;
  workflowSummary?: string;
  workflowTrigger?: string;
  graph?: WorkflowGraph | null;
  context?: {
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
