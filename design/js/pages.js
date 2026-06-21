function renderProjectTree() {
  const mount = document.getElementById("project-tree");
  if (!mount) return;
  const projectPageActive = NEXUS.state.currentPage === "project-detail" || String(NEXUS.state.currentPage || "").startsWith("project-");
  mount.innerHTML = NEXUS.projects.map((project) => {
    const isCurrentProject = projectPageActive && project.id === NEXUS.state.currentProjectId;
    return `
    <div class="project-tree-item">
      <button class="project-entry project-root ${isCurrentProject ? "active" : ""}" type="button" onclick="openProject('${project.id}')">
        <span class="project-dot" style="background:${project.status === "ready" ? "var(--teal)" : "var(--orange)"}"></span>
        <span class="project-name">${escapeHtml(project.name)}</span>
        <span class="project-mini">${project.agents}</span>
      </button>
      <div class="project-children">
        <button class="project-child ${isCurrentProject && NEXUS.state.currentPage === "project-detail" ? "active" : ""}" type="button" onclick="openProject('${project.id}')">Overview</button>
        <button class="project-child ${isCurrentProject && NEXUS.state.currentPage === "project-agents" ? "active" : ""}" type="button" onclick="openProjectResource('${project.id}', 'agents')">Agents</button>
        <button class="project-child ${isCurrentProject && NEXUS.state.currentPage === "project-rules" ? "active" : ""}" type="button" onclick="openProjectResource('${project.id}', 'rules')">Rules</button>
        <button class="project-child ${isCurrentProject && NEXUS.state.currentPage === "project-skills" ? "active" : ""}" type="button" onclick="openProjectResource('${project.id}', 'skills')">Skills</button>
        <button class="project-child ${isCurrentProject && NEXUS.state.currentPage === "project-workflows" ? "active" : ""}" type="button" onclick="openProjectResource('${project.id}', 'workflows')">Workflows</button>
      </div>
    </div>
  `;
  }).join("");
}

function openProject(projectId) {
  NEXUS.state.currentProjectId = projectId;
  renderProjectTree();
  navigate("project-detail");
}

function openProjectResource(projectId, resourceKind) {
  NEXUS.state.currentProjectId = projectId;
  renderProjectTree();
  navigate(`project-${resourceKind}`);
}

function renderProjectsPage() {
  const table = document.getElementById("project-rows");
  if (!table) return;
  table.innerHTML = NEXUS.projects.map((project) => `
    <tr>
      <td>
        <button class="link-btn" type="button" onclick="openProject('${project.id}')">${escapeHtml(project.name)}</button>
        <div class="dim mono">${escapeHtml(project.path)}</div>
      </td>
      <td>${statusChip(project.status)}</td>
      <td>${project.agents}</td>
      <td>${project.rules}</td>
      <td>${project.skills}</td>
      <td class="muted">${escapeHtml(project.updatedAt)}</td>
      <td style="text-align:right">
        <button class="link-btn" type="button" onclick="openProject('${project.id}')">详情</button>
        <button class="link-btn" type="button" onclick="openSyncPreview()">同步预览</button>
      </td>
    </tr>
  `).join("");
}

function renderProjectDetail() {
  const project = findById(NEXUS.projects, NEXUS.state.currentProjectId) || NEXUS.projects[0];
  const description = document.getElementById("project-detail-description");
  if (description) description.textContent = project.path;

  const stats = document.getElementById("project-detail-stats");
  if (stats) {
    stats.innerHTML = `
      <div class="stat-card"><div class="stat-icon purple">A</div><div><div class="stat-num">${project.agents}</div><div class="stat-label">Agents</div></div></div>
      <div class="stat-card"><div class="stat-icon teal">R</div><div><div class="stat-num">${project.rules}</div><div class="stat-label">Rules</div></div></div>
      <div class="stat-card"><div class="stat-icon orange">S</div><div><div class="stat-num">${project.skills}</div><div class="stat-label">Skills</div></div></div>
      <div class="stat-card"><div class="stat-icon pink">W</div><div><div class="stat-num">${project.workflows}</div><div class="stat-label">Workflows</div></div></div>
    `;
  }

  renderProjectSyncSummary(project.id);
  renderProjectKindSummary(project.id);
  renderProjectRecentCopies(project.id);
  renderProjectConfigSet(project.id);
}

function agentCardTone(agent) {
  if (agent.model.includes("mini")) return "model-mini";
  if (agent.model === "gpt-5.4") return "model-pro";
  return "model-direct";
}

function agentDescription(agent) {
  const focus = {
    sisyphus: "负责把模糊需求拆成可执行协作路径，明确各角色的输入、输出和交接边界。",
    prometheus: "负责方案设计、取舍分析和计划落地，把目标、风险和验收标准整理成清晰工作包。",
    hephaestus: "负责多文件实现和重构落地，沿用项目既有模式并保持调用链、验证步骤清楚。",
    worker: "负责明确子任务的稳定执行，把局部修改、文件影响和交付结果控制在清晰边界内。",
    quick: "负责小范围快速修改和补丁整理，适合单文件、低风险、需要快速反馈的任务。",
    oracle: "负责根因分析和调试路径还原，从日志、调用链和复现线索中收敛修复方向。",
    debugger: "负责日志优先的问题定位，快速缩小故障范围并输出可验证的下一步动作。",
    "reviewer-logic": "负责业务逻辑、事务边界和并发风险审查，给出可执行的修复建议。",
    "reviewer-perf": "负责性能热点、内存压力和循环复杂度审查，关注吞吐、缓存和退化路径。",
    "reviewer-security": "负责权限、输入信任和经济风险审查，优先识别高影响的安全边界。",
    librarian: "负责 API、文档和项目知识检索，把外部信息转成可落地的工程上下文。",
    gatekeeper: "负责提交前风险扫描，汇总变更影响、验证状态和仍需确认的问题。",
  };
  return focus[agent.id] || `${agent.capability}，负责把任务输入、执行边界和交付结果整理清楚。`;
}

function renderAgentsPage() {
  const grid = document.getElementById("agent-card-grid");
  if (!grid) return;
  const agents = NEXUS.agents;
  grid.innerHTML = agents.map((agent) => `
    <article class="asset-card agent ${agentCardTone(agent)}">
      <div class="asset-card-header">
        <div class="asset-icon asset-icon-employee" aria-hidden="true"></div>
        <div>
          <div class="asset-title">${escapeHtml(agent.name)}</div>
          <div class="asset-meta">${escapeHtml(agent.role)} / Template Library</div>
        </div>
        ${statusChip(agent.status || "ready")}
      </div>
      <div class="asset-card-body">
        <div class="asset-desc">${escapeHtml(agentDescription(agent))}</div>
        ${templateMeta(agent)}
      </div>
      <div class="asset-card-footer">
        <div class="asset-chip-row">
          ${chip(agent.model, agent.model.includes("mini") ? "orange" : agent.model === "gpt-5.5" ? "purple" : "teal")}
          ${chip(`${agent.rules} rules`, "purple")}
          ${chip(`${agent.skills} skills`, "teal")}
        </div>
        <div class="asset-actions">
          <button class="link-btn" type="button" onclick="openAgentDrawer('${agent.id}')">查看</button>
        </div>
      </div>
    </article>
  `).join("");
}

function contentPreview(content, fallback) {
  const text = (content || fallback || "").replace(/\s+/g, " ").trim();
  return text.length > 150 ? `${text.slice(0, 150)}...` : text;
}

function syncStatusChip(status) {
  const label = {
    synced: "Synced",
    project_modified: "Project Modified",
    template_updated: "Template Updated",
    diverged: "Diverged",
    detached: "Detached",
  }[status] || status || "Unknown";
  const tone = {
    synced: "green",
    project_modified: "orange",
    template_updated: "purple",
    diverged: "red",
    detached: "gray",
  }[status] || "gray";
  return chip(label, tone);
}

function projectKindMeta(kind) {
  return {
    agent: { label: "Agents", field: "agents", route: "agents", icon: "asset-icon-employee" },
    rule: { label: "Rules", field: "rules", route: "rules", icon: "asset-icon-handbook" },
    skill: { label: "Skills", field: "skills", route: "skills", icon: "asset-icon-manual" },
    workflow: { label: "Workflows", field: "workflows", route: "workflows", icon: "asset-icon-process" },
  }[kind];
}

function projectConfigCopies(projectId) {
  return (NEXUS.projectConfigSets && NEXUS.projectConfigSets[projectId]) || [];
}

function renderProjectSyncSummary(projectId) {
  const mount = document.getElementById("project-sync-summary");
  if (!mount) return;
  const copies = projectConfigCopies(projectId);
  if (!copies.length) {
    mount.innerHTML = `<div class="panel-pad muted">这个项目还没有纳入同步跟踪的配置副本。</div>`;
    return;
  }

  const statuses = ["synced", "template_updated", "project_modified", "diverged", "detached"];
  const statusNotes = {
    synced: "与模板一致",
    template_updated: "模板可同步",
    project_modified: "项目有本地改动",
    diverged: "模板与项目都变更",
    detached: "项目独立维护",
  };
  mount.innerHTML = statuses.map((status) => {
    const count = copies.filter((copy) => copy.status === status).length;
    return `
      <div class="sync-summary-card">
        <div class="sync-summary-head">${syncStatusChip(status)}</div>
        <div class="sync-summary-count">${count}</div>
        <div class="sync-summary-note">${escapeHtml(statusNotes[status])}</div>
      </div>
    `;
  }).join("");
}

function renderProjectKindSummary(projectId) {
  const mount = document.getElementById("project-kind-summary");
  if (!mount) return;
  const project = findById(NEXUS.projects, projectId) || NEXUS.projects[0];
  const copies = projectConfigCopies(projectId);
  const kinds = ["agent", "rule", "skill", "workflow"];
  mount.innerHTML = kinds.map((kind) => {
    const meta = projectKindMeta(kind);
    const tracked = copies.filter((copy) => copy.kind === kind).length;
    const total = project[meta.field] ?? tracked;
    return `
      <button class="project-kind-card" type="button" onclick="openProjectResource('${project.id}', '${meta.route}')">
          <span class="asset-icon ${meta.icon}" aria-hidden="true"></span>
        <span class="project-kind-main">
          <span class="project-kind-title">${escapeHtml(meta.label)}</span>
          <span class="project-kind-meta">${tracked} 个同步副本 / ${total} 个导入项</span>
        </span>
        <span class="project-kind-count">${total}</span>
      </button>
    `;
  }).join("");
}

function projectCopyPriority(copy) {
  return {
    template_updated: 0,
    diverged: 1,
    project_modified: 2,
    detached: 3,
    synced: 4,
  }[copy.status] ?? 5;
}

function renderProjectRecentCopies(projectId) {
  const mount = document.getElementById("project-recent-copies");
  if (!mount) return;
  const copies = projectConfigCopies(projectId)
    .slice()
    .sort((left, right) => projectCopyPriority(left) - projectCopyPriority(right))
    .slice(0, 5);
  if (!copies.length) {
    mount.innerHTML = `<div class="panel-pad muted">没有需要关注的同步项。</div>`;
    return;
  }

  mount.innerHTML = copies.map((copy) => {
    const template = findTemplateForCopy(copy);
    const origin = copy.origin;
    const originText = origin
      ? `${origin.templateId} / v${origin.baseVersion}`
      : "project-only";
    return `
      <div class="project-focus-item">
        <div class="project-focus-main">
          <div class="project-focus-title">
            <span class="mono">${escapeHtml(copy.kind)}</span>
            <strong>${escapeHtml(copy.name)}</strong>
            ${syncStatusChip(copy.status)}
          </div>
          <div class="project-focus-meta">来源 ${escapeHtml(template?.name || originText)} / localVersion ${escapeHtml(copy.localVersion)}</div>
        </div>
        <button class="link-btn" type="button" onclick="openProjectCopyDrawer('${copy.id}')">查看</button>
      </div>
    `;
  }).join("");
}

function templateUsageCount(templateId) {
  return Object.values(NEXUS.projectConfigSets || {})
    .flat()
    .filter((copy) => copy.origin && copy.origin.templateId === templateId)
    .length;
}

function templateMeta(item) {
  return `
    <div class="asset-template-meta">
      <span>v${escapeHtml(item.version || 1)}</span>
      <span>${templateUsageCount(item.id)} projects</span>
      <span>${escapeHtml(item.updatedAt || "not synced")}</span>
    </div>
  `;
}

function renderProjectConfigSet(projectId) {
  const mount = document.getElementById("project-config-set");
  if (!mount) return;
  const copies = projectConfigCopies(projectId);
  mount.innerHTML = copies.map((copy) => {
    const origin = copy.origin;
    const originId = origin?.templateId || "project-only";
    const baseVersion = origin?.baseVersion || "-";
    const baseHash = origin?.baseHash || "-";
    return `
      <div class="config-copy-card">
        <div class="config-copy-main">
          <div class="config-copy-title">
            <span class="mono">${escapeHtml(copy.kind)}</span>
            <strong>${escapeHtml(copy.name)}</strong>
            ${syncStatusChip(copy.status)}
          </div>
          <div class="config-copy-meta mono">origin.templateId ${escapeHtml(originId)} / baseVersion ${escapeHtml(baseVersion)} / baseHash ${escapeHtml(baseHash)}</div>
          <div class="config-copy-meta">localVersion ${escapeHtml(copy.localVersion)} / syncMode ${escapeHtml(copy.syncMode)} / ${escapeHtml(copy.path)}</div>
        </div>
        <div class="asset-actions">
          <button class="link-btn" type="button" onclick="openProjectCopyDrawer('${copy.id}')">查看</button>
        </div>
      </div>
    `;
  }).join("") || `<div class="panel-pad muted">这个项目还没有配置副本。</div>`;
}

function templateCollectionForKind(kind) {
  return {
    agent: NEXUS.agents,
    rule: NEXUS.rules,
    skill: NEXUS.skills,
    workflow: NEXUS.workflows,
  }[kind] || [];
}

function findTemplateForCopy(copy) {
  const templateId = copy.origin?.templateId;
  if (!templateId) return null;
  return findById(templateCollectionForKind(copy.kind), templateId);
}

function projectCopySummary(copy, template) {
  if (copy.status === "detached") return "Project-only or detached copy. Manual sync can keep the local version or reconnect later through a new import flow.";
  if (copy.status === "diverged") return "Template and project copy both changed. Review the diff before deciding whether to sync or keep the project version.";
  if (copy.status === "template_updated") return "Template Library has a newer version. Sync is manual and previewed before it updates this project copy.";
  if (copy.status === "project_modified") return "Project copy has local edits. V1 keeps those edits local unless a user manually creates a new template.";
  return template?.summary || template?.purpose || template?.description || template?.capability || "Project copy is aligned with its template origin.";
}

function renderProjectResourceCard(copy) {
  const template = findTemplateForCopy(copy);
  const origin = copy.origin;
  const originId = origin?.templateId || "project-only";
  const baseVersion = origin?.baseVersion || "-";
  const icon = {
    agent: "asset-icon-employee",
    rule: "asset-icon-handbook",
    skill: "asset-icon-manual",
    workflow: "asset-icon-process",
  }[copy.kind] || "asset-icon-process";
  const templateLabel = template ? template.name : originId;
  return `
    <article class="asset-card project-copy ${escapeHtml(copy.kind)}">
      <div class="asset-card-header">
        <div class="asset-icon ${icon}" aria-hidden="true"></div>
        <div>
          <div class="asset-title">${escapeHtml(copy.name)}</div>
          <div class="asset-meta">${escapeHtml(copy.kind)} copy / from ${escapeHtml(templateLabel)}</div>
        </div>
        ${syncStatusChip(copy.status)}
      </div>
      <div class="asset-card-body">
        <div class="asset-desc">${escapeHtml(projectCopySummary(copy, template))}</div>
        <div class="asset-template-meta">
          <span>origin ${escapeHtml(originId)}</span>
          <span>base v${escapeHtml(baseVersion)}</span>
          <span>${escapeHtml(copy.path)}</span>
        </div>
      </div>
      <div class="asset-card-footer">
        <div class="asset-chip-row">
          ${chip(`localVersion ${copy.localVersion}`, "purple")}
          ${chip(`syncMode ${copy.syncMode}`, "teal")}
        </div>
        <div class="asset-actions">
          <button class="link-btn" type="button" onclick="openProjectCopyDrawer('${copy.id}')">查看</button>
        </div>
      </div>
    </article>
  `;
}

function renderProjectResourcePage(kind, mountId) {
  const mount = document.getElementById(mountId);
  if (!mount) return;
  const projectId = NEXUS.state.currentProjectId || NEXUS.projects[0]?.id;
  const search = document.getElementById(`project-${kind}-search`)?.value?.trim().toLowerCase() || "";
  const status = document.getElementById(`project-${kind}-status`)?.value || "all";
  const copies = projectConfigCopies(projectId)
    .filter((copy) => copy.kind === kind)
    .filter((copy) => status === "all" || copy.status === status)
    .filter((copy) => {
      if (!search) return true;
      const origin = copy.origin || {};
      return [
        copy.name,
        copy.kind,
        copy.path,
        copy.status,
        copy.syncMode,
        origin.templateId,
        origin.baseVersion,
        origin.baseHash,
      ].join(" ").toLowerCase().includes(search);
    });

  mount.innerHTML = copies.map(renderProjectResourceCard).join("") || `
    <div class="panel-pad muted">No ${escapeHtml(kind)} copies in this Project Config Set.</div>
  `;
}

function renderProjectAgentsPage() {
  renderProjectResourcePage("agent", "project-agent-card-grid");
}

function renderProjectRulesPage() {
  renderProjectResourcePage("rule", "project-rule-card-grid");
}

function renderProjectSkillsPage() {
  renderProjectResourcePage("skill", "project-skill-card-grid");
}

function renderProjectWorkflowsPage() {
  if (typeof renderProjectWorkflowCanvas === "function") renderProjectWorkflowCanvas();
}

function renderCurrentProjectResourcePage() {
  const page = NEXUS.state.currentPage;
  if (page === "project-agents") renderProjectAgentsPage();
  if (page === "project-rules") renderProjectRulesPage();
  if (page === "project-skills") renderProjectSkillsPage();
  if (page === "project-workflows") renderProjectWorkflowsPage();
}

function renderRulesPage() {
  const grid = document.getElementById("rule-card-grid");
  if (!grid) return;
  const rules = NEXUS.rules;
  grid.innerHTML = rules.map((rule) => `
    <article class="asset-card rule">
      <div class="asset-card-header">
        <div class="asset-icon asset-icon-handbook" aria-hidden="true"></div>
        <div>
          <div class="asset-title">${escapeHtml(rule.name)}</div>
          <div class="asset-meta">Template Library / ${escapeHtml(rule.source)}</div>
        </div>
        ${statusChip(rule.status || "active")}
      </div>
      <div class="asset-card-body">
        <div class="asset-desc">${escapeHtml(contentPreview(rule.content, rule.summary))}</div>
        ${templateMeta(rule)}
      </div>
      <div class="asset-card-footer">
        <div class="asset-actions">
          <button class="link-btn" type="button" onclick="openRuleDrawer('${rule.id}')">查看</button>
        </div>
      </div>
    </article>
  `).join("");
}

function renderSkillsPage() {
  const grid = document.getElementById("skill-card-grid");
  if (!grid) return;
  const skills = NEXUS.skills;
  grid.innerHTML = skills.map((skill) => `
    <article class="asset-card skill">
      <div class="asset-card-header">
        <div class="asset-icon asset-icon-manual" aria-hidden="true"></div>
        <div>
          <div class="asset-title">${escapeHtml(skill.name)}</div>
          <div class="asset-meta">Template Library / ${escapeHtml(skill.entry || skill.source)}</div>
        </div>
        ${statusChip(skill.status)}
      </div>
      <div class="asset-card-body">
        <div class="asset-desc">${escapeHtml(contentPreview(skill.content, skill.purpose))}</div>
        ${templateMeta(skill)}
      </div>
      <div class="asset-card-footer">
        <div class="asset-chip-row">
          ${chip(skill.appliesTo || "project agents", "teal")}
        </div>
        <div class="asset-actions">
          <button class="link-btn" type="button" onclick="openSkillDrawer('${skill.id}')">查看</button>
        </div>
      </div>
    </article>
  `).join("");
}

function renderModelRoutesPage() {
  const rows = document.getElementById("route-rows");
  if (!rows) return;
  rows.innerHTML = NEXUS.modelRoutes.map((route) => `
    <tr>
      <td>${escapeHtml(route.client)}</td>
      <td><button class="link-btn mono" type="button" onclick="openRouteDrawer('${route.id}')">${escapeHtml(route.source)}</button></td>
      <td>${escapeHtml(route.target)}</td>
      <td>${chip(route.provider, route.provider.includes("Winky") ? "teal" : "purple")}</td>
      <td>${statusChip(route.status)}</td>
      <td style="text-align:right">
        <button class="link-btn" type="button" onclick="openRouteDrawer('${route.id}')">配置</button>
        <button class="link-btn" type="button" onclick="openProxyTest('${route.id}')">测试</button>
      </td>
    </tr>
  `).join("");
}

function renderRunsPage() {
  const rows = document.getElementById("run-rows");
  if (!rows) return;
  rows.innerHTML = NEXUS.runs.map((run) => `
    <tr>
      <td><button class="link-btn mono" type="button" onclick="openWorkflowRun('${run.id}')">${escapeHtml(run.id)}</button><div class="dim">${escapeHtml(run.workflow)}</div></td>
      <td>${escapeHtml(run.project)}</td>
      <td>${statusChip(run.status)}</td>
      <td>${escapeHtml(run.modelCost)}</td>
      <td>${escapeHtml(run.duration)}</td>
      <td>${escapeHtml(run.summary)}</td>
      <td style="text-align:right"><button class="link-btn" type="button" onclick="openWorkflowRun('${run.id}')">详情</button></td>
    </tr>
  `).join("");
}
