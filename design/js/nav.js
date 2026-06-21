const PAGES = {
  'projects': { id: 'page-projects', url: 'pages/projects.html', title: '项目', onShow: renderProjectsPage },
  'project-detail': { id: 'page-project-detail', url: 'pages/project-detail.html', title: '项目详情', onShow: renderProjectDetail },
  'project-agents': { id: 'page-project-agents', url: 'pages/project-agents.html', title: 'Project Agents', onShow: renderProjectAgentsPage },
  'project-rules': { id: 'page-project-rules', url: 'pages/project-rules.html', title: 'Project Rules', onShow: renderProjectRulesPage },
  'project-skills': { id: 'page-project-skills', url: 'pages/project-skills.html', title: 'Project Skills', onShow: renderProjectSkillsPage },
  'project-workflows': { id: 'page-project-workflows', url: 'pages/project-workflows.html', title: 'Project Workflows', onShow: renderProjectWorkflowsPage },
  'agents': { id: 'page-agents', url: 'pages/agents.html', title: 'Agents', onShow: renderAgentsPage },
  'rules': { id: 'page-rules', url: 'pages/rules.html', title: 'Rules', onShow: renderRulesPage },
  'skills': { id: 'page-skills', url: 'pages/skills.html', title: 'Skills', onShow: renderSkillsPage },
  'workflows': { id: 'page-workflows', url: 'pages/workflows.html', title: 'Workflows', onShow: renderWorkflowCanvas },
  'model-routes': { id: 'page-model-routes', url: 'pages/model-routes.html', title: '模型代理', onShow: renderModelRoutesPage },
};

const OVERLAYS = {
  'modal-import-project': { id: 'modal-import-project', url: 'overlays/modal-import-project.html' },
  'drawer-sync-preview': { id: 'drawer-sync-preview', url: 'overlays/drawer-sync-preview.html' },
  'drawer-agent': { id: 'drawer-agent', url: 'overlays/drawer-agent.html' },
  'drawer-rule': { id: 'drawer-rule', url: 'overlays/drawer-rule.html' },
  'drawer-skill': { id: 'drawer-skill', url: 'overlays/drawer-skill.html' },
  'drawer-node-config': { id: 'drawer-node-config', url: 'overlays/drawer-node-config.html' },
  'drawer-workflow-run': { id: 'drawer-workflow-run', url: 'overlays/drawer-workflow-run.html' },
  'drawer-project-copy': { id: 'drawer-project-copy', url: 'overlays/drawer-project-copy.html' },
  'drawer-route': { id: 'drawer-route', url: 'overlays/drawer-route.html' },
  'drawer-proxy-test': { id: 'drawer-proxy-test', url: 'overlays/drawer-proxy-test.html' },
};

const loadedFragments = new Set();

document.querySelectorAll("[data-nav]").forEach((item) => {
  item.addEventListener("click", () => navigate(item.dataset.nav));
});

async function loadFragment(url, mountSelector) {
  if (loadedFragments.has(url)) return;
  let html = "";
  const cached = window.NEXUS_FRAGMENTS?.[url];
  if (window.location.protocol === "file:" && cached) {
    html = cached;
  } else {
    try {
      const response = await fetch(`${url}${url.includes("?") ? "&" : "?"}b=${Date.now()}`, { cache: "no-store" });
      if (!response.ok) throw new Error(`Failed to load ${url}: ${response.status}`);
      html = await response.text();
    } catch (error) {
      if (!cached) throw error;
      html = cached;
    }
  }
  const mount = document.querySelector(mountSelector);
  mount.insertAdjacentHTML("beforeend", html);
  loadedFragments.add(url);
}

async function navigate(pageId) {
  const page = PAGES[pageId];
  if (!page) {
    console.warn("Unknown page", pageId);
    return;
  }
  try {
    await loadFragment(page.url, "#page-container");
  } catch (error) {
    console.error(error);
    showToast(`页面加载失败: ${page.url}`);
    return;
  }
  Object.values(PAGES).forEach((spec) => {
    const el = document.getElementById(spec.id);
    if (el) el.classList.remove("active");
  });
  document.getElementById(page.id)?.classList.add("active");
  NEXUS.state.currentPage = pageId;
  updateSidebar(pageId);
  updateTopbar(pageId);
  if (typeof page.onShow === "function") page.onShow();
}

async function loadOverlay(overlayId) {
  const overlay = OVERLAYS[overlayId];
  if (!overlay) {
    console.warn("Unknown overlay", overlayId);
    return;
  }
  await loadFragment(overlay.url, "#overlay-container");
}

function projectResourceTitle(pageId) {
  return {
    "project-agents": "Agents",
    "project-rules": "Rules",
    "project-skills": "Skills",
    "project-workflows": "Workflows",
  }[pageId] || "";
}

function updateSidebar(pageId) {
  document.querySelectorAll("[data-nav]").forEach((item) => {
    item.classList.toggle("active", item.dataset.nav === pageId);
  });
  if (pageId === "project-detail" || pageId.startsWith("project-")) {
    document.querySelector('[data-nav="projects"]')?.classList.add("active");
  }
  if (typeof renderProjectTree === "function") renderProjectTree();
}

function updateTopbar(pageId) {
  const bc = document.getElementById("breadcrumb");
  if (!bc) return;
  const page = PAGES[pageId];
  if (pageId === "project-detail") {
    const project = findById(NEXUS.projects, NEXUS.state.currentProjectId) || NEXUS.projects[0];
    bc.innerHTML = `<span class="breadcrumb-link" onclick="navigate('projects')">Projects</span><span class="breadcrumb-sep">/</span><span class="breadcrumb-current">${escapeHtml(project.name)}</span>`;
    return;
  }
  if (pageId.startsWith("project-")) {
    const project = findById(NEXUS.projects, NEXUS.state.currentProjectId) || NEXUS.projects[0];
    const resourceTitle = projectResourceTitle(pageId);
    bc.innerHTML = `<span class="breadcrumb-link" onclick="navigate('projects')">Projects</span><span class="breadcrumb-sep">/</span><span class="breadcrumb-link" onclick="openProject('${project.id}')">${escapeHtml(project.name)}</span><span class="breadcrumb-sep">/</span><span class="breadcrumb-current">${escapeHtml(resourceTitle)}</span>`;
    return;
  }
  bc.innerHTML = `<span class="breadcrumb-current">${escapeHtml(page?.title || "Nexus Agents")}</span>`;
}
