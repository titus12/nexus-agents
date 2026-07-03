import test from "node:test";
import assert from "node:assert/strict";
import { buildWorkflowRunDraft } from "../.tmp-tests/workflow-task-run.js";

test("buildWorkflowRunDraft infers Unity UI workflow payload", () => {
  const draft = buildWorkflowRunDraft({
    projectId: "btd-client",
    sessionId: "sess-ui-1",
    workflowTemplateId: "unity-ui-feature-development",
    workflowName: "Unity UI Feature Development",
    graph: {
      id: "unity-ui-feature-development",
      name: "Unity UI Feature Development",
      nodes: [{ id: "n1", agent: "unity-ui-developer", label: "Implement UI", type: "agent", category: "execution", detail: "" }],
      edges: [],
    },
  });

  assert.equal(draft.workflowType, "ui-feature-development");
  assert.equal(draft.payload.projectId, "btd-client");
  assert.equal(draft.payload.workflowTemplateId, "unity-ui-feature-development");
  assert.equal(draft.payload.workflowType, "ui-feature-development");
  assert.equal(draft.payload.sessionId, "sess-ui-1");
  assert.equal(draft.payload.context.agent, "unity-ui-developer");
  assert.equal(draft.payload.context.sessionId, "sess-ui-1");
  assert.equal(draft.payload.evidence.uiChecks.openClose, true);
});

test("buildWorkflowRunDraft infers Go bugfix payload", () => {
  const draft = buildWorkflowRunDraft({
    workflowTemplateId: "go-bugfix",
    workflowName: "Go Bugfix",
  });

  assert.equal(draft.workflowType, "bugfix");
  assert.equal(draft.payload.projectId, "global-workflows");
  assert.equal(draft.payload.context.agent, "debugger");
  assert.deepEqual(draft.payload.context.skills, ["wf-go-bugfix"]);
});
