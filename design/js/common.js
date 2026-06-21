function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function chip(label, tone) {
  return `<span class="chip chip-${tone || "gray"}">${escapeHtml(label)}</span>`;
}

function statusChip(state) {
  const map = {
    ok: ["OK", "green"],
    ready: ["Ready", "green"],
    completed: ["Completed", "green"],
    warn: ["Warn", "orange"],
    waiting: ["Waiting", "orange"],
    paused: ["Paused", "orange"],
    draft: ["Draft", "gray"],
    failed: ["Failed", "red"],
    synced: ["Synced", "green"],
    project_modified: ["Project Modified", "orange"],
    template_updated: ["Template Updated", "purple"],
    diverged: ["Diverged", "red"],
    detached: ["Detached", "gray"],
    create: ["Create", "teal"],
    update: ["Update", "orange"],
    idle: ["Idle", "gray"],
    running: ["Running", "orange"],
    done: ["Done", "green"],
  };
  const [label, tone] = map[state] || [state || "Unknown", "gray"];
  return chip(label, tone);
}

function findById(items, id) {
  return (items || []).find((item) => item.id === id);
}

function showToast(message, duration = 2200) {
  const container = document.getElementById("toast-container");
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.textContent = message;
  container.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add("open"));
  setTimeout(() => {
    toast.classList.remove("open");
    setTimeout(() => toast.remove(), 220);
  }, duration);
}

function closeOverlay(rootId) {
  const root = document.getElementById(rootId);
  if (!root) return;
  const backdrop = root.previousElementSibling;
  if (backdrop && (backdrop.classList.contains("drawer-overlay") || backdrop.classList.contains("modal-backdrop"))) {
    backdrop.classList.remove("open");
  }
  root.classList.remove("open");
}

function openDrawer(rootId) {
  const root = document.getElementById(rootId);
  if (!root) return;
  const backdrop = root.previousElementSibling;
  if (backdrop && backdrop.classList.contains("drawer-overlay")) backdrop.classList.add("open");
  root.classList.add("open");
}

function openModal(rootId) {
  const root = document.getElementById(rootId);
  if (!root) return;
  const backdrop = root.previousElementSibling;
  if (backdrop && backdrop.classList.contains("modal-backdrop")) backdrop.classList.add("open");
  root.classList.add("open");
}

async function openImportProject() {
  await loadOverlay("modal-import-project");
  const input = document.getElementById("import-project-path");
  if (input) input.value = "D:\\workspace\\src\\btd-game-server";
  openModal("modal-import-project");
}

function simulateImportProject() {
  closeOverlay("modal-import-project");
  NEXUS.state.currentProjectId = "btd-game-server";
  showToast("项目已导入并完成静态扫描");
  navigate("project-detail");
}

async function openSyncPreview() {
  await loadOverlay("drawer-sync-preview");
  const project = findById(NEXUS.projects, NEXUS.state.currentProjectId) || NEXUS.projects[0];
  const body = document.getElementById("sync-preview-body");
  if (body) {
    body.innerHTML = project.syncDiffs.map((item) => `
      <div class="panel" style="margin-bottom:12px">
        <div class="panel-header">
          <div>
            <div class="panel-title">${escapeHtml(item.kind || "item")} / ${escapeHtml(item.name || item.file)}</div>
            <div class="drawer-subtitle mono">${escapeHtml(item.path || item.file || "")}</div>
          </div>
          <div class="section-spacer"></div>
          ${statusChip(item.status)}
        </div>
        <div class="panel-body">
          <div class="asset-meta-strip" style="margin-bottom:10px">
            <span><strong>origin.templateId</strong>${escapeHtml(item.origin?.templateId || "project-only")}</span>
            <span><strong>baseVersion</strong>${escapeHtml(item.origin?.baseVersion || "-")}</span>
            <span><strong>baseHash</strong>${escapeHtml(item.origin?.baseHash || "-")}</span>
          </div>
          <div class="asset-meta-strip" style="margin-bottom:10px">
            <span><strong>localVersion</strong>${escapeHtml(item.localVersion || "-")}</span>
            <span><strong>syncMode</strong>${escapeHtml(item.syncMode || "manual")}</span>
            <span><strong>action</strong>manual preview</span>
          </div>
          <pre class="diff-block">${escapeHtml(item.diff)}</pre>
          <div class="asset-actions" style="margin-top:10px">
            <button class="link-btn" type="button" onclick="syncTemplateToProject('${item.id}')">同步模板到项目</button>
            <button class="link-btn" type="button" onclick="keepProjectVersion('${item.id}')">保留项目版本</button>
            <button class="link-btn" type="button" onclick="detachTemplateCopy('${item.id}')">解除模板关联</button>
          </div>
        </div>
      </div>
    `).join("") || `<div class="panel-pad muted">当前没有待同步变更。</div>`;
  }
  openDrawer("drawer-sync-preview");
}

function applySyncPreview() {
  closeOverlay("drawer-sync-preview");
  showToast("已模拟手动同步。V1 不会自动覆盖项目配置。");
}

function findProjectCopy(copyId) {
  return Object.values(NEXUS.projectConfigSets || {})
    .flat()
    .find((copy) => copy.id === copyId);
}

function renderProjectCopyDrawerContent(copyId) {
  const copy = findProjectCopy(copyId);
  if (!copy) return;
  const template = typeof findTemplateForCopy === "function" ? findTemplateForCopy(copy) : null;
  const origin = copy.origin || {};
  const originId = origin.templateId || "project-only";
  const baseVersion = origin.baseVersion || "-";
  const baseHash = origin.baseHash || "-";
  const project = findById(NEXUS.projects, NEXUS.state.currentProjectId) || NEXUS.projects[0];

  document.getElementById("project-copy-title").textContent = copy.name;
  document.getElementById("project-copy-subtitle").textContent = `${project?.name || "Project"} / ${copy.kind} / ${copy.path}`;
  document.getElementById("project-copy-body").innerHTML = `
    <section class="panel" style="margin-bottom:12px">
      <div class="panel-header">
        <div>
          <div class="panel-title">Project Config Set</div>
          <div class="drawer-subtitle">Project-local copy with manual template sync.</div>
        </div>
        <div class="section-spacer"></div>
        ${syncStatusChip(copy.status)}
      </div>
      <div class="panel-body">
        <div class="asset-meta-strip">
          <span><strong>origin.templateId</strong>${escapeHtml(originId)}</span>
          <span><strong>baseVersion</strong>${escapeHtml(baseVersion)}</span>
          <span><strong>baseHash</strong>${escapeHtml(baseHash)}</span>
        </div>
        <div class="asset-meta-strip" style="margin-top:8px">
          <span><strong>localVersion</strong>${escapeHtml(copy.localVersion)}</span>
          <span><strong>syncMode</strong>${escapeHtml(copy.syncMode)}</span>
          <span><strong>path</strong>${escapeHtml(copy.path)}</span>
        </div>
      </div>
    </section>
    <section class="panel" style="margin-bottom:12px">
      <div class="panel-header"><div class="panel-title">Template Link</div></div>
      <div class="panel-body source-list">
        <div class="source-item"><span>Template</span><span>${escapeHtml(template?.name || originId)}</span></div>
        <div class="source-item"><span>Template version</span><span>v${escapeHtml(template?.version || baseVersion)}</span></div>
        <div class="source-item"><span>Entry</span><span class="mono">${escapeHtml(template?.entry || template?.source || "detached")}</span></div>
      </div>
    </section>
    <section class="panel">
      <div class="panel-header"><div class="panel-title">Sync Preview</div></div>
      <div class="panel-body">
        <pre class="diff-block">${escapeHtml(copy.diff || "No diff available.")}</pre>
      </div>
    </section>
  `;
  setDrawerFooter("drawer-project-copy", "project-copy-footer", `
    <button class="btn-secondary" type="button" onclick="closeOverlay('drawer-project-copy')">关闭</button>
    <button class="btn-primary" type="button" onclick="syncTemplateToProject('${copy.id}')">同步模板</button>
  `);
}

async function openProjectCopyDrawer(copyId) {
  await loadOverlay("drawer-project-copy");
  NEXUS.state.currentProjectCopyId = copyId;
  renderProjectCopyDrawerContent(copyId);
  openDrawer("drawer-project-copy");
}

function syncTemplateToProject(copyId) {
  const copy = findProjectCopy(copyId);
  if (copy) copy.status = "synced";
  if (typeof renderProjectConfigSet === "function") renderProjectConfigSet(NEXUS.state.currentProjectId);
  if (typeof renderCurrentProjectResourcePage === "function") renderCurrentProjectResourcePage();
  if (NEXUS.state.currentProjectCopyId === copyId) renderProjectCopyDrawerContent(copyId);
  showToast("已模拟同步模板到项目副本");
}

function keepProjectVersion(copyId) {
  const copy = findProjectCopy(copyId);
  if (copy) copy.status = "project_modified";
  if (typeof renderProjectConfigSet === "function") renderProjectConfigSet(NEXUS.state.currentProjectId);
  if (typeof renderCurrentProjectResourcePage === "function") renderCurrentProjectResourcePage();
  if (NEXUS.state.currentProjectCopyId === copyId) renderProjectCopyDrawerContent(copyId);
  showToast("已保留项目版本，等待后续手动处理");
}

function detachTemplateCopy(copyId) {
  const copy = findProjectCopy(copyId);
  if (copy) copy.status = "detached";
  if (typeof renderProjectConfigSet === "function") renderProjectConfigSet(NEXUS.state.currentProjectId);
  if (typeof renderCurrentProjectResourcePage === "function") renderCurrentProjectResourcePage();
  if (NEXUS.state.currentProjectCopyId === copyId) renderProjectCopyDrawerContent(copyId);
  showToast("已模拟解除模板关联");
}

function viewAgentRules(agentId) {
  const agent = findById(NEXUS.agents, agentId) || NEXUS.agents[0];
  NEXUS.state.ruleProjectFilter = NEXUS.state.currentProjectId || "all";
  closeOverlay("drawer-agent");
  navigate("rules");
  showToast(`已切到 ${agent.name} 可用的 Rules`);
}

function viewAgentSkills(agentId) {
  const agent = findById(NEXUS.agents, agentId) || NEXUS.agents[0];
  NEXUS.state.skillProjectFilter = NEXUS.state.currentProjectId || "all";
  closeOverlay("drawer-agent");
  navigate("skills");
  showToast(`已切到 ${agent.name} 可用的 Skills`);
}

async function openAgentDrawer(agentId) {
  await loadOverlay("drawer-agent");
  const agent = findById(NEXUS.agents, agentId) || NEXUS.agents[0];
  document.getElementById("agent-drawer-title").textContent = agent.name;
  document.getElementById("agent-drawer-subtitle").textContent = `${agent.role} / ${agent.model} / ${agent.route}`;
  document.getElementById("agent-drawer-body").innerHTML = `
    <div class="grid-2">
      <section class="panel">
        <div class="panel-header"><div class="panel-title">角色说明</div></div>
        <div class="panel-body">
          <p class="muted" style="line-height:1.6">${escapeHtml(agent.capability)}</p>
          <div style="margin-top:12px">${chip(agent.model, "purple")} ${chip(agent.route, agent.route.includes("Winky") ? "teal" : "orange")}</div>
        </div>
      </section>
      <section class="panel">
        <div class="panel-header"><div class="panel-title">绑定</div></div>
        <div class="panel-body source-list">
          <div class="source-item"><span>Rules</span><button class="link-btn" type="button" onclick="viewAgentRules('${agent.id}')">${agent.rules} rules</button></div>
          <div class="source-item"><span>Skills</span><button class="link-btn" type="button" onclick="viewAgentSkills('${agent.id}')">${agent.skills} skills</button></div>
          <div class="source-item"><span>Tools</span><span class="muted">${escapeHtml(agent.tools)}</span></div>
          <div class="source-item"><span>MCP</span><span class="muted">${escapeHtml(agent.mcp)}</span></div>
        </div>
      </section>
    </div>
    <div class="section-header"><div class="section-title">Source and Projection</div></div>
    <pre class="code-block">semantic_source = "${escapeHtml(agent.source)}"
codex_projection = "${escapeHtml(agent.projection)}"
project_override = "none"
sync_policy = "preview_then_apply"</pre>
  `;
  openDrawer("drawer-agent");
}

function projectSelectOptions(selectedProject) {
  const options = [{ id: "all", name: "ALL" }, ...(NEXUS.projects || [])];
  return options
    .map((project) => {
      const selected = project.id === selectedProject ? " selected" : "";
      return `<option value="${escapeHtml(project.id)}"${selected}>${escapeHtml(project.name)}</option>`;
    })
    .join("");
}

function setDrawerFooter(drawerId, footerId, html) {
  const footer = document.getElementById(footerId) || document.querySelector(`#${drawerId} .drawer-footer`);
  if (!footer) return;
  footer.id = footerId;
  footer.innerHTML = html;
}

async function openRuleDrawer(ruleId) {
  await loadOverlay("drawer-rule");
  const rule = findById(NEXUS.rules, ruleId) || NEXUS.rules[0];
  document.getElementById("rule-drawer-title").textContent = rule.name;
  document.getElementById("rule-drawer-subtitle").textContent = rule.source;
  document.getElementById("rule-drawer-body").innerHTML = `
    <section class="panel" style="margin-bottom:12px">
      <div class="panel-header"><div class="panel-title">Template Metadata</div></div>
      <div class="panel-body asset-meta-strip">
        <span><strong>id</strong>${escapeHtml(rule.id)}</span>
        <span><strong>version</strong>v${escapeHtml(rule.version || 1)}</span>
        <span><strong>files</strong>${escapeHtml((rule.files || [rule.source]).join(", "))}</span>
      </div>
    </section>
    <div class="drawer-editor-layout">
      <div class="asset-edit-grid">
        <label class="form-label">Rule Name
          <input id="rule-name-editor" class="asset-edit-input" type="text" value="${escapeHtml(rule.name)}">
        </label>
        <label class="form-label">Project
          <select id="rule-project-editor" class="asset-edit-input">${projectSelectOptions(rule.project)}</select>
        </label>
        <label class="form-label">Scope
          <input id="rule-scope-editor" class="asset-edit-input" type="text" value="${escapeHtml(rule.scope)}">
        </label>
        <label class="form-label">Source
          <input id="rule-source-editor" class="asset-edit-input" type="text" value="${escapeHtml(rule.source)}">
        </label>
        <label class="form-label asset-edit-span-2">Projection
          <input id="rule-projection-editor" class="asset-edit-input" type="text" value="${escapeHtml(rule.projection)}">
        </label>
      </div>
      <label class="form-label" for="rule-summary-editor">Summary</label>
      <textarea id="rule-summary-editor" class="asset-editor-summary" rows="3">${escapeHtml(rule.summary)}</textarea>
      <label class="form-label" for="rule-content-editor">Rule Content</label>
      <textarea id="rule-content-editor" class="asset-editor-textarea">${escapeHtml(rule.content || rule.summary)}</textarea>
    </div>
  `;
  setDrawerFooter("drawer-rule", "rule-drawer-footer", `
    <button class="btn-secondary" type="button" onclick="closeOverlay('drawer-rule')">关闭</button>
    <button class="btn-primary" type="button" onclick="saveRuleDraft('${escapeHtml(rule.id)}')">保存修改</button>
  `);
  openDrawer("drawer-rule");
}

function saveRuleDraft(ruleId) {
  const rule = findById(NEXUS.rules, ruleId);
  if (!rule) return;
  rule.name = document.getElementById("rule-name-editor")?.value || rule.name;
  rule.project = document.getElementById("rule-project-editor")?.value || rule.project;
  rule.scope = document.getElementById("rule-scope-editor")?.value || rule.scope;
  rule.source = document.getElementById("rule-source-editor")?.value || rule.source;
  rule.projection = document.getElementById("rule-projection-editor")?.value || rule.projection;
  rule.summary = document.getElementById("rule-summary-editor")?.value || rule.summary;
  rule.content = document.getElementById("rule-content-editor")?.value || rule.content;
  document.getElementById("rule-drawer-title").textContent = rule.name;
  document.getElementById("rule-drawer-subtitle").textContent = rule.source;
  if (typeof renderRulesPage === "function") renderRulesPage();
  showToast("Rule 修改已保存到原型数据");
}

async function openSkillDrawer(skillId) {
  await loadOverlay("drawer-skill");
  const skill = findById(NEXUS.skills, skillId) || NEXUS.skills[0];
  document.getElementById("skill-drawer-title").textContent = skill.name;
  document.getElementById("skill-drawer-subtitle").textContent = skill.source;
  document.getElementById("skill-drawer-body").innerHTML = `
    <section class="panel" style="margin-bottom:12px">
      <div class="panel-header"><div class="panel-title">Template Metadata</div></div>
      <div class="panel-body asset-meta-strip">
        <span><strong>id</strong>${escapeHtml(skill.id)}</span>
        <span><strong>version</strong>v${escapeHtml(skill.version || 1)}</span>
        <span><strong>entry</strong>${escapeHtml(skill.entry || skill.source || "templates/skills/skill.md")}</span>
      </div>
      <div class="panel-body">
        <pre class="diff-block">id: ${escapeHtml(skill.id)}
kind: skill
slug: ${escapeHtml(skill.slug || skill.id)}
version: ${escapeHtml(skill.version || 1)}
entry: ${escapeHtml(skill.entry || skill.source || "templates/skills/skill.md")}
files:
${(skill.files || [skill.entry || skill.source || "templates/skills/skill.md"]).map((file) => `  - ${escapeHtml(file)}`).join("\n")}</pre>
      </div>
    </section>
    <div class="drawer-editor-layout">
      <div class="asset-edit-grid">
        <label class="form-label">Skill Name
          <input id="skill-name-editor" class="asset-edit-input" type="text" value="${escapeHtml(skill.name)}">
        </label>
        <label class="form-label">Project
          <select id="skill-project-editor" class="asset-edit-input">${projectSelectOptions(skill.project)}</select>
        </label>
        <label class="form-label">Module
          <input id="skill-module-editor" class="asset-edit-input" type="text" value="${escapeHtml(skill.module)}">
        </label>
        <label class="form-label">Source
          <input id="skill-source-editor" class="asset-edit-input" type="text" value="${escapeHtml(skill.source)}">
        </label>
        <label class="form-label">Trigger
          <input id="skill-trigger-editor" class="asset-edit-input" type="text" value="${escapeHtml(skill.trigger)}">
        </label>
        <label class="form-label">Applies To
          <input id="skill-applies-editor" class="asset-edit-input" type="text" value="${escapeHtml(skill.appliesTo || "project roles")}">
        </label>
      </div>
      <label class="form-label" for="skill-purpose-editor">Purpose</label>
      <textarea id="skill-purpose-editor" class="asset-editor-summary" rows="3">${escapeHtml(skill.purpose || "Project operation manual for matched agents.")}</textarea>
      <label class="form-label" for="skill-content-editor">Skill Content</label>
      <textarea id="skill-content-editor" class="asset-editor-textarea">${escapeHtml(skill.content || skill.purpose || skill.trigger)}</textarea>
    </div>
  `;
  setDrawerFooter("drawer-skill", "skill-drawer-footer", `
    <button class="btn-secondary" type="button" onclick="closeOverlay('drawer-skill')">关闭</button>
    <button class="btn-primary" type="button" onclick="saveSkillDraft('${escapeHtml(skill.id)}')">保存修改</button>
  `);
  openDrawer("drawer-skill");
}

function saveSkillDraft(skillId) {
  const skill = findById(NEXUS.skills, skillId);
  if (!skill) return;
  skill.name = document.getElementById("skill-name-editor")?.value || skill.name;
  skill.project = document.getElementById("skill-project-editor")?.value || skill.project;
  skill.module = document.getElementById("skill-module-editor")?.value || skill.module;
  skill.source = document.getElementById("skill-source-editor")?.value || skill.source;
  skill.trigger = document.getElementById("skill-trigger-editor")?.value || skill.trigger;
  skill.appliesTo = document.getElementById("skill-applies-editor")?.value || skill.appliesTo;
  skill.purpose = document.getElementById("skill-purpose-editor")?.value || skill.purpose;
  skill.content = document.getElementById("skill-content-editor")?.value || skill.content;
  document.getElementById("skill-drawer-title").textContent = skill.name;
  document.getElementById("skill-drawer-subtitle").textContent = skill.source;
  if (typeof renderSkillsPage === "function") renderSkillsPage();
  showToast("Skill 修改已保存到原型数据");
}

async function openRouteDrawer(routeId) {
  await loadOverlay("drawer-route");
  const route = findById(NEXUS.modelRoutes, routeId) || NEXUS.modelRoutes[0];
  document.getElementById("route-drawer-title").textContent = route.source;
  document.getElementById("route-drawer-subtitle").textContent = `${route.client} -> ${route.target}`;
  document.getElementById("route-drawer-body").innerHTML = `
    <div class="source-list">
      <div class="source-item"><span>Client</span><span>${escapeHtml(route.client)}</span></div>
      <div class="source-item"><span>Source model</span><span class="mono">${escapeHtml(route.source)}</span></div>
      <div class="source-item"><span>Target</span><span>${escapeHtml(route.target)}</span></div>
      <div class="source-item"><span>Provider</span><span>${escapeHtml(route.provider)}</span></div>
      <div class="source-item"><span>Auth</span><span>${escapeHtml(route.auth)}</span></div>
    </div>
    <div class="section-header"><div class="section-title">Endpoint</div></div>
    <pre class="code-block">${escapeHtml(route.endpoint)}</pre>
  `;
  openDrawer("drawer-route");
}

async function openProxyTest(routeId) {
  await loadOverlay("drawer-proxy-test");
  const route = routeId ? findById(NEXUS.modelRoutes, routeId) : NEXUS.modelRoutes[1];
  document.getElementById("proxy-test-title").textContent = route ? `测试 ${route.source}` : "代理测试";
  document.getElementById("proxy-test-body").innerHTML = `
    <div class="source-list">
      <div class="source-item"><span>Route</span><span>${escapeHtml(route?.source || "gpt-5.4")} -> ${escapeHtml(route?.target || "deepseek-v4-pro")}</span></div>
      <div class="source-item"><span>Mode</span><span>Responses + stream</span></div>
      <div class="source-item"><span>Patch check</span>${statusChip("ok")}</div>
    </div>
    <div class="section-header"><div class="section-title">Result</div></div>
    <pre class="code-block" id="proxy-test-log">ready&gt; click "Run test" to simulate a proxy call</pre>
  `;
  openDrawer("drawer-proxy-test");
}

function runProxyTest() {
  const log = document.getElementById("proxy-test-log");
  if (!log) return;
  log.textContent = "request> POST /v1/responses model=gpt-5.4 stream=true\nroute> LiteLLM -> Winky deepseek-v4-pro\npatch> dropped namespace/custom/tool_search tools\nresponse> event: response.completed\nstatus> ok";
  showToast("代理测试已模拟完成");
}

async function openWorkflowRun(runId) {
  await loadOverlay("drawer-workflow-run");
  const run = findById(NEXUS.runs, runId) || NEXUS.runs[0];
  document.getElementById("workflow-run-title").textContent = run.id;
  document.getElementById("workflow-run-subtitle").textContent = `${run.workflow} / ${run.project}`;
  document.getElementById("workflow-run-body").innerHTML = `
    <div class="source-list">
      <div class="source-item"><span>Status</span>${statusChip(run.status)}</div>
      <div class="source-item"><span>Started</span><span>${escapeHtml(run.startedAt)}</span></div>
      <div class="source-item"><span>Duration</span><span>${escapeHtml(run.duration)}</span></div>
      <div class="source-item"><span>Model mix</span><span>${escapeHtml(run.modelCost)}</span></div>
    </div>
    <div class="section-header"><div class="section-title">Timeline</div></div>
    <div class="timeline">
      <div class="timeline-item"><div class="timeline-title">prometheus</div><div class="timeline-meta">planned review branches and input schema</div></div>
      <div class="timeline-item"><div class="timeline-title">parallel reviewers</div><div class="timeline-meta">logic, perf, security completed in parallel</div></div>
      <div class="timeline-item"><div class="timeline-title">join</div><div class="timeline-meta">${escapeHtml(run.summary)}</div></div>
    </div>
  `;
  openDrawer("drawer-workflow-run");
}

function copyText(text) {
  navigator.clipboard?.writeText(text).then(() => showToast("已复制")).catch(() => showToast("复制失败"));
}
