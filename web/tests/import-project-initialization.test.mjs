import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../src/App.vue", import.meta.url), "utf8");
const start = app.indexOf("async function importProjectAction");
const end = app.indexOf("async function finishProjectImport", start);
const importFlow = app.slice(start, end);

test("import project initializes AI config, CodeGraph, and rescans idempotently", () => {
  assert.notEqual(start, -1, "import flow should exist");
  assert.notEqual(end, -1, "project finish flow should follow import flow");
  assert.match(importFlow, /previewTemplateInitialization/);
  assert.match(importFlow, /applyTemplateInitialization/);
  assert.match(importFlow, /ensureProjectCodeGraph/);
  assert.match(importFlow, /rescanProject/);
  assert.match(importFlow, /fetchKnowledgeSyncProfile/);
  assert.match(importFlow, /initializeKnowledgePreview/);
  assert.match(importFlow, /rebuildKnowledgeGraph/);
  assert.match(importFlow, /setImportProgress/);
  assert.match(importFlow, /project\.localPath \|\| project\.path/);
  assert.ok(
    importFlow.indexOf("previewTemplateInitialization") <
      importFlow.indexOf("applyTemplateInitialization") &&
      importFlow.indexOf("applyTemplateInitialization") <
        importFlow.indexOf("ensureProjectCodeGraph") &&
      importFlow.indexOf("ensureProjectCodeGraph") <
        importFlow.indexOf("rescanProject") &&
      importFlow.indexOf("rescanProject") <
        importFlow.indexOf("bootstrapImportedProjectKnowledge"),
    "import setup should apply templates, initialize CodeGraph, rescan, then bootstrap GBrain",
  );
});

test("import modal advertises the actual CodeGraph MCP artifacts", () => {
  assert.match(app, /\.codex\/config\.toml/);
  assert.match(app, /\.codegraph\//);
  assert.match(app, /保持幂等/);
  assert.match(app, /import-progress-status/);
  assert.match(app, /knowledge-progress-indeterminate/);
  assert.match(app, /indeterminate/);
});

test("check and enrich also idempotently rebuilds the existing approved Knowledge index in GBrain", () => {
  const start = app.indexOf("async function runKnowledgeInitialization");
  const end = app.indexOf("async function checkKnowledgeUpdatesForCurrentProject", start);
  const knowledgeFlow = app.slice(start, end);
  assert.notEqual(start, -1, "knowledge initialization flow should exist");
  assert.notEqual(end, -1, "knowledge update flow should follow initialization flow");
  assert.match(knowledgeFlow, /mode === "enrich"/);
  assert.match(knowledgeFlow, /fetchProjectKnowledgeExport/);
  assert.match(knowledgeFlow, /flattenKnowledgeNodes/);
  assert.match(knowledgeFlow, /knowledgeGraphRebuildInProgress/);
  assert.match(knowledgeFlow, /rebuildKnowledgeGraph/);
  assert.match(knowledgeFlow, /幂等/);
  assert.ok(
    knowledgeFlow.indexOf("initializeKnowledgePreview") < knowledgeFlow.indexOf("rebuildKnowledgeGraph"),
    "the approved index should be rebuilt only after the enrich proposal has been generated",
  );
});

test("checking Git updates runs the automatic proposal flow and exposes recent-run reasons", () => {
  const start = app.indexOf("async function checkKnowledgeUpdatesForCurrentProject");
  const end = app.indexOf("function selectKnowledgeProposal", start);
  const checkFlow = app.slice(start, end);
  assert.notEqual(start, -1, "check updates flow should exist");
  assert.notEqual(end, -1, "proposal selection should follow check updates");
  assert.match(checkFlow, /knowledgeOperation\.value = "auto-enrich"/);
  assert.match(checkFlow, /checkKnowledgeUpdates/);
  assert.match(checkFlow, /result\.proposal/);
  assert.match(app, /knowledgeSyncRuns\.slice\(0, 10\)/);
  assert.match(app, /run\.reason/);
});
