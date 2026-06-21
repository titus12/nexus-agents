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
