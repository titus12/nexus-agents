import type {
  BootstrapData,
  InfrastructureItem,
  LocalDirectoryPickerResponse,
  LocalDirectoriesResponse,
  ModelRoute,
  ModelRouteResolution,
  Project,
  ProjectCopy,
  ProjectInput,
  ProjectRescanResult,
  ProjectWorkflowCreateResult,
  TemplateInput,
  TemplateItem,
  TemplateKind,
  WorkflowGraph,
  WorkflowInput,
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

export function fetchProjects(): Promise<Project[]> {
  return fetchJSON<Project[]>("/api/projects");
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

export function fetchTemplates(kind: TemplateKind): Promise<TemplateItem[]> {
  return fetchJSON<TemplateItem[]>(`/api/templates/${kind}`);
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
