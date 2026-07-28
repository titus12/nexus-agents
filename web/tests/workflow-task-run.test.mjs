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

test("buildWorkflowRunDraft infers .NET feature payload", () => {
  const draft = buildWorkflowRunDraft({
    projectId: "dotnet-worker",
    workflowTemplateId: "dotnet-feature-development",
    workflowName: ".NET Feature Development",
  });

  assert.equal(draft.workflowType, "dotnet-feature-development");
  assert.equal(draft.payload.context.agent, "dotnet-developer");
  assert.deepEqual(draft.payload.context.rules, [
    "dotnet-00-routing",
    "dotnet-01-project-model",
    "dotnet-02-runtime-safety",
    "dotnet-03-library-compatibility",
  ]);
  assert.deepEqual(draft.payload.context.skills, [
    "wf-dotnet-feature",
    "dotnet-development",
    "dotnet-testing",
  ]);
  assert.deepEqual(draft.payload.context.tools, ["workflow-graph"]);
  assert.deepEqual(draft.payload.evidence.tags, ["dotnet", "feature", "workflow-runner"]);
  assert.deepEqual(draft.payload.evidence.build, {
    passed: false,
    status: "not_provided",
  });
});

test("buildWorkflowRunDraft infers .NET bugfix and review owners", () => {
  const bugfix = buildWorkflowRunDraft({ workflowTemplateId: "dotnet-bugfix" });
  const review = buildWorkflowRunDraft({ workflowTemplateId: "dotnet-code-review" });

  assert.equal(bugfix.workflowType, "dotnet-bugfix");
  assert.equal(bugfix.payload.context.agent, "dotnet-debugger");
  assert.deepEqual(bugfix.payload.context.skills, [
    "wf-dotnet-bugfix",
    "dotnet-development",
    "dotnet-testing",
  ]);
  assert.equal(review.workflowType, "dotnet-code-review");
  assert.equal(review.payload.context.agent, "dotnet-reviewer");
  assert.deepEqual(review.payload.context.skills, ["wf-dotnet-review", "dotnet-dependency-safety"]);
});
