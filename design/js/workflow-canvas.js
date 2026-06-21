const WORKFLOW_NODE_TYPES = ["input", "agent", "tool", "sequence", "parallel", "condition", "join", "transform", "human_approval", "decorator"];
const WORKFLOW_NODE_WIDTH = 236;
const WORKFLOW_NODE_CATEGORY_CLASSES = ["node-action", "node-control", "node-condition", "node-data", "node-human", "node-event", "node-decorator"];

const WORKFLOW_NODE_META = {
  input: { category: "event", label: "Event", tone: "gray", role: "触发 / 输入" },
  agent: { category: "action", label: "Agent", tone: "purple", role: "角色动作" },
  tool: { category: "action", label: "Tool", tone: "purple", role: "工具动作" },
  sequence: { category: "control", label: "Sequence", tone: "teal", role: "顺序控制" },
  parallel: { category: "control", label: "Parallel", tone: "teal", role: "并行控制" },
  join: { category: "control", label: "Join", tone: "teal", role: "结果汇聚" },
  condition: { category: "condition", label: "Condition", tone: "orange", role: "分支判断" },
  transform: { category: "data", label: "Transform", tone: "gray", role: "数据映射" },
  human_approval: { category: "human", label: "Human", tone: "pink", role: "人工门禁" },
  decorator: { category: "decorator", label: "Decorator", tone: "orange", role: "重试 / 超时 / Guard" },
};

function workflowNodeCategory(type) {
  return WORKFLOW_NODE_META[type]?.category || "data";
}

function workflowNodeMeta(type) {
  return WORKFLOW_NODE_META[type] || { category: "data", label: type || "Node", tone: "gray", role: "数据处理" };
}

function isWorkflowEditing() {
  return Boolean(NEXUS.state.workflowEditorMode);
}

function currentWorkflowMeta() {
  return findById(NEXUS.workflows, NEXUS.state.currentWorkflowId) || NEXUS.workflows?.[0];
}

function renderWorkflowCanvas() {
  renderWorkflowList();
  renderNodePalette();

  const canvas = document.getElementById("workflow-canvas");
  if (!canvas) return;
  const previousScroll = { left: canvas.scrollLeft, top: canvas.scrollTop };
  canvas.innerHTML = `<div class="workflow-stage" id="workflow-stage"><svg class="workflow-edge-layer" id="workflow-edge-layer"></svg></div>`;
  const stage = document.getElementById("workflow-stage");

  NEXUS.workflow.nodes.forEach((node) => {
    const meta = workflowNodeMeta(node.type);
    const el = document.createElement("button");
    el.type = "button";
    el.className = `workflow-node node-${workflowNodeCategory(node.type)} ${node.status || "idle"} ${node.id === NEXUS.state.selectedNodeId ? "selected" : ""}`;
    el.style.left = `${node.x}px`;
    el.style.top = `${node.y}px`;
    el.dataset.nodeId = node.id;
    el.onclick = () => selectWorkflowNode(node.id);
    el.innerHTML = `
      <div class="node-head">
        <span class="node-title">${escapeHtml(node.label)}</span>
        <span class="node-type-badge">${escapeHtml(meta.label)}</span>
      </div>
      <div class="node-body">
        <div class="node-detail">${escapeHtml(node.detail)}</div>
        <div class="node-role">${escapeHtml(meta.role)}</div>
        <div class="node-port-row"><span>in</span><span>out</span></div>
      </div>
    `;
    stage.appendChild(el);
  });

  renderWorkflowEdges();
  renderWorkflowInspector();
  canvas.scrollLeft = previousScroll.left;
  canvas.scrollTop = previousScroll.top;
}

function projectWorkflowCopies() {
  const projectId = NEXUS.state.currentProjectId || NEXUS.projects[0]?.id;
  const query = document.getElementById("project-workflow-search")?.value?.trim().toLowerCase() || "";
  const status = document.getElementById("project-workflow-status")?.value || "all";
  return projectConfigCopies(projectId)
    .filter((copy) => copy.kind === "workflow")
    .filter((copy) => status === "all" || copy.status === status)
    .filter((copy) => {
      if (!query) return true;
      const origin = copy.origin || {};
      return [
        copy.name,
        copy.path,
        copy.status,
        copy.syncMode,
        origin.templateId,
        origin.baseHash,
      ].join(" ").toLowerCase().includes(query);
    });
}

function currentProjectWorkflowCopy() {
  const copies = projectWorkflowCopies();
  return findById(copies, NEXUS.state.currentProjectWorkflowCopyId) || copies[0] || null;
}

function projectWorkflowTemplate(copy) {
  return copy ? findTemplateForCopy(copy) || findById(NEXUS.workflows, copy.origin?.templateId) || NEXUS.workflows[0] : NEXUS.workflows[0];
}

function renderProjectWorkflowCanvas() {
  renderProjectWorkflowList();
  renderProjectNodePalette();

  const canvas = document.getElementById("project-workflow-canvas");
  if (!canvas) return;
  const previousScroll = { left: canvas.scrollLeft, top: canvas.scrollTop };
  canvas.innerHTML = `<div class="workflow-stage" id="project-workflow-stage"><svg class="workflow-edge-layer" id="project-workflow-edge-layer"></svg></div>`;
  const stage = document.getElementById("project-workflow-stage");

  NEXUS.workflow.nodes.forEach((node) => {
    const meta = workflowNodeMeta(node.type);
    const el = document.createElement("button");
    el.type = "button";
    el.className = `workflow-node node-${workflowNodeCategory(node.type)} ${node.status || "idle"} ${node.id === NEXUS.state.selectedNodeId ? "selected" : ""}`;
    el.style.left = `${node.x}px`;
    el.style.top = `${node.y}px`;
    el.dataset.nodeId = node.id;
    el.onclick = () => selectProjectWorkflowNode(node.id);
    el.innerHTML = `
      <div class="node-head">
        <span class="node-title">${escapeHtml(node.label)}</span>
        <span class="node-type-badge">${escapeHtml(meta.label)}</span>
      </div>
      <div class="node-body">
        <div class="node-detail">${escapeHtml(node.detail)}</div>
        <div class="node-role">${escapeHtml(meta.role)}</div>
        <div class="node-port-row"><span>in</span><span>out</span></div>
      </div>
    `;
    stage.appendChild(el);
  });

  renderProjectWorkflowEdges();
  renderProjectWorkflowInspector();
  canvas.scrollLeft = previousScroll.left;
  canvas.scrollTop = previousScroll.top;
}

function renderProjectWorkflowList() {
  const mount = document.getElementById("project-workflow-list");
  if (!mount) return;
  const copies = projectWorkflowCopies();
  if (!copies.length) {
    mount.innerHTML = `<div class="source-item"><span>No workflow copies in this Project Config Set</span>${chip("empty", "gray")}</div>`;
    return;
  }
  if (!findById(copies, NEXUS.state.currentProjectWorkflowCopyId)) {
    NEXUS.state.currentProjectWorkflowCopyId = copies[0].id;
  }

  mount.innerHTML = copies.map((copy) => {
    const workflow = projectWorkflowTemplate(copy);
    const origin = copy.origin || {};
    return `
      <div class="workflow-card ${copy.id === NEXUS.state.currentProjectWorkflowCopyId ? "active" : ""}">
        <button class="workflow-card-main" type="button" onclick="selectProjectWorkflowCopy('${copy.id}')">
          <div class="workflow-card-head">
            <div class="asset-icon asset-icon-process workflow-card-icon" aria-hidden="true"></div>
            <div>
              <div class="workflow-card-title">${escapeHtml(copy.name)}</div>
              <div class="asset-meta">Project Config Set / ${escapeHtml(origin.templateId || "project-only")}</div>
            </div>
          </div>
          <div class="workflow-card-desc">${escapeHtml(projectCopySummary(copy, workflow))}</div>
          <div class="asset-template-meta">
            <span>base v${escapeHtml(origin.baseVersion || "-")}</span>
            <span>localVersion ${escapeHtml(copy.localVersion)}</span>
            <span>syncMode ${escapeHtml(copy.syncMode)}</span>
          </div>
          <div class="workflow-card-meta">
            ${syncStatusChip(copy.status)}
            ${chip(`${workflow?.nodeCount || NEXUS.workflow.nodes.length} nodes`, "purple")}
            ${chip(`${workflow?.edgeCount || NEXUS.workflow.edges.length} edges`, "teal")}
          </div>
        </button>
        <div class="workflow-card-actions">
          <button class="link-btn" type="button" onclick="editProjectWorkflow('${copy.id}')">编辑</button>
          <button class="link-btn" type="button" onclick="syncTemplateToProject('${copy.id}')">同步模板</button>
        </div>
      </div>
    `;
  }).join("");
}

function renderProjectNodePalette() {
  const mount = document.getElementById("project-workflow-palette");
  if (!mount) return;
  if (!isWorkflowEditing()) {
    mount.innerHTML = "";
    return;
  }
  mount.innerHTML = `
    <div class="section-header">
      <div class="section-title">Node Palette</div>
      <div class="section-spacer"></div>
      <button class="link-btn" type="button" onclick="cancelProjectWorkflowEdit()">退出编辑</button>
    </div>
    <div class="source-list">
      ${WORKFLOW_NODE_TYPES.map((type) => {
        const meta = workflowNodeMeta(type);
        return `<div class="source-item node-palette-item node-${meta.category}"><span>${escapeHtml(meta.label)}</span><span class="chip chip-${meta.tone}">${escapeHtml(meta.role)}</span></div>`;
      }).join("")}
    </div>
  `;
}

function selectProjectWorkflowCopy(copyId) {
  NEXUS.state.currentProjectWorkflowCopyId = copyId;
  NEXUS.state.workflowEditorMode = false;
  renderProjectWorkflowCanvas();
}

function editProjectWorkflow(copyId) {
  NEXUS.state.currentProjectWorkflowCopyId = copyId;
  NEXUS.state.workflowEditorMode = true;
  renderProjectWorkflowCanvas();
  showToast("正在编辑项目工作流");
}

function cancelProjectWorkflowEdit() {
  NEXUS.state.workflowEditorMode = false;
  renderProjectWorkflowCanvas();
  showToast("已退出节点编排模式");
}

function renderWorkflowList() {
  const mount = document.getElementById("workflow-list");
  if (!mount) return;
  const query = document.getElementById("workflow-search")?.value?.trim().toLowerCase() || "";
  const workflows = (NEXUS.workflows || []).filter((workflow) => {
    if (!query) return true;
    return [
      workflow.name,
      workflow.id,
      workflow.project,
      workflow.trigger,
      workflow.description,
      ...(workflow.tags || []),
    ].join(" ").toLowerCase().includes(query);
  });

  if (!workflows.length) {
    mount.innerHTML = `<div class="source-item"><span>没有匹配的工作流</span>${chip("empty", "gray")}</div>`;
    return;
  }

  mount.innerHTML = workflows.map((workflow) => `
    <div class="workflow-card ${workflow.id === NEXUS.state.currentWorkflowId ? "active" : ""}">
      <button class="workflow-card-main" type="button" onclick="selectWorkflow('${workflow.id}')">
        <div class="workflow-card-head">
          <div class="asset-icon asset-icon-process workflow-card-icon" aria-hidden="true"></div>
          <div>
            <div class="workflow-card-title">${escapeHtml(workflow.name)}</div>
            <div class="asset-meta">Template Library / ${escapeHtml(workflow.trigger)}</div>
          </div>
        </div>
        <div class="workflow-card-desc">${escapeHtml(workflow.description)}</div>
        <div class="asset-template-meta">
          <span>v${escapeHtml(workflow.version || 1)}</span>
          <span>${templateUsageCount(workflow.id)} projects</span>
          <span>${escapeHtml(workflow.entry || "templates/workflows/workflow.md")}</span>
        </div>
        <div class="workflow-card-meta">
          ${statusChip(workflow.status)}
          ${chip(`${workflow.nodeCount} nodes`, "purple")}
          ${chip(`${workflow.edgeCount} edges`, "teal")}
        </div>
      </button>
      <div class="workflow-card-actions">
        <button class="link-btn" type="button" onclick="editWorkflow('${workflow.id}')">编辑</button>
        <button class="link-btn" type="button" onclick="deleteWorkflow('${workflow.id}')">删除</button>
      </div>
    </div>
  `).join("");
}

function renderNodePalette() {
  const mount = document.getElementById("workflow-palette");
  if (!mount) return;

  if (!isWorkflowEditing()) {
    mount.innerHTML = "";
    return;
  }

  mount.innerHTML = `
    <div class="section-header">
      <div class="section-title">Node Palette</div>
      <div class="section-spacer"></div>
      <button class="link-btn" type="button" onclick="cancelWorkflowEdit()">退出编辑</button>
    </div>
    <div class="source-list">
      ${WORKFLOW_NODE_TYPES.map((type) => {
        const meta = workflowNodeMeta(type);
        return `<div class="source-item node-palette-item node-${meta.category}"><span>${escapeHtml(meta.label)}</span><span class="chip chip-${meta.tone}">${escapeHtml(meta.role)}</span></div>`;
      }).join("")}
    </div>
  `;
}

function createWorkflow() {
  const id = `draft-workflow-${Date.now()}`;
  NEXUS.workflows.unshift({
    id,
    name: "Untitled workflow",
    project: "all",
    status: "draft",
    updatedAt: "now",
    owner: "current user",
    nodeCount: 3,
    edgeCount: 2,
    runCount: 0,
    trigger: "manual",
    tags: ["draft", "manual"],
    description: "新建工作流草稿。后续版本会进入真实节点编辑和保存流程。",
    kind: "workflow",
    version: 1,
    templateId: id,
    slug: id,
    entry: `templates/workflows/${id}.md`,
    files: [`templates/workflows/${id}.md`],
  });
  NEXUS.state.currentWorkflowId = id;
  NEXUS.state.workflowEditorMode = true;
  renderWorkflowCanvas();
  showToast("已创建工作流草稿");
}

function selectWorkflow(workflowId) {
  const workflow = findById(NEXUS.workflows, workflowId);
  if (!workflow) return;
  NEXUS.state.currentWorkflowId = workflowId;
  NEXUS.state.workflowEditorMode = false;
  renderWorkflowCanvas();
}

function editWorkflow(workflowId) {
  const workflow = findById(NEXUS.workflows, workflowId);
  if (!workflow) return;
  NEXUS.state.currentWorkflowId = workflowId;
  NEXUS.state.workflowEditorMode = true;
  renderWorkflowCanvas();
  showToast(`正在编辑 ${workflow.name}`);
}

function cancelWorkflowEdit() {
  NEXUS.state.workflowEditorMode = false;
  renderWorkflowCanvas();
  showToast("已退出节点编排模式");
}

function duplicateWorkflow(workflowId) {
  const workflow = findById(NEXUS.workflows, workflowId);
  if (!workflow) return;
  const copy = {
    ...workflow,
    id: `${workflow.id}-copy-${Date.now()}`,
    name: `${workflow.name} copy`,
    status: "draft",
    updatedAt: "now",
    runCount: 0,
  };
  NEXUS.workflows.unshift(copy);
  NEXUS.state.currentWorkflowId = copy.id;
  NEXUS.state.workflowEditorMode = true;
  renderWorkflowCanvas();
  showToast("已复制为草稿工作流");
}

function deleteWorkflow(workflowId) {
  const workflow = findById(NEXUS.workflows, workflowId);
  if (!workflow) return;
  if (NEXUS.workflows.length <= 1) {
    showToast("至少保留一个工作流");
    return;
  }
  NEXUS.workflows = NEXUS.workflows.filter((item) => item.id !== workflowId);
  if (NEXUS.state.currentWorkflowId === workflowId) {
    NEXUS.state.currentWorkflowId = NEXUS.workflows[0]?.id || "";
  }
  NEXUS.state.workflowEditorMode = false;
  renderWorkflowCanvas();
  showToast(`已删除 ${workflow.name}`);
}

function renderWorkflowEdges() {
  const svg = document.getElementById("workflow-edge-layer");
  const stage = document.getElementById("workflow-stage");
  if (!svg || !stage) return;
  const nodes = Object.fromEntries(NEXUS.workflow.nodes.map((node) => [node.id, node]));
  const width = stage.clientWidth || 1420;
  const height = stage.clientHeight || 620;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.innerHTML = NEXUS.workflow.edges.map((edge) => {
    const from = nodes[edge.from];
    const to = nodes[edge.to];
    if (!from || !to) return "";
    const x1 = from.x + WORKFLOW_NODE_WIDTH;
    const y1 = from.y + 48;
    const x2 = to.x;
    const y2 = to.y + 48;
    const mid = Math.max(40, (x2 - x1) / 2);
    const labelX = (x1 + x2) / 2 - 24;
    const labelY = (y1 + y2) / 2 - 8;
    return `
      <path class="workflow-edge" d="M ${x1} ${y1} C ${x1 + mid} ${y1}, ${x2 - mid} ${y2}, ${x2} ${y2}"></path>
      <text class="workflow-edge-label" x="${labelX}" y="${labelY}">${escapeHtml(edge.label)}</text>
    `;
  }).join("");
}

function renderProjectWorkflowEdges() {
  const svg = document.getElementById("project-workflow-edge-layer");
  const stage = document.getElementById("project-workflow-stage");
  if (!svg || !stage) return;
  const nodes = Object.fromEntries(NEXUS.workflow.nodes.map((node) => [node.id, node]));
  const width = stage.clientWidth || 1420;
  const height = stage.clientHeight || 620;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.innerHTML = NEXUS.workflow.edges.map((edge) => {
    const from = nodes[edge.from];
    const to = nodes[edge.to];
    if (!from || !to) return "";
    const x1 = from.x + WORKFLOW_NODE_WIDTH;
    const y1 = from.y + 48;
    const x2 = to.x;
    const y2 = to.y + 48;
    const mid = Math.max(40, (x2 - x1) / 2);
    const labelX = (x1 + x2) / 2 - 24;
    const labelY = (y1 + y2) / 2 - 8;
    return `
      <path class="workflow-edge" d="M ${x1} ${y1} C ${x1 + mid} ${y1}, ${x2 - mid} ${y2}, ${x2} ${y2}"></path>
      <text class="workflow-edge-label" x="${labelX}" y="${labelY}">${escapeHtml(edge.label)}</text>
    `;
  }).join("");
}

function selectWorkflowNode(nodeId) {
  NEXUS.state.selectedNodeId = nodeId;
  renderWorkflowCanvas();
}

function selectProjectWorkflowNode(nodeId) {
  NEXUS.state.selectedNodeId = nodeId;
  renderProjectWorkflowCanvas();
}

function renderWorkflowInspector() {
  const inspector = document.getElementById("workflow-inspector");
  if (!inspector) return;

  if (!isWorkflowEditing()) {
    const workflow = currentWorkflowMeta();
    inspector.innerHTML = `
      <div class="panel-header"><div class="panel-title">Workflow Summary</div></div>
      <div class="panel-body">
        <div class="source-list">
          <div class="source-item"><span>Name</span><span>${escapeHtml(workflow?.name || "-")}</span></div>
          <div class="source-item"><span>Project</span><span>${escapeHtml(workflow?.project || "-")}</span></div>
          <div class="source-item"><span>Status</span>${statusChip(workflow?.status || "draft")}</div>
          <div class="source-item"><span>Trigger</span><span>${escapeHtml(workflow?.trigger || "manual")}</span></div>
        </div>
        <div class="section-header"><div class="section-title">Preview</div></div>
        <p class="muted" style="line-height:1.6">${escapeHtml(workflow?.description || "选择一个工作流查看摘要。")}</p>
        <button class="btn-primary full-width" type="button" onclick="editWorkflow('${workflow?.id || ""}')">编辑工作流</button>
      </div>
    `;
    return;
  }

  const node = findById(NEXUS.workflow.nodes, NEXUS.state.selectedNodeId) || NEXUS.workflow.nodes[0];
  inspector.innerHTML = `
    <div class="panel-header"><div class="panel-title">Selected Node</div></div>
    <div class="panel-body">
      <div class="source-list">
        <div class="source-item"><span>ID</span><span class="mono">${escapeHtml(node.id)}</span></div>
        <div class="source-item"><span>Type</span>${chip(node.type, workflowNodeMeta(node.type).tone)}</div>
        <div class="source-item"><span>Agent</span><span>${escapeHtml(node.agent)}</span></div>
        <div class="source-item"><span>Status</span>${statusChip(node.status)}</div>
      </div>
      <div class="section-header"><div class="section-title">Schema</div></div>
      <pre class="code-block">input:
  context: markdown
  diff: optional
output:
  result: markdown
  findings: array</pre>
      <button class="btn-primary full-width" type="button" onclick="openNodeConfig('${node.id}')">配置节点</button>
    </div>
  `;
}

function renderProjectWorkflowInspector() {
  const inspector = document.getElementById("project-workflow-inspector");
  if (!inspector) return;
  const copy = currentProjectWorkflowCopy();
  const workflow = projectWorkflowTemplate(copy);
  if (!copy) {
    inspector.innerHTML = `
      <div class="panel-header"><div class="panel-title">Workflow Summary</div></div>
      <div class="panel-body muted">No workflow copy selected.</div>
    `;
    return;
  }

  if (!isWorkflowEditing()) {
    const origin = copy.origin || {};
    inspector.innerHTML = `
      <div class="panel-header"><div class="panel-title">Workflow Summary</div></div>
      <div class="panel-body">
        <div class="source-list">
          <div class="source-item"><span>Name</span><span>${escapeHtml(copy.name)}</span></div>
          <div class="source-item"><span>Status</span>${syncStatusChip(copy.status)}</div>
          <div class="source-item"><span>Template</span><span>${escapeHtml(origin.templateId || "project-only")}</span></div>
          <div class="source-item"><span>Version</span><span>base v${escapeHtml(origin.baseVersion || "-")} / local ${escapeHtml(copy.localVersion)}</span></div>
          <div class="source-item"><span>Path</span><span class="mono">${escapeHtml(copy.path)}</span></div>
        </div>
        <div class="section-header"><div class="section-title">Preview</div></div>
        <p class="muted" style="line-height:1.6">${escapeHtml(projectCopySummary(copy, workflow))}</p>
        <button class="btn-primary full-width" type="button" onclick="editProjectWorkflow('${copy.id}')">编辑工作流</button>
      </div>
    `;
    return;
  }

  const node = findById(NEXUS.workflow.nodes, NEXUS.state.selectedNodeId) || NEXUS.workflow.nodes[0];
  inspector.innerHTML = `
    <div class="panel-header"><div class="panel-title">Selected Node</div></div>
    <div class="panel-body">
      <div class="source-list">
        <div class="source-item"><span>ID</span><span class="mono">${escapeHtml(node.id)}</span></div>
        <div class="source-item"><span>Type</span>${chip(node.type, workflowNodeMeta(node.type).tone)}</div>
        <div class="source-item"><span>Agent</span><span>${escapeHtml(node.agent)}</span></div>
        <div class="source-item"><span>Status</span>${statusChip(node.status)}</div>
      </div>
      <div class="section-header"><div class="section-title">Project Mapping</div></div>
      <pre class="code-block">workflowCopy: ${escapeHtml(copy.id)}
origin.templateId: ${escapeHtml(copy.origin?.templateId || "project-only")}
syncMode: ${escapeHtml(copy.syncMode)}</pre>
      <button class="btn-primary full-width" type="button" onclick="openNodeConfig('${node.id}')">配置节点</button>
    </div>
  `;
}

async function openNodeConfig(nodeId) {
  await loadOverlay("drawer-node-config");
  const node = findById(NEXUS.workflow.nodes, nodeId || NEXUS.state.selectedNodeId) || NEXUS.workflow.nodes[0];
  document.getElementById("node-config-title").textContent = node.label;
  document.getElementById("node-config-subtitle").textContent = `${node.type} / ${node.agent}`;
  document.getElementById("node-config-body").innerHTML = `
    <div class="source-list">
      <div class="source-item"><span>Node ID</span><span class="mono">${escapeHtml(node.id)}</span></div>
      <div class="source-item"><span>Type</span>${chip(node.type, workflowNodeMeta(node.type).tone)}</div>
      <div class="source-item"><span>Bound agent</span><span>${escapeHtml(node.agent)}</span></div>
      <div class="source-item"><span>Execution</span><span>text flow mock</span></div>
    </div>
    <div class="section-header"><div class="section-title">Input Mapping</div></div>
    <pre class="code-block">context <- previous.output.result
project <- projects.current
rules <- project.rules[*]
skills <- agent.skills[*]</pre>
    <div class="section-header"><div class="section-title">Prompt Context</div></div>
    <textarea>Role: ${escapeHtml(node.agent)}
Task: ${escapeHtml(node.detail)}
Return structured markdown and JSON summary.</textarea>
  `;
  openDrawer("drawer-node-config");
}

function simulateWorkflowRun() {
  const order = ["node-plan", "node-condition", "node-parallel", "node-logic", "node-perf", "node-security", "node-join", "node-guard", "node-human"];
  NEXUS.workflow.nodes.forEach((node) => {
    if (node.id !== "node-input") node.status = "idle";
  });
  let index = 0;
  const tick = () => {
    if (index > 0) {
      const prev = findById(NEXUS.workflow.nodes, order[index - 1]);
      if (prev) prev.status = "done";
    }
    const current = findById(NEXUS.workflow.nodes, order[index]);
    if (current) current.status = index === order.length - 1 ? "waiting" : "running";
    renderWorkflowCanvas();
    index += 1;
    if (index <= order.length) {
      setTimeout(tick, 360);
    } else {
      showToast("工作流模拟运行完成，等待人工确认");
    }
  };
  tick();
}
