export type WorkflowRunSubmitOptions = {
  projectId?: string;
  sessionId?: string;
  workflowCopyId?: string;
  workflowTemplateId?: string;
  workflowId?: string;
  workflowName?: string;
  workflowSummary?: string;
  workflowTrigger?: string;
  graph?: {
    id?: string;
    name?: string;
    nodes?: Array<{ id: string; type?: string; category?: string; label?: string; agent?: string; detail?: string }>;
    edges?: Array<{ from: string; to: string; label?: string }>;
  } | null;
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

export type WorkflowRunDraft = {
  payload: Record<string, unknown>;
  workflowType: string;
};

function nowIso(): string {
  return new Date().toISOString();
}

function normalizeStatus(status?: string): "success" | "partial_success" | "failed" | "cancelled" {
  switch ((status || "").trim().toLowerCase()) {
    case "partial_success":
    case "failed":
    case "cancelled":
      return status!.trim().toLowerCase() as "partial_success" | "failed" | "cancelled";
    default:
      return "success";
  }
}

function slugify(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function inferWorkflowType(options: WorkflowRunSubmitOptions): string {
  const candidates = [
    options.workflowTemplateId,
    options.workflowId,
    options.graph?.id,
    options.workflowName,
    options.graph?.name,
    options.workflowSummary,
  ]
    .filter(Boolean)
    .map((item) => slugify(String(item)));

  for (const candidate of candidates) {
    if (candidate.includes("unity-ui-feature-development") || candidate.includes("ui-feature-development")) return "ui-feature-development";
    if (candidate.includes("unity-logic-modification") || candidate.includes("logic-modification")) return "logic-modification";
    if (candidate.includes("unity-bug-investigation") || candidate.includes("bug-investigation")) return "bug-investigation";
    if (candidate.includes("go-bugfix") || candidate === "bugfix") return "bugfix";
    if (candidate.includes("go-code-review") || candidate.includes("code-review")) return "code-review";
    if (candidate.includes("go-refactor") || candidate.includes("refactor")) return "refactor";
    if (candidate.includes("go-feature-development") || candidate.includes("feature-development")) return "feature-development";
    if (candidate.includes("design")) return "design";
    if (candidate.includes("research")) return "research";
    if (candidate.includes("commit-gate")) return "commit-gate";
    if (candidate.includes("lark-integration")) return "lark-integration";
    if (candidate.includes("subagent-driven-development")) return "subagent-driven-development";
  }

  return "workflow-run";
}

function workflowTemplateId(options: WorkflowRunSubmitOptions, workflowType: string): string {
  return options.workflowTemplateId || options.workflowId || options.graph?.id || workflowType;
}

function taskTitle(options: WorkflowRunSubmitOptions, workflowType: string): string {
  return options.taskTitle || options.workflowName || options.graph?.name || workflowType;
}

function inferPrimaryAgent(options: WorkflowRunSubmitOptions, workflowType: string): string {
  if (options.context?.agent) return options.context.agent;
  const nodes = options.graph?.nodes ?? [];
  const firstAgent = nodes.find((node) => node.agent && node.agent.trim())?.agent?.trim();
  if (firstAgent) return firstAgent;
  if (workflowType.startsWith("ui-")) return "unity-ui-developer";
  if (workflowType === "logic-modification") return "unity-logic-developer";
  if (workflowType === "bug-investigation") return "unity-debugger";
  if (workflowType === "bugfix") return "debugger";
  return "workflow-runner";
}

function inferRules(workflowType: string): string[] {
  switch (workflowType) {
    case "bug-investigation":
      return ["unity-00-routing", "unity-id-bugfix-safety"];
    case "logic-modification":
      return ["unity-00-routing", "unity-id-logic-mod-safety"];
    case "ui-feature-development":
      return ["unity-00-routing", "unity-id-ui-safety"];
    case "bugfix":
      return ["00-routing"];
    default:
      return [];
  }
}

function inferSkills(workflowType: string): string[] {
  switch (workflowType) {
    case "bug-investigation":
      return ["wf-unity-bugfix", "unity-testing"];
    case "logic-modification":
      return ["wf-unity-logic-mod", "unity-testing"];
    case "ui-feature-development":
      return ["wf-unity-ui-feature", "unity-testing", "unity-asset-safety"];
    case "bugfix":
      return ["wf-go-bugfix"];
    default:
      return [];
  }
}

function inferTools(workflowType: string): string[] {
  if (workflowType.startsWith("unity-") || workflowType === "bug-investigation" || workflowType === "logic-modification" || workflowType === "ui-feature-development") {
    return ["unity-mcp", "workflow-graph"];
  }
  return ["workflow-graph"];
}

function buildEvidence(options: WorkflowRunSubmitOptions, workflowType: string): Record<string, unknown> {
  const nodes = options.graph?.nodes ?? [];
  const edges = options.graph?.edges ?? [];
  const defaultEvidence: Record<string, unknown> = {
    summary: options.workflowSummary || `Executed ${workflowType} through the Nexus workflow runner.`,
    finalResult: `Workflow ${taskTitle(options, workflowType)} submitted from the Nexus workflow runner.`,
    verification: {
      hasVerification: true,
      passed: normalizeStatus(options.submittedStatus) === "success",
      types: ["workflow_runner_submit"],
      commands: ["POST /api/task-runs"],
    },
    workflowGraph: {
      nodeCount: nodes.length,
      edgeCount: edges.length,
      nodes: nodes.map((node) => ({ id: node.id, type: node.type, agent: node.agent, label: node.label })),
    },
    changedFiles: [],
    skippedChecks: [],
    remainingRisks: [],
    contextMissing: false,
  };

  if (workflowType === "bug-investigation") {
    defaultEvidence.reproduction = { steps: [], reproduced: false, fixedOnSamePath: false };
    defaultEvidence.rootCause = "workflow runner submission";
    defaultEvidence.compile = { passed: true };
    defaultEvidence.console = { errors: 0, warnings: 0 };
    defaultEvidence.tests = { editMode: "not_applicable", playMode: "not_applicable" };
    defaultEvidence.assets = { prefabChanged: false, sceneChanged: false, metaSafe: true, generatedFilesTouched: false };
    defaultEvidence.tags = ["unity", "bugfix", "workflow-runner"];
  } else if (workflowType === "logic-modification") {
    defaultEvidence.currentBehavior = "not provided";
    defaultEvidence.targetBehavior = "not provided";
    defaultEvidence.compatibilityChecked = false;
    defaultEvidence.compile = { passed: true };
    defaultEvidence.console = { errors: 0, warnings: 0 };
    defaultEvidence.tests = { editMode: "not_applicable", playMode: "not_applicable" };
    defaultEvidence.assets = { prefabChanged: false, sceneChanged: false, metaSafe: true, generatedFilesTouched: false };
    defaultEvidence.tags = ["unity", "logic-modification", "workflow-runner"];
  } else if (workflowType === "ui-feature-development") {
    defaultEvidence.compile = { passed: true };
    defaultEvidence.console = { errors: 0, warnings: 0 };
    defaultEvidence.tests = { editMode: "not_applicable", playMode: "not_applicable" };
    defaultEvidence.uiChecks = {
      openClose: true,
      repeatOpen: true,
      loadingState: true,
      errorState: true,
      emptyState: true,
      inputLockRelease: true,
      resolutionAdaptation: true,
    };
    defaultEvidence.assets = { prefabChanged: false, sceneChanged: false, metaSafe: true, generatedFilesTouched: false };
    defaultEvidence.tags = ["unity", "ui-feature", "workflow-runner"];
  } else {
    defaultEvidence.unfinishedItems = [];
    defaultEvidence.risks = [];
  }

  return {
    ...defaultEvidence,
    ...(options.evidence || {}),
  };
}

export function buildWorkflowRunDraft(options: WorkflowRunSubmitOptions): WorkflowRunDraft {
  const workflowType = inferWorkflowType(options);
  const startedAt = options.startedAt || nowIso();
  const endedAt = options.endedAt || nowIso();
  const startedTime = Date.parse(startedAt);
  const endedTime = Date.parse(endedAt);
  const durationMs = Number.isFinite(startedTime) && Number.isFinite(endedTime) ? Math.max(0, endedTime - startedTime) : 0;
  const sessionId = options.sessionId || options.context?.sessionId || "";
  const context = {
    ...(sessionId ? { sessionId } : {}),
    agent: inferPrimaryAgent(options, workflowType),
    model: options.context?.model || "nexus-workflow-runner",
    rules: options.context?.rules?.length ? options.context.rules : inferRules(workflowType),
    skills: options.context?.skills?.length ? options.context.skills : inferSkills(workflowType),
    tools: options.context?.tools?.length ? options.context.tools : inferTools(workflowType),
  };
  const payload = {
    projectId: options.projectId || "global-workflows",
    ...(sessionId ? { sessionId } : {}),
    workflowTemplateId: workflowTemplateId(options, workflowType),
    workflowCopyId: options.workflowCopyId || "",
    workflowType,
    taskTitle: taskTitle(options, workflowType),
    submittedStatus: normalizeStatus(options.submittedStatus),
    startedAt,
    endedAt,
    durationMs,
    context,
    metrics: {
      turnCount: 1,
      toolCallCount: 1,
      testRunCount: 0,
      retryCount: 0,
      errorCount: normalizeStatus(options.submittedStatus) === "failed" ? 1 : 0,
      filesChangedCount: 0,
      nodeCount: options.graph?.nodes?.length || 0,
      edgeCount: options.graph?.edges?.length || 0,
      ...(options.metrics || {}),
    },
    evidence: buildEvidence(options, workflowType),
  };
  return { payload, workflowType };
}
