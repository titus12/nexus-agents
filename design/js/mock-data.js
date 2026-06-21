window.NEXUS = {
  state: {
    currentPage: "projects",
    currentProjectId: "btd-game-server",
    selectedNodeId: "node-plan",
    currentWorkflowId: "btd-review-flow",
    workflowEditorMode: false,
    workflowRunState: "idle",
  },
  projects: [
    {
      id: "btd-game-server",
      name: "btd-game-server",
      path: "D:\\workspace\\src\\btd-game-server",
      description: "多人协作 Go 游戏服务器项目，已具备 Claude/Codex 双工具配置。",
      status: "ready",
      updatedAt: "2026-06-18 15:47",
      agents: 12,
      rules: 4,
      skills: 12,
      workflows: 3,
      sources: [
        { name: ".claude/agents", type: "Claude source", count: 12, state: "ok" },
        { name: ".claude/rules", type: "shared rules", count: 4, state: "ok" },
        { name: ".claude/skills", type: "shared skills", count: 12, state: "ok" },
        { name: ".codex/agents", type: "Codex projection", count: 12, state: "ok" },
        { name: ".mcp.json", type: "MCP servers", count: 1, state: "ok" },
        { name: ".proxy", type: "LiteLLM proxy", count: 1, state: "warn" },
      ],
      health: [
        { label: "Claude Markdown semantic source", state: "ok", note: "12 roles discovered" },
        { label: "Codex TOML projection", state: "ok", note: "models mapped to gpt-5.5 / 5.4 / mini" },
        { label: "LiteLLM Responses tool patch", state: "ok", note: "unknown tool types dropped before Winky" },
        { label: "Proxy env", state: "warn", note: "DEEPSEEK_API_KEY is runtime-only" },
      ],
      syncDiffs: [
        {
          file: "templates/workflows/go-code-review.md",
          status: "create",
          diff: "+ id: main-review\n+ nodes:\n+   - agent: reviewer-logic\n+   - parallel: [reviewer-perf, reviewer-security]",
        },
        {
          file: ".codex/agents/worker.toml",
          status: "update",
          diff: "- model = \"gpt-5.4\"\n+ model = \"gpt-5.4\"\n+ model_reasoning_effort = \"high\"",
        },
      ],
    },
    {
      id: "nexus-agents",
      name: "nexus-agents",
      path: "D:\\workspace\\src\\nexus-agents",
      description: "当前管理服务原型仓库。",
      status: "draft",
      updatedAt: "2026-06-19 10:12",
      agents: 0,
      rules: 0,
      skills: 0,
      workflows: 1,
      sources: [],
      health: [],
      syncDiffs: [],
    },
  ],
  agents: [
    { id: "sisyphus", name: "sisyphus", role: "主编排", model: "gpt-5.5", route: "codex passthrough", capability: "需求拆解 / 多角色协调", rules: 4, skills: 2, tools: "codegraph, git", mcp: "codegraph", source: ".claude/agents/sisyphus.md", projection: ".codex/agents/sisyphus.toml" },
    { id: "prometheus", name: "prometheus", role: "规划", model: "gpt-5.5", route: "codex passthrough", capability: "方案设计 / 取舍分析", rules: 4, skills: 3, tools: "rg, codegraph", mcp: "codegraph", source: ".claude/agents/prometheus.md", projection: ".codex/agents/prometheus.toml" },
    { id: "hephaestus", name: "hephaestus", role: "实现", model: "gpt-5.4", route: "Winky deepseek-v4-pro", capability: "多文件实现 / 重构", rules: 4, skills: 6, tools: "go, git, rg", mcp: "codegraph", source: ".claude/agents/hephaestus.md", projection: ".codex/agents/hephaestus.toml" },
    { id: "worker", name: "worker", role: "执行", model: "gpt-5.4", route: "Winky deepseek-v4-pro", capability: "明确子任务执行", rules: 4, skills: 5, tools: "go, rg", mcp: "codegraph", source: ".claude/agents/worker.md", projection: ".codex/agents/worker.toml" },
    { id: "quick", name: "quick", role: "快改", model: "gpt-5.4-mini", route: "Winky deepseek-v4-flash", capability: "单文件小改", rules: 4, skills: 2, tools: "rg", mcp: "-", source: ".claude/agents/quick.md", projection: ".codex/agents/quick.toml" },
    { id: "oracle", name: "oracle", role: "调试", model: "gpt-5.5", route: "codex passthrough", capability: "根因分析 / 调用链追踪", rules: 4, skills: 4, tools: "codegraph, logs", mcp: "codegraph", source: ".claude/agents/oracle.md", projection: ".codex/agents/oracle.toml" },
    { id: "debugger", name: "debugger", role: "排查", model: "gpt-5.4-mini", route: "Winky deepseek-v4-flash", capability: "日志优先定位", rules: 4, skills: 3, tools: "rg, git", mcp: "-", source: ".claude/agents/debugger.md", projection: ".codex/agents/debugger.toml" },
    { id: "reviewer-logic", name: "reviewer-logic", role: "逻辑审查", model: "gpt-5.5", route: "codex passthrough", capability: "边界 / 事务 / 并发", rules: 4, skills: 4, tools: "codegraph", mcp: "codegraph", source: ".claude/agents/reviewer-logic.md", projection: ".codex/agents/reviewer-logic.toml" },
    { id: "reviewer-perf", name: "reviewer-perf", role: "性能审查", model: "gpt-5.4", route: "Winky deepseek-v4-pro", capability: "热点 / 内存 / 循环", rules: 4, skills: 4, tools: "go test, rg", mcp: "codegraph", source: ".claude/agents/reviewer-perf.md", projection: ".codex/agents/reviewer-perf.toml" },
    { id: "reviewer-security", name: "reviewer-security", role: "安全审查", model: "gpt-5.5", route: "codex passthrough", capability: "权限 / 输入 / 经济风险", rules: 4, skills: 4, tools: "rg", mcp: "codegraph", source: ".claude/agents/reviewer-security.md", projection: ".codex/agents/reviewer-security.toml" },
    { id: "librarian", name: "librarian", role: "文档", model: "gpt-5.4-mini", route: "Winky deepseek-v4-flash", capability: "API / 文档查询", rules: 4, skills: 2, tools: "web, docs", mcp: "-", source: ".claude/agents/librarian.md", projection: ".codex/agents/librarian.toml" },
    { id: "gatekeeper", name: "gatekeeper", role: "门禁", model: "gpt-5.4-mini", route: "Winky deepseek-v4-flash", capability: "提交前风险扫描", rules: 4, skills: 3, tools: "git diff", mcp: "-", source: ".claude/agents/gatekeeper.md", projection: ".codex/agents/gatekeeper.toml" },
  ],
  rules: [
    { id: "00-routing", name: "00-routing", project: "all", source: ".claude/rules/00-routing.md", scope: "global", status: "active", summary: "按 --feat / --mod / --bug / --rev 等前缀激活工作流。", projection: "Codex reads directly", content: "# 00-routing\n\n- --feat 进入需求拆解和方案确认流程。\n- --bug 先复现、定位根因，再给修复建议。\n- --rev 进入多 reviewer 并行审查流程。\n- 未命中前缀时，由主编排 agent 判断是否需要追问。" },
    { id: "01-communication", name: "01-communication", project: "all", source: ".claude/rules/01-communication.md", scope: "global", status: "active", summary: "中文回复、先澄清再执行、输出保持紧凑。", projection: "Codex reads directly", content: "# 01-communication\n\n- 默认用中文沟通，命令、路径、代码保持原文。\n- 需求不清楚时先澄清，能从仓库发现的信息先自己查。\n- 输出以结论和下一步为主，不重复无关背景。\n- 对风险、失败和未验证内容明确说明。" },
    { id: "02-safety", name: "02-safety", project: "btd-game-server", source: ".claude/rules/02-safety.md", scope: "project", status: "active", summary: "禁止破坏性操作、构建验证、保护用户改动。", projection: "Codex reads directly", content: "# 02-safety\n\n- 不执行 git reset --hard、强制删除或覆盖用户改动。\n- 修改前检查工作区状态，遇到同文件外部改动先读再改。\n- 涉及构建、生成、批量替换时保留可验证步骤。\n- 提交前说明已运行的验证命令和结果。" },
    { id: "03-project-model", name: "03-project-model", project: "btd-game-server", source: ".claude/rules/03-project-model.md", scope: "project", status: "active", summary: "Actor 模型、事务、Manager Singleton、配置读取规范。", projection: "Codex reads directly", content: "# 03-project-model\n\n- Actor 只通过消息和服务接口协作，不跨层直接修改状态。\n- Manager 负责生命周期和索引，不承载业务规则。\n- 事务边界要明确，失败路径必须可回滚或可重试。\n- 配置读取优先走项目既有 loader，避免散落解析逻辑。" },
  ],
  skills: [
    { id: "coding-rules", name: "coding-rules", project: "btd-game-server", source: ".claude/skills/coding-rules.md", module: "Go coding", trigger: "实现和重构", appliesTo: "hephaestus, worker", purpose: "约束 Go 服务端实现、重构和项目惯例。", status: "ready", content: "# coding-rules\n\n- 优先复用项目内已有 manager、service、loader 模式。\n- 多文件改动先确认调用链，再分层落地。\n- 新增导出类型要说明使用边界。\n- 避免为了原型引入新的全局状态。" },
    { id: "testing", name: "testing", project: "all", source: ".claude/skills/testing.md", module: "verification", trigger: "测试和构建", appliesTo: "gatekeeper, reviewer-*", purpose: "定义测试、构建和提交前验证动作。", status: "ready", content: "# testing\n\n- 小改动至少运行相关静态检查或目标测试。\n- 高风险逻辑需要补回归用例。\n- 无法运行验证时记录原因和剩余风险。\n- 测试输出要以命令和结果为准。" },
    { id: "dev-workflow", name: "dev-workflow", project: "all", source: ".claude/skills/dev-workflow.md", module: "workflow", trigger: "--feat", appliesTo: "sisyphus, prometheus", purpose: "把功能需求拆成计划、角色分工和确认点。", status: "ready", content: "# dev-workflow\n\n- 先识别目标、范围、验收标准。\n- 拆分设计、实现、验证三个阶段。\n- 多 agent 任务要明确输入、输出和交接物。\n- 方案确认后再进入实现。" },
    { id: "review-feedback", name: "review-feedback", project: "all", source: ".claude/skills/review-feedback.md", module: "review", trigger: "收到审查反馈", appliesTo: "reviewer-logic, gatekeeper", purpose: "处理代码审查反馈，区分必须修复和可讨论项。", status: "ready", content: "# review-feedback\n\n- 先复述反馈指向的具体代码行为。\n- 能验证的先验证，不盲目接受或拒绝。\n- 修复时保持补丁最小。\n- 结束时列出已处理和仍需确认的反馈。" },
    { id: "high-risk-api", name: "high-risk-api", project: "btd-game-server", source: ".claude/skills/high-risk-api.md", module: "safety", trigger: "高风险 API", appliesTo: "reviewer-security, gatekeeper", purpose: "审查支付、权限、库存和经济系统接口。", status: "ready", content: "# high-risk-api\n\n- 优先检查鉴权、幂等、重放和参数信任边界。\n- 涉及经济资源时必须说明扣减和补偿路径。\n- 对外接口要检查错误码和日志是否足够定位。\n- 风险结论要给可执行修复建议。" },
    { id: "cross-gate", name: "cross-gate", project: "btd-game-server", source: ".claude/skills/cross-gate.md", module: "gateway", trigger: "网关 / 鉴权", appliesTo: "reviewer-security, oracle", purpose: "处理网关、鉴权和跨服入口问题。", status: "ready", content: "# cross-gate\n\n- 先确认请求入口、用户态和服务态身份。\n- 区分客户端参数和服务端可信上下文。\n- 检查跨服转发是否保留 trace 和错误上下文。\n- 修改鉴权逻辑必须覆盖拒绝路径。" },
    { id: "cross-social", name: "cross-social", project: "btd-game-server", source: ".claude/skills/cross-social.md", module: "social", trigger: "好友 / 聊天", appliesTo: "worker, reviewer-logic", purpose: "沉淀社交模块的好友、聊天、关系链处理方式。", status: "ready", content: "# cross-social\n\n- 好友关系变更要考虑双方视图和缓存刷新。\n- 聊天消息处理要区分持久化、推送和敏感词流程。\n- 跨服关系链以服务端状态为准。\n- 审查时重点看并发和重复请求。" },
    { id: "cross-config", name: "cross-config", project: "btd-game-server", source: ".claude/skills/cross-config.md", module: "config", trigger: "proto / xbean", appliesTo: "hephaestus, librarian", purpose: "指导 proto、xbean 和配置生成链路。", status: "ready", content: "# cross-config\n\n- 先找现有配置入口和生成命令。\n- 字段新增要确认客户端、服务器和配置表同步关系。\n- 读取配置时使用统一 loader，不手写散落解析。\n- 变更后检查默认值和缺省兼容。" },
    { id: "cross-client", name: "cross-client", project: "btd-game-server", source: ".claude/skills/cross-client.md", module: "client", trigger: "Unity / 前端", appliesTo: "librarian, reviewer-logic", purpose: "记录服务端与 Unity/前端协议协作注意事项。", status: "ready", content: "# cross-client\n\n- 协议字段变更要标明客户端兼容策略。\n- 返回结构保持稳定，新增字段优先向后兼容。\n- 错误码要能让客户端区分重试、提示和静默失败。\n- 涉及 UI 展示的值要确认本地化来源。" },
    { id: "quest-system", name: "quest-system", project: "btd-game-server", source: ".claude/skills/quest-system.md", module: "quest", trigger: "任务 / 成就", appliesTo: "worker, reviewer-perf", purpose: "处理任务、成就、进度和奖励发放逻辑。", status: "ready", content: "# quest-system\n\n- 任务进度更新要考虑重复上报和乱序事件。\n- 奖励发放要和完成状态写入保持一致。\n- 批量扫描任务时关注复杂度和缓存。\n- 审查时重点看补偿路径和边界条件。" },
    { id: "pmconf-pattern", name: "pmconf-pattern", project: "btd-game-server", source: ".claude/skills/pmconf-pattern.md", module: "config", trigger: "pmconf", appliesTo: "hephaestus, oracle", purpose: "记录 pmconf 配置读取、校验和发布模式。", status: "ready", content: "# pmconf-pattern\n\n- 读取 pmconf 前先确认环境和发布版本。\n- 配置 key 必须有默认值或缺失处理。\n- 热更新路径要考虑旧连接和缓存刷新。\n- 排查时记录配置来源、版本和命中结果。" },
    { id: "skill-standard", name: "skill-standard", project: "all", source: ".claude/skills/skill-standard.md", module: "meta", trigger: "创建 skill", appliesTo: "sisyphus, librarian", purpose: "定义新增或提升 Skill 的写作标准。", status: "ready", content: "# skill-standard\n\n- Skill 必须说明适用场景、触发词和不适用场景。\n- 内容优先写可执行步骤，不堆背景说明。\n- 引用项目文件时使用稳定路径。\n- 升级为全局 Skill 前先在项目内验证。" },
  ],
  modelRoutes: [
    { id: "codex-gpt-55", client: "Codex Responses", source: "gpt-5.5", target: "ChatGPT Codex backend", provider: "official passthrough", endpoint: "https://chatgpt.com/backend-api/codex/responses", auth: "Codex bearer", status: "ready" },
    { id: "codex-gpt-54", client: "Codex Responses", source: "gpt-5.4", target: "deepseek-v4-pro", provider: "Winky DeepSeek", endpoint: "https://lumos.diandian.info/winky/deepseek/v1", auth: "DEEPSEEK_API_KEY", status: "ready" },
    { id: "codex-gpt-54-mini", client: "Codex Responses", source: "gpt-5.4-mini", target: "deepseek-v4-flash", provider: "Winky DeepSeek", endpoint: "https://lumos.diandian.info/winky/deepseek/v1", auth: "DEEPSEEK_API_KEY", status: "ready" },
    { id: "claude-native", client: "Claude Messages", source: "claude-*", target: "Winky Claude", provider: "Winky Claude", endpoint: "https://lumos.diandian.info/winky/claude/v1/messages", auth: "x-api-key", status: "ready" },
    { id: "claude-deepseek", client: "Claude Messages", source: "deepseek-*", target: "Winky DeepSeek", provider: "Winky DeepSeek", endpoint: "https://lumos.diandian.info/winky/deepseek/v1/messages", auth: "Bearer", status: "ready" },
  ],
  workflows: [
    { id: "btd-review-flow", name: "btd-game-server review workflow", project: "btd-game-server", status: "ready", updatedAt: "2026-06-19 09:21", owner: "prometheus", nodeCount: 10, edgeCount: 11, runCount: 18, trigger: "pull request / --rev", tags: ["parallel", "review"], description: "并行编排 logic、performance、security 三类 reviewer，再由 join 节点合并结论。" },
    { id: "feature-plan", name: "feature plan workflow", project: "btd-game-server", status: "draft", updatedAt: "2026-06-18 18:42", owner: "sisyphus", nodeCount: 6, edgeCount: 5, runCount: 7, trigger: "--feat", tags: ["sequence", "planning"], description: "从需求澄清到任务拆解，再输出 agents 分工和待确认实现计划。" },
    { id: "bug-triage", name: "bug triage workflow", project: "btd-game-server", status: "paused", updatedAt: "2026-06-18 14:08", owner: "oracle", nodeCount: 5, edgeCount: 4, runCount: 11, trigger: "--bug", tags: ["debug", "logs"], description: "日志优先定位问题，按根因、影响面、验证项输出修复建议。" },
  ],
  workflow: {
    id: "btd-review-flow",
    name: "btd-game-server review workflow",
    nodes: [
      { id: "node-input", type: "input", label: "User Request", x: 36, y: 220, status: "done", agent: "-", detail: "接收需求、diff、上下文。" },
      { id: "node-plan", type: "agent", label: "prometheus", x: 260, y: 110, status: "idle", agent: "prometheus", detail: "输出计划和任务拆解。" },
      { id: "node-condition", type: "condition", label: "review gate", x: 500, y: 70, status: "idle", agent: "-", detail: "判断是否需要多 reviewer 并行审查。" },
      { id: "node-parallel", type: "parallel", label: "parallel reviewers", x: 500, y: 210, status: "idle", agent: "reviewer-*", detail: "并行派发 logic / perf / security。" },
      { id: "node-logic", type: "agent", label: "reviewer-logic", x: 740, y: 70, status: "idle", agent: "reviewer-logic", detail: "检查逻辑、边界、事务。" },
      { id: "node-perf", type: "agent", label: "reviewer-perf", x: 740, y: 210, status: "idle", agent: "reviewer-perf", detail: "检查热点、循环、内存。" },
      { id: "node-security", type: "agent", label: "reviewer-security", x: 740, y: 350, status: "idle", agent: "reviewer-security", detail: "检查权限、输入、经济风险。" },
      { id: "node-join", type: "join", label: "merge findings", x: 980, y: 210, status: "idle", agent: "-", detail: "去重、排序、合并审查结论。" },
      { id: "node-guard", type: "decorator", label: "approval guard", x: 1238, y: 120, status: "idle", agent: "-", detail: "设置 timeout、retry 和风险阈值。" },
      { id: "node-human", type: "human_approval", label: "human approval", x: 1238, y: 300, status: "idle", agent: "owner", detail: "等待用户确认后同步或执行。" },
    ],
    edges: [
      { from: "node-input", to: "node-plan", label: "request" },
      { from: "node-plan", to: "node-condition", label: "plan" },
      { from: "node-condition", to: "node-parallel", label: "yes" },
      { from: "node-parallel", to: "node-logic", label: "diff" },
      { from: "node-parallel", to: "node-perf", label: "diff" },
      { from: "node-parallel", to: "node-security", label: "diff" },
      { from: "node-logic", to: "node-join", label: "findings[]" },
      { from: "node-perf", to: "node-join", label: "findings[]" },
      { from: "node-security", to: "node-join", label: "findings[]" },
      { from: "node-join", to: "node-guard", label: "report.md" },
      { from: "node-guard", to: "node-human", label: "approved?" },
    ],
  },
  runs: [
    { id: "run_20260619_001", workflow: "btd-review-flow", project: "btd-game-server", status: "completed", modelCost: "mixed", startedAt: "2026-06-19 09:21", duration: "2m 18s", summary: "3 reviewers completed, 5 findings merged." },
    { id: "run_20260618_014", workflow: "feature-plan", project: "btd-game-server", status: "waiting", modelCost: "gpt-5.5", startedAt: "2026-06-18 18:42", duration: "paused", summary: "Waiting for human approval before sync." },
    { id: "run_20260618_009", workflow: "bug-triage", project: "btd-game-server", status: "failed", modelCost: "mini + pro", startedAt: "2026-06-18 14:08", duration: "52s", summary: "Proxy route test failed: missing runtime key." },
  ],
};

(function enrichTemplateLibraryMockData() {
  const versions = {
    agents: { default: 4 },
    rules: { default: 3 },
    skills: { default: 5 },
    workflows: { default: 2 },
  };

  NEXUS.agents.forEach((agent, index) => {
    agent.templateId = agent.id;
    agent.kind = "agent";
    agent.version = versions.agents.default + (index % 3);
    agent.updatedAt = index % 2 === 0 ? "2026-06-18 15:47" : "2026-06-17 18:30";
    agent.slug = agent.id;
    agent.files = [agent.source || `.claude/agents/${agent.id}.md`, agent.projection || `.codex/agents/${agent.id}.toml`];
  });

  NEXUS.rules.forEach((rule, index) => {
    rule.templateId = rule.id;
    rule.kind = "rule";
    rule.version = versions.rules.default + index;
    rule.updatedAt = index % 2 === 0 ? "2026-06-18 12:20" : "2026-06-16 21:10";
    rule.slug = rule.id;
    rule.entry = rule.source || `.claude/rules/${rule.id}.md`;
    rule.files = [rule.entry];
  });

  NEXUS.skills.forEach((skill, index) => {
    skill.templateId = skill.id;
    skill.kind = "skill";
    skill.version = versions.skills.default + (index % 4);
    skill.updatedAt = index % 2 === 0 ? "2026-06-18 14:18" : "2026-06-15 19:42";
    skill.slug = skill.id;
    skill.entry = skill.source || `templates/skills/${skill.id}.md`;
    skill.files = [skill.entry];
  });

  NEXUS.workflows.forEach((workflow, index) => {
    workflow.templateId = workflow.id;
    workflow.kind = "workflow";
    workflow.version = versions.workflows.default + index;
    workflow.updatedAt = workflow.updatedAt || "2026-06-18 10:00";
    workflow.slug = workflow.id;
    workflow.entry = workflow.source || `templates/workflows/${workflow.id}.md`;
    workflow.files = [workflow.entry];
  });

  NEXUS.projectConfigSets = {
    "btd-game-server": [
      {
        id: "proj_agent_btd_worker",
        kind: "agent",
        name: "worker",
        origin: { templateId: "worker", baseVersion: 5, baseHash: "sha256:9f0b-worker" },
        localVersion: 2,
        syncMode: "manual",
        status: "template_updated",
        path: ".claude/agents/worker.md",
        diff: "- model_reasoning_effort = \"medium\"\n+ model_reasoning_effort = \"high\"",
      },
      {
        id: "proj_rule_btd_project_model",
        kind: "rule",
        name: "03-project-model",
        origin: { templateId: "03-project-model", baseVersion: 6, baseHash: "sha256:71a4-project-model" },
        localVersion: 3,
        syncMode: "manual",
        status: "project_modified",
        path: ".claude/rules/03-project-model.md",
        diff: "- Manager handles lifecycle.\n+ Manager handles lifecycle and actor index rebuild.",
      },
      {
        id: "proj_skill_btd_testing",
        kind: "skill",
        name: "testing",
        origin: { templateId: "testing", baseVersion: 6, baseHash: "sha256:41d2-testing" },
        localVersion: 1,
        syncMode: "manual",
        status: "synced",
        path: "templates/skills/go-testing.md",
        diff: "No content changes.",
      },
      {
        id: "proj_skill_btd_high_risk_api",
        kind: "skill",
        name: "high-risk-api",
        origin: { templateId: "high-risk-api", baseVersion: 5, baseHash: "sha256:6b80-risk-api" },
        localVersion: 4,
        syncMode: "manual",
        status: "diverged",
        path: "templates/skills/high-risk-api.md",
        diff: "- template: add replay audit checklist\n+ project: add payment rollback checklist",
      },
      {
        id: "proj_workflow_btd_review",
        kind: "workflow",
        name: "btd-game-server review workflow",
        origin: { templateId: "btd-review-flow", baseVersion: 2, baseHash: "sha256:ad77-review-flow" },
        localVersion: 2,
        syncMode: "manual",
        status: "detached",
        path: "templates/workflows/go-code-review.md",
        diff: "Detached after project-specific reviewer routing changes.",
      },
    ],
    "nexus-agents": [
      {
        id: "proj_workflow_nexus_design",
        kind: "workflow",
        name: "prototype design workflow",
        origin: null,
        localVersion: 1,
        syncMode: "manual",
        status: "detached",
        path: "templates/workflows/design.md",
        diff: "Project-only workflow. No template origin.",
      },
    ],
  };

  const btd = NEXUS.projects.find((project) => project.id === "btd-game-server");
  if (btd) {
    btd.syncDiffs = NEXUS.projectConfigSets["btd-game-server"];
  }
})();
