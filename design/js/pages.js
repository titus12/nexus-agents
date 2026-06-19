function renderProjectTree() {
  const mount = document.getElementById("project-tree");
  if (!mount) return;
  mount.innerHTML = NEXUS.projects.map((project) => `
    <button class="project-entry ${project.id === NEXUS.state.currentProjectId ? "active" : ""}" type="button" onclick="openProject('${project.id}')">
      <span class="project-dot" style="background:${project.status === "ready" ? "var(--teal)" : "var(--orange)"}"></span>
      <span class="project-name">${escapeHtml(project.name)}</span>
      <span class="project-mini">${project.agents}</span>
    </button>
  `).join("");
}

function openProject(projectId) {
  NEXUS.state.currentProjectId = projectId;
  renderProjectTree();
  navigate("project-detail");
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

  const sources = document.getElementById("project-sources");
  if (sources) {
    sources.innerHTML = project.sources.map((source) => `
      <div class="source-item">
        <div>
          <div class="mono">${escapeHtml(source.name)}</div>
          <div class="dim">${escapeHtml(source.type)} / ${source.count} items</div>
        </div>
        ${statusChip(source.state)}
      </div>
    `).join("");
  }

  const health = document.getElementById("project-health");
  if (health) {
    health.innerHTML = project.health.map((item) => `
      <div class="timeline-item">
        <div class="timeline-title">${escapeHtml(item.label)} ${statusChip(item.state)}</div>
        <div class="timeline-meta">${escapeHtml(item.note)}</div>
      </div>
    `).join("");
  }
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
  renderAgentProjectFilter();
  const grid = document.getElementById("agent-card-grid");
  if (!grid) return;
  const selectedProject = NEXUS.state.agentProjectFilter || "all";
  const agents = NEXUS.agents.filter((agent) => projectMatches(agent.project || "all", selectedProject));
  grid.innerHTML = agents.map((agent) => `
    <article class="asset-card agent ${agentCardTone(agent)}">
      <div class="asset-card-header">
        <div class="asset-icon asset-icon-employee" aria-hidden="true"></div>
        <div>
          <div class="asset-title">${escapeHtml(agent.name)}</div>
          <div class="asset-meta">${escapeHtml(agent.role)}</div>
        </div>
        ${statusChip(agent.status || "ready")}
      </div>
      <div class="asset-card-body">
        <div class="asset-desc">${escapeHtml(agentDescription(agent))}</div>
      </div>
      <div class="asset-card-footer">
        <div class="asset-chip-row">
          ${chip(agent.model, agent.model.includes("mini") ? "orange" : agent.model === "gpt-5.5" ? "purple" : "teal")}
          ${chip(`${agent.rules} rules`, "purple")}
          ${chip(`${agent.skills} skills`, "teal")}
        </div>
        <div class="asset-actions">
          <button class="link-btn" type="button" onclick="openAgentDrawer('${agent.id}')">查看</button>
          <button class="link-btn" type="button" onclick="openAgentDrawer('${agent.id}')">编辑</button>
        </div>
      </div>
    </article>
  `).join("");
}

function projectMatches(itemProject, selectedProject) {
  return selectedProject === "all" || itemProject === "all" || itemProject === selectedProject;
}

function contentPreview(content, fallback) {
  const text = (content || fallback || "").replace(/\s+/g, " ").trim();
  return text.length > 150 ? `${text.slice(0, 150)}...` : text;
}

function renderProjectFilter(mountId, selectedProject, handlerName) {
  const mount = document.getElementById(mountId);
  if (!mount) return;
  const options = [
    { id: "all", name: "ALL" },
    ...NEXUS.projects.map((project) => ({ id: project.id, name: project.name })),
  ];
  mount.innerHTML = `
    <label class="project-filter-label">
      <span>Project</span>
      <select class="project-filter-select" onchange="${handlerName}(this.value)">
        ${options.map((option) => `<option value="${escapeHtml(option.id)}"${option.id === selectedProject ? " selected" : ""}>${escapeHtml(option.name)}</option>`).join("")}
      </select>
    </label>
  `;
}

function renderAgentProjectFilter() {
  renderProjectFilter("agent-project-filter", NEXUS.state.agentProjectFilter || "all", "setAgentProjectFilter");
}

function setAgentProjectFilter(projectId) {
  NEXUS.state.agentProjectFilter = projectId;
  renderAgentsPage();
}

function renderRuleProjectFilter() {
  renderProjectFilter("rule-project-filter", NEXUS.state.ruleProjectFilter || "all", "setRuleProjectFilter");
}

function setRuleProjectFilter(projectId) {
  NEXUS.state.ruleProjectFilter = projectId;
  renderRulesPage();
}

function renderSkillProjectFilter() {
  renderProjectFilter("skill-project-filter", NEXUS.state.skillProjectFilter || "all", "setSkillProjectFilter");
}

function setSkillProjectFilter(projectId) {
  NEXUS.state.skillProjectFilter = projectId;
  renderSkillsPage();
}

function renderRulesPage() {
  renderRuleProjectFilter();
  const grid = document.getElementById("rule-card-grid");
  if (!grid) return;
  const selectedProject = NEXUS.state.ruleProjectFilter || "all";
  const rules = NEXUS.rules.filter((rule) => projectMatches(rule.project, selectedProject));
  grid.innerHTML = rules.map((rule) => `
    <article class="asset-card rule">
      <div class="asset-card-header">
        <div class="asset-icon asset-icon-handbook" aria-hidden="true"></div>
        <div>
          <div class="asset-title">${escapeHtml(rule.name)}</div>
          <div class="asset-meta">${escapeHtml(rule.project === "all" ? "ALL projects" : rule.project)} / ${escapeHtml(rule.source)}</div>
        </div>
        ${statusChip(rule.status || "active")}
      </div>
      <div class="asset-card-body">
        <div class="asset-desc">${escapeHtml(contentPreview(rule.content, rule.summary))}</div>
      </div>
      <div class="asset-card-footer">
        <div class="asset-actions">
          <button class="link-btn" type="button" onclick="openRuleDrawer('${rule.id}')">查看</button>
          <button class="link-btn" type="button" onclick="openRuleDrawer('${rule.id}')">编辑</button>
        </div>
      </div>
    </article>
  `).join("");
}

function renderSkillsPage() {
  renderSkillProjectFilter();
  const grid = document.getElementById("skill-card-grid");
  if (!grid) return;
  const selectedProject = NEXUS.state.skillProjectFilter || "all";
  const skills = NEXUS.skills.filter((skill) => projectMatches(skill.project, selectedProject));
  grid.innerHTML = skills.map((skill) => `
    <article class="asset-card skill">
      <div class="asset-card-header">
        <div class="asset-icon asset-icon-manual" aria-hidden="true"></div>
        <div>
          <div class="asset-title">${escapeHtml(skill.name)}</div>
          <div class="asset-meta">${escapeHtml(skill.project === "all" ? "ALL projects" : skill.project)} / ${escapeHtml(skill.source)}</div>
        </div>
        ${statusChip(skill.status)}
      </div>
      <div class="asset-card-body">
        <div class="asset-desc">${escapeHtml(contentPreview(skill.content, skill.purpose))}</div>
      </div>
      <div class="asset-card-footer">
        <div class="asset-chip-row">
          ${chip(skill.appliesTo || "btd-game-server agents", "teal")}
        </div>
        <div class="asset-actions">
          <button class="link-btn" type="button" onclick="openSkillDrawer('${skill.id}')">查看</button>
          <button class="link-btn" type="button" onclick="openSkillDrawer('${skill.id}')">编辑</button>
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
