import type { WorkflowEdge, WorkflowGraph, WorkflowNode } from "./types";

export type WorkflowNodePatch = Partial<Pick<WorkflowNode, "type" | "category" | "label" | "agent" | "detail" | "x" | "y">>;

export type WorkflowNodePreset = {
  key: string;
  title: string;
  type: string;
  category: string;
  label: string;
  agent: string;
  detail: string;
};

export const workflowNodePresets: WorkflowNodePreset[] = [
  { key: "agent", title: "Agent Action", type: "agent", category: "action", label: "agent action", agent: "worker", detail: "Run one agent role with mapped input." },
  { key: "sequence", title: "Sequence", type: "sequence", category: "control", label: "sequence", agent: "orchestrator", detail: "Run child steps in order and pass outputs forward." },
  { key: "parallel", title: "Parallel", type: "parallel", category: "control", label: "parallel", agent: "orchestrator", detail: "Fan out to multiple agents or branches at the same time." },
  { key: "condition", title: "Condition Gate", type: "condition", category: "condition", label: "condition", agent: "router", detail: "Choose the next branch from context or agent output." },
  { key: "loop", title: "Loop / Repeat", type: "loop", category: "condition", label: "loop control", agent: "orchestrator", detail: "Repeat a bounded section until success, retry budget exhaustion, or an exit condition." },
  { key: "transform", title: "Transform", type: "transform", category: "data", label: "transform", agent: "mapper", detail: "Map, merge, or reshape data for the next node." },
  { key: "join", title: "Join", type: "join", category: "data", label: "join", agent: "orchestrator", detail: "Merge branch outputs and deduplicate results." },
  { key: "human", title: "Human Approval", type: "human_approval", category: "human", label: "approval", agent: "owner", detail: "Pause until a human reviews risk or scope." },
  { key: "decorator", title: "Retry / Timeout", type: "decorator", category: "decorator", label: "retry policy", agent: "runtime", detail: "Wrap the next action with retry, timeout, or fallback policy." },
];

export function moveWorkflowNode(graph: WorkflowGraph, nodeId: string, x: number, y: number): WorkflowGraph {
  return updateWorkflowNode(graph, nodeId, { x: Math.round(x), y: Math.round(y) });
}

export function updateWorkflowNode(graph: WorkflowGraph, nodeId: string, patch: WorkflowNodePatch): WorkflowGraph {
  return {
    ...graph,
    nodes: graph.nodes.map((node) => (node.id === nodeId ? { ...node, ...patch } : node)),
  };
}

export function addWorkflowEdge(graph: WorkflowGraph, from: string, to: string, label = "next"): WorkflowGraph {
  if (!from || !to || from === to) return graph;
  const hasFrom = graph.nodes.some((node) => node.id === from);
  const hasTo = graph.nodes.some((node) => node.id === to);
  if (!hasFrom || !hasTo) return graph;
  if (graph.edges.some((edge) => edge.from === from && edge.to === to)) return graph;
  return {
    ...graph,
    edges: [...graph.edges, { from, to, label }],
  };
}

export function deleteWorkflowEdge(graph: WorkflowGraph, edge: WorkflowEdge): WorkflowGraph {
  return {
    ...graph,
    edges: graph.edges.filter((item) => !(item.from === edge.from && item.to === edge.to && item.label === edge.label)),
  };
}

export function addWorkflowNode(graph: WorkflowGraph, preset: WorkflowNodePreset, x: number, y: number): WorkflowGraph {
  const id = uniqueNodeId(graph, preset.key);
  return {
    ...graph,
    nodes: [
      ...graph.nodes,
      {
        id,
        type: preset.type,
        category: preset.category,
        label: preset.label,
        agent: preset.agent,
        detail: preset.detail,
        x: Math.round(x),
        y: Math.round(y),
      },
    ],
  };
}

function uniqueNodeId(graph: WorkflowGraph, base: string): string {
  const existing = new Set(graph.nodes.map((node) => node.id));
  if (!existing.has(base)) return base;
  for (let index = 2; ; index += 1) {
    const candidate = `${base}-${index}`;
    if (!existing.has(candidate)) return candidate;
  }
}
