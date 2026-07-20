import type {
  BootstrapData,
  Evaluation,
  EvaluationReviewInput,
  EvaluationSummary,
  EvaluationProjectHealth,
  EvaluationProposal,
  EvaluationProposalReviewInput,
  EvaluationProposalsResponse,
  EvaluationProjectsResponse,
  InfrastructureItem,
  KnowledgeExportData,
  KnowledgeExportManifest,
  KnowledgeGraphSyncResponse,
  KnowledgeGraphSyncRun,
  KnowledgeGraphSyncState,
  KnowledgeGraphShadowRun,
  KnowledgeGraphShadowSearchResponse,
  KnowledgeGraphShadowSummary,
  KnowledgeMaintenanceReport,
  KnowledgeRenderedDocument,
  KnowledgeRetrievalResult,
  KnowledgeRenderTree,
  KnowledgeRoutePreview,
  KnowledgeValidationReport,
  KnowledgeDiscoveryResponse,
  KnowledgeProposal,
  KnowledgeSyncProfile,
  KnowledgeSyncProfileResponse,
  KnowledgeSyncResult,
  KnowledgeSyncRun,
  KnowledgeSyncState,
  LearningCase,
  LearningCaseHit,
  StatisticsTasksResponse,
  LocalDirectoryPickerResponse,
  LocalDirectoriesResponse,
  ModelRoute,
  ModelRouteResolution,
  Project,
  ProjectCopy,
  ProjectCopyKind,
  ProjectGroup,
  ProjectGroupInput,
  ProjectInput,
  ProjectRescanResult,
  ProjectTemplateSyncResult,
  ProjectWorkflowCreateResult,
  TemplateInput,
  TemplateInitializationInput,
  TemplateInitializationPreview,
  TemplateInitializationResult,
  TemplateItem,
  TemplateKind,
  TaskRun,
  TaskRunInput,
  WorkflowRunFinishInput,
  WorkflowGraph,
  WorkflowInput,
  WorkflowRunRecord,
  WorkflowRunStartInput,
  WorkflowSummary,
} from "./types";

async function fetchJSON<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    throw new Error(`${path} returned ${response.status}`);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export function fetchBootstrap(): Promise<BootstrapData> {
  return fetchJSON<BootstrapData>("/api/bootstrap");
}

export function createTaskRun(input: TaskRunInput): Promise<TaskRun> {
  const sessionId =
    (typeof input.sessionId === "string" ? input.sessionId.trim() : "") ||
    (typeof input.context?.sessionId === "string" ? input.context.sessionId.trim() : "");
  return fetchJSON<TaskRun>("/api/task-runs", {
    method: "POST",
    headers: sessionId ? { "Session-Id": sessionId } : undefined,
    body: JSON.stringify(input),
  });
}

export function fetchTaskRuns(): Promise<TaskRun[]> {
  return fetchJSON<TaskRun[]>("/api/task-runs");
}

export async function deleteTaskRun(taskRunId: string): Promise<void> {
  await fetchJSON<void>(`/api/task-runs/${encodeURIComponent(taskRunId)}`, {
    method: "DELETE",
  });
}

export function startWorkflowRun(input: WorkflowRunStartInput): Promise<WorkflowRunRecord> {
  return fetchJSON<WorkflowRunRecord>("/api/workflow-runs/start", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function completeWorkflowRun(runId: string, input: WorkflowRunFinishInput): Promise<{ run: WorkflowRunRecord; taskRun: TaskRun }> {
  return fetchJSON<{ run: WorkflowRunRecord; taskRun: TaskRun }>(`/api/workflow-runs/${encodeURIComponent(runId)}/complete`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function failWorkflowRun(runId: string, input: WorkflowRunFinishInput): Promise<{ run: WorkflowRunRecord; taskRun: TaskRun }> {
  return fetchJSON<{ run: WorkflowRunRecord; taskRun: TaskRun }>(`/api/workflow-runs/${encodeURIComponent(runId)}/fail`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function fetchEvaluations(): Promise<Evaluation[]> {
  return fetchJSON<Evaluation[]>("/api/evaluations");
}

export function runPendingEvaluations(): Promise<{ evaluated: number }> {
  return fetchJSON<{ evaluated: number }>("/api/evaluations/run-pending", {
    method: "POST",
  });
}

export function fetchEvaluationSummary(): Promise<EvaluationSummary> {
  return fetchJSON<EvaluationSummary>("/api/evaluations/summary");
}

export function reviewEvaluation(evaluationId: string, input: EvaluationReviewInput): Promise<unknown> {
  return fetchJSON<unknown>(`/api/evaluations/${encodeURIComponent(evaluationId)}/review`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}


export function fetchEvaluationProjects(): Promise<EvaluationProjectsResponse> {
  return fetchJSON<EvaluationProjectsResponse>("/api/evaluation/projects");
}

export function fetchEvaluationProposals(projectId?: string, status?: string): Promise<EvaluationProposalsResponse> {
  const params = new URLSearchParams();
  if (projectId) params.set("projectId", projectId);
  if (status) params.set("status", status);
  const query = params.toString();
  return fetchJSON<EvaluationProposalsResponse>(`/api/evaluation/proposals${query ? `?${query}` : ""}`);
}

export function reviewEvaluationProposal(proposalId: string, input: EvaluationProposalReviewInput): Promise<EvaluationProposal> {
  return fetchJSON<EvaluationProposal>(`/api/evaluation/proposals/${encodeURIComponent(proposalId)}/review`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function fetchStatisticsTasks(view: string, range: string): Promise<StatisticsTasksResponse> {
  const params = new URLSearchParams({ view, range });
  return fetchJSON<StatisticsTasksResponse>(`/api/statistics/tasks?${params.toString()}`);
}

export function fetchLearningCases(): Promise<LearningCase[]> {
  return fetchJSON<LearningCase[]>("/api/learning-cases");
}

export function searchLearningCases(query: string, limit = 5): Promise<LearningCaseHit[]> {
  const params = new URLSearchParams({ q: query, limit: String(limit) });
  return fetchJSON<LearningCaseHit[]>(`/api/learning-cases/search?${params.toString()}`);
}

export function rebuildLearningCaseIndex(): Promise<{ indexed: number }> {
  return fetchJSON<{ indexed: number }>("/api/learning-cases/rebuild-index", {
    method: "POST",
  });
}

export function fetchProjects(): Promise<Project[]> {
  return fetchJSON<Project[]>("/api/projects");
}

export function fetchProjectKnowledgeExport(projectId: string): Promise<KnowledgeExportData> {
  return fetchJSON<KnowledgeExportData>(`/api/projects/${encodeURIComponent(projectId)}/knowledge/export`);
}

export function refreshProjectKnowledgeExport(projectId: string): Promise<KnowledgeExportManifest> {
  return fetchJSON<KnowledgeExportManifest>(`/api/projects/${encodeURIComponent(projectId)}/knowledge/export/refresh`, {
    method: "POST",
  });
}

export function validateProjectKnowledge(projectId: string): Promise<KnowledgeValidationReport> {
  return fetchJSON<KnowledgeValidationReport>(`/api/projects/${encodeURIComponent(projectId)}/knowledge/validate`);
}

export function previewProjectKnowledgeRoute(projectId: string, task: string): Promise<KnowledgeRoutePreview> {
  return fetchJSON<KnowledgeRoutePreview>(`/api/projects/${encodeURIComponent(projectId)}/knowledge/route?task=${encodeURIComponent(task)}`);
}

export function retrieveProjectKnowledge(projectId: string, query: string, mode = "routing", maxTokens = 6000): Promise<KnowledgeRetrievalResult> {
  return fetchJSON<KnowledgeRetrievalResult>(`/api/projects/${encodeURIComponent(projectId)}/knowledge/retrieve?q=${encodeURIComponent(query)}&mode=${encodeURIComponent(mode)}&maxTokens=${encodeURIComponent(String(maxTokens))}`);
}

export function fetchProjectKnowledgeTree(projectId: string): Promise<KnowledgeRenderTree> {
  return fetchJSON<KnowledgeRenderTree>(`/api/projects/${encodeURIComponent(projectId)}/knowledge/render`);
}

export function fetchProjectKnowledgeDocument(projectId: string, path: string): Promise<KnowledgeRenderedDocument> {
  return fetchJSON<KnowledgeRenderedDocument>(`/api/projects/${encodeURIComponent(projectId)}/knowledge/render?path=${encodeURIComponent(path)}`);
}

export function fetchProjectKnowledgeExportDocument(projectId: string, path: string): Promise<KnowledgeRenderedDocument> {
  return fetchJSON<KnowledgeRenderedDocument>(`/api/projects/${encodeURIComponent(projectId)}/knowledge/export/doc?path=${encodeURIComponent(path)}`);
}

export function fetchProjectKnowledgeMaintenance(projectId: string): Promise<KnowledgeMaintenanceReport> {
  return fetchJSON<KnowledgeMaintenanceReport>(`/api/projects/${encodeURIComponent(projectId)}/knowledge/maintenance`);
}

export function fetchKnowledgeSyncProfile(projectId: string): Promise<KnowledgeSyncProfileResponse> {
  return fetchJSON<KnowledgeSyncProfileResponse>(`/api/projects/${encodeURIComponent(projectId)}/knowledge/sync-profile`);
}

export function saveKnowledgeSyncProfile(projectId: string, profile: KnowledgeSyncProfile): Promise<KnowledgeSyncProfileResponse> {
  return fetchJSON<KnowledgeSyncProfileResponse>(`/api/projects/${encodeURIComponent(projectId)}/knowledge/sync-profile`, {
    method: "PUT",
    body: JSON.stringify(profile),
  });
}

export function fetchKnowledgeSyncStatus(projectId: string): Promise<KnowledgeSyncState> {
  return fetchJSON<KnowledgeSyncState>(`/api/projects/${encodeURIComponent(projectId)}/knowledge/sync-status`);
}

export function discoverKnowledgePolicy(projectId: string): Promise<KnowledgeDiscoveryResponse> {
  return fetchJSON<KnowledgeDiscoveryResponse>(`/api/projects/${encodeURIComponent(projectId)}/knowledge/discovery-preview`, {
    method: "POST",
  });
}

export function initializeKnowledgePreview(
  projectId: string,
  input: { mode?: string; externalReferences?: Array<{ url: string; kind?: string }> } = {},
): Promise<KnowledgeSyncResult> {
  return fetchJSON<KnowledgeSyncResult>(`/api/projects/${encodeURIComponent(projectId)}/knowledge/initialize-preview`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function checkKnowledgeUpdates(projectId: string): Promise<KnowledgeSyncResult> {
  return fetchJSON<KnowledgeSyncResult>(`/api/projects/${encodeURIComponent(projectId)}/knowledge/check-updates`, {
    method: "POST",
  });
}

export function fetchKnowledgeSyncRuns(projectId: string): Promise<KnowledgeSyncRun[]> {
  return fetchJSON<KnowledgeSyncRun[]>(`/api/projects/${encodeURIComponent(projectId)}/knowledge/runs`);
}

export function fetchKnowledgeGraphStatus(projectId: string): Promise<KnowledgeGraphSyncState> {
  return fetchJSON<KnowledgeGraphSyncState>(`/api/projects/${encodeURIComponent(projectId)}/knowledge/graph/status`);
}

export function fetchKnowledgeGraphRuns(projectId: string): Promise<KnowledgeGraphSyncRun[]> {
  return fetchJSON<KnowledgeGraphSyncRun[]>(`/api/projects/${encodeURIComponent(projectId)}/knowledge/graph/runs`);
}

export function syncKnowledgeGraph(projectId: string): Promise<KnowledgeGraphSyncResponse> {
  return fetchJSON<KnowledgeGraphSyncResponse>(`/api/projects/${encodeURIComponent(projectId)}/knowledge/graph/sync`, {
    method: "POST",
  });
}

export function rebuildKnowledgeGraph(projectId: string): Promise<KnowledgeGraphSyncResponse> {
  return fetchJSON<KnowledgeGraphSyncResponse>(`/api/projects/${encodeURIComponent(projectId)}/knowledge/graph/rebuild`, {
    method: "POST",
  });
}

export function searchKnowledgeGraph(
  projectId: string,
  query: string,
  expectedPaths: string[] = [],
): Promise<KnowledgeGraphShadowSearchResponse> {
  return fetchJSON<KnowledgeGraphShadowSearchResponse>(`/api/projects/${encodeURIComponent(projectId)}/knowledge/graph/shadow-search`, {
    method: "POST",
    body: JSON.stringify({ query, mode: "routing", maxTokens: 6000, expectedPaths }),
  });
}

export function fetchKnowledgeGraphShadowRuns(projectId: string, limit = 50): Promise<KnowledgeGraphShadowRun[]> {
  return fetchJSON<KnowledgeGraphShadowRun[]>(`/api/projects/${encodeURIComponent(projectId)}/knowledge/graph/shadow-runs?limit=${encodeURIComponent(String(limit))}`);
}

export function fetchKnowledgeGraphShadowSummary(projectId: string): Promise<KnowledgeGraphShadowSummary> {
  return fetchJSON<KnowledgeGraphShadowSummary>(`/api/projects/${encodeURIComponent(projectId)}/knowledge/graph/shadow-summary`);
}

export function fetchKnowledgeProposals(projectId: string): Promise<KnowledgeProposal[]> {
  return fetchJSON<KnowledgeProposal[]>(`/api/projects/${encodeURIComponent(projectId)}/knowledge/proposals`);
}

export function fetchKnowledgeProposal(projectId: string, proposalId: string): Promise<KnowledgeProposal> {
  return fetchJSON<KnowledgeProposal>(`/api/projects/${encodeURIComponent(projectId)}/knowledge/proposals/${encodeURIComponent(proposalId)}`);
}

export function applyKnowledgeProposal(projectId: string, proposalId: string, paths: string[] = []): Promise<KnowledgeSyncResult> {
  return fetchJSON<KnowledgeSyncResult>(`/api/projects/${encodeURIComponent(projectId)}/knowledge/proposals/${encodeURIComponent(proposalId)}/apply`, {
    method: "POST",
    body: JSON.stringify({ paths }),
  });
}

export function rejectKnowledgeProposal(projectId: string, proposalId: string): Promise<KnowledgeSyncResult> {
  return fetchJSON<KnowledgeSyncResult>(`/api/projects/${encodeURIComponent(projectId)}/knowledge/proposals/${encodeURIComponent(proposalId)}/reject`, {
    method: "POST",
  });
}

export function fetchLocalDirectories(path?: string): Promise<LocalDirectoriesResponse> {
  const query = path ? `?path=${encodeURIComponent(path)}` : "";
  return fetchJSON<LocalDirectoriesResponse>(`/api/local-directories${query}`);
}

export async function fetchNativeLocalDirectory(signal?: AbortSignal): Promise<LocalDirectoryPickerResponse | null> {
  const response = await fetch("/api/local-directory-picker", {
    method: "POST",
    signal,
    headers: { Accept: "application/json" },
  });
  if (response.status === 204) {
    return null;
  }
  if (!response.ok) {
    throw new Error(`/api/local-directory-picker returned ${response.status}`);
  }
  return (await response.json()) as LocalDirectoryPickerResponse;
}

export function importProject(input: ProjectInput): Promise<Project> {
  return fetchJSON<Project>("/api/projects/import", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function fetchProjectGroups(): Promise<ProjectGroup[]> {
  return fetchJSON<ProjectGroup[]>("/api/project-groups");
}

export function createProjectGroup(input: ProjectGroupInput): Promise<ProjectGroup> {
  return fetchJSON<ProjectGroup>("/api/project-groups", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateProjectGroup(groupId: string, input: ProjectGroupInput): Promise<ProjectGroup> {
  return fetchJSON<ProjectGroup>(`/api/project-groups/${encodeURIComponent(groupId)}`, {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export async function deleteProjectGroup(groupId: string): Promise<void> {
  await fetchJSON<void>(`/api/project-groups/${encodeURIComponent(groupId)}`, {
    method: "DELETE",
  });
}

export function updateProjectGroups(projectId: string, groupIds: string[]): Promise<ProjectGroup[]> {
  return fetchJSON<ProjectGroup[]>(`/api/projects/${encodeURIComponent(projectId)}/groups`, {
    method: "PUT",
    body: JSON.stringify({ groupIds }),
  });
}

export function fetchProject(projectId: string): Promise<Project> {
  return fetchJSON<Project>(`/api/projects/${encodeURIComponent(projectId)}`);
}

export async function deleteProject(projectId: string): Promise<void> {
  await fetchJSON<void>(`/api/projects/${encodeURIComponent(projectId)}`, {
    method: "DELETE",
  });
}

export function fetchProjectConfig(projectId: string, kind?: string): Promise<ProjectCopy[]> {
  const query = kind ? `?kind=${encodeURIComponent(kind)}` : "";
  return fetchJSON<ProjectCopy[]>(`/api/projects/${encodeURIComponent(projectId)}/config${query}`);
}

export function rescanProject(projectId: string): Promise<ProjectRescanResult> {
  return fetchJSON<ProjectRescanResult>(`/api/projects/${encodeURIComponent(projectId)}/rescan`, {
    method: "POST",
  });
}

export function syncProjectTemplates(projectId: string): Promise<ProjectTemplateSyncResult> {
  return fetchJSON<ProjectTemplateSyncResult>(`/api/projects/${encodeURIComponent(projectId)}/template-sync`, {
    method: "POST",
  });
}

export function fetchSyncPreview(projectId: string): Promise<ProjectCopy[]> {
  return fetchJSON<ProjectCopy[]>(`/api/projects/${encodeURIComponent(projectId)}/sync-preview`);
}

export function syncProjectCopy(projectId: string, copyId: string): Promise<ProjectCopy> {
  return fetchJSON<ProjectCopy>(`/api/projects/${encodeURIComponent(projectId)}/config/${encodeURIComponent(copyId)}/sync`, {
    method: "POST",
  });
}

export function detachProjectCopy(projectId: string, copyId: string): Promise<ProjectCopy> {
  return fetchJSON<ProjectCopy>(`/api/projects/${encodeURIComponent(projectId)}/config/${encodeURIComponent(copyId)}/detach`, {
    method: "POST",
  });
}

export function addProjectCopyFromTemplate(projectId: string, kind: ProjectCopyKind, templateId: string): Promise<ProjectCopy> {
  return fetchJSON<ProjectCopy>(`/api/projects/${encodeURIComponent(projectId)}/config/from-template`, {
    method: "POST",
    body: JSON.stringify({ kind, templateId }),
  });
}

export function fetchTemplates(kind: TemplateKind): Promise<TemplateItem[]> {
  return fetchJSON<TemplateItem[]>(`/api/templates/${kind}`);
}

export function previewTemplateInitialization(input: TemplateInitializationInput): Promise<TemplateInitializationPreview> {
  return fetchJSON<TemplateInitializationPreview>("/api/templates/initialize/preview", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function applyTemplateInitialization(planId: string): Promise<TemplateInitializationResult> {
  return fetchJSON<TemplateInitializationResult>("/api/templates/initialize/apply", {
    method: "POST",
    body: JSON.stringify({ planId }),
  });
}

export function createTemplate(kind: TemplateKind, input: TemplateInput): Promise<TemplateItem> {
  return fetchJSON<TemplateItem>(`/api/templates/${kind}`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateTemplate(kind: TemplateKind, templateId: string, input: TemplateInput): Promise<TemplateItem> {
  return fetchJSON<TemplateItem>(`/api/templates/${kind}/${encodeURIComponent(templateId)}`, {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export async function deleteTemplate(kind: TemplateKind, templateId: string): Promise<void> {
  await fetchJSON<void>(`/api/templates/${kind}/${encodeURIComponent(templateId)}`, {
    method: "DELETE",
  });
}

export function fetchWorkflows(): Promise<WorkflowSummary[]> {
  return fetchJSON<WorkflowSummary[]>("/api/workflows");
}

export function createWorkflow(input: WorkflowInput): Promise<WorkflowSummary> {
  return fetchJSON<WorkflowSummary>("/api/workflows", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateWorkflow(workflowId: string, input: WorkflowInput): Promise<WorkflowSummary> {
  return fetchJSON<WorkflowSummary>(`/api/workflows/${encodeURIComponent(workflowId)}`, {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function duplicateWorkflow(workflowId: string): Promise<WorkflowSummary> {
  return fetchJSON<WorkflowSummary>(`/api/workflows/${encodeURIComponent(workflowId)}/duplicate`, {
    method: "POST",
  });
}

export async function deleteWorkflow(workflowId: string): Promise<void> {
  await fetchJSON<void>(`/api/workflows/${encodeURIComponent(workflowId)}`, {
    method: "DELETE",
  });
}

export function fetchWorkflowGraph(workflowId: string): Promise<WorkflowGraph> {
  return fetchJSON<WorkflowGraph>(`/api/workflows/${encodeURIComponent(workflowId)}`);
}

export function updateWorkflowGraph(workflowId: string, graph: WorkflowGraph): Promise<WorkflowGraph> {
  return fetchJSON<WorkflowGraph>(`/api/workflows/${encodeURIComponent(workflowId)}/graph`, {
    method: "PUT",
    body: JSON.stringify(graph),
  });
}

export function fetchProjectWorkflowGraph(projectId: string, copyId: string): Promise<WorkflowGraph> {
  return fetchJSON<WorkflowGraph>(`/api/projects/${encodeURIComponent(projectId)}/config/${encodeURIComponent(copyId)}/graph`);
}

export function updateProjectWorkflowGraph(projectId: string, copyId: string, graph: WorkflowGraph): Promise<WorkflowGraph> {
  return fetchJSON<WorkflowGraph>(`/api/projects/${encodeURIComponent(projectId)}/config/${encodeURIComponent(copyId)}/graph`, {
    method: "PUT",
    body: JSON.stringify(graph),
  });
}

export function createProjectWorkflow(projectId: string, input: WorkflowInput): Promise<ProjectWorkflowCreateResult> {
  return fetchJSON<ProjectWorkflowCreateResult>(`/api/projects/${encodeURIComponent(projectId)}/config/workflows`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function deleteProjectWorkflow(projectId: string, copyId: string): Promise<void> {
  await fetchJSON<void>(`/api/projects/${encodeURIComponent(projectId)}/config/${encodeURIComponent(copyId)}`, {
    method: "DELETE",
  });
}

export function fetchModelRoutes(): Promise<ModelRoute[]> {
  return fetchJSON<ModelRoute[]>("/api/model-routes");
}

export function resolveModelRoute(client: string, model: string): Promise<ModelRouteResolution> {
  const query = new URLSearchParams({ client, model });
  return fetchJSON<ModelRouteResolution>(`/api/model-routes/resolve?${query.toString()}`);
}

export function fetchInfrastructure(): Promise<InfrastructureItem[]> {
  return fetchJSON<InfrastructureItem[]>("/api/infrastructure");
}

export function fetchInfrastructureCatalog(): Promise<InfrastructureItem[]> {
  return fetchJSON<InfrastructureItem[]>("/api/infrastructure/catalog");
}

export function checkInfrastructure(id: string): Promise<InfrastructureItem> {
  return fetchJSON<InfrastructureItem>(`/api/infrastructure/${encodeURIComponent(id)}/check`, {
    method: "POST",
  });
}

export function installInfrastructure(id: string): Promise<InfrastructureItem> {
  return fetchJSON<InfrastructureItem>(`/api/infrastructure/${encodeURIComponent(id)}/install`, {
    method: "POST",
  });
}

export function updateInfrastructure(id: string): Promise<InfrastructureItem> {
  return fetchJSON<InfrastructureItem>(`/api/infrastructure/${encodeURIComponent(id)}/update`, {
    method: "POST",
  });
}
