import test from "node:test";
import assert from "node:assert/strict";
import { addWorkflowEdge, moveWorkflowNode, updateWorkflowNode } from "../.tmp-tests/workflow-graph.js";

function sampleGraph() {
  return {
    id: "review",
    name: "review",
    nodes: [
      { id: "start", type: "input", category: "event", label: "start", agent: "-", detail: "begin", x: 10, y: 20 },
      { id: "agent", type: "agent", category: "action", label: "agent", agent: "worker", detail: "work", x: 220, y: 20 },
    ],
    edges: [{ from: "start", to: "agent", label: "request" }],
  };
}

test("moveWorkflowNode returns a graph with one node position changed", () => {
  const graph = sampleGraph();
  const moved = moveWorkflowNode(graph, "agent", 360, 140);

  assert.equal(moved.nodes.find((node) => node.id === "agent")?.x, 360);
  assert.equal(moved.nodes.find((node) => node.id === "agent")?.y, 140);
  assert.equal(graph.nodes.find((node) => node.id === "agent")?.x, 220);
});

test("updateWorkflowNode edits node fields without mutating the original graph", () => {
  const graph = sampleGraph();
  const updated = updateWorkflowNode(graph, "agent", { label: "reviewer", agent: "reviewer-logic", detail: "review diff" });

  const node = updated.nodes.find((item) => item.id === "agent");
  assert.equal(node?.label, "reviewer");
  assert.equal(node?.agent, "reviewer-logic");
  assert.equal(node?.detail, "review diff");
  assert.equal(graph.nodes.find((item) => item.id === "agent")?.label, "agent");
});

test("addWorkflowEdge rejects self links and duplicate links", () => {
  const graph = sampleGraph();

  assert.equal(addWorkflowEdge(graph, "agent", "agent", "self").edges.length, 1);
  assert.equal(addWorkflowEdge(graph, "start", "agent", "again").edges.length, 1);

  const withNewEdge = addWorkflowEdge(graph, "agent", "start", "result");
  assert.equal(withNewEdge.edges.length, 2);
  assert.deepEqual(withNewEdge.edges[1], { from: "agent", to: "start", label: "result" });
});
