import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const styles = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");
const designStyles = readFileSync(new URL("../../design/shared.css", import.meta.url), "utf8");
const app = readFileSync(new URL("../src/App.vue", import.meta.url), "utf8");
const types = readFileSync(new URL("../src/types.ts", import.meta.url), "utf8");

function projectResourceSection() {
  const start = app.indexOf('v-else-if="activePage === \'project-agents\'');
  const end = app.indexOf('v-else-if="activePage === \'workflows\'', start);
  assert.notEqual(start, -1, "project resource section should exist");
  assert.notEqual(end, -1, "workflow section should follow project resources");
  return app.slice(start, end);
}

function infrastructureSection() {
  const start = app.indexOf('v-else-if="activePage === \'infrastructure\'');
  const end = app.indexOf('v-else-if="activePage === \'model-routes\'', start);
  assert.notEqual(start, -1, "infrastructure page should exist");
  assert.notEqual(end, -1, "model routes page should follow infrastructure");
  return app.slice(start, end);
}

test("asset cards carry the accepted design accent and chip rules", () => {
  assert.match(styles, /\.asset-card::before\s*\{[^}]*var\(--asset-accent, var\(--accent\)\)/s);
  assert.match(styles, /\.asset-card:hover\s*\{[^}]*background:\s*#1f2230/s);
  assert.match(styles, /\.asset-card\.agent\.model-pro\s*\{\s*--asset-accent:\s*#48bbb2;/);
  assert.match(styles, /\.asset-card\.agent\.model-mini\s*\{\s*--asset-accent:\s*#c99561;/);
  assert.match(styles, /\.asset-card\.rule\s*\{\s*--asset-accent:\s*#64bdb6;/);
  assert.match(styles, /\.asset-card\.skill\s*\{\s*--asset-accent:\s*#c597b5;/);
  assert.match(styles, /\.asset-card \.chip\s*\{[^}]*background:\s*rgba\(255, 255, 255, 0\.045\)/s);
  assert.match(styles, /\.asset-card-footer \.asset-chip-row \.chip\s*\{[^}]*max-width:\s*150px/s);
});

test("resource card grids use the accepted five-column desktop layout", () => {
  for (const [name, css] of [
    ["vue", styles],
    ["design", designStyles],
  ]) {
    assert.match(css, /\.asset-grid\s*\{[^}]*grid-template-columns:\s*repeat\(5,\s*minmax\(0,\s*1fr\)\)/s, `${name} asset grid should be five columns`);
    assert.match(css, /\.asset-grid\.compact\s*\{[^}]*grid-template-columns:\s*repeat\(5,\s*minmax\(0,\s*1fr\)\)/s, `${name} compact asset grid should be five columns`);
  }
});

test("template and project resource cards share card classes and keep bulky sync metadata out of the body", () => {
  assert.match(app, /class="asset-card"\s+:class="\[item\.kind, agentModelClass\(item\)\]"/);
  assert.match(app, /class="asset-card project-copy"\s+:class="\[copy\.kind, projectCopyModelClass\(copy\)\]"/);

  const section = projectResourceSection();
  const bodyStart = section.indexOf("<div class=\"asset-card-body\">");
  const bodyEnd = section.indexOf("<div class=\"asset-card-footer\">", bodyStart);
  const body = section.slice(bodyStart, bodyEnd);

  assert.doesNotMatch(body, /copy\.diff/);
  assert.doesNotMatch(body, /copy\.origin\?\.baseHash/);
  assert.doesNotMatch(body, /copy\.path/);
  assert.match(body, /projectCopySummary\(copy\)/);
  assert.match(body, /projectCopyMeta\(copy\)/);
});

test("project agent rule and skill cards reuse source template footer chips", () => {
  assert.match(app, /function projectCopyFooterChips\(copy: ProjectCopy\): string\[\]\s*\{[\s\S]*const template = templateForProjectCopy\(copy\)/);
  assert.match(app, /copy\.kind !== "workflow"[\s\S]*templateFooterChips\(template\)/);
  assert.match(app, /return \[`local v\$\{copy\.localVersion\}`, copy\.syncMode\];/);
});

test("import directory picker opens as a separate modal overlay", () => {
  assert.match(app, /@click="chooseProjectDirectory\(\)"/);
  assert.match(app, /async function chooseProjectDirectory\([^)]*\)/);
  assert.match(app, /fetchNativeLocalDirectory/);
  assert.match(app, /AbortController/);
  assert.match(app, /nativePickerTimeout/);
  assert.match(app, /正在打开系统目录选择器/);
  assert.match(app, /系统目录选择器未响应，已打开内置选择器/);
  assert.match(app, /openDirectoryBrowser\(\)/);
  assert.match(app, /v-if="showDirectoryBrowser"\s+class="modal-backdrop directory-picker-backdrop"/);
  assert.match(app, /class="modal directory-picker-modal"/);
  assert.match(app, /@click="closeDirectoryBrowser"/);
  assert.match(app, /function closeDirectoryBrowser\(\)/);
  assert.match(app, /async function openDirectoryBrowser\(\)\s*\{[\s\S]*void browseDirectory\(\);[\s\S]*\}/);
  assert.match(app, /directoryBrowser\?\.path \|\| '选择一个目录入口'/);
  assert.match(app, /class="explorer-address-bar"/);
  assert.match(app, /v-model="directoryAddress"/);
  assert.match(app, /class="explorer-sidebar"/);
  assert.match(app, /directoryBrowser\?\.roots/);
  assert.match(app, /directoryBrowser\?\.shortcuts/);
  assert.match(app, /class="explorer-selected-path mono"/);
  assert.match(app, /@dblclick="browseDirectory\(entry\.path\)"/);
  assert.match(styles, /\.directory-picker-backdrop\s*\{[^}]*z-index:\s*130/s);
  assert.match(styles, /\.directory-picker-modal\s*\{[^}]*width:\s*min\(980px,\s*calc\(100vw - 32px\)\)/s);
  assert.match(styles, /\.explorer-layout\s*\{[^}]*grid-template-columns:\s*220px minmax\(0,\s*1fr\)/s);
});

test("system infrastructure page renders managed cards and curated third-party install actions", () => {
  assert.match(app, /activePage === 'infrastructure'/);
  assert.match(app, /openPage\('infrastructure'\)/);
  assert.match(app, />Infrastructure</);
  assert.match(app, /v-for="item in infrastructureItems"/);
  assert.match(app, /openInfrastructureDrawer\(item\)/);
  assert.match(app, /drawerMode === 'infrastructure' && selectedInfrastructure/);
  assert.match(app, /updateSelectedInfrastructure/);
  assert.match(app, /selectedInfrastructure\.githubUrl/);
  assert.match(app, />查看</);
  assert.doesNotMatch(app, />Check Version</);
  assert.match(app, /availableInfrastructureItems/);
  assert.match(app, /fetchInfrastructureCatalog/);
  assert.match(app, /installInfrastructure/);
  assert.match(app, /openInfrastructureCatalog/);
  assert.match(app, /v-for="candidate in availableInfrastructureItems"/);
  assert.match(app, /installSelectedInfrastructure/);
  assert.match(app, />新增基建</);
  assert.match(app, />安装</);
  assert.match(app, /infrastructure-card/);
  assert.match(styles, /\.infrastructure-card\s*\{/);
  assert.match(styles, /\.infrastructure-card\.gbrain\s*\{\s*--asset-accent:\s*#817cff;/);
  assert.match(types, /id:\s*"rtk"\s*\|\s*"codegraph"\s*\|\s*"openwiki"\s*\|\s*"gbrain"/);
  assert.match(types, /"knowledge_graph"/);

  const section = infrastructureSection();
  assert.doesNotMatch(section, /lastCheckedAt/);
  assert.match(section, /local GBrain knowledge graph/);
});

test("model proxy page exposes the embedded Codex router endpoints", () => {
  assert.match(app, /activePage === 'model-routes'/);
  assert.match(app, /\/proxy\/codex\/v1/);
  assert.match(app, /\/proxy\/codex\/model-catalog\.json/);
  assert.match(app, /model_provider = "nexus-codex"/);
  assert.match(app, /requires_openai_auth = true/);
});

test("knowledge sync uses English navigation and dark, readable metadata cards", () => {
  assert.match(app, /activeKnowledgeView === 'sync'[^>]*>Sync<\/button>/);
  assert.doesNotMatch(app, /activeKnowledgeView === 'sync'[^>]*>同步<\/button>/);
  assert.match(styles, /--panel-soft:\s*rgba\(255,\s*255,\s*255,\s*0\.045\)/);
  assert.match(styles, /\.knowledge-sync-facts\s*\{[^}]*repeat\(auto-fit,\s*minmax\(150px,\s*1fr\)\)/s);
  assert.match(styles, /\.knowledge-sync-facts\s*\{[^}]*width:\s*100%/s);
  assert.match(styles, /\.knowledge-sync-facts > div\s*\{[^}]*background:[\s\S]*var\(--bg-card\)/);
  assert.match(styles, /\.knowledge-sync-facts strong\s*\{[^}]*color:\s*var\(--text-primary\)/s);
  assert.doesNotMatch(styles, /\.knowledge-sync-facts > div\s*\{[^}]*#f8fafc/s);
});

test("project groups compose sources without duplicating project knowledge", () => {
  assert.match(types, /export type ProjectGroup = \{[\s\S]*projectIds: string\[\]/);
  assert.match(app, /项目组由 Nexus 统一维护/);
  assert.match(app, /只能选择 Nexus 中已经维护的项目组/);
  assert.doesNotMatch(app, /importNewGroupName/);
  assert.doesNotMatch(app, /projectNewGroupName/);
  assert.doesNotMatch(app, /knowledgeSearchScope/);
  assert.doesNotMatch(app, /knowledgeSearchGroupId/);
  assert.match(app, /根据当前项目自动使用所属项目组/);
  assert.match(app, /const knowledgeSearchEngine = ref<"gbrain" \| "fts5">\("gbrain"\)/);
  assert.match(app, /<option value="gbrain">GBrain<\/option>/);
  assert.match(app, /<option value="fts5">SQLite FTS5<\/option>/);
  assert.doesNotMatch(app, /<option value="compare">Compare<\/option>/);
  assert.match(styles, /\.project-group-picker,/);
  assert.match(styles, /\.knowledge-scope-select\s*\{/);
});
