<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import {
  addProjectCopyFromTemplate,
  createTemplate,
  createTaskRun,
  createProjectWorkflow,
  createWorkflow as createWorkflowApi,
  deleteProject as deleteProjectApi,
  deleteProjectWorkflow,
  deleteTemplate,
  deleteWorkflow as deleteWorkflowApi,
  detachProjectCopy,
  duplicateWorkflow as duplicateWorkflowApi,
  fetchBootstrap,
  fetchEvaluationProjects,
  fetchEvaluationProposals,
  fetchEvaluationSummary,
  fetchLearningCases,
  fetchStatisticsTasks,
  fetchInfrastructure,
  fetchInfrastructureCatalog,
  fetchLocalDirectories,
  fetchNativeLocalDirectory,
  fetchModelRoutes,
  fetchProjectConfig,
  fetchProjectWorkflowGraph,
  fetchSyncPreview,
  fetchWorkflowGraph,
  fetchWorkflows,
  installInfrastructure,
  importProject,
  rebuildLearningCaseIndex,
  resolveModelRoute,
  reviewEvaluationProposal,
  runPendingEvaluations,
  rescanProject,
  syncProjectCopy,
  updateTemplate,
  updateInfrastructure,
  updateWorkflow,
  updateWorkflowGraph,
  updateProjectWorkflowGraph,
} from "./api";
import { buildWorkflowRunDraft } from "./workflow-task-run";
import {
  addWorkflowEdge,
  addWorkflowNode,
  deleteWorkflowEdge,
  moveWorkflowNode,
  updateWorkflowNode,
  workflowNodePresets,
} from "./workflow-graph";
import { projectDeleteImpactMessage, upsertProject } from "./project-state";
import type {
  BootstrapData,
  EvaluationProjectHealth,
  EvaluationProposal,
  EvaluationSummary,
  LearningCase,
  InfrastructureItem,
  LocalDirectoryEntry,
  LocalDirectoriesResponse,
  ModelRoute,
  ModelRouteResolution,
  Project,
  ProjectCopy,
  ProjectCopyKind,
  ProjectInput,
  SyncStatus,
  TemplateInput,
  TemplateItem,
  TemplateKind,
  StatisticsTaskItem,
  TemplateLibrary,
  RouteMetricRoleRollup,
  TokenUsageRoleRollup,
  WorkflowRouteMetrics,
  WorkflowTokenUsage,
  WorkflowGraph,
  WorkflowEdge,
  WorkflowNode,
  WorkflowSummary,
} from "./types";
import type { WorkflowNodePreset, WorkflowNodePatch } from "./workflow-graph";

type Page =
  | "projects"
  | "agents"
  | "rules"
  | "skills"
  | "workflows"
  | "infrastructure"
  | "model-routes"
  | "statistics"
  | "evaluation"
  | "project-detail"
  | "project-agents"
  | "project-rules"
  | "project-skills"
  | "project-workflows";

type DrawerMode =
  | "template"
  | "project-copy"
  | "sync-preview"
  | "route"
  | "proxy-test"
  | "workflow-run"
  | "infrastructure"
  | "infrastructure-catalog"
  | null;

type WorkflowCard = {
  key: string;
  graphId: string;
  name: string;
  status: string;
  summary: string;
  nodeCount: number;
  edgeCount: number;
  copy?: ProjectCopy;
  evaluation?: {
    sampleCount: number;
    averageScore: number;
    successRate: number;
  };
};

const emptyLibrary: TemplateLibrary = {
  agents: [],
  rules: [],
  skills: [],
  workflows: [],
};

const activePage = ref<Page>("projects");
const selectedProjectId = ref("");
const selectedWorkflowTemplateId = ref("");
const selectedWorkflowId = ref("");
const selectedWorkflowCardKey = ref("");
const selectedNodeId = ref("");
const selectedEdgeKey = ref("");
const workflowEditorMode = ref(false);
const workflowConnectMode = ref(false);
const pendingConnectionFrom = ref("");
const workflowQuery = ref("");
const loading = ref(true);
const error = ref("");
const toast = ref("");
const drawerMode = ref<DrawerMode>(null);
const showImportModal = ref(false);
const showDirectoryBrowser = ref(false);
const directoryBrowser = ref<LocalDirectoriesResponse | null>(null);
const directoryBrowserLoading = ref(false);
const directoryBrowserError = ref("");
const directoryAddress = ref("");
const selectedDirectoryPath = ref("");
const nativePickerTimeout = 2500;

const templateLibrary = ref<TemplateLibrary>(emptyLibrary);
const projects = ref<Project[]>([]);
const projectConfigSets = ref<Record<string, ProjectCopy[]>>({});
const workflows = ref<WorkflowSummary[]>([]);
const workflowGraph = ref<WorkflowGraph | null>(null);
const workflowStageRef = ref<HTMLElement | null>(null);
const infrastructureItems = ref<InfrastructureItem[]>([]);
const availableInfrastructureItems = ref<InfrastructureItem[]>([]);
const modelRoutes = ref<ModelRoute[]>([]);
const evaluationSummary = ref<EvaluationSummary | null>(null);
const evaluationBusy = ref(false);
const learningCases = ref<LearningCase[]>([]);
const learningCaseBusy = ref(false);
const evaluationProjects = ref<EvaluationProjectHealth[]>([]);
const evaluationProposals = ref<EvaluationProposal[]>([]);
const selectedEvaluationProjectId = ref("");
const proposalBusyId = ref("");
const proposalReviewNote = ref("");
const statisticsRange = ref<"24h" | "7d" | "30d" | "all">("7d");
const statisticsView = ref<"failed" | "successful" | "top_scored" | "low_scored">("failed");
const statisticsTasks = ref<StatisticsTaskItem[]>([]);
const statisticsLoading = ref(false);

const selectedTemplateKind = ref<TemplateKind>("agents");
const selectedTemplate = ref<TemplateItem | null>(null);
const templateForm = ref<TemplateInput>({});
const selectedProjectCopy = ref<ProjectCopy | null>(null);
const syncPreview = ref<ProjectCopy[]>([]);
const selectedRoute = ref<ModelRoute | null>(null);
const proxyResult = ref<ModelRouteResolution | null>(null);
const proxyError = ref("");
const workflowRunPayload = ref<Record<string, unknown> | null>(null);
const workflowRunSubmitting = ref(false);
const workflowRunError = ref("");
const workflowRunResult = ref<{ id: string; workflowType: string; projectId: string } | null>(null);
const routeSaveFeedback = ref("");
const selectedInfrastructure = ref<InfrastructureItem | null>(null);
const infrastructureBusy = ref(false);
const infrastructureFeedback = ref("");
const WORKFLOW_NODE_WIDTH = 236;
const WORKFLOW_NODE_HEIGHT = 126;
const WORKFLOW_STAGE_PADDING = 20;
const WORKFLOW_DRAG_THRESHOLD = 4;
const workflowCanvasRef = ref<HTMLElement | null>(null);
const nodeDragState = ref<{
  nodeId: string;
  pointerId: number;
  offsetX: number;
  offsetY: number;
  startX: number;
  startY: number;
  dragging: boolean;
} | null>(null);

const importForm = ref<ProjectInput>({
  name: "",
  path: "",
});

const syncOrder: SyncStatus[] = ["synced", "template_updated", "project_modified", "diverged", "detached"];

const projectResourceKindByPage: Partial<Record<Page, ProjectCopyKind>> = {
  "project-agents": "agent",
  "project-rules": "rule",
  "project-skills": "skill",
  "project-workflows": "workflow",
};

const templateKindByPage: Partial<Record<Page, TemplateKind>> = {
  agents: "agents",
  rules: "rules",
  skills: "skills",
  workflows: "workflows",
};

const currentProject = computed(() => {
  return projects.value.find((project) => project.id === selectedProjectId.value) ?? projects.value[0];
});

const currentProjectCopies = computed(() => {
  if (!currentProject.value) return [];
  return projectConfigSets.value[currentProject.value.id] ?? [];
});

const currentProjectResourceCopies = computed(() => {
  const kind = projectResourceKindByPage[activePage.value];
  if (!kind) return currentProjectCopies.value;
  return currentProjectCopies.value.filter((copy) => copy.kind === kind);
});

const currentTemplateKind = computed(() => templateKindByPage[activePage.value] ?? "agents");

const currentTemplateItems = computed<TemplateItem[]>(() => {
  const kind = templateKindByPage[activePage.value];
  return kind ? templateLibrary.value[kind] : [];
});

const syncSummary = computed(() => {
  return syncOrder.map((status) => ({
    status,
    count: currentProjectCopies.value.filter((copy) => copy.status === status).length,
  }));
});

const focusCopies = computed(() => {
  const priority: Record<SyncStatus, number> = {
    template_updated: 0,
    diverged: 1,
    project_modified: 2,
    detached: 3,
    synced: 4,
  };
  return [...currentProjectCopies.value]
    .sort((left, right) => priority[left.status] - priority[right.status])
    .slice(0, 5);
});

const visibleWorkflowCards = computed<WorkflowCard[]>(() => {
  const query = workflowQuery.value.trim().toLowerCase();
  const cards =
    activePage.value === "project-workflows"
      ? currentProjectResourceCopies.value.map(projectCopyToWorkflowCard)
      : workflows.value.map((workflow) => ({
          key: workflow.id,
          graphId: workflow.id,
          name: workflow.name,
          status: workflow.status,
          summary: workflow.summary,
          nodeCount: workflow.nodeCount,
          edgeCount: workflow.edgeCount,
          evaluation: workflowEvaluationFor(workflow.id, workflow.id),
        }));

  if (!query) return cards;
  return cards.filter((card) => `${card.name} ${card.summary} ${card.status}`.toLowerCase().includes(query));
});

const selectedNode = computed<WorkflowNode | null>(() => {
  if (!workflowGraph.value || !selectedNodeId.value) return null;
  return workflowGraph.value.nodes.find((node) => node.id === selectedNodeId.value) ?? null;
});

const selectedEdge = computed<WorkflowEdge | null>(() => {
  if (!workflowGraph.value || !selectedEdgeKey.value) return null;
  return workflowGraph.value.edges.find((edge, index) => workflowEdgeKey(edge, index) === selectedEdgeKey.value) ?? null;
});

const selectedWorkflowCard = computed(() => {
  return visibleWorkflowCards.value.find((card) => card.key === selectedWorkflowCardKey.value) ?? null;
});

const breadcrumb = computed(() => {
  if (activePage.value.startsWith("project-") && currentProject.value) {
    const leaf =
      activePage.value === "project-detail"
        ? "Overview"
        : activePage.value.replace("project-", "").replace(/^\w/, (char) => char.toUpperCase());
    return ["Projects", currentProject.value.name, leaf];
  }
  if (activePage.value === "infrastructure") return ["System", "Infrastructure"];
  if (activePage.value === "model-routes") return ["System", "Model Proxy"];
  if (activePage.value === "statistics") return ["System", "Statistics"];
  if (activePage.value === "evaluation") return ["System", "Evaluation"];
  if (activePage.value === "projects") return ["Projects"];
  return ["Template Library", pageTitle(activePage.value)];
});

const dimensionStatsByType = computed(() => {
  const groups: Record<string, { dimension: string; name: string; sampleCount: number; averageScore: number; successRate: number }[]> = {
    agent: [],
    model: [],
    rules: [],
  };
  for (const stat of evaluationSummary.value?.dimensionStats ?? []) {
    if (stat.dimension === "agent" || stat.dimension === "model" || stat.dimension === "rules") {
      groups[stat.dimension].push(stat);
    }
  }
  return groups;
});

const componentStatsList = computed(() => {
  return Object.entries(evaluationSummary.value?.componentStats ?? {}).map(([component, stat]) => ({
    component,
    sampleCount: stat.sampleCount,
    averageScore: stat.averageScore,
  }));
});

const statisticsLineChart = computed(() => {
  const items = statisticsTasks.value.slice(0, 8).reverse();
  const series = items.length > 0 ? items.map((item) => Math.round(item.score || 0)) : [62, 68, 72, 79, 84, 88];
  const labels =
    items.length > 0
      ? items.map((item) => formatShortDate(item.createdAt))
      : ["-5", "-4", "-3", "-2", "-1", "Now"];
  const width = 640;
  const height = 220;
  const padding = 28;
  const min = Math.min(50, ...series);
  const max = Math.max(100, ...series);
  const points = series.map((value, index) => {
    const x = padding + (index * (width - padding * 2)) / Math.max(1, series.length - 1);
    const y = height - padding - ((value - min) * (height - padding * 2)) / Math.max(1, max - min);
    return { x, y, value, label: labels[index] };
  });
  return {
    width,
    height,
    points,
    path: points.map((point, index) => `${index === 0 ? "M" : "L"} ${point.x.toFixed(1)} ${point.y.toFixed(1)}`).join(" "),
  };
});

const selectedEvaluationProject = computed(() => {
  return evaluationProjects.value.find((item) => item.projectId === selectedEvaluationProjectId.value) ?? null;
});

const evaluationProjectOptions = computed(() => {
  return evaluationProjects.value.map((item) => ({
    ...item,
    pendingBadge: `${item.pendingCount} pending`,
  }));
});

const visibleLearningCases = computed(() => (Array.isArray(learningCases.value) ? learningCases.value.slice(0, 4) : []));

const selectedProjectProposals = computed(() => {
  if (!selectedEvaluationProjectId.value) return evaluationProposals.value;
  return evaluationProposals.value.filter((item) => item.projectId === selectedEvaluationProjectId.value);
});


const selectedEvaluationProjectTasks = computed(() => {
  if (!selectedEvaluationProjectId.value) return [] as StatisticsTaskItem[];
  return statisticsTasks.value
    .filter((item) => item.projectId === selectedEvaluationProjectId.value)
    .slice()
    .sort((left, right) => new Date(right.createdAt).getTime() - new Date(left.createdAt).getTime());
});

function learningCaseTags(item: LearningCase): string[] {
  return Array.isArray(item.tags) ? item.tags.slice(0, 3) : [];
}

function formatShortDate(value: string): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${String(date.getMonth() + 1).padStart(2, "0")}/${String(date.getDate()).padStart(2, "0")}`;
}

function formatDateTime(value: string): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}


function asWorkflowTokenUsage(value: unknown): WorkflowTokenUsage | null {
  if (!value || typeof value !== "object") return null;
  return value as WorkflowTokenUsage;
}

function asWorkflowRouteMetrics(value: unknown): WorkflowRouteMetrics | null {
  if (!value || typeof value !== "object") return null;
  return value as WorkflowRouteMetrics;
}

function formatNumber(value: number | undefined | null): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "-";
  return new Intl.NumberFormat().format(Math.round(value));
}

function formatDurationMinutes(value: number | undefined | null): string {
  if (typeof value !== "number" || Number.isNaN(value) || value <= 0) return "-";
  if (value < 1000) return `${Math.round(value)} ms`;
  if (value < 60_000) return `${(value / 1000).toFixed(1)} s`;
  return `${(value / 60_000).toFixed(1)} min`;
}

function roleMetricEntries(record: Record<string, unknown> | undefined | null): Array<[string, unknown]> {
  if (!record || typeof record !== "object") return [];
  return Object.entries(record).sort((left, right) => left[0].localeCompare(right[0]));
}

function topModelsLabel(models: Record<string, number> | undefined | null): string {
  if (!models || typeof models !== "object") return "-";
  const entries = Object.entries(models).sort((left, right) => right[1] - left[1]).slice(0, 2);
  if (entries.length === 0) return "-";
  return entries.map(([model, count]) => `${model} ?${count}`).join(", ");
}

function statisticsViewLabel(view: string): string {
  return {
    failed: "Failed",
    successful: "Successful",
    top_scored: "Top Scored",
    low_scored: "Low Scored",
  }[view] ?? view;
}

function proposalSeverityClass(severity: string): string {
  switch ((severity || "").toLowerCase()) {
    case "high":
      return "chip-red";
    case "medium":
      return "chip-orange";
    default:
      return "chip-teal";
  }
}

function escalationFlags(item: { needsEscalation?: boolean; highRiskWorkflow?: boolean; failedTask?: boolean; escalationReasons?: string[] }): string[] {
  const flags: string[] = [];
  if (item.needsEscalation) flags.push("needs-escalation");
  if (item.highRiskWorkflow) flags.push("high-risk");
  if (item.failedTask) flags.push("failed-task");
  for (const reason of item.escalationReasons ?? []) {
    if (!flags.includes(reason)) flags.push(reason);
  }
  return flags;
}

function boolLabel(value?: boolean): string {
  return value ? "Yes" : "No";
}

function proposalStatusClass(status: string): string {
  switch ((status || "").toLowerCase()) {
    case "approved":
      return "chip-green";
    case "rejected":
      return "chip-red";
    case "later":
      return "chip-purple";
    default:
      return "chip-orange";
  }
}

const templateDrawerReadOnly = computed(() => {
  return selectedTemplate.value?.kind === "rule" || selectedTemplate.value?.kind === "skill";
});

function pageTitle(page: Page): string {
  const titles: Record<Page, string> = {
    projects: "Projects",
    agents: "Agents",
    rules: "Rules",
    skills: "Skills",
    workflows: "Workflows",
    infrastructure: "Infrastructure",
    "model-routes": "Model Proxy",
    statistics: "Statistics",
    evaluation: "Evaluation",
    "project-detail": "Overview",
    "project-agents": "Agents",
    "project-rules": "Rules",
    "project-skills": "Skills",
    "project-workflows": "Workflows",
  };
  return titles[page];
}

function kindLabel(kind: string): string {
  return {
    agent: "Agent",
    agents: "Agent",
    rule: "Rule",
    rules: "Rule",
    skill: "Skill",
    skills: "Skill",
    workflow: "Workflow",
    workflows: "Workflow",
  }[kind] ?? kind;
}

function projectCopyToWorkflowCard(copy: ProjectCopy): WorkflowCard {
  const graphId = workflowGraphIdForCopy(copy);
  const summary = workflows.value.find((workflow) => workflow.id === graphId);
  const template = templateLibrary.value.workflows.find((workflow) => workflow.id === graphId);
  const isSelectedProjectGraph = activePage.value === "project-workflows" && selectedWorkflowCardKey.value === copy.id;
  return {
    key: copy.id,
    graphId,
    name: summary?.name ?? template?.name ?? copy.name,
    status: copy.status,
    summary: summary?.summary ?? "Project workflow copy with manual sync metadata.",
    nodeCount: isSelectedProjectGraph && workflowGraph.value ? workflowGraph.value.nodes.length : summary?.nodeCount ?? 0,
    edgeCount: isSelectedProjectGraph && workflowGraph.value ? workflowGraph.value.edges.length : summary?.edgeCount ?? 0,
    evaluation: workflowEvaluationFor(graphId, graphId),
    copy,
  };
}

function workflowEvaluationFor(templateId: string, workflowType: string) {
  const metrics = evaluationSummary.value?.workflowMetrics ?? [];
  const found = metrics.find((metric) => metric.workflowTemplateId === templateId || metric.workflowType === workflowType);
  if (!found) return undefined;
  return {
    sampleCount: found.sampleCount,
    averageScore: found.averageScore,
    successRate: found.successRate,
  };
}

function workflowGraphIdForCopy(copy: ProjectCopy): string {
  return copy.origin?.templateId ?? workflowTemplateIdForProjectCopy(copy) ?? "";
}

function workflowTemplateIdForProjectCopy(copy: ProjectCopy): string | undefined {
  if (copy.kind !== "workflow") return undefined;
  const candidates = new Set(
    [copy.id, copy.name, copy.path.split(/[\\/]/).pop() ?? ""]
      .map((value) => value.replace(/\.md$/i, "").trim().toLowerCase())
      .filter(Boolean),
  );
  return templateLibrary.value.workflows.find((workflow) => {
    const values = [workflow.id, workflow.slug, workflow.name]
      .map((value) => value.replace(/\.md$/i, "").trim().toLowerCase())
      .filter(Boolean);
    return values.some((value) => candidates.has(value));
  })?.id;
}

function templateUsageCount(templateId: string): number {
  return Object.values(projectConfigSets.value)
    .flat()
    .filter((copy) => copy.origin?.templateId === templateId).length;
}

function templateFooterChips(item: TemplateItem): string[] {
  if (item.kind === "agent") {
    return [item.modelTier ?? "model route", `${item.rulesCount ?? 0} rules`, `${item.skillsCount ?? 0} skills`];
  }
  if (item.kind === "skill") {
    const agentCount = item.applicableAgents?.length ?? 0;
    return agentCount > 0 ? [`${agentCount} roles`, `v${item.version}`] : [`v${item.version}`];
  }
  if (item.kind === "rule") {
    return [`v${item.version}`];
  }
  if (item.kind === "workflow") {
    return [`${templateUsageCount(item.id)} projects`, `v${item.version}`];
  }
  return [`v${item.version}`];
}

const systemAgentIds = new Set(["workflow-evaluator", "learning-curator", "model-arbiter", "unity-asset-safety-evaluator", "unity-regression-evaluator", "unity-workflow-evaluator"]);

function agentModelClass(item: TemplateItem): string {
  if (item.kind !== "agent") return "";
  return [modelClassForTier(item.modelTier), systemAgentIds.has(item.id) ? "system-agent" : ""].filter(Boolean).join(" ");
}

function projectCopyModelClass(copy: ProjectCopy): string {
  if (copy.kind !== "agent") return "";
  return modelClassForTier(templateForProjectCopy(copy)?.modelTier);
}

function modelClassForTier(modelTier?: string): string {
  const normalized = (modelTier ?? "").toLowerCase();
  if (normalized.includes("mini") || normalized.includes("flash")) return "model-mini";
  if (normalized.includes("5.4") || normalized.includes("pro") || normalized.includes("deepseek") || normalized.includes("glm")) return "model-pro";
  return "model-direct";
}

function templateForProjectCopy(copy: ProjectCopy): TemplateItem | undefined {
  const kindMap: Record<ProjectCopyKind, TemplateKind> = {
    agent: "agents",
    rule: "rules",
    skill: "skills",
    workflow: "workflows",
  };
  const templateId = copy.origin?.templateId ?? (copy.kind === "workflow" ? workflowTemplateIdForProjectCopy(copy) : undefined);
  if (!templateId) return undefined;
  return templateLibrary.value[kindMap[copy.kind]].find((item) => item.id === templateId);
}

function projectCopySummary(copy: ProjectCopy): string {
  const template = templateForProjectCopy(copy);
  if (template?.summary) return template.summary;
  if (copy.status === "template_updated") return "Template has updates available. Open details to review the diff and sync manually.";
  if (copy.status === "project_modified") return "Project copy has local edits. Open details to inspect the local change before syncing.";
  if (copy.status === "diverged") return "Template and project copy both changed. Open details to decide the manual sync path.";
  if (copy.status === "detached") return "Project-only configuration copy without a template origin.";
  return "Synced project copy that follows its template origin.";
}

function projectCopyMeta(copy: ProjectCopy): string[] {
  const origin = copy.origin?.templateId ? `from ${copy.origin.templateId}` : "project-only";
  const base = copy.origin?.baseVersion ? `base v${copy.origin.baseVersion}` : "no base";
  return [origin, base, copy.syncMode];
}

function projectCopyFooterChips(copy: ProjectCopy): string[] {
  const template = templateForProjectCopy(copy);
  if (template && copy.kind !== "workflow") {
    return templateFooterChips(template);
  }
  return [`local v${copy.localVersion}`, copy.syncMode];
}

function workflowEdgePath(edge: WorkflowEdge): string {
  const endpoints = workflowEdgeEndpoints(edge);
  if (!endpoints) return "";
  const { startX, startY, endX, endY, startSide, endSide } = endpoints;
  if ((startSide === "bottom" && endSide === "top") || (startSide === "top" && endSide === "bottom")) {
    const midY = startY + (endY - startY) / 2;
    return `M ${startX} ${startY} L ${startX} ${midY} L ${endX} ${midY} L ${endX} ${endY}`;
  }
  if ((startSide === "right" && endSide === "left") || (startSide === "left" && endSide === "right")) {
    const midX = startX + (endX - startX) / 2;
    return `M ${startX} ${startY} L ${midX} ${startY} L ${midX} ${endY} L ${endX} ${endY}`;
  }
  const horizontalGap = Math.abs(endX - startX);
  const verticalGap = Math.abs(endY - startY);
  if (verticalGap >= horizontalGap) {
    const midY = startY + (endY - startY) / 2;
    return `M ${startX} ${startY} L ${startX} ${midY} L ${endX} ${midY} L ${endX} ${endY}`;
  }
  const midX = startX + (endX - startX) / 2;
  return `M ${startX} ${startY} L ${midX} ${startY} L ${midX} ${endY} L ${endX} ${endY}`;
}

function workflowEdgeLabelX(edge: WorkflowEdge): number {
  const endpoints = workflowEdgeEndpoints(edge);
  if (!endpoints) return 0;
  return (endpoints.startX + endpoints.endX) / 2;
}

function workflowEdgeLabelY(edge: WorkflowEdge): number {
  const endpoints = workflowEdgeEndpoints(edge);
  if (!endpoints) return 0;
  const horizontalGap = Math.abs(endpoints.endX - endpoints.startX);
  const verticalGap = Math.abs(endpoints.endY - endpoints.startY);
  return verticalGap >= horizontalGap
    ? endpoints.startY + (endpoints.endY - endpoints.startY) / 2 - 10
    : (endpoints.startY + endpoints.endY) / 2 - 10;
}

function workflowEdgeEndpoints(edge: WorkflowEdge) {
  const nodes = workflowGraph.value?.nodes ?? [];
  const from = nodes.find((node) => node.id === edge.from);
  const to = nodes.find((node) => node.id === edge.to);
  if (!from || !to) return null;
  const fromCenterX = from.x + WORKFLOW_NODE_WIDTH / 2;
  const fromCenterY = from.y + WORKFLOW_NODE_HEIGHT / 2;
  const toCenterX = to.x + WORKFLOW_NODE_WIDTH / 2;
  const toCenterY = to.y + WORKFLOW_NODE_HEIGHT / 2;
  const dx = toCenterX - fromCenterX;
  const dy = toCenterY - fromCenterY;

  let startSide: "top" | "bottom" | "left" | "right";
  let endSide: "top" | "bottom" | "left" | "right";
  const preferredAnchors = workflowPreferredAnchors(edge);
  if (preferredAnchors) {
    startSide = preferredAnchors.startSide;
    endSide = preferredAnchors.endSide;
  } else if (Math.abs(dy) >= Math.abs(dx)) {
    startSide = dy >= 0 ? "bottom" : "top";
    endSide = dy >= 0 ? "top" : "bottom";
  } else {
    startSide = dx >= 0 ? "right" : "left";
    endSide = dx >= 0 ? "left" : "right";
  }

  const start = workflowNodeAnchor(from, startSide);
  const end = workflowNodeAnchor(to, endSide);
  return {
    startX: start.x,
    startY: start.y,
    endX: end.x,
    endY: end.y,
    startSide,
    endSide,
  };
}

function workflowPreferredAnchors(edge: WorkflowEdge):
  | { startSide: "top" | "bottom" | "left" | "right"; endSide: "top" | "bottom" | "left" | "right" }
  | null {
  const verticalFlow = new Set([
    "start->owner",
    "owner->reproduce",
    "reproduce->evidence_gate",
    "evidence_gate->diagnose",
    "diagnose->patch",
    "patch->verify",
    "verify->loop_control",
    "loop_control->capsule",
  ]);
  const rightBranch = new Set([
    "verify->review",
    "review->loop_control",
    "loop_control->exit_gate",
    "exit_gate->submit",
  ]);
  const leftBranch = new Set([
    "evidence_gate->probe",
    "probe->exit_gate",
    "capsule->diagnose",
  ]);
  const key = `${edge.from}->${edge.to}`;
  if (verticalFlow.has(key)) {
    return { startSide: "bottom", endSide: "top" };
  }
  if (rightBranch.has(key)) {
    return { startSide: "right", endSide: "left" };
  }
  if (leftBranch.has(key)) {
    return { startSide: "left", endSide: "right" };
  }
  return null;
}

function workflowNodeAnchor(node: WorkflowNode, side: "top" | "bottom" | "left" | "right") {
  switch (side) {
    case "top":
      return { x: node.x + WORKFLOW_NODE_WIDTH / 2, y: node.y };
    case "bottom":
      return { x: node.x + WORKFLOW_NODE_WIDTH / 2, y: node.y + WORKFLOW_NODE_HEIGHT };
    case "left":
      return { x: node.x, y: node.y + WORKFLOW_NODE_HEIGHT / 2 };
    case "right":
    default:
      return { x: node.x + WORKFLOW_NODE_WIDTH, y: node.y + WORKFLOW_NODE_HEIGHT / 2 };
  }
}

const workflowStageWidth = computed(() => {
  const nodes = workflowGraph.value?.nodes ?? [];
  const maxRight = nodes.reduce((max, node) => Math.max(max, node.x + WORKFLOW_NODE_WIDTH), 0);
  return Math.max(1180, maxRight + 80);
});

const workflowStageHeight = computed(() => {
  const nodes = workflowGraph.value?.nodes ?? [];
  const maxBottom = nodes.reduce((max, node) => Math.max(max, node.y + WORKFLOW_NODE_HEIGHT), 0);
  return Math.max(960, maxBottom + 100);
});

function workflowEdgeKey(edge: WorkflowEdge, index: number): string {
  return `${edge.from}->${edge.to}:${index}`;
}

function workflowNodeDisplayName(nodeId: string): string {
  const node = workflowGraph.value?.nodes.find((item) => item.id === nodeId);
  return node?.label || nodeId;
}

function workflowEdgeDisplay(edge: WorkflowEdge): string {
  const from = workflowNodeDisplayName(edge.from);
  const to = workflowNodeDisplayName(edge.to);
  return edge.label ? `${from} --${edge.label}--> ${to}` : `${from} -> ${to}`;
}

function selectWorkflowEdge(edge: WorkflowEdge, index: number) {
  selectedNodeId.value = "";
  selectedEdgeKey.value = workflowEdgeKey(edge, index);
}

function toggleConnectMode() {
  workflowConnectMode.value = !workflowConnectMode.value;
  pendingConnectionFrom.value = "";
  showToast(workflowConnectMode.value ? "连线模式：先点起点节点，再点目标节点。" : "已退出连线模式");
}

function handleWorkflowNodeClick(node: WorkflowNode) {
  if (workflowEditorMode.value && workflowConnectMode.value) {
    if (!pendingConnectionFrom.value) {
      pendingConnectionFrom.value = node.id;
      selectedNodeId.value = node.id;
      selectedEdgeKey.value = "";
      showToast(`连线起点：${node.label}`);
      return;
    }
    if (workflowGraph.value) {
      workflowGraph.value = addWorkflowEdge(workflowGraph.value, pendingConnectionFrom.value, node.id, "next");
    }
    pendingConnectionFrom.value = "";
    workflowConnectMode.value = false;
    selectedNodeId.value = node.id;
    selectedEdgeKey.value = "";
    showToast("已创建连线");
    return;
  }
  selectedNodeId.value = node.id;
  selectedEdgeKey.value = "";
}

function startWorkflowNodeDrag(event: PointerEvent, node: WorkflowNode) {
  selectedNodeId.value = node.id;
  selectedEdgeKey.value = "";
  if (!workflowEditorMode.value || workflowConnectMode.value || !workflowStageRef.value) return;
  const point = workflowStagePoint(event);
  nodeDragState.value = {
    nodeId: node.id,
    pointerId: event.pointerId,
    offsetX: point.x - node.x,
    offsetY: point.y - node.y,
    startX: point.x,
    startY: point.y,
    dragging: false,
  };
  (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
}

function dragWorkflowNode(event: PointerEvent) {
  if (!nodeDragState.value || !workflowGraph.value) return;
  const point = workflowStagePoint(event);
  const movedX = point.x - nodeDragState.value.startX;
  const movedY = point.y - nodeDragState.value.startY;
  if (!nodeDragState.value.dragging && Math.hypot(movedX, movedY) < WORKFLOW_DRAG_THRESHOLD) {
    return;
  }
  nodeDragState.value.dragging = true;
  const nextX = clamp(
    point.x - nodeDragState.value.offsetX,
    WORKFLOW_STAGE_PADDING,
    workflowStageWidth.value - WORKFLOW_NODE_WIDTH - WORKFLOW_STAGE_PADDING,
  );
  const nextY = clamp(
    point.y - nodeDragState.value.offsetY,
    WORKFLOW_STAGE_PADDING,
    workflowStageHeight.value - WORKFLOW_NODE_HEIGHT - WORKFLOW_STAGE_PADDING,
  );
  workflowGraph.value = moveWorkflowNode(workflowGraph.value, nodeDragState.value.nodeId, nextX, nextY);
}

function endWorkflowNodeDrag(event: PointerEvent) {
  if (!nodeDragState.value) return;
  try {
    (event.currentTarget as HTMLElement).releasePointerCapture(nodeDragState.value.pointerId);
  } catch {
    // Pointer capture may already be released when the pointer leaves the button.
  }
  nodeDragState.value = null;
}

function workflowStagePoint(event: PointerEvent) {
  const rect = workflowStageRef.value?.getBoundingClientRect();
  const canvasRect = workflowCanvasRef.value?.getBoundingClientRect();
  if (!rect || !canvasRect) return { x: 0, y: 0 };
  return {
    x: event.clientX - canvasRect.left + (workflowCanvasRef.value?.scrollLeft ?? 0),
    y: event.clientY - canvasRect.top + (workflowCanvasRef.value?.scrollTop ?? 0),
  };
}

function updateSelectedWorkflowNode(patch: WorkflowNodePatch) {
  if (!workflowGraph.value || !selectedNodeId.value) return;
  workflowGraph.value = updateWorkflowNode(workflowGraph.value, selectedNodeId.value, patch);
}

function addNodeFromPalette(preset: WorkflowNodePreset) {
  if (!workflowGraph.value) return;
  const offset = workflowGraph.value.nodes.length * 18;
  workflowGraph.value = addWorkflowNode(workflowGraph.value, preset, 90 + (offset % 260), 80 + (offset % 220));
  selectedNodeId.value = workflowGraph.value.nodes[workflowGraph.value.nodes.length - 1]?.id ?? "";
  selectedEdgeKey.value = "";
}

function deleteSelectedWorkflowEdge() {
  if (!workflowGraph.value || !selectedEdge.value) return;
  workflowGraph.value = deleteWorkflowEdge(workflowGraph.value, selectedEdge.value);
  selectedEdgeKey.value = "";
  showToast("已删除连线");
}

function updateSelectedWorkflowEdgeLabel(label: string) {
  if (!workflowGraph.value || !selectedEdgeKey.value) return;
  workflowGraph.value = {
    ...workflowGraph.value,
    edges: workflowGraph.value.edges.map((edge, index) =>
      workflowEdgeKey(edge, index) === selectedEdgeKey.value ? { ...edge, label } : edge,
    ),
  };
}

function inputValue(event: Event): string {
  return (event.target as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement).value;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function projectCopyCount(kind: ProjectCopyKind): number {
  return currentProjectCopies.value.filter((copy) => copy.kind === kind).length;
}

function statusTone(status: string): string {
  if (status === "synced" || status === "ready" || status === "active") return "green";
  if (status === "template_updated") return "purple";
  if (status === "project_modified" || status === "draft") return "orange";
  if (status === "diverged") return "red";
  return "gray";
}

function kindIcon(kind: string): string {
  return (
    {
      agent: "asset-icon-employee",
      agents: "asset-icon-employee",
      rule: "asset-icon-handbook",
      rules: "asset-icon-handbook",
      skill: "asset-icon-manual",
      skills: "asset-icon-manual",
      workflow: "asset-icon-process",
      workflows: "asset-icon-process",
    }[kind] ?? "asset-icon-process"
  );
}

function openPage(page: Page) {
  activePage.value = page;
  drawerMode.value = null;
  if (page !== "workflows") {
    workflowEditorMode.value = false;
    workflowConnectMode.value = false;
    pendingConnectionFrom.value = "";
  }
  if (page === "workflows") {
    void selectWorkflowCard({
      key: selectedWorkflowId.value,
      graphId: selectedWorkflowId.value,
      name: "",
      status: "",
      summary: "",
      nodeCount: 0,
      edgeCount: 0,
    });
  }
}

function openProject(projectId: string) {
  selectedProjectId.value = projectId;
  activePage.value = "project-detail";
  drawerMode.value = null;
  workflowEditorMode.value = false;
  workflowConnectMode.value = false;
  pendingConnectionFrom.value = "";
}

async function openProjectResource(projectId: string, page: Page) {
  selectedProjectId.value = projectId;
  activePage.value = page;
  drawerMode.value = null;
  workflowEditorMode.value = false;
  workflowConnectMode.value = false;
  pendingConnectionFrom.value = "";
  const copies = await fetchProjectConfig(projectId);
  projectConfigSets.value = {
    ...projectConfigSets.value,
    [projectId]: copies,
  };
  if (page === "project-workflows") {
    const card = copies.filter((copy) => copy.kind === "workflow").map(projectCopyToWorkflowCard)[0];
    if (card) await selectWorkflowCard(card);
  }
}

async function selectWorkflowCard(card: WorkflowCard) {
  selectedWorkflowCardKey.value = card.key;
  selectedWorkflowId.value = card.graphId;
  selectedNodeId.value = "";
  selectedEdgeKey.value = "";
  pendingConnectionFrom.value = "";
  workflowConnectMode.value = false;
  try {
    workflowGraph.value =
      activePage.value === "project-workflows" && currentProject.value && card.copy
        ? await fetchProjectWorkflowGraph(currentProject.value.id, card.copy.id)
        : await fetchWorkflowGraph(card.graphId);
  } catch {
    workflowGraph.value = null;
    showToast(`未找到工作流画布：${card.graphId}`);
  }
}

function openTemplateDrawer(kind: TemplateKind, item: TemplateItem) {
  selectedTemplateKind.value = kind;
  selectedTemplate.value = item;
  templateForm.value = {
    name: item.name,
    slug: item.slug,
    summary: item.summary,
    entry: item.entry,
    files: item.files ? [...item.files] : [],
    status: item.status,
  };
  drawerMode.value = "template";
}

function openProjectCopyDrawer(copy: ProjectCopy) {
  selectedProjectCopy.value = copy;
  drawerMode.value = "project-copy";
}

async function openSyncPreviewDrawer() {
  if (!currentProject.value) return;
  syncPreview.value = await fetchSyncPreview(currentProject.value.id);
  drawerMode.value = "sync-preview";
}

function openRouteDrawer(route: ModelRoute) {
  selectedRoute.value = route;
  proxyResult.value = null;
  proxyError.value = "";
  routeSaveFeedback.value = "";
  drawerMode.value = "route";
}

function openInfrastructureDrawer(item: InfrastructureItem) {
  selectedInfrastructure.value = item;
  infrastructureFeedback.value = item.output ?? "";
  drawerMode.value = "infrastructure";
}

function openInfrastructureCatalog() {
  selectedInfrastructure.value = availableInfrastructureItems.value[0] ?? null;
  infrastructureFeedback.value = selectedInfrastructure.value?.output ?? "";
  drawerMode.value = "infrastructure-catalog";
}

function selectInfrastructureCandidate(candidate: InfrastructureItem) {
  selectedInfrastructure.value = candidate;
  infrastructureFeedback.value = candidate.output ?? "";
}

function replaceInfrastructureItem(item: InfrastructureItem) {
  const existing = infrastructureItems.value.some((current) => current.id === item.id);
  infrastructureItems.value = existing
    ? infrastructureItems.value.map((current) => (current.id === item.id ? item : current))
    : [...infrastructureItems.value, item];
  availableInfrastructureItems.value = availableInfrastructureItems.value.map((current) => (current.id === item.id ? item : current));
  selectedInfrastructure.value = item;
  infrastructureFeedback.value = item.output ?? "";
}

async function installSelectedInfrastructure() {
  if (!selectedInfrastructure.value) return;
  const target = selectedInfrastructure.value;
  if (!window.confirm(`Install ${target.name} on this Nexus Agents host?`)) return;
  infrastructureBusy.value = true;
  infrastructureFeedback.value = `Installing ${target.name}...`;
  try {
    replaceInfrastructureItem(await installInfrastructure(target.id));
    drawerMode.value = "infrastructure";
    showToast(`Installed ${target.name}`);
  } catch (err) {
    infrastructureFeedback.value = err instanceof Error ? err.message : "Infrastructure install failed.";
  } finally {
    infrastructureBusy.value = false;
  }
}

async function updateSelectedInfrastructure() {
  if (!selectedInfrastructure.value) return;
  const target = selectedInfrastructure.value;
  if (!window.confirm(`Update ${target.name} on this Nexus Agents host?`)) return;
  infrastructureBusy.value = true;
  infrastructureFeedback.value = `Updating ${target.name}...`;
  try {
    replaceInfrastructureItem(await updateInfrastructure(target.id));
    showToast(`Updated ${target.name}`);
  } catch (err) {
    infrastructureFeedback.value = err instanceof Error ? err.message : "Infrastructure update failed.";
  } finally {
    infrastructureBusy.value = false;
  }
}

function openInfrastructureGitHub() {
  if (!selectedInfrastructure.value) return;
  window.open(selectedInfrastructure.value.githubUrl, "_blank", "noopener,noreferrer");
}

async function openProxyTest(route: ModelRoute) {
  selectedRoute.value = route;
  proxyResult.value = null;
  proxyError.value = "";
  drawerMode.value = "proxy-test";
  await runProxyTest();
}

function closeDrawer() {
  drawerMode.value = null;
}

async function refreshBootstrap() {
  applyBootstrap(await fetchBootstrap());
}

async function rescanCurrentProject() {
  if (!currentProject.value) return;
  const result = await rescanProject(currentProject.value.id);
  projects.value = upsertProject(projects.value, result.project);
  projectConfigSets.value = {
    ...projectConfigSets.value,
    [result.project.id]: result.copies,
  };
  selectedProjectId.value = result.project.id;
  showToast(`已重新扫描项目：${result.project.name}`);
}

function replaceProjectCopy(projectId: string, copy: ProjectCopy) {
  const copies = projectConfigSets.value[projectId] ?? [];
  projectConfigSets.value = {
    ...projectConfigSets.value,
    [projectId]: copies.map((item) => (item.id === copy.id ? copy : item)),
  };
  selectedProjectCopy.value = copy;
}

async function createTemplateForCurrentPage() {
  const kind = currentTemplateKind.value;
  const label = kindLabel(kind);
  const item = await createTemplate(kind, {
    name: `new-${label.toLowerCase()}-${currentTemplateItems.value.length + 1}`,
    summary: `New ${label} template created from Nexus Agents.`,
    entry: kind === "skills" ? "templates/skills/new-skill.md" : kind === "workflows" ? "templates/workflows/new-workflow.md" : `templates/${kind}/new.md`,
    files: kind === "skills" ? ["templates/skills/new-skill.md"] : kind === "workflows" ? ["templates/workflows/new-workflow.md"] : [],
  });
  templateLibrary.value = {
    ...templateLibrary.value,
    [kind]: [...templateLibrary.value[kind], item],
  };
  showToast(`已创建 ${label} 模板：${item.name}`);
  openTemplateDrawer(kind, item);
}

async function saveTemplateDrawer() {
  if (!selectedTemplate.value) return;
  if (templateDrawerReadOnly.value) {
    showToast("Rule / Skill 详情在 V1 中只读，修改请新建模板。");
    return;
  }
  const updated = await updateTemplate(selectedTemplateKind.value, selectedTemplate.value.id, templateForm.value);
  templateLibrary.value = {
    ...templateLibrary.value,
    [selectedTemplateKind.value]: templateLibrary.value[selectedTemplateKind.value].map((item) =>
      item.id === updated.id ? updated : item,
    ),
  };
  selectedTemplate.value = updated;
  showToast(`已保存模板：${updated.name}`);
}

async function deleteSelectedTemplate() {
  if (!selectedTemplate.value) return;
  const templateID = selectedTemplate.value.id;
  await deleteTemplate(selectedTemplateKind.value, templateID);
  templateLibrary.value = {
    ...templateLibrary.value,
    [selectedTemplateKind.value]: templateLibrary.value[selectedTemplateKind.value].filter((item) => item.id !== templateID),
  };
  closeDrawer();
  showToast(`已删除模板：${templateID}`);
}

async function openDirectoryBrowser() {
  showDirectoryBrowser.value = true;
  directoryBrowserError.value = "";
  if (!directoryBrowser.value) {
    void browseDirectory();
  }
}

function applyProjectDirectory(path: string) {
  importForm.value.path = path;
  if (!importForm.value.name) {
    importForm.value.name = baseNameFromPath(path);
  }
}

async function chooseProjectDirectory() {
  directoryBrowserError.value = "";
  showToast("正在打开系统目录选择器...");
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), nativePickerTimeout);
  try {
    const selected = await fetchNativeLocalDirectory(controller.signal);
    if (!selected) {
      return;
    }
    applyProjectDirectory(selected.path);
  } catch {
    showToast("系统目录选择器未响应，已打开内置选择器");
    await openDirectoryBrowser();
  } finally {
    window.clearTimeout(timeout);
  }
}

async function browseDirectory(path?: string) {
  directoryBrowserLoading.value = true;
  directoryBrowserError.value = "";
  try {
    directoryBrowser.value = await fetchLocalDirectories(path);
    directoryAddress.value = directoryBrowser.value.path;
    selectedDirectoryPath.value = directoryBrowser.value.path;
  } catch (err) {
    directoryBrowserError.value = err instanceof Error ? err.message : "目录不可访问";
  } finally {
    directoryBrowserLoading.value = false;
  }
}

function selectDirectory(path: string) {
  selectedDirectoryPath.value = path;
  directoryAddress.value = path;
}

function openSelectedDirectory() {
  if (!selectedDirectoryPath.value) return;
  void browseDirectory(selectedDirectoryPath.value);
}

function browseDirectoryEntry(entry: LocalDirectoryEntry) {
  selectDirectory(entry.path);
  void browseDirectory(entry.path);
}

function submitDirectoryAddress() {
  const path = directoryAddress.value.trim();
  if (!path) {
    void browseDirectory();
    return;
  }
  void browseDirectory(path);
}

function useBrowsedDirectory() {
  const selectedPath = selectedDirectoryPath.value || directoryBrowser.value?.path || "";
  if (!selectedPath) return;
  applyProjectDirectory(selectedPath);
  closeDirectoryBrowser();
}

function closeDirectoryBrowser() {
  showDirectoryBrowser.value = false;
  directoryBrowserError.value = "";
}

function baseNameFromPath(path: string): string {
  const normalized = path.replace(/[\\\/]+$/, "");
  return normalized.split(/[\\\/]/).pop() || "imported-project";
}

async function importProjectAction() {
  const project = await importProject(importForm.value);
  const rescanned = await rescanProject(project.id);
  projects.value = upsertProject(projects.value, rescanned.project);
  projectConfigSets.value = {
    ...projectConfigSets.value,
    [project.id]: rescanned.copies,
  };
  selectedProjectId.value = project.id;
  activePage.value = "project-detail";
  showImportModal.value = false;
  showToast(`已导入项目：${project.name}`);
}

async function deleteProjectAction(project: Project) {
  if (!window.confirm(projectDeleteImpactMessage(project))) {
    return;
  }
  await deleteProjectApi(project.id);
  projects.value = projects.value.filter((item) => item.id !== project.id);
  const nextSets = { ...projectConfigSets.value };
  delete nextSets[project.id];
  projectConfigSets.value = nextSets;
  if (selectedProjectId.value === project.id) {
    selectedProjectId.value = projects.value[0]?.id ?? "";
    activePage.value = "projects";
  }
  showToast(`已移除项目：${project.name}`);
}

async function syncSelectedProjectCopy() {
  if (!selectedProjectCopy.value || !currentProject.value) return;
  const copy = await syncProjectCopy(currentProject.value.id, selectedProjectCopy.value.id);
  replaceProjectCopy(currentProject.value.id, copy);
  showToast(`已同步模板到项目副本：${copy.name}`);
}

async function detachSelectedProjectCopy() {
  if (!selectedProjectCopy.value || !currentProject.value) return;
  const copy = await detachProjectCopy(currentProject.value.id, selectedProjectCopy.value.id);
  replaceProjectCopy(currentProject.value.id, copy);
  showToast(`已解除模板关联：${copy.name}`);
}

async function addWorkflowTemplateToProject() {
  if (!currentProject.value) return;
  const templateId = selectedWorkflowTemplateId.value || templateLibrary.value.workflows[0]?.id;
  if (!templateId) {
    showToast("没有可添加的工作流模板。");
    return;
  }
  const copy = await addProjectCopyFromTemplate(currentProject.value.id, "workflow", templateId);
  const copies = await fetchProjectConfig(currentProject.value.id);
  projectConfigSets.value = {
    ...projectConfigSets.value,
    [currentProject.value.id]: copies,
  };
  const next = copies.find((item) => item.id === copy.id) ?? copy;
  await selectWorkflowCard(projectCopyToWorkflowCard(next));
  showToast(`已添加模板工作流到项目：${next.name}`);
}

async function createWorkflow() {
  if (activePage.value === "project-workflows") {
    if (!currentProject.value) return;
    const result = await createProjectWorkflow(currentProject.value.id, {
      name: `new workflow ${currentProjectResourceCopies.value.length + 1}`,
      summary: "New project AI development process.",
      trigger: "manual",
    });
    const copies = await fetchProjectConfig(currentProject.value.id);
    projectConfigSets.value = {
      ...projectConfigSets.value,
      [currentProject.value.id]: copies,
    };
    const copy = copies.find((item) => item.id === result.copy.id) ?? result.copy;
    workflowEditorMode.value = true;
    await selectWorkflowCard(projectCopyToWorkflowCard(copy));
    showToast(`已进入新建项目工作流：${copy.name}`);
    return;
  }
  const workflow = await createWorkflowApi({
    name: `new workflow ${workflows.value.length + 1}`,
    summary: "New AI development process.",
    trigger: "manual",
  });
  workflows.value = [...workflows.value, workflow];
  workflowEditorMode.value = true;
  await selectWorkflowCard({
    key: workflow.id,
    graphId: workflow.id,
    name: workflow.name,
    status: workflow.status,
    summary: workflow.summary,
    nodeCount: workflow.nodeCount,
    edgeCount: workflow.edgeCount,
  });
  showToast(`已进入新建工作流：${workflow.name}`);
}

async function editWorkflow(card: WorkflowCard) {
  workflowEditorMode.value = true;
  await selectWorkflowCard(card);
  showToast(`已进入编辑模式：${card.name}`);
}

async function saveWorkflow() {
  if (!workflowGraph.value) {
    showToast("没有可保存的工作流画布。");
    return;
  }
  if (activePage.value === "project-workflows") {
    const copy = selectedWorkflowCard.value?.copy;
    if (!currentProject.value || !copy) {
      showToast("请选择项目工作流后再保存。");
      return;
    }
    workflowGraph.value = await updateProjectWorkflowGraph(currentProject.value.id, copy.id, workflowGraph.value);
    const copies = await fetchProjectConfig(currentProject.value.id);
    projectConfigSets.value = {
      ...projectConfigSets.value,
      [currentProject.value.id]: copies,
    };
    selectedWorkflowCardKey.value = copy.id;
    workflowEditorMode.value = false;
    showToast(`已保存项目工作流：${workflowGraph.value.name}`);
    return;
  }
  if (activePage.value !== "workflows" || !selectedWorkflowId.value) {
    showToast("请选择工作流后再保存。");
    return;
  }
  workflowGraph.value = await updateWorkflowGraph(selectedWorkflowId.value, workflowGraph.value);
  await updateWorkflow(selectedWorkflowId.value, {
    status: "ready",
  });
  workflows.value = await fetchWorkflows();
  workflowEditorMode.value = false;
  showToast(`已保存工作流：${workflowGraph.value.name}`);
}

async function deleteWorkflow(card: WorkflowCard) {
  if (activePage.value !== "workflows") {
    if (!currentProject.value || !card.copy) return;
    await deleteProjectWorkflow(currentProject.value.id, card.copy.id);
    const copies = await fetchProjectConfig(currentProject.value.id);
    projectConfigSets.value = {
      ...projectConfigSets.value,
      [currentProject.value.id]: copies,
    };
    if (selectedWorkflowCardKey.value === card.key) {
      const next = copies.filter((copy) => copy.kind === "workflow").map(projectCopyToWorkflowCard)[0];
      if (next) {
        await selectWorkflowCard(next);
      } else {
        workflowGraph.value = null;
        selectedWorkflowCardKey.value = "";
      }
    }
    showToast(`已删除项目工作流：${card.name}`);
    return;
  }
  await deleteWorkflowApi(card.graphId);
  workflows.value = workflows.value.filter((workflow) => workflow.id !== card.graphId);
  if (selectedWorkflowId.value === card.graphId) {
    const next = workflows.value[0];
    if (next) {
      await selectWorkflowCard({
        key: next.id,
        graphId: next.id,
        name: next.name,
        status: next.status,
        summary: next.summary,
        nodeCount: next.nodeCount,
        edgeCount: next.edgeCount,
      });
    } else {
      workflowGraph.value = null;
    }
  }
  showToast(`已删除工作流：${card.name}`);
}

async function duplicateWorkflow(card: WorkflowCard) {
  if (activePage.value !== "workflows") {
    showToast("项目工作流副本不做模板复制，请回到全局 Workflows 操作。");
    return;
  }
  const duplicated = await duplicateWorkflowApi(card.graphId);
  workflows.value = [...workflows.value, duplicated];
  workflowEditorMode.value = true;
  await selectWorkflowCard({
    key: duplicated.id,
    graphId: duplicated.id,
    name: duplicated.name,
    status: duplicated.status,
    summary: duplicated.summary,
    nodeCount: duplicated.nodeCount,
    edgeCount: duplicated.edgeCount,
  });
  showToast(`已复制工作流：${duplicated.name}`);
}

function runWorkflow() {
  workflowRunError.value = "";
  workflowRunResult.value = null;
  const draft = buildWorkflowRunDraft({
    projectId: currentProject.value?.id || (activePage.value === "project-workflows" ? "selected-project" : "global-workflows"),
    workflowCopyId: selectedWorkflowCard.value?.copy?.id,
    workflowTemplateId: selectedWorkflowCard.value?.graphId || selectedWorkflowId.value || workflowGraph.value?.id,
    workflowId: selectedWorkflowId.value || workflowGraph.value?.id,
    workflowName: selectedWorkflowCard.value?.name || workflowGraph.value?.name,
    workflowSummary: selectedWorkflowCard.value?.summary,
    graph: workflowGraph.value
      ? {
          id: workflowGraph.value.id,
          name: workflowGraph.value.name,
          nodes: workflowGraph.value.nodes,
          edges: workflowGraph.value.edges,
        }
      : null,
  });
  workflowRunPayload.value = draft.payload;
  drawerMode.value = "workflow-run";
}

async function submitWorkflowRun() {
  if (!workflowRunPayload.value || workflowRunSubmitting.value) return;
  workflowRunSubmitting.value = true;
  workflowRunError.value = "";
  workflowRunResult.value = null;
  try {
    const created = await createTaskRun(workflowRunPayload.value as any);
    workflowRunResult.value = {
      id: created.id,
      workflowType: created.workflowType,
      projectId: created.projectId,
    };
    showToast(`已自动提交 Task Run：${created.workflowType}`);
  } catch (err) {
    workflowRunError.value = errorMessage(err);
    showToast(workflowRunError.value);
  } finally {
    workflowRunSubmitting.value = false;
  }
}

async function runProxyTest() {
  if (!selectedRoute.value) return;
  proxyResult.value = null;
  proxyError.value = "";
  const model = modelForRoute(selectedRoute.value);
  const client = selectedRoute.value.client.startsWith("Codex") ? "codex" : "claude";
  try {
    proxyResult.value = await resolveModelRoute(client, model);
  } catch (err) {
    proxyError.value = err instanceof Error ? err.message : "route test failed";
  }
}

async function runProxyFailureTest() {
  proxyResult.value = null;
  proxyError.value = "";
  try {
    await resolveModelRoute("codex", "unknown-model");
    proxyError.value = "Unexpected success for failure sample.";
  } catch (err) {
    proxyError.value = err instanceof Error ? err.message : "route test failed";
  }
}

function saveRouteMock() {
  routeSaveFeedback.value = "路由配置已模拟保存，实际 API key 仍只读取运行时环境变量。";
  showToast("模型路由已模拟保存");
}

function modelForRoute(route: ModelRoute): string {
  if (route.source === "claude-*") return "claude-sonnet-4";
  if (route.source === "deepseek-*") return "deepseek-v4-pro";
  if (route.source === "glm-*") return "glm-5.2";
  return route.source;
}

function showToast(message: string) {
  toast.value = message;
  window.setTimeout(() => {
    if (toast.value === message) toast.value = "";
  }, 1800);
}

async function loadData() {
  loading.value = true;
  error.value = "";
  const failures: string[] = [];

  const bootstrapResult = await Promise.allSettled([
    fetchBootstrap(),
    fetchWorkflows(),
    fetchModelRoutes(),
    fetchInfrastructure(),
    fetchInfrastructureCatalog(),
    fetchEvaluationSummary(),
    fetchLearningCases(),
    fetchEvaluationProjects(),
    fetchStatisticsTasks(statisticsView.value, statisticsRange.value),
  ]);

  const [bootstrap, workflowList, routeList, infraList, infraCatalog, evalSummary, caseList, projectHealth, statsTasks] = bootstrapResult;

  if (bootstrap.status === "fulfilled") {
    applyBootstrap(bootstrap.value);
  } else {
    failures.push(errorMessage(bootstrap.reason));
  }

  if (workflowList.status === "fulfilled") {
    workflows.value = workflowList.value;
    const firstWorkflow = workflowList.value[0];
    if (firstWorkflow) {
      selectedWorkflowCardKey.value = firstWorkflow.id;
      selectedWorkflowId.value = firstWorkflow.id;
      void selectWorkflowCard({
        key: firstWorkflow.id,
        graphId: firstWorkflow.id,
        name: firstWorkflow.name,
        status: firstWorkflow.status,
        summary: firstWorkflow.summary,
        nodeCount: firstWorkflow.nodeCount,
        edgeCount: firstWorkflow.edgeCount,
      });
    }
  } else {
    failures.push(errorMessage(workflowList.reason));
  }

  if (routeList.status === "fulfilled") {
    modelRoutes.value = routeList.value;
  } else {
    failures.push(errorMessage(routeList.reason));
  }

  if (infraList.status === "fulfilled") {
    infrastructureItems.value = infraList.value;
  } else {
    failures.push(errorMessage(infraList.reason));
  }

  if (infraCatalog.status === "fulfilled") {
    availableInfrastructureItems.value = infraCatalog.value;
  } else {
    failures.push(errorMessage(infraCatalog.reason));
  }

  if (evalSummary.status === "fulfilled") {
    evaluationSummary.value = evalSummary.value;
  }

  if (caseList.status === "fulfilled") {
    learningCases.value = Array.isArray(caseList.value) ? caseList.value : [];
  }

  if (projectHealth.status === "fulfilled") {
    evaluationProjects.value = Array.isArray(projectHealth.value.projects) ? projectHealth.value.projects : [];
    if (!selectedEvaluationProjectId.value && evaluationProjects.value.length > 0) {
      selectedEvaluationProjectId.value = evaluationProjects.value[0].projectId;
    }
  }

  if (statsTasks.status === "fulfilled") {
    statisticsTasks.value = Array.isArray(statsTasks.value.items) ? statsTasks.value.items : [];
  }

  if (selectedEvaluationProjectId.value) {
    try {
      await loadEvaluationProposals("pending");
    } catch (err) {
      failures.push(errorMessage(err));
    }
  }

  error.value = failures.length > 0 ? failures.join("; ") : "";
  loading.value = false;
}

async function loadEvaluationProjects() {
  const result = await fetchEvaluationProjects();
  evaluationProjects.value = Array.isArray(result.projects) ? result.projects : [];
  if (!selectedEvaluationProjectId.value && evaluationProjects.value.length > 0) {
    selectedEvaluationProjectId.value = evaluationProjects.value[0].projectId;
  }
  if (selectedEvaluationProjectId.value && !evaluationProjects.value.some((item) => item.projectId === selectedEvaluationProjectId.value)) {
    selectedEvaluationProjectId.value = evaluationProjects.value[0]?.projectId ?? "";
  }
}

async function loadEvaluationProposals(status = "pending") {
  const projectId = selectedEvaluationProjectId.value || undefined;
  const result = await fetchEvaluationProposals(projectId, status);
  evaluationProposals.value = Array.isArray(result.items) ? result.items : [];
}

async function loadStatisticsTasks() {
  statisticsLoading.value = true;
  try {
    const result = await fetchStatisticsTasks(statisticsView.value, statisticsRange.value);
    statisticsTasks.value = Array.isArray(result.items) ? result.items : [];
  } finally {
    statisticsLoading.value = false;
  }
}

async function selectEvaluationProject(projectId: string) {
  selectedEvaluationProjectId.value = projectId;
  await loadEvaluationProposals("pending");
}

async function reviewProposal(proposal: EvaluationProposal, status: "approved" | "rejected" | "later") {
  proposalBusyId.value = proposal.id;
  try {
    await reviewEvaluationProposal(proposal.id, {
      status,
      reviewNote: proposalReviewNote.value.trim() || `${status} via Evaluation panel`,
    });
    proposalReviewNote.value = "";
    await Promise.all([loadEvaluationProjects(), loadEvaluationProposals("pending")]);
    showToast(`Proposal ${status}: ${proposal.action}`);
  } catch (err) {
    showToast(errorMessage(err));
  } finally {
    proposalBusyId.value = "";
  }
}

function setStatisticsView(view: "failed" | "successful" | "top_scored" | "low_scored") {
  statisticsView.value = view;
  void loadStatisticsTasks();
}

function setStatisticsRange(range: "24h" | "7d" | "30d" | "all") {
  statisticsRange.value = range;
  void loadStatisticsTasks();
}

async function evaluatePendingRuns() {
  evaluationBusy.value = true;
  try {
    const result = await runPendingEvaluations();
    evaluationSummary.value = await fetchEvaluationSummary();
    learningCases.value = await fetchLearningCases().then((items) => (Array.isArray(items) ? items : []));
    await Promise.all([loadEvaluationProjects(), loadStatisticsTasks()]);
    if (selectedEvaluationProjectId.value) {
      await loadEvaluationProposals("pending");
    }
    showToast(`Evaluated ${result.evaluated} task runs`);
  } catch (err) {
    showToast(errorMessage(err));
  } finally {
    evaluationBusy.value = false;
  }
}

async function rebuildLearningCases() {
  learningCaseBusy.value = true;
  try {
    const result = await rebuildLearningCaseIndex();
    learningCases.value = await fetchLearningCases().then((items) => (Array.isArray(items) ? items : []));
    showToast(`已重建 ${result.indexed} 条学习案例索引`);
  } catch (err) {
    showToast(errorMessage(err));
  } finally {
    learningCaseBusy.value = false;
  }
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err || "Failed to load Nexus Agents data.");
}

function applyBootstrap(data: BootstrapData) {
  templateLibrary.value = data.templateLibrary ?? emptyLibrary;
  projects.value = data.projects ?? [];
  projectConfigSets.value = data.projectConfigSets ?? {};
  selectedProjectId.value = projects.value[0]?.id ?? selectedProjectId.value;
}

onMounted(loadData);
</script>

<template>
  <div class="app">
    <aside class="sidebar">
      <div class="sidebar-logo">
        <div class="logo-icon">N</div>
        <div>
          <div class="logo-text">Nexus Agents</div>
          <div class="logo-sub">AI Development Console</div>
        </div>
      </div>

      <div class="sidebar-section">
        <div class="sidebar-label">Template Library</div>
        <button class="sidebar-item" :class="{ active: activePage === 'agents' }" type="button" @click="openPage('agents')">
          <span class="sidebar-icon">A</span><span>Agents</span>
        </button>
        <button class="sidebar-item" :class="{ active: activePage === 'rules' }" type="button" @click="openPage('rules')">
          <span class="sidebar-icon">R</span><span>Rules</span>
        </button>
        <button class="sidebar-item" :class="{ active: activePage === 'skills' }" type="button" @click="openPage('skills')">
          <span class="sidebar-icon">S</span><span>Skills</span>
        </button>
        <button class="sidebar-item" :class="{ active: activePage === 'workflows' }" type="button" @click="openPage('workflows')">
          <span class="sidebar-icon">W</span><span>Workflows</span>
        </button>
      </div>

      <div class="sidebar-section">
        <div class="sidebar-label">System</div>
        <button class="sidebar-item" :class="{ active: activePage === 'infrastructure' }" type="button" @click="openPage('infrastructure')">
          <span class="sidebar-icon">I</span><span>Infrastructure</span>
        </button>
        <button class="sidebar-item" :class="{ active: activePage === 'model-routes' }" type="button" @click="openPage('model-routes')">
          <span class="sidebar-icon">M</span><span>Model Proxy</span>
        </button>
        <button class="sidebar-item" :class="{ active: activePage === 'statistics' }" type="button" @click="openPage('statistics')">
          <span class="sidebar-icon">?</span><span>Statistics</span>
        </button>
        <button class="sidebar-item" :class="{ active: activePage === 'evaluation' }" type="button" @click="openPage('evaluation')">
          <span class="sidebar-icon">E</span><span>Evaluation</span>
        </button>
      </div>

      <div class="sidebar-section compact">
        <div class="sidebar-label">Projects</div>
        <button class="sidebar-item" :class="{ active: activePage === 'projects' }" type="button" @click="openPage('projects')">
          <span class="sidebar-icon">P</span><span>All Projects</span>
        </button>
      </div>
      <div class="project-list">
        <div v-for="project in projects" :key="project.id" class="project-tree-item">
          <button
            class="project-entry project-root"
            :class="{ active: selectedProjectId === project.id && activePage.startsWith('project-') }"
            type="button"
            @click="openProject(project.id)"
          >
            <span class="project-dot" :class="project.status"></span>
            <span class="project-name">{{ project.name }}</span>
            <span class="project-mini">{{ project.configSummary.agents }}</span>
          </button>
          <div class="project-children">
            <button class="project-child" :class="{ active: selectedProjectId === project.id && activePage === 'project-detail' }" type="button" @click="openProject(project.id)">Overview</button>
            <button class="project-child" :class="{ active: selectedProjectId === project.id && activePage === 'project-agents' }" type="button" @click="openProjectResource(project.id, 'project-agents')">Agents</button>
            <button class="project-child" :class="{ active: selectedProjectId === project.id && activePage === 'project-rules' }" type="button" @click="openProjectResource(project.id, 'project-rules')">Rules</button>
            <button class="project-child" :class="{ active: selectedProjectId === project.id && activePage === 'project-skills' }" type="button" @click="openProjectResource(project.id, 'project-skills')">Skills</button>
            <button class="project-child" :class="{ active: selectedProjectId === project.id && activePage === 'project-workflows' }" type="button" @click="openProjectResource(project.id, 'project-workflows')">Workflows</button>
          </div>
        </div>
      </div>
    </aside>

    <main class="main">
      <header class="topbar">
        <nav class="breadcrumb">
          <template v-for="(part, index) in breadcrumb" :key="part">
            <span :class="index === breadcrumb.length - 1 ? 'breadcrumb-current' : 'breadcrumb-link'">{{ part }}</span>
            <span v-if="index < breadcrumb.length - 1" class="breadcrumb-sep">/</span>
          </template>
        </nav>
      </header>

      <section class="content">
        <div v-if="loading" class="panel panel-pad">Loading Nexus Agents...</div>
        <div v-else-if="error" class="panel panel-pad error-text">{{ error }}</div>

        <template v-else>
          <section v-if="activePage === 'projects'" class="page active">
            <div class="page-header">
              <div class="page-description">统一导入和管理项目自己的 Agents、Rules、Skills、Workflows 配置副本，模板同步保持手动触发。</div>
              <button class="btn-primary" type="button" @click="showImportModal = true">导入项目</button>
            </div>
            <div class="stats-row">
              <div class="stat-card"><div class="stat-icon purple">P</div><div><div class="stat-num">{{ projects.length }}</div><div class="stat-label">Managed projects</div></div></div>
              <div class="stat-card"><div class="stat-icon teal">A</div><div><div class="stat-num">{{ projects[0]?.configSummary.agents ?? 0 }}</div><div class="stat-label">Imported agents</div></div></div>
              <div class="stat-card"><div class="stat-icon orange">R</div><div><div class="stat-num">{{ projects[0]?.configSummary.rules ?? 0 }}</div><div class="stat-label">Active rules</div></div></div>
              <div class="stat-card"><div class="stat-icon pink">S</div><div><div class="stat-num">{{ projects[0]?.configSummary.skills ?? 0 }}</div><div class="stat-label">Shared skills</div></div></div>
            </div>
            <div class="table-wrap">
              <table>
                <thead><tr><th>Project</th><th>Status</th><th>Agents</th><th>Rules</th><th>Skills</th><th>Updated</th><th></th></tr></thead>
                <tbody>
                  <tr v-for="project in projects" :key="project.id">
                    <td><button class="link-btn" type="button" @click="openProject(project.id)">{{ project.name }}</button><div class="dim mono">{{ project.path }}</div></td>
                    <td><span class="chip" :class="`chip-${statusTone(project.status)}`">{{ project.status }}</span></td>
                    <td>{{ project.configSummary.agents }}</td>
                    <td>{{ project.configSummary.rules }}</td>
                    <td>{{ project.configSummary.skills }}</td>
                    <td class="muted">{{ project.updatedAt }}</td>
                    <td class="row-actions">
                      <button class="link-btn" type="button" @click="openProject(project.id)">详情</button>
                      <button class="link-btn danger" type="button" @click="deleteProjectAction(project)">删除</button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <section v-else-if="activePage === 'agents' || activePage === 'rules' || activePage === 'skills'" class="page active">
            <div class="page-header">
              <div class="page-description">这里是全局模板库。项目会复制模板生成自己的 Project Config Set 副本，后续通过手动同步追踪版本差异。</div>
              <button class="btn-primary" type="button" @click="createTemplateForCurrentPage">新建 {{ pageTitle(activePage) }}</button>
            </div>
            <div class="asset-grid">
              <article v-for="item in currentTemplateItems" :key="item.id" class="asset-card" :class="[item.kind, agentModelClass(item)]">
                <div class="asset-card-header">
                  <div class="asset-icon" :class="kindIcon(item.kind)" aria-hidden="true"></div>
                  <div>
                    <div class="asset-title">{{ item.name }}</div>
                    <div class="asset-meta">Template Library / {{ item.entry || item.slug }}</div>
                  </div>
                  <span class="chip" :class="`chip-${statusTone(item.status)}`">{{ item.status }}</span>
                </div>
                <div class="asset-card-body">
                  <div class="asset-desc">{{ item.summary }}</div>
                  <div class="asset-template-meta">
                    <span v-if="systemAgentIds.has(item.id)">system-level evaluator</span>
                    <span v-if="item.kind === 'agent'">{{ item.modelTier }}</span>
                    <span v-else-if="item.kind === 'rule'">{{ item.source || item.entry }}</span>
                    <span v-else-if="item.kind === 'skill'">{{ item.source || item.entry }}</span>
                    <span v-else>v{{ item.version }}</span>
                    <span>{{ templateUsageCount(item.id) }} projects</span>
                    <span>{{ item.updatedAt }}</span>
                  </div>
                </div>
                <div class="asset-card-footer">
                  <div class="asset-chip-row"><span v-for="chip in templateFooterChips(item)" :key="chip" class="chip chip-teal">{{ chip }}</span></div>
                  <button class="link-btn" type="button" @click="openTemplateDrawer(currentTemplateKind, item)">查看</button>
                </div>
              </article>
            </div>
          </section>

          <section v-else-if="activePage === 'project-detail' && currentProject" class="page active">
            <div class="page-header">
              <div class="page-description">{{ currentProject.name }} 当前展示运行时扫描到的项目配置副本，来源包括 .claude、.codex 和路由规则展开出的工作流。本地导入信息从项目根目录的 .nexus 文件读取。</div>
              <div class="page-actions">
                <button class="btn-secondary" type="button" @click="rescanCurrentProject">重新扫描</button>
                <button class="btn-secondary" type="button" @click="openSyncPreviewDrawer">同步预览</button>
                <button class="btn-secondary danger" type="button" @click="deleteProjectAction(currentProject)">从 Nexus 移除</button>
              </div>
            </div>
            <section class="panel panel-pad local-import-panel">
              <div class="detail-list">
                <div><span>local path</span><strong class="mono">{{ currentProject.localPath || currentProject.path }}</strong></div>
                <div><span>repo key</span><strong class="mono">{{ currentProject.repoKey || '-' }}</strong></div>
                <div><span>local config</span><strong class="mono">{{ currentProject.localConfigPath || '.nexus' }}</strong></div>
                <div><span>git ignore</span><strong>{{ currentProject.localConfigIgnored ? 'ignored' : 'not ignored' }}</strong></div>
              </div>
            </section>
            <div class="stats-row">
              <div class="stat-card"><div class="stat-icon purple">A</div><div><div class="stat-num">{{ currentProject.configSummary.agents }}</div><div class="stat-label">Agents</div></div></div>
              <div class="stat-card"><div class="stat-icon teal">R</div><div><div class="stat-num">{{ currentProject.configSummary.rules }}</div><div class="stat-label">Rules</div></div></div>
              <div class="stat-card"><div class="stat-icon orange">S</div><div><div class="stat-num">{{ currentProject.configSummary.skills }}</div><div class="stat-label">Skills</div></div></div>
              <div class="stat-card"><div class="stat-icon pink">W</div><div><div class="stat-num">{{ currentProject.configSummary.workflows }}</div><div class="stat-label">Workflows</div></div></div>
            </div>
            <div class="grid-2 project-overview-grid">
              <section class="panel">
                <div class="panel-header"><div><div class="panel-title">同步状态</div><div class="drawer-subtitle">按 Project Config Set 聚合。</div></div></div>
                <div class="panel-body project-sync-summary">
                  <div v-for="entry in syncSummary" :key="entry.status" class="sync-summary-card">
                    <div class="sync-summary-head"><span class="chip" :class="`chip-${statusTone(entry.status)}`">{{ entry.status }}</span></div>
                    <div class="sync-summary-count">{{ entry.count }}</div>
                  </div>
                </div>
              </section>
              <section class="panel">
                <div class="panel-header"><div><div class="panel-title">资源入口</div><div class="drawer-subtitle">进入项目自己的配置副本。</div></div></div>
                <div class="panel-body project-kind-summary">
                  <button class="project-kind-card" type="button" @click="openProjectResource(currentProject.id, 'project-agents')"><span class="asset-icon asset-icon-employee"></span><span><strong>Agents</strong><span>{{ projectCopyCount('agent') }} tracked</span></span><b>{{ currentProject.configSummary.agents }}</b></button>
                  <button class="project-kind-card" type="button" @click="openProjectResource(currentProject.id, 'project-rules')"><span class="asset-icon asset-icon-handbook"></span><span><strong>Rules</strong><span>{{ projectCopyCount('rule') }} tracked</span></span><b>{{ currentProject.configSummary.rules }}</b></button>
                  <button class="project-kind-card" type="button" @click="openProjectResource(currentProject.id, 'project-skills')"><span class="asset-icon asset-icon-manual"></span><span><strong>Skills</strong><span>{{ projectCopyCount('skill') }} tracked</span></span><b>{{ currentProject.configSummary.skills }}</b></button>
                  <button class="project-kind-card" type="button" @click="openProjectResource(currentProject.id, 'project-workflows')"><span class="asset-icon asset-icon-process"></span><span><strong>Workflows</strong><span>{{ projectCopyCount('workflow') }} tracked</span></span><b>{{ currentProject.configSummary.workflows }}</b></button>
                </div>
              </section>
            </div>
          </section>

          <section v-else-if="activePage === 'project-agents' || activePage === 'project-rules' || activePage === 'project-skills'" class="page active">
            <div class="page-description">Project Config Set 中的项目副本。卡片保持模板库样式，版本、hash 和同步操作放在详情抽屉里。</div>
            <div class="asset-grid">
              <article v-for="copy in currentProjectResourceCopies" :key="copy.id" class="asset-card project-copy" :class="[copy.kind, projectCopyModelClass(copy)]">
                <div class="asset-card-header">
                  <div class="asset-icon" :class="kindIcon(copy.kind)" aria-hidden="true"></div>
                  <div><div class="asset-title">{{ copy.name }}</div><div class="asset-meta">{{ copy.kind }} copy / from {{ copy.origin?.templateId || 'project-only' }}</div></div>
                  <span class="chip" :class="`chip-${statusTone(copy.status)}`">{{ copy.status }}</span>
                </div>
                <div class="asset-card-body">
                  <div class="asset-desc">{{ projectCopySummary(copy) }}</div>
                  <div class="asset-template-meta"><span v-for="meta in projectCopyMeta(copy)" :key="meta">{{ meta }}</span></div>
                </div>
                <div class="asset-card-footer">
                  <div class="asset-chip-row"><span v-for="chip in projectCopyFooterChips(copy)" :key="chip" class="chip chip-teal">{{ chip }}</span></div>
                  <button class="link-btn" type="button" @click="openProjectCopyDrawer(copy)">查看</button>
                </div>
              </article>
            </div>
          </section>

          <section v-else-if="activePage === 'workflows' || activePage === 'project-workflows'" class="page active">
            <div class="page-header">
              <div class="page-description">{{ activePage === 'workflows' ? '全局工作流模板库，用来沉淀可复用的 AI 开发工序。' : '项目工作流展示该项目的 Project Config Set 副本和同步状态。' }}</div>
              <div v-if="activePage === 'workflows'" class="page-actions">
                <button class="btn-secondary" type="button" @click="runWorkflow">模拟运行</button>
                <button class="btn-secondary" type="button" :disabled="evaluationBusy" @click="evaluatePendingRuns">
                  {{ evaluationBusy ? '评估中...' : '评估待处理' }}
                </button>
                <button class="btn-secondary" type="button" :disabled="learningCaseBusy" @click="rebuildLearningCases">
                  {{ learningCaseBusy ? '索引中...' : '重建案例索引' }}
                </button>
                <button class="btn-primary" type="button" @click="createWorkflow">新建工作流</button>
              </div>
            </div>
            <div class="workflow-shell">
              <aside class="panel workflow-library">
                <div class="panel-header">
                  <div><div class="panel-title">{{ activePage === 'workflows' ? 'Workflow Library' : 'Project Workflows' }}</div><div class="drawer-subtitle">选择工作流进入画布预览。</div></div>
                </div>
                <div class="panel-body">
                  <input v-model="workflowQuery" class="field-input" type="search" placeholder="查询工作流" />
                  <div v-if="workflowEditorMode" class="node-palette">
                    <div class="section-title">Node Palette</div>
                    <button
                      v-for="preset in workflowNodePresets"
                      :key="preset.key"
                      class="node-palette-item"
                      :class="`node-${preset.category}`"
                      type="button"
                      @click="addNodeFromPalette(preset)"
                    >
                      {{ preset.title }}
                    </button>
                    <button class="btn-secondary full" :class="{ active: workflowConnectMode }" type="button" @click="toggleConnectMode">
                      {{ workflowConnectMode ? '退出连线模式' : '连线模式' }}
                    </button>
                  </div>
                  <div class="workflow-list">
                    <article v-for="card in visibleWorkflowCards" :key="card.key" class="workflow-card" :class="{ active: card.key === selectedWorkflowCardKey }">
                      <button class="workflow-card-main" type="button" @click="selectWorkflowCard(card)">
                        <div class="workflow-card-title">{{ card.name }}</div>
                        <div class="workflow-card-desc">{{ card.summary }}</div>
                        <div class="workflow-card-meta">
                          <span class="chip" :class="`chip-${statusTone(card.status)}`">{{ card.status }}</span>
                          <span class="chip chip-purple">{{ card.nodeCount }} nodes</span>
                          <span v-if="card.evaluation" class="chip chip-teal">{{ card.evaluation.sampleCount }} runs</span>
                          <span v-if="card.evaluation" class="chip chip-orange">score {{ card.evaluation.averageScore }}</span>
                          <span v-if="card.evaluation" class="chip chip-green">{{ card.evaluation.successRate }}% success</span>
                        </div>
                      </button>
                      <div class="workflow-card-actions">
                        <button class="link-btn" type="button" @click="editWorkflow(card)">编辑</button>
                        <button v-if="activePage === 'project-workflows' && card.copy" class="link-btn" type="button" @click="openProjectCopyDrawer(card.copy)">同步</button>
                        <button v-if="activePage === 'workflows'" class="link-btn" type="button" @click="duplicateWorkflow(card)">复制</button>
                        <button class="link-btn danger" type="button" @click="deleteWorkflow(card)">删除</button>
                      </div>
                    </article>
                  </div>
                </div>
              </aside>
              <section class="workflow-canvas-shell">
                <div v-if="activePage === 'project-workflows'" class="workflow-canvas-toolbar">
                  <select v-model="selectedWorkflowTemplateId" class="field-input compact-select workflow-template-select" aria-label="选择工作流模板">
                    <option value="">选择模板工作流</option>
                    <option v-for="workflow in templateLibrary.workflows" :key="workflow.id" :value="workflow.id">{{ workflow.name }}</option>
                  </select>
                  <button class="btn-secondary" type="button" @click="addWorkflowTemplateToProject">从模板添加</button>
                  <button class="btn-primary" type="button" @click="createWorkflow">新建工作流</button>
                </div>
                <div ref="workflowCanvasRef" class="workflow-canvas">
                  <div
                    v-if="workflowGraph"
                    ref="workflowStageRef"
                    class="workflow-stage"
                    :style="{ width: `${workflowStageWidth}px`, minWidth: `${workflowStageWidth}px`, height: `${workflowStageHeight}px`, minHeight: `${workflowStageHeight}px` }"
                  >
                  <svg class="workflow-edges" :viewBox="`0 0 ${workflowStageWidth} ${workflowStageHeight}`" :style="{ width: `${workflowStageWidth}px`, height: `${workflowStageHeight}px` }" aria-hidden="true">
                    <defs>
                      <marker id="workflow-edge-arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth">
                        <path d="M 0 0 L 10 5 L 0 10 z"></path>
                      </marker>
                    </defs>
                    <g>
                      <path
                        v-for="(edge, index) in workflowGraph.edges"
                        :key="workflowEdgeKey(edge, index)"
                        class="workflow-edge"
                        :class="{ selected: selectedEdgeKey === workflowEdgeKey(edge, index) }"
                        :d="workflowEdgePath(edge)"
                        @click.stop="selectWorkflowEdge(edge, index)"
                      />
                    </g>
                    <g>
                      <text
                        v-for="(edge, index) in workflowGraph.edges"
                        :key="`label-${workflowEdgeKey(edge, index)}`"
                        class="workflow-edge-label"
                        :x="workflowEdgeLabelX(edge)"
                        :y="workflowEdgeLabelY(edge)"
                      >
                        {{ edge.label }}
                      </text>
                    </g>
                  </svg>
                  <button
                    v-for="node in workflowGraph.nodes"
                    :key="node.id"
                    class="workflow-node"
                    :class="[`node-${node.category}`, { selected: selectedNodeId === node.id }]"
                    :style="{ left: `${node.x}px`, top: `${node.y}px` }"
                    type="button"
                    @click="handleWorkflowNodeClick(node)"
                    @pointerdown="startWorkflowNodeDrag($event, node)"
                    @pointermove="dragWorkflowNode"
                    @pointerup="endWorkflowNodeDrag"
                    @pointercancel="endWorkflowNodeDrag"
                  >
                    <div class="node-head"><span class="node-title">{{ node.label }}</span><span class="node-type-badge">{{ node.type }}</span></div>
                    <div class="node-body"><div class="node-detail">{{ node.detail }}</div><div class="node-role">{{ node.agent }}</div></div>
                  </button>
                </div>
                  <div v-else class="empty-state">当前工作流没有可预览的画布。</div>
                </div>
              </section>
              <aside class="panel workflow-inspector">
                <div class="panel-header"><div class="panel-title">{{ selectedNode ? 'Node Config' : selectedEdge ? 'Edge Config' : 'Selected Workflow' }}</div></div>
                <div class="panel-body" v-if="workflowGraph">
                  <template v-if="selectedNode">
                    <label class="field-label">Label
                      <input class="field-input" :value="selectedNode.label" :readonly="!workflowEditorMode" @input="updateSelectedWorkflowNode({ label: inputValue($event) })" />
                    </label>
                    <label class="field-label">Type
                      <input class="field-input" :value="selectedNode.type" :readonly="!workflowEditorMode" @input="updateSelectedWorkflowNode({ type: inputValue($event) })" />
                    </label>
                    <label class="field-label">Category
                      <select class="field-input" :value="selectedNode.category" :disabled="!workflowEditorMode" @change="updateSelectedWorkflowNode({ category: inputValue($event) })">
                        <option value="event">event</option>
                        <option value="action">action</option>
                        <option value="control">control</option>
                        <option value="condition">condition</option>
                        <option value="data">data</option>
                        <option value="human">human</option>
                        <option value="decorator">decorator</option>
                      </select>
                    </label>
                    <label class="field-label">Agent
                      <input class="field-input" :value="selectedNode.agent" :readonly="!workflowEditorMode" @input="updateSelectedWorkflowNode({ agent: inputValue($event) })" />
                    </label>
                    <label class="field-label">Detail
                      <textarea class="field-textarea" :value="selectedNode.detail" :readonly="!workflowEditorMode" @input="updateSelectedWorkflowNode({ detail: inputValue($event) })"></textarea>
                    </label>
                    <div class="detail-list">
                      <div><span>Input</span><strong>request.context</strong></div>
                      <div><span>Output</span><strong>{{ selectedNode.id }}.result</strong></div>
                      <div><span>Position</span><strong>{{ selectedNode.x }}, {{ selectedNode.y }}</strong></div>
                    </div>
                  </template>
                  <template v-else-if="selectedEdge">
                    <label class="field-label">Label
                      <input class="field-input" :value="selectedEdge.label" :readonly="!workflowEditorMode" @input="updateSelectedWorkflowEdgeLabel(inputValue($event))" />
                    </label>
                    <div class="detail-list">
                      <div><span>From</span><strong>{{ workflowNodeDisplayName(selectedEdge.from) }}</strong></div>
                      <div><span>To</span><strong>{{ workflowNodeDisplayName(selectedEdge.to) }}</strong></div>
                    </div>
                    <button v-if="workflowEditorMode" class="btn-secondary danger full" type="button" @click="deleteSelectedWorkflowEdge">删除连线</button>
                  </template>
                  <template v-else>
                    <strong>{{ workflowGraph.name }}</strong>
                    <div class="form-hint">{{ workflowGraph.nodes.length }} nodes / {{ workflowGraph.edges.length }} edges</div>
                    <div v-if="selectedWorkflowCard?.copy" class="detail-list">
                      <div><span>origin.templateId</span><strong>{{ selectedWorkflowCard.copy.origin?.templateId || 'project-only' }}</strong></div>
                      <div><span>origin.baseVersion</span><strong>{{ selectedWorkflowCard.copy.origin?.baseVersion || '-' }}</strong></div>
                      <div><span>origin.baseHash</span><strong>{{ selectedWorkflowCard.copy.origin?.baseHash || '-' }}</strong></div>
                      <div><span>localVersion</span><strong>{{ selectedWorkflowCard.copy.localVersion }}</strong></div>
                      <div><span>syncMode</span><strong>{{ selectedWorkflowCard.copy.syncMode }}</strong></div>
                      <div><span>status</span><strong>{{ selectedWorkflowCard.copy.status }}</strong></div>
                      <div><span>path</span><strong>{{ selectedWorkflowCard.copy.path }}</strong></div>
                    </div>
                    <pre v-if="selectedWorkflowCard?.copy?.diff" class="code-block">{{ selectedWorkflowCard.copy.diff }}</pre>
                    <pre class="code-block">{{ workflowGraph.edges.map(workflowEdgeDisplay).join('\n') }}</pre>
                  </template>
                  <button v-if="workflowEditorMode" class="btn-primary full" type="button" @click="saveWorkflow">保存修改</button>
                </div>
              </aside>
            </div>
          </section>

          <section v-else-if="activePage === 'statistics'" class="page active">
            <div class="page-header">
              <div>
                <div class="page-title">Statistics</div>
                <div class="page-description">Query task results and trend signals by time range and score view.</div>
              </div>
              <div class="page-actions">
                <button class="btn-secondary" type="button" :disabled="evaluationBusy" @click="evaluatePendingRuns">
                  {{ evaluationBusy ? 'Evaluating...' : 'Evaluate Pending' }}
                </button>
              </div>
            </div>

            <section class="panel statistics-panel">
              <div class="panel-body statistics-panel-body">
                <div class="statistics-toolbar">
                  <div class="statistics-filter-group">
                    <span class="toolbar-label">Time Range</span>
                    <div class="chip-row">
                      <button class="btn-secondary" :class="{ active: statisticsRange === '24h' }" type="button" @click="setStatisticsRange('24h')">Last 24h</button>
                      <button class="btn-secondary" :class="{ active: statisticsRange === '7d' }" type="button" @click="setStatisticsRange('7d')">Last 7d</button>
                      <button class="btn-secondary" :class="{ active: statisticsRange === '30d' }" type="button" @click="setStatisticsRange('30d')">Last 30d</button>
                      <button class="btn-secondary" :class="{ active: statisticsRange === 'all' }" type="button" @click="setStatisticsRange('all')">All</button>
                    </div>
                  </div>
                  <div class="statistics-filter-group">
                    <span class="toolbar-label">View</span>
                    <div class="chip-row">
                      <button class="btn-secondary" :class="{ active: statisticsView === 'failed' }" type="button" @click="setStatisticsView('failed')">Failed</button>
                      <button class="btn-secondary" :class="{ active: statisticsView === 'successful' }" type="button" @click="setStatisticsView('successful')">Successful</button>
                      <button class="btn-secondary" :class="{ active: statisticsView === 'top_scored' }" type="button" @click="setStatisticsView('top_scored')">Top Scored</button>
                      <button class="btn-secondary" :class="{ active: statisticsView === 'low_scored' }" type="button" @click="setStatisticsView('low_scored')">Low Scored</button>
                    </div>
                  </div>
                </div>

                <div class="statistics-line-card compact">
                  <div class="statistics-line-head">
                    <div>
                      <div class="section-title">Score Trend</div>
                      <div class="drawer-subtitle">Recent scores for the selected statistics view.</div>
                    </div>
                    <span class="chip chip-green">{{ statisticsViewLabel(statisticsView) }}</span>
                  </div>
                  <svg class="statistics-line-chart" :viewBox="`0 0 ${statisticsLineChart.width} ${statisticsLineChart.height}`" role="img" aria-label="statistics score trend">
                    <line x1="28" y1="192" x2="612" y2="192" class="chart-grid-line" />
                    <line x1="28" y1="28" x2="28" y2="192" class="chart-grid-line" />
                    <path :d="statisticsLineChart.path" class="chart-trend-line" />
                    <g v-for="point in statisticsLineChart.points" :key="`${point.label}-${point.x}`">
                      <circle :cx="point.x" :cy="point.y" r="5" class="chart-point" />
                      <text :x="point.x" :y="point.y - 12" class="chart-value" text-anchor="middle">{{ point.value }}</text>
                      <text :x="point.x" y="210" class="chart-label" text-anchor="middle">{{ point.label }}</text>
                    </g>
                  </svg>
                </div>

                <div class="table-shell">
                  <table class="task-table">
                    <thead>
                      <tr>
                        <th>Task</th>
                        <th>Project</th>
                        <th>Workflow</th>
                        <th>Status</th>
                        <th>Score</th>
                        <th>Model</th>
                        <th>Arbiter</th>
                        <th>Flags</th>
                        <th>Agent</th>
                        <th>Time</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="item in statisticsTasks" :key="item.runId">
                        <td>
                          <div class="task-title-cell">{{ item.taskTitle || item.runId }}</div>
                          <div class="table-subtext">{{ item.evaluationId || 'pending evaluation' }}</div>
                        </td>
                        <td>{{ item.projectId }}</td>
                        <td>{{ item.workflowType }}</td>
                        <td><span class="chip" :class="item.status === 'success' ? 'chip-green' : 'chip-orange'">{{ item.status }}</span></td>
                        <td>{{ Number(item.score || 0).toFixed(1) }}</td>
                        <td>{{ item.model || '-' }}</td>
                        <td>
                          <div class="task-title-cell">{{ item.arbiterModel || '-' }}</div>
                          <div class="table-subtext">Escalation: {{ item.escalationModel || '-' }}</div>
                        </td>
                        <td>
                          <div class="flag-list">
                            <span v-for="flag in escalationFlags(item)" :key="`${item.runId}-${flag}`" class="chip chip-teal">{{ flag }}</span>
                            <span v-if="escalationFlags(item).length === 0" class="table-subtext">-</span>
                          </div>
                          <div class="table-subtext">Escalate: {{ boolLabel(item.needsEscalation) }}</div>
                        </td>
                        <td>{{ item.agent || '-' }}</td>
                        <td>{{ formatDateTime(item.createdAt) }}</td>
                      </tr>
                      <tr v-if="!statisticsLoading && statisticsTasks.length === 0">
                        <td colspan="10">
                          <div class="empty-inline">No task runs found for this view and time range.</div>
                        </td>
                      </tr>
                      <tr v-if="statisticsLoading">
                        <td colspan="10">
                          <div class="empty-inline">Loading statistics...</div>
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>
            </section>
          </section>

          <section v-else-if="activePage === 'evaluation'" class="page active">
            <div class="page-header">
              <div>
                <div class="page-title">Evaluation</div>
                <div class="page-description">Objective evaluation only: evidence, metrics, workflow history, and retrieval entry points for manual review in project Codex.</div>
              </div>
              <div class="page-actions">
                <button class="btn-secondary" type="button" :disabled="evaluationBusy" @click="evaluatePendingRuns">
                  {{ evaluationBusy ? 'Evaluating...' : 'Evaluate Pending' }}
                </button>
              </div>
            </div>

            <div class="evaluation-layout">
              <section class="panel">
                <div class="panel-header">
                  <div>
                    <div class="panel-title">Projects</div>
                    <div class="drawer-subtitle">Health summary grouped by project.</div>
                  </div>
                </div>
                <div class="panel-body evaluation-project-grid">
                  <button
                    v-for="item in evaluationProjectOptions"
                    :key="item.projectId"
                    type="button"
                    class="evaluation-project-card"
                    :class="{ active: selectedEvaluationProjectId === item.projectId }"
                    @click="selectEvaluationProject(item.projectId)"
                  >
                    <div class="asset-card-header compact">
                      <strong>{{ item.projectId }}</strong>
                      <span class="chip chip-orange">{{ item.pendingCount }} pending</span>
                    </div>
                    <div class="project-health-grid">
                      <div><span>Avg Score</span><strong>{{ item.averageScore || 0 }}</strong></div>
                      <div><span>Success Rate</span><strong>{{ item.successRate || 0 }}%</strong></div>
                      <div><span>Failed</span><strong>{{ item.failedCount }}</strong></div>
                      <div><span>Pending</span><strong>{{ item.pendingCount }}</strong></div>
                    </div>
                  </button>
                  <div v-if="evaluationProjectOptions.length === 0" class="empty-state compact">No evaluated projects yet.</div>
                </div>
              </section>

              <section class="panel">
                <div class="panel-header">
                  <div>
                    <div class="panel-title">Manual Review Signals</div>
                    <div class="drawer-subtitle">Evaluation exposes evidence, objective metrics, and retrieval signals. Optimization decisions are made manually in project Codex.</div>
                  </div>
                  <div class="asset-chip-row" v-if="selectedEvaluationProject">
                    <span class="chip chip-purple">{{ selectedEvaluationProject.projectId }}</span>
                    <span class="chip chip-green">{{ selectedEvaluationProject.successRate }}% success</span>
                  </div>
                </div>
                <div class="panel-body proposal-list">
                  <article v-if="selectedEvaluationProject" class="proposal-card">
                    <div class="asset-card-header compact">
                      <strong>{{ selectedEvaluationProject.projectId }}</strong>
                      <div class="asset-chip-row">
                        <span class="chip chip-green">{{ selectedEvaluationProject.successRate }}% success</span>
                        <span class="chip chip-purple">{{ selectedEvaluationProject.totalRuns }} runs</span>
                      </div>
                    </div>
                    <div class="proposal-meta-row">
                      <span>Average Score: {{ selectedEvaluationProject.averageScore || 0 }}</span>
                      <span>Failed: {{ selectedEvaluationProject.failedCount }}</span>
                      <span>Pending: {{ selectedEvaluationProject.pendingCount }}</span>
                    </div>
                    <p class="proposal-reason">Use the evidence, workflow metrics, dimension statistics, and learning-case retrieval below to manually review whether agents, models, workflows, rules, or skills need adjustment.</p>
                    <div class="proposal-routing-grid">
                      <div><span>Objective Eval</span><strong>Enabled</strong></div>
                      <div><span>Auto Suggestions</span><strong>Disabled</strong></div>
                      <div><span>Review Mode</span><strong>Manual in Codex</strong></div>
                      <div><span>Retrieval</span><strong>Learning Cases</strong></div>
                    </div>
                  </article>
                  <div v-else class="empty-state compact">Select a project to inspect evidence, metrics, and retrieval signals.</div>

                  <div v-if="selectedEvaluationProject" class="evaluation-task-stack">
                    <div class="panel-title mini">Project Tasks</div>
                    <article v-for="item in selectedEvaluationProjectTasks" :key="`eval-task-${item.runId}`" class="proposal-card evaluation-task-card">
                      <div class="asset-card-header compact">
                        <div>
                          <strong>{{ item.taskTitle || item.runId }}</strong>
                          <div class="table-subtext">{{ item.workflowType }} ? {{ formatDateTime(item.createdAt) }}</div>
                        </div>
                        <div class="asset-chip-row">
                          <span class="chip" :class="item.status === 'success' ? 'chip-green' : item.status === 'failed' ? 'chip-red' : 'chip-orange'">{{ item.status }}</span>
                          <span class="chip chip-purple">score {{ Number(item.score || 0).toFixed(1) }}</span>
                        </div>
                      </div>
                      <div class="proposal-meta-row">
                        <span>Agent: {{ item.agent || '-' }}</span>
                        <span>Model: {{ item.model || '-' }}</span>
                        <span>Run ID: {{ item.runId }}</span>
                      </div>

                      <template v-if="asWorkflowTokenUsage(item.tokenUsage)">
                        <div class="task-metric-section">
                          <div class="task-metric-title">Token Usage</div>
                          <div class="proposal-routing-grid">
                            <div><span>Total</span><strong>{{ formatNumber(asWorkflowTokenUsage(item.tokenUsage)?.totalTokens) }}</strong></div>
                            <div><span>Input</span><strong>{{ formatNumber(asWorkflowTokenUsage(item.tokenUsage)?.inputTokens) }}</strong></div>
                            <div><span>Cached</span><strong>{{ formatNumber(asWorkflowTokenUsage(item.tokenUsage)?.cachedInputTokens) }}</strong></div>
                            <div><span>Output</span><strong>{{ formatNumber(asWorkflowTokenUsage(item.tokenUsage)?.outputTokens) }}</strong></div>
                            <div><span>Requests</span><strong>{{ formatNumber(asWorkflowTokenUsage(item.tokenUsage)?.requestCount) }}</strong></div>
                            <div><span>Cache Hit</span><strong>{{ asWorkflowTokenUsage(item.tokenUsage)?.cacheHitRate ?? 0 }}%</strong></div>
                          </div>
                          <div class="role-breakdown-grid">
                            <div v-for="[role, stat] in roleMetricEntries(asWorkflowTokenUsage(item.tokenUsage)?.roles)" :key="`${item.runId}-token-${role}`" class="role-breakdown-card">
                              <strong>{{ role }}</strong>
                              <div class="table-subtext">{{ topModelsLabel((stat as TokenUsageRoleRollup).models) }}</div>
                              <div class="detail-list compact-detail-list">
                                <div><span>Req</span><strong>{{ formatNumber((stat as TokenUsageRoleRollup).requestCount) }}</strong></div>
                                <div><span>Total</span><strong>{{ formatNumber((stat as TokenUsageRoleRollup).totalTokens) }}</strong></div>
                                <div><span>Cache</span><strong>{{ (stat as TokenUsageRoleRollup).cacheHitRate }}%</strong></div>
                              </div>
                            </div>
                          </div>
                        </div>
                      </template>

                      <template v-if="asWorkflowRouteMetrics(item.routeMetrics)">
                        <div class="task-metric-section">
                          <div class="task-metric-title">Route Metrics</div>
                          <div class="proposal-routing-grid">
                            <div><span>Requests</span><strong>{{ formatNumber(asWorkflowRouteMetrics(item.routeMetrics)?.requestCount) }}</strong></div>
                            <div><span>Errors</span><strong>{{ formatNumber(asWorkflowRouteMetrics(item.routeMetrics)?.errorCount) }}</strong></div>
                            <div><span>Error Rate</span><strong>{{ asWorkflowRouteMetrics(item.routeMetrics)?.errorRate ?? 0 }}%</strong></div>
                            <div><span>Duration</span><strong>{{ formatDurationMinutes(asWorkflowRouteMetrics(item.routeMetrics)?.durationMs) }}</strong></div>
                            <div><span>Avg Req</span><strong>{{ formatDurationMinutes(asWorkflowRouteMetrics(item.routeMetrics)?.averageRequestDurationMs) }}</strong></div>
                            <div><span>Tool Calls</span><strong>{{ formatNumber(asWorkflowRouteMetrics(item.routeMetrics)?.toolCallCount) }}</strong></div>
                          </div>
                          <div class="role-breakdown-grid">
                            <div v-for="[role, stat] in roleMetricEntries(asWorkflowRouteMetrics(item.routeMetrics)?.roles)" :key="`${item.runId}-route-${role}`" class="role-breakdown-card">
                              <strong>{{ role }}</strong>
                              <div class="table-subtext">{{ topModelsLabel((stat as RouteMetricRoleRollup).models) }}</div>
                              <div class="detail-list compact-detail-list">
                                <div><span>Req</span><strong>{{ formatNumber((stat as RouteMetricRoleRollup).requestCount) }}</strong></div>
                                <div><span>Err</span><strong>{{ formatNumber((stat as RouteMetricRoleRollup).errorCount) }}</strong></div>
                                <div><span>Tools</span><strong>{{ formatNumber((stat as RouteMetricRoleRollup).toolCallCount) }}</strong></div>
                                <div><span>Avg</span><strong>{{ formatDurationMinutes((stat as RouteMetricRoleRollup).averageRequestDurationMs) }}</strong></div>
                              </div>
                            </div>
                          </div>
                        </div>
                      </template>
                    </article>
                    <div v-if="selectedEvaluationProjectTasks.length === 0" class="empty-state compact">No task-level evaluation records found for this project in the current statistics range.</div>
                  </div>
                </div>
              </section>
            </div>
          </section>

          <section v-else-if="activePage === 'infrastructure'" class="page active">
            <div class="page-description">Global AI infrastructure used by Nexus Agents to reduce shell-output tokens and improve code-context retrieval. These items are host-level services, not project templates.</div>
            <div class="page-actions infrastructure-actions">
              <button class="btn-primary" type="button" @click="openInfrastructureCatalog">新增基建</button>
            </div>
            <div class="asset-grid compact infrastructure-grid">
              <article
                v-for="item in infrastructureItems"
                :key="item.id"
                class="asset-card infrastructure-card"
                :class="item.id"
                @click="openInfrastructureDrawer(item)"
              >
                <div class="asset-card-header">
                  <div class="asset-icon asset-icon-process"></div>
                  <div>
                    <div class="asset-title">{{ item.name }}</div>
                    <div class="asset-meta">{{ item.kind }}</div>
                  </div>
                  <span class="chip" :class="`chip-${statusTone(item.status)}`">{{ item.status }}</span>
                </div>
                <div class="asset-card-body">
                  <div class="asset-desc">{{ item.summary }}</div>
                  <div class="asset-template-meta">
                    <span>{{ item.githubUrl.replace('https://github.com/', 'github.com/') }}</span>
                  </div>
                  <div class="asset-template-meta mono">
                    <span>{{ item.installCommand }}</span>
                  </div>
                </div>
                <div class="asset-card-footer">
                  <div class="asset-chip-row">
                    <span class="chip">{{ item.version ? `v${item.version}` : 'not checked' }}</span>
                  </div>
                  <div class="asset-actions">
                    <button class="link-btn" type="button" @click.stop="openInfrastructureDrawer(item)">查看</button>
                  </div>
                </div>
              </article>
            </div>
          </section>

          <section v-else-if="activePage === 'model-routes'" class="page active">
            <div class="page-description">Model Proxy 是全局服务配置。Projects 和模板只引用 provider route，不参与模板同步。</div>
            <div class="grid-3">
              <section class="panel panel-pad">
                <div class="section-title">Codex Router</div>
                <span class="chip chip-green">Embedded</span>
                <div class="form-hint mono">http://127.0.0.1:8766/proxy/codex/v1</div>
              </section>
              <section class="panel panel-pad">
                <div class="section-title">Model Catalog</div>
                <span class="chip chip-green">GPT + DeepSeek + GLM</span>
                <div class="form-hint mono">http://127.0.0.1:8766/proxy/codex/model-catalog.json</div>
              </section>
              <section class="panel panel-pad">
                <div class="section-title">Auth Policy</div>
                <span class="chip chip-orange">Runtime env</span>
                <div class="form-hint">GPT routes reuse Codex bearer auth. DeepSeek and GLM routes share the server-side Winky API key env var.</div>
              </section>
            </div>
            <section class="panel panel-pad">
              <div class="section-title">Codex Config</div>
              <pre class="code-block">model_provider = "nexus-codex"
model = "gpt-5.5"
model_catalog_json = "C:/Users/YOUR_USER/.codex/nexus-model-catalog.json"

[model_providers.nexus-codex]
name = "Nexus Codex"
base_url = "http://127.0.0.1:8766/proxy/codex/v1"
wire_api = "responses"
requires_openai_auth = true</pre>
            </section>
            <div class="table-wrap">
              <table>
                <thead><tr><th>Client</th><th>Source</th><th>Target</th><th>Provider</th><th>Status</th><th></th></tr></thead>
                <tbody>
                  <tr v-for="route in modelRoutes" :key="route.id">
                    <td>{{ route.client }}</td>
                    <td class="mono">{{ route.source }}</td>
                    <td>{{ route.target }}</td>
                    <td>{{ route.provider }}</td>
                    <td><span class="chip chip-green">{{ route.status }}</span></td>
                    <td class="row-actions">
                      <button class="link-btn" type="button" @click="openRouteDrawer(route)">详情</button>
                      <button class="link-btn" type="button" @click="openProxyTest(route)">测试</button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
        </template>
      </section>
    </main>

    <div v-if="showImportModal" class="modal-backdrop" @click.self="showImportModal = false">
      <section class="modal">
        <div class="modal-header"><div><div class="panel-title">导入已有项目</div><div class="drawer-subtitle">V1 会扫描项目下的 .claude / .codex 配置。</div></div><button class="icon-btn" type="button" @click="showImportModal = false">×</button></div>
        <div class="modal-body">
          <label class="field-label">项目名称<input v-model="importForm.name" class="field-input" /></label>
          <label class="field-label">项目路径
            <div class="path-picker-row">
              <input v-model="importForm.path" class="field-input mono" />
              <button class="btn-secondary" type="button" @click="chooseProjectDirectory()">选择目录</button>
            </div>
          </label>
          <div class="scan-preview">
            <span class="chip chip-green">.claude</span>
            <span class="chip chip-green">.codex</span>
            <span class="chip chip-green">.mcp.json</span>
            <span class="chip chip-orange">.agents</span>
          </div>
        </div>
        <div class="modal-footer"><button class="btn-secondary" type="button" @click="showImportModal = false">取消</button><button class="btn-primary" type="button" @click="importProjectAction">导入</button></div>
      </section>
    </div>

    <div v-if="showDirectoryBrowser" class="modal-backdrop directory-picker-backdrop" @click.self="closeDirectoryBrowser">
      <section class="modal directory-picker-modal">
        <div class="modal-header">
          <div>
            <div class="panel-title">选择项目目录</div>
            <div class="drawer-subtitle mono">{{ directoryBrowser?.path || '选择一个目录入口' }}</div>
          </div>
          <button class="icon-btn" type="button" @click="closeDirectoryBrowser">×</button>
        </div>
        <div class="modal-body directory-picker-body">
          <div class="directory-browser">
            <form class="explorer-address-bar" @submit.prevent="submitDirectoryAddress">
              <button class="btn-secondary" type="button" @click="browseDirectory(directoryBrowser?.parent)" :disabled="directoryBrowserLoading || !directoryBrowser?.parent">上一级</button>
              <input v-model="directoryAddress" class="field-input mono" placeholder="输入或粘贴目录路径，例如 D:\workspace\src" />
              <button class="btn-secondary" type="submit" :disabled="directoryBrowserLoading">转到</button>
              <button class="btn-secondary" type="button" @click="browseDirectory(directoryBrowser?.path)" :disabled="directoryBrowserLoading">刷新</button>
            </form>

            <div v-if="directoryBrowserError" class="notice danger">{{ directoryBrowserError }}</div>
            <div class="explorer-layout">
              <aside class="explorer-sidebar">
                <div class="explorer-sidebar-title">快速访问</div>
                <button
                  v-for="shortcut in directoryBrowser?.shortcuts || []"
                  :key="`shortcut-${shortcut.path}`"
                  class="explorer-nav-item"
                  type="button"
                  @click="browseDirectoryEntry(shortcut)"
                >
                  <span>★</span><span>{{ shortcut.name }}</span>
                </button>
                <div class="explorer-sidebar-title">此电脑</div>
                <button
                  v-for="root in directoryBrowser?.roots || []"
                  :key="`root-${root.path}`"
                  class="explorer-nav-item"
                  type="button"
                  @click="browseDirectoryEntry(root)"
                >
                  <span>▣</span><span>{{ root.name }}</span>
                </button>
              </aside>

              <section class="explorer-main">
                <div class="explorer-main-head">
                  <div>
                    <div class="section-title">文件夹</div>
                    <div class="form-hint">单击选中，双击进入；选中项目根目录后点击“选择此文件夹”。</div>
                  </div>
                  <span class="chip chip-purple">{{ directoryBrowser?.entries.length || 0 }} folders</span>
                </div>
                <div v-if="directoryBrowserLoading" class="empty-state compact">正在读取目录...</div>
                <div v-else class="directory-list explorer-directory-list">
                  <button
                    v-if="directoryBrowser?.parent"
                    class="directory-row"
                    type="button"
                    @click="selectDirectory(directoryBrowser.parent)"
                    @dblclick="browseDirectory(directoryBrowser.parent)"
                  >
                    <span class="directory-icon">↟</span>
                    <span class="directory-name">上一级</span>
                    <span class="directory-path mono">{{ directoryBrowser.parent }}</span>
                  </button>
                  <button
                    v-for="entry in directoryBrowser?.entries || []"
                    :key="entry.path"
                    class="directory-row"
                    :class="{ selected: selectedDirectoryPath === entry.path }"
                    type="button"
                    @click="selectDirectory(entry.path)"
                    @dblclick="browseDirectory(entry.path)"
                  >
                    <span class="directory-icon">📁</span>
                    <span class="directory-name">{{ entry.name }}</span>
                    <span class="directory-path mono">{{ entry.path }}</span>
                  </button>
                  <div v-if="directoryBrowser && directoryBrowser.entries.length === 0" class="form-hint">当前目录没有可继续进入的子目录。</div>
                </div>
              </section>
            </div>

            <div class="explorer-selected">
              <span>已选文件夹</span>
              <strong class="explorer-selected-path mono">{{ selectedDirectoryPath || directoryBrowser?.path || "尚未选择" }}</strong>
              <button class="btn-secondary" type="button" @click="openSelectedDirectory" :disabled="!selectedDirectoryPath || directoryBrowserLoading">打开</button>
            </div>
          </div>
        </div>
        <div class="modal-footer">
          <button class="btn-secondary" type="button" @click="closeDirectoryBrowser">取消</button>
          <button class="btn-primary" type="button" @click="useBrowsedDirectory" :disabled="!(selectedDirectoryPath || directoryBrowser?.path)">选择此文件夹</button>
        </div>
      </section>
    </div>

    <div v-if="drawerMode" class="drawer-backdrop" @click.self="closeDrawer">
      <aside class="drawer">
        <template v-if="drawerMode === 'template' && selectedTemplate">
          <div class="drawer-header">
            <div><div class="drawer-title">{{ selectedTemplate.name }}</div><div class="drawer-subtitle">{{ kindLabel(selectedTemplate.kind) }} Template / {{ selectedTemplate.id }}</div></div>
            <button class="icon-btn" type="button" @click="closeDrawer">×</button>
          </div>
          <div class="drawer-body template-drawer-body">
            <label class="field-label">Name<input v-model="templateForm.name" class="field-input" :readonly="templateDrawerReadOnly" /></label>
            <label class="field-label">Summary<textarea v-model="templateForm.summary" class="field-textarea template-summary-textarea" :readonly="templateDrawerReadOnly"></textarea></label>
            <div class="detail-list template-detail-list">
              <div><span>slug</span><strong>{{ selectedTemplate.slug }}</strong></div>
              <div><span>version</span><strong>v{{ selectedTemplate.version }}</strong></div>
              <div><span>entry</span><strong>{{ selectedTemplate.entry || '-' }}</strong></div>
              <div><span>used by</span><strong>{{ templateUsageCount(selectedTemplate.id) }} projects</strong></div>
              <div v-if="selectedTemplate.modelTier"><span>model tier</span><strong>{{ selectedTemplate.modelTier }}</strong></div>
              <div v-if="selectedTemplate.relatedRules?.length"><span>related rules</span><strong>{{ selectedTemplate.relatedRules.join(', ') }}</strong></div>
              <div v-if="selectedTemplate.relatedSkills?.length"><span>related skills</span><strong>{{ selectedTemplate.relatedSkills.join(', ') }}</strong></div>
              <div v-if="selectedTemplate.applicableAgents?.length"><span>applicable agents</span><strong>{{ selectedTemplate.applicableAgents.join(', ') }}</strong></div>
              <div v-if="selectedTemplate.tools?.length"><span>tools</span><strong>{{ selectedTemplate.tools.join(', ') }}</strong></div>
              <div v-if="selectedTemplate.mcp?.length"><span>MCP</span><strong>{{ selectedTemplate.mcp.join(', ') }}</strong></div>
              <div v-if="selectedTemplate.claudeSource"><span>Claude source</span><strong>{{ selectedTemplate.claudeSource }}</strong></div>
            </div>
            <div class="drawer-preview-stack">
              <pre class="code-block drawer-preview-block">{{ selectedTemplate.content || selectedTemplate.files?.join('\n') || 'single-file template' }}</pre>
              <pre v-if="selectedTemplate.codexProjection" class="code-block drawer-preview-block">{{ selectedTemplate.codexProjection }}</pre>
              <pre v-if="selectedTemplate.sourcePaths?.length" class="code-block drawer-preview-block compact">{{ selectedTemplate.sourcePaths.join('\n') }}</pre>
            </div>
            <div v-if="templateDrawerReadOnly" class="notice">Rule / Skill 内容在 V1 抽屉中只读；需要沉淀新版本时，请在模板库手动新建模板。</div>
          </div>
          <div class="drawer-footer">
            <button class="btn-secondary danger" type="button" @click="deleteSelectedTemplate">删除模板</button>
            <button class="btn-primary" type="button" @click="saveTemplateDrawer">保存</button>
          </div>
        </template>

        <template v-else-if="drawerMode === 'project-copy' && selectedProjectCopy">
          <div class="drawer-header">
            <div><div class="drawer-title">{{ selectedProjectCopy.name }}</div><div class="drawer-subtitle">{{ selectedProjectCopy.kind }} copy / {{ currentProject?.name }}</div></div>
            <button class="icon-btn" type="button" @click="closeDrawer">×</button>
          </div>
          <div class="drawer-body project-copy-drawer-body">
            <div class="detail-list compact-detail-list">
              <div><span>status</span><strong>{{ selectedProjectCopy.status }}</strong></div>
              <div><span>path</span><strong>{{ selectedProjectCopy.path }}</strong></div>
              <div><span>origin.templateId</span><strong>{{ selectedProjectCopy.origin?.templateId || 'project-only' }}</strong></div>
              <div><span>origin.baseVersion</span><strong>{{ selectedProjectCopy.origin?.baseVersion || '-' }}</strong></div>
              <div><span>origin.baseHash</span><strong>{{ selectedProjectCopy.origin?.baseHash || '-' }}</strong></div>
              <div><span>localVersion</span><strong>{{ selectedProjectCopy.localVersion }}</strong></div>
              <div><span>syncMode</span><strong>{{ selectedProjectCopy.syncMode }}</strong></div>
            </div>
            <div class="drawer-preview-stack">
              <pre class="code-block drawer-preview-block">{{ selectedProjectCopy.content || selectedProjectCopy.diff || 'No project content loaded.' }}</pre>
              <pre v-if="selectedProjectCopy.diff" class="code-block drawer-preview-block compact">{{ selectedProjectCopy.diff }}</pre>
              <pre v-if="selectedProjectCopy.sourcePaths?.length" class="code-block drawer-preview-block compact">{{ selectedProjectCopy.sourcePaths.join('\n') }}</pre>
            </div>
          </div>
          <div class="drawer-footer">
            <button class="btn-secondary" type="button" @click="detachSelectedProjectCopy">解除关联</button>
            <button class="btn-primary" type="button" @click="syncSelectedProjectCopy">同步模板</button>
          </div>
        </template>

        <template v-else-if="drawerMode === 'sync-preview'">
          <div class="drawer-header">
            <div><div class="drawer-title">同步预览</div><div class="drawer-subtitle">{{ currentProject?.name }} / Template Library -> Project Config Set</div></div>
            <button class="icon-btn" type="button" @click="closeDrawer">×</button>
          </div>
          <div class="drawer-body">
            <div v-for="copy in syncPreview" :key="copy.id" class="sync-preview-item">
              <div class="sync-preview-head">
                <strong>{{ copy.kind }} / {{ copy.name }}</strong>
                <span class="chip" :class="`chip-${statusTone(copy.status)}`">{{ copy.status }}</span>
              </div>
              <div class="detail-list">
                <div><span>origin.templateId</span><strong>{{ copy.origin?.templateId || 'project-only' }}</strong></div>
                <div><span>baseVersion</span><strong>{{ copy.origin?.baseVersion || '-' }}</strong></div>
                <div><span>baseHash</span><strong>{{ copy.origin?.baseHash || '-' }}</strong></div>
                <div><span>localVersion</span><strong>{{ copy.localVersion }}</strong></div>
                <div><span>syncMode</span><strong>{{ copy.syncMode }}</strong></div>
              </div>
              <pre class="code-block">{{ copy.diff }}</pre>
            </div>
          </div>
          <div class="drawer-footer"><button class="btn-primary" type="button" @click="showToast('同步预览不会自动覆盖项目副本。')">确认预览</button></div>
        </template>

        <template v-else-if="drawerMode === 'infrastructure' && selectedInfrastructure">
          <div class="drawer-header">
            <div><div class="drawer-title">{{ selectedInfrastructure.name }}</div><div class="drawer-subtitle">System Infrastructure / {{ selectedInfrastructure.kind }}</div></div>
            <button class="icon-btn" type="button" @click="closeDrawer">x</button>
          </div>
          <div class="drawer-body">
            <div class="detail-list">
              <div><span>status</span><strong>{{ selectedInfrastructure.status }}</strong></div>
              <div><span>version</span><strong>{{ selectedInfrastructure.version || '-' }}</strong></div>
              <div><span>executable</span><strong>{{ selectedInfrastructure.executablePath || '-' }}</strong></div>
            </div>
            <div class="notice">{{ selectedInfrastructure.description }}</div>
            <div class="detail-list">
              <div><span>GitHub</span><strong>{{ selectedInfrastructure.githubUrl }}</strong></div>
              <div><span>Install</span><strong>{{ selectedInfrastructure.installCommand }}</strong></div>
            </div>
            <div class="panel panel-pad">
              <div class="section-title">Common Commands</div>
              <div class="infra-command-list">
                <code v-for="command in selectedInfrastructure.commonCommands" :key="command">{{ command }}</code>
              </div>
            </div>
            <pre class="code-block">{{ infrastructureFeedback || selectedInfrastructure.output || 'No check output yet.' }}</pre>
          </div>
          <div class="drawer-footer">
            <button class="btn-secondary" type="button" @click="openInfrastructureGitHub">Open GitHub</button>
            <button class="btn-primary" type="button" :disabled="infrastructureBusy" @click="updateSelectedInfrastructure">Update Version</button>
          </div>
        </template>

        <template v-else-if="drawerMode === 'infrastructure-catalog'">
          <div class="drawer-header">
            <div><div class="drawer-title">新增 AI 基建</div><div class="drawer-subtitle">Curated third-party infrastructure</div></div>
            <button class="icon-btn" type="button" @click="closeDrawer">x</button>
          </div>
          <div class="drawer-body">
            <div class="notice">这里只列出 Nexus Agents 维护的白名单基建项；安装时后端只执行固定命令，不接收自定义 shell。</div>
            <div class="infra-candidate-list">
              <article
                v-for="candidate in availableInfrastructureItems"
                :key="candidate.id"
                class="asset-card infrastructure-card"
                :class="{ selected: selectedInfrastructure?.id === candidate.id }"
                @click="selectInfrastructureCandidate(candidate)"
              >
                <div class="asset-card-header">
                  <div class="asset-icon asset-icon-process"></div>
                  <div>
                    <div class="asset-title">{{ candidate.name }}</div>
                    <div class="asset-meta">{{ candidate.kind }}</div>
                  </div>
                  <span class="chip" :class="`chip-${statusTone(candidate.status)}`">{{ candidate.status }}</span>
                </div>
                <div class="asset-card-body">
                  <div class="asset-desc">{{ candidate.summary }}</div>
                  <div class="asset-template-meta">
                    <span>{{ candidate.githubUrl.replace('https://github.com/', 'github.com/') }}</span>
                  </div>
                  <div class="asset-template-meta mono">
                    <span>{{ candidate.installCommand }}</span>
                  </div>
                </div>
                <div class="asset-card-footer">
                  <div class="asset-chip-row">
                    <span class="chip">{{ candidate.source }}</span>
                  </div>
                  <div class="asset-actions">
                    <button class="link-btn" type="button" @click.stop="selectInfrastructureCandidate(candidate)">查看</button>
                  </div>
                </div>
              </article>
            </div>
            <template v-if="selectedInfrastructure">
              <div class="detail-list">
                <div><span>GitHub</span><strong>{{ selectedInfrastructure.githubUrl }}</strong></div>
                <div><span>Install</span><strong>{{ selectedInfrastructure.installCommand }}</strong></div>
                <div><span>source</span><strong>{{ selectedInfrastructure.source }}</strong></div>
                <div><span>status</span><strong>{{ selectedInfrastructure.status }}</strong></div>
              </div>
              <div class="notice">{{ selectedInfrastructure.description }}</div>
              <pre class="code-block">{{ infrastructureFeedback || selectedInfrastructure.output || 'No install output yet.' }}</pre>
            </template>
          </div>
          <div class="drawer-footer">
            <button class="btn-secondary" type="button" @click="closeDrawer">取消</button>
            <button class="btn-primary" type="button" :disabled="infrastructureBusy || !selectedInfrastructure?.installable" @click="installSelectedInfrastructure">安装</button>
          </div>
        </template>

        <template v-else-if="drawerMode === 'route' && selectedRoute">
          <div class="drawer-header">
            <div><div class="drawer-title">{{ selectedRoute.source }}</div><div class="drawer-subtitle">{{ selectedRoute.client }} / {{ selectedRoute.provider }}</div></div>
            <button class="icon-btn" type="button" @click="closeDrawer">×</button>
          </div>
          <div class="drawer-body">
            <div class="detail-list">
              <div><span>target</span><strong>{{ selectedRoute.target }}</strong></div>
              <div><span>endpoint</span><strong>{{ selectedRoute.endpoint }}</strong></div>
              <div><span>auth</span><strong>{{ selectedRoute.auth }}</strong></div>
              <div><span>status</span><strong>{{ selectedRoute.status }}</strong></div>
            </div>
            <div v-if="routeSaveFeedback" class="notice">{{ routeSaveFeedback }}</div>
            <div class="notice">模型代理是全局服务配置，不会被复制到项目配置副本中。</div>
          </div>
          <div class="drawer-footer">
            <button class="btn-secondary" type="button" @click="saveRouteMock">保存配置</button>
            <button class="btn-primary" type="button" @click="openProxyTest(selectedRoute)">测试路由</button>
          </div>
        </template>

        <template v-else-if="drawerMode === 'proxy-test' && selectedRoute">
          <div class="drawer-header">
            <div><div class="drawer-title">代理测试</div><div class="drawer-subtitle">{{ selectedRoute.client }} / {{ modelForRoute(selectedRoute) }}</div></div>
            <button class="icon-btn" type="button" @click="closeDrawer">×</button>
          </div>
          <div class="drawer-body">
            <div class="page-actions">
              <button class="btn-secondary" type="button" @click="runProxyTest">重新测试</button>
              <button class="btn-secondary danger" type="button" @click="runProxyFailureTest">失败样例</button>
            </div>
            <pre v-if="proxyResult" class="code-block">{{ JSON.stringify(proxyResult, null, 2) }}</pre>
            <div v-else-if="proxyError" class="notice danger">{{ proxyError }}</div>
            <div v-else class="notice">正在解析路由...</div>
          </div>
        </template>

        <template v-else-if="drawerMode === 'workflow-run'">
          <div class="drawer-header">
            <div><div class="drawer-title">模拟运行</div><div class="drawer-subtitle">{{ workflowGraph?.name || selectedWorkflowId }}</div></div>
            <button class="icon-btn" type="button" @click="closeDrawer">×</button>
          </div>
          <div class="drawer-body">
            <div class="run-steps">
              <div class="run-step done">接收请求和上下文</div>
              <div class="run-step done">并行派发 reviewer</div>
              <div class="run-step active">合并 findings</div>
              <div class="run-step">等待 human approval</div>
            </div>
            <div class="notice">当前已产品化为自动提交 Task Run。点击下方按钮会把当前 Go / Unity workflow 结果自动 POST 到 <code>/api/task-runs</code>。</div>
            <div v-if="workflowRunError" class="notice danger">{{ workflowRunError }}</div>
            <div v-if="workflowRunResult" class="notice">已提交：run={{ workflowRunResult.id }} / workflow={{ workflowRunResult.workflowType }} / project={{ workflowRunResult.projectId }}</div>
            <pre v-if="workflowRunPayload" class="code-block drawer-preview-block compact">{{ JSON.stringify(workflowRunPayload, null, 2) }}</pre>
          </div>
          <div class="drawer-footer">
            <button class="btn-secondary" type="button" @click="closeDrawer">关闭</button>
            <button class="btn-primary" type="button" :disabled="workflowRunSubmitting || !workflowRunPayload" @click="submitWorkflowRun">
              {{ workflowRunSubmitting ? '提交中...' : '自动上传 Task Run' }}
            </button>
          </div>
        </template>
      </aside>
    </div>

    <div v-if="toast" class="toast open">{{ toast }}</div>
  </div>
</template>
