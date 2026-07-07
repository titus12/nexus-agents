# btd-client OKF 知识库优先改造实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 先把 `D:\workspace\src\btd-client` 作为 OKF 知识库试点项目，完成知识库结构化、工作流轻改、校验/展示/维护能力验证，再把最有价值的能力沉淀回 `nexus-agents`。

**架构：** 第一阶段不先做泛化平台，而是直接改造 btd-client 已有的 `design/KnowledgeBase`，补齐 OKF frontmatter、`index.md`、`log.md`、routing 和维护规则。第二阶段只轻改 btd-client workflow，让工作流先读 `design/KnowledgeBase/project/routing.md`，再按领域读取必要知识。第三阶段把试点中确认为有用的能力做进 Nexus Agents：OKF 校验、Kiso 风格文档展示、route preview、maintenance report、workflow eval 的 Loaded Knowledge 检查。

**技术栈：** Markdown + YAML frontmatter、btd-client 现有 `AGENTS.md` / `.claude` / `.agents` / `design/KnowledgeBase`、Go 1.22、Vue 3 + TypeScript、Nexus Agents 现有项目导入/扫描/工作流/eval 能力。

---

## 总体原则

1. **先改 btd-client，不先做大平台。**
2. **不动 Unity 业务代码，不动 prefab，不动 scene，不动 `.meta`。**
3. **不重写现有知识，只补结构、入口、frontmatter、路由和维护规则。**
4. **不把 QAnything / RAG 做进主链路。**
5. **OKF 是 source of truth；Nexus 只做校验、展示、路由预览和维护报告。**
6. **工作流只轻改，加 Knowledge Loading，不重写 workflow。**
7. **维护默认只出报告和建议 patch，不自动改硬规则。**

---

## 我会做哪些事情

### 第一部分：先改造 btd-client 项目知识库

我会在 `D:\workspace\src\btd-client` 做这些：

1. 盘点现有知识库：
   - `D:\workspace\src\btd-client\AGENTS.md`
   - `D:\workspace\src\btd-client\CLAUDE.md`
   - `D:\workspace\src\btd-client\.claude\workflows`
   - `D:\workspace\src\btd-client\.agents\skills`
   - `D:\workspace\src\btd-client\design\KnowledgeBase`
   - `D:\workspace\src\btd-client\design\UIArchitect`
   - `D:\workspace\src\btd-client\design\UIDevelop`

2. 保留现有 `design/KnowledgeBase` 目录，不另起一套知识库。

3. 给现有 Markdown 文件补 OKF frontmatter：
   - `type`
   - `title`
   - `description`
   - `resource`
   - `tags`
   - `timestamp`

4. 补齐 OKF 根文件：
   - `design/KnowledgeBase/index.md`
   - `design/KnowledgeBase/log.md`

5. 标准化路由文件：
   - `design/KnowledgeBase/project/routing.md`
   - `design/KnowledgeBase/domains/ui/routing.md`
   - `design/KnowledgeBase/domains/uiarchitect/routing.md`

6. 补充 workflow 知识索引：
   - `design/KnowledgeBase/workflows/index.md`
   - `design/KnowledgeBase/workflows/unity-ui-feature.md`
   - `design/KnowledgeBase/workflows/unity-ui-quick.md`
   - `design/KnowledgeBase/workflows/unity-bugfix.md`
   - `design/KnowledgeBase/workflows/unity-logic-mod.md`

7. 增加知识库维护规则：
   - `design/KnowledgeBase/schema/maintenance_rules.md`
   - `design/KnowledgeBase/schema/sync_checklist.md`

8. 检查并修复知识库内部 Markdown 链接。

9. 不删除现有知识文件；如需重命名，会先保留兼容链接或在 routing 中说明新旧路径。

### 第二部分：轻改 btd-client 工作流

我会在 btd-client 的工作流里加一小段 `Knowledge Loading`：

1. `D:\workspace\src\btd-client\.claude\workflows\unity-ui-feature-development.md`
2. `D:\workspace\src\btd-client\.claude\workflows\unity-ui-quick.md`
3. `D:\workspace\src\btd-client\.claude\workflows\unity-bug-investigation.md`
4. `D:\workspace\src\btd-client\.claude\workflows\unity-logic-modification.md`

新增内容大概是：

```markdown
## Knowledge Loading

Before implementation:

1. Read `design/KnowledgeBase/project/routing.md` when present.
2. Select the relevant domain based on the task.
3. Read the selected domain `routing.md`.
4. Read only the required knowledge files listed by routing.
5. Include a `Loaded Knowledge` section in the final summary.

If `design/KnowledgeBase` is missing or incomplete, continue with existing workflow rules and report the missing knowledge files.
```

UI feature workflow 会更具体：

```markdown
## Knowledge Loading

Before Unity UI implementation:

1. Read `design/KnowledgeBase/project/routing.md`.
2. Read `design/KnowledgeBase/domains/ui/routing.md`.
3. For new UI features, read UI coding rules, data-flow rules, and MVVM templates listed by routing.
4. For quick fixes, do not read large templates unless creating or reshaping View/ViewModel structure.
5. Include a `Loaded Knowledge` section in the final summary.
```

### 第三部分：把试点沉淀到 nexus-agents

在 btd-client 试点跑通后，我会把有价值的部分做进 `D:\workspace\src\nexus-agents`：

1. `internal/knowledgebase` 后端包：
   - 扫描 `design/KnowledgeBase`
   - 解析 OKF frontmatter
   - 校验必填字段
   - 检查 `index.md` / `log.md`
   - 检查 Markdown 断链
   - 检查过期文档
   - 检查超大文档
   - 检查重复硬规则

2. btd-client profile：
   - 只为 Unity client 试点做 profile
   - 暂不优先做 `go-game-server`
   - 等 btd-client 试点稳定后再扩展 btd-game-server

3. Kiso 风格展示：
   - 文档树
   - frontmatter 信息
   - Markdown 基础渲染
   - 点击文档查看内容
   - 不做完整 CMS 编辑器

4. Route Preview：
   - 输入任务描述
   - Nexus 告诉你应该读哪些知识文件
   - 例如“新增活动奖励弹窗 UI”会路由到：
     - `design/KnowledgeBase/project/routing.md`
     - `design/KnowledgeBase/domains/ui/routing.md`
     - `design/KnowledgeBase/domains/ui/coding_rules.md`
     - `design/KnowledgeBase/domains/ui/data_flow_rules.md`
     - 必要时 `templates/mvvm-view.md`

5. Maintenance Report：
   - 缺 frontmatter
   - 缺字段
   - 断链
   - 超大文件
   - 过期文件
   - 重复硬规则
   - 建议拆分或同步的文件

6. Workflow Eval：
   - 检查最终输出是否包含：

```markdown
## Loaded Knowledge

- `design/KnowledgeBase/project/routing.md`
- `design/KnowledgeBase/domains/ui/routing.md`
```

   - 初期只作为 warning，不作为硬失败。

---

## 具体执行步骤

## Task 1：盘点 btd-client 现有知识库

**文件：**
- 读取：`D:\workspace\src\btd-client\AGENTS.md`
- 读取：`D:\workspace\src\btd-client\CLAUDE.md`
- 读取：`D:\workspace\src\btd-client\design\KnowledgeBase`
- 输出：`D:\workspace\src\nexus-agents\docs\superpowers\plans\btd-client-okf-inventory.md`

- [ ] **Step 1：列出 btd-client 知识文件**

运行：

```powershell
Get-ChildItem -Recurse -File D:\workspace\src\btd-client\design\KnowledgeBase |
  Select-Object FullName,Length |
  Format-Table -AutoSize
```

预期：拿到现有 KnowledgeBase 文件列表。

- [ ] **Step 2：列出 btd-client workflow 和 skill**

运行：

```powershell
Get-ChildItem -Recurse -File D:\workspace\src\btd-client\.claude\workflows,D:\workspace\src\btd-client\.agents\skills |
  Select-Object FullName,Length |
  Format-Table -AutoSize
```

预期：拿到 workflow / skill 入口。

- [ ] **Step 3：写盘点文档**

创建：

`D:\workspace\src\nexus-agents\docs\superpowers\plans\btd-client-okf-inventory.md`

内容包括：

```markdown
# btd-client OKF 知识库盘点

## 现有入口

- AGENTS.md
- CLAUDE.md
- .claude/workflows
- .agents/skills
- design/KnowledgeBase

## 现有 KnowledgeBase 文件

列出路径、用途、大小、是否已有 frontmatter。

## 初步问题

- 缺 OKF frontmatter 的文件
- 缺 index.md / log.md 的目录
- routing 是否清楚
- 是否存在超大模板
- 是否存在重复规则

## 改造建议

- 保留哪些
- 补哪些
- 暂不动哪些
```

- [ ] **Step 4：提交盘点文档**

```powershell
git add docs/superpowers/plans/btd-client-okf-inventory.md
git commit -m "docs: inventory btd-client knowledge base"
```

---

## Task 2：给 btd-client KnowledgeBase 补 OKF 根结构

**文件：**
- 创建或修改：`D:\workspace\src\btd-client\design\KnowledgeBase\index.md`
- 创建或修改：`D:\workspace\src\btd-client\design\KnowledgeBase\log.md`
- 创建或修改：`D:\workspace\src\btd-client\design\KnowledgeBase\project\routing.md`

- [ ] **Step 1：新增或更新根 index**

目标内容：

```markdown
---
type: Index
title: btd-client Knowledge Base
description: OKF-compatible knowledge entry for btd-client Unity development.
resource: design/KnowledgeBase/index.md
tags: [btd-client, unity, knowledge-base, okf]
timestamp: 2026-07-07T00:00:00+08:00
---

# btd-client Knowledge Base

Start with [project routing](./project/routing.md), then read only the domain files required by the active workflow.

## Domains

- [UI](./domains/ui/README.md)
- [UIArchitect](./domains/uiarchitect/README.md)
- [Gameplay](./domains/gameplay/README.md)
- [Network](./domains/network/README.md)
- [Behaviour Tree](./domains/behaviour_tree/README.md)

## Workflow Knowledge

- [Workflow Index](./workflows/index.md)

## Maintenance

- [Maintenance Rules](./schema/maintenance_rules.md)
- [Sync Checklist](./schema/sync_checklist.md)
```

- [ ] **Step 2：新增或更新 log**

目标内容：

```markdown
---
type: Log
title: btd-client Knowledge Change Log
description: Human-reviewed changes to btd-client project knowledge.
resource: design/KnowledgeBase/log.md
tags: [btd-client, knowledge-base, log]
timestamp: 2026-07-07T00:00:00+08:00
---

# Knowledge Change Log

- 2026-07-07: Initialized OKF-compatible knowledge entry and routing.
```

- [ ] **Step 3：新增或更新 project/routing.md**

目标内容应包含这些路由：

```markdown
---
type: Routing
title: btd-client Project Knowledge Routing
description: Routes Unity client tasks to minimum required knowledge files.
resource: design/KnowledgeBase/project/routing.md
tags: [btd-client, routing, unity, workflow]
timestamp: 2026-07-07T00:00:00+08:00
---

# Project Routing

## New UI feature

Read:

1. `domains/ui/routing.md`
2. `domains/ui/coding_rules.md`
3. `domains/ui/data_flow_rules.md`
4. `domains/ui/development_workflow.md`

Read templates only when creating or reshaping View/ViewModel structure:

1. `domains/ui/templates/mvvm-view.md`
2. `domains/ui/templates/mvvm-viewmodel.md`

## UI quick fix

Read:

1. `domains/ui/routing.md`
2. `domains/ui/coding_rules.md`
3. `domains/ui/examples/anti_patterns.md`

Do not read large templates unless the fix requires new View/ViewModel structure.

## UIArchitect or PSD import

Read:

1. `domains/uiarchitect/routing.md`
2. `../UIArchitect/PSD_Import_Design.md`
3. `../UIArchitect/PSD_NameComponent_Design.md`

## Data layer, Cache, Service, or DataEvents

Read:

1. `domains/ui/routing.md`
2. `domains/ui/data_flow_rules.md`
3. `../UIDevelop/DataLayer_Design.md`

## Unity bug investigation

Read:

1. `domains/ui/routing.md` when UI is involved.
2. `schema/sync_checklist.md`

Use Unity MCP for console, prefab, scene, or generated binding verification when available.
```

- [ ] **Step 4：检查文件存在**

运行：

```powershell
Test-Path D:\workspace\src\btd-client\design\KnowledgeBase\index.md
Test-Path D:\workspace\src\btd-client\design\KnowledgeBase\log.md
Test-Path D:\workspace\src\btd-client\design\KnowledgeBase\project\routing.md
```

预期：全部输出 `True`。

---

## Task 3：给 btd-client 现有知识文件补 OKF frontmatter

**文件：**
- 修改：`D:\workspace\src\btd-client\design\KnowledgeBase\domains\ui\*.md`
- 修改：`D:\workspace\src\btd-client\design\KnowledgeBase\domains\uiarchitect\*.md`
- 修改：`D:\workspace\src\btd-client\design\KnowledgeBase\project\*.md`
- 修改：`D:\workspace\src\btd-client\design\KnowledgeBase\schema\*.md`

- [ ] **Step 1：找出缺 frontmatter 的文件**

运行：

```powershell
Get-ChildItem -Recurse -File D:\workspace\src\btd-client\design\KnowledgeBase -Filter *.md |
  ForEach-Object {
    $first = Get-Content -LiteralPath $_.FullName -TotalCount 1
    if ($first -ne "---") { $_.FullName }
  }
```

预期：输出需要补 frontmatter 的文件。

- [ ] **Step 2：按文件类型补 frontmatter**

规则：

```yaml
---
type: Domain | Routing | CodingRules | Workflow | Template | Example | Schema | Guide
title: 可读标题
description: 一句话说明用途
resource: design/KnowledgeBase/相对路径
tags: [btd-client, unity, ...]
timestamp: 2026-07-07T00:00:00+08:00
---
```

示例：`domains/ui/coding_rules.md`

```markdown
---
type: CodingRules
title: btd-client UI Coding Rules
description: Stable rules for UGUI and R3 MVVM UI development.
resource: design/KnowledgeBase/domains/ui/coding_rules.md
tags: [btd-client, unity, ui, ugui, mvvm, coding-rules]
timestamp: 2026-07-07T00:00:00+08:00
---
```

- [ ] **Step 3：不改正文语义**

只允许：

- 增加 frontmatter
- 修正明显断链
- 补标题
- 补“Read order”

不允许：

- 改业务规则
- 删除现有规则
- 改 Unity 代码规范内容

---

## Task 4：补 btd-client workflow 知识索引

**文件：**
- 创建：`D:\workspace\src\btd-client\design\KnowledgeBase\workflows\index.md`
- 创建：`D:\workspace\src\btd-client\design\KnowledgeBase\workflows\unity-ui-feature.md`
- 创建：`D:\workspace\src\btd-client\design\KnowledgeBase\workflows\unity-ui-quick.md`
- 创建：`D:\workspace\src\btd-client\design\KnowledgeBase\workflows\unity-bugfix.md`
- 创建：`D:\workspace\src\btd-client\design\KnowledgeBase\workflows\unity-logic-mod.md`

- [ ] **Step 1：创建 workflow index**

内容：

```markdown
---
type: Index
title: btd-client Workflow Knowledge
description: Knowledge routing for btd-client AI workflows.
resource: design/KnowledgeBase/workflows/index.md
tags: [btd-client, workflow, knowledge-base]
timestamp: 2026-07-07T00:00:00+08:00
---

# Workflow Knowledge

- [Unity UI Feature](./unity-ui-feature.md)
- [Unity UI Quick Fix](./unity-ui-quick.md)
- [Unity Bug Investigation](./unity-bugfix.md)
- [Unity Logic Modification](./unity-logic-mod.md)
```

- [ ] **Step 2：创建 UI feature workflow 知识文件**

内容：

```markdown
---
type: Workflow
title: Unity UI Feature Knowledge Loading
description: Required knowledge files for new btd-client UI feature workflows.
resource: design/KnowledgeBase/workflows/unity-ui-feature.md
tags: [btd-client, unity, ui, workflow]
timestamp: 2026-07-07T00:00:00+08:00
---

# Unity UI Feature Knowledge Loading

Read:

1. `../project/routing.md`
2. `../domains/ui/routing.md`
3. `../domains/ui/coding_rules.md`
4. `../domains/ui/data_flow_rules.md`
5. `../domains/ui/development_workflow.md`

Read templates only when creating or reshaping View/ViewModel structure:

1. `../domains/ui/templates/mvvm-view.md`
2. `../domains/ui/templates/mvvm-viewmodel.md`

Final output must include:

```markdown
## Loaded Knowledge

- `design/KnowledgeBase/project/routing.md`
- `design/KnowledgeBase/domains/ui/routing.md`
```
```

- [ ] **Step 3：为其他 workflow 创建同类文件**

每个文件都写清：

- 该 workflow 应读哪些知识
- 哪些大模板不默认读
- 最终输出要包含 `Loaded Knowledge`

---

## Task 5：轻改 btd-client 工作流

**文件：**
- 修改：`D:\workspace\src\btd-client\.claude\workflows\unity-ui-feature-development.md`
- 修改：`D:\workspace\src\btd-client\.claude\workflows\unity-ui-quick.md`
- 修改：`D:\workspace\src\btd-client\.claude\workflows\unity-bug-investigation.md`
- 修改：`D:\workspace\src\btd-client\.claude\workflows\unity-logic-modification.md`

- [ ] **Step 1：给 UI feature workflow 加 Knowledge Loading**

加入：

```markdown
## Knowledge Loading

Before Unity UI implementation:

1. Read `design/KnowledgeBase/project/routing.md` when present.
2. For UI work, read `design/KnowledgeBase/domains/ui/routing.md`.
3. For new UI features, read UI coding rules, data-flow rules, and MVVM templates listed by routing.
4. For quick fixes, do not read large templates unless creating or reshaping View/ViewModel structure.
5. Include a `Loaded Knowledge` section in the final summary.

If `design/KnowledgeBase` is missing or incomplete, continue with existing workflow rules and report the missing knowledge files.
```

- [ ] **Step 2：给其他 workflow 加通用 Knowledge Loading**

加入：

```markdown
## Knowledge Loading

Before code changes:

1. Read `design/KnowledgeBase/project/routing.md` when present.
2. Select the relevant domain based on the user task.
3. Read the selected domain `routing.md`.
4. Read only the required domain knowledge files listed by routing.
5. Include a `Loaded Knowledge` section in the final summary.

If `design/KnowledgeBase` is missing or incomplete, continue with existing workflow rules and report the missing knowledge files.
```

- [ ] **Step 3：确认 workflow 不重复粘贴知识正文**

运行：

```powershell
Select-String -Path D:\workspace\src\btd-client\.claude\workflows\*.md -Pattern "Knowledge Loading|Loaded Knowledge|design/KnowledgeBase" |
  Format-Table Path,LineNumber,Line -AutoSize
```

预期：只出现路由和加载规则，不出现大段知识正文。

---

## Task 6：在 Nexus Agents 中加入 btd-client OKF 校验能力

**文件：**
- 创建：`D:\workspace\src\nexus-agents\internal\knowledgebase\types.go`
- 创建：`D:\workspace\src\nexus-agents\internal\knowledgebase\frontmatter.go`
- 创建：`D:\workspace\src\nexus-agents\internal\knowledgebase\scan.go`
- 创建：`D:\workspace\src\nexus-agents\internal\knowledgebase\validate.go`
- 创建测试：`D:\workspace\src\nexus-agents\internal\knowledgebase\*_test.go`

- [ ] **Step 1：实现 frontmatter parser**

支持：

- `type`
- `title`
- `description`
- `resource`
- `tags`
- `timestamp`

- [ ] **Step 2：实现扫描**

扫描：

```text
design/KnowledgeBase/**/*.md
```

输出：

- 文档路径
- 标题
- 类型
- tags
- links
- 文件大小
- 修改时间

- [ ] **Step 3：实现校验**

检查：

- 是否存在 `design/KnowledgeBase`
- 是否存在 `index.md`
- 是否存在 `log.md`
- 是否缺 frontmatter
- 是否缺必填/推荐字段
- 是否有 Markdown 断链
- 是否有超过 120KB 的大文件
- 是否有超过 90 天的 stale doc
- 是否有重复硬规则

- [ ] **Step 4：测试**

运行：

```powershell
go test ./internal/knowledgebase
```

预期：PASS。

---

## Task 7：在 Nexus Agents 项目详情中加入 Knowledge Base 页面（导入后自动导出 + 手动刷新）

**文件：**
- 创建：`D:\workspace\src\nexus-agents\internal\knowledgebase\render.go`
- 创建：`D:\workspace\src\nexus-agents\internal\knowledgebase\export.go`
- 修改：`D:\workspace\src\nexus-agents\internal\httpapi\server.go`
- 修改：`D:\workspace\src\nexus-agents\web\src\App.vue`

- [ ] **Step 0：明确导出模型**

你的需求不是“每次打开页面都直接遍历项目目录实时渲染”，而是：

```text
导入项目
  → Nexus 自动扫描项目的 design/KnowledgeBase
  → Nexus 生成一份本地 Knowledge Base export
  → 前端 Knowledge Base 页面读取这份 export 展示
  → 点击 Refresh Export 时重新扫描并重新导出
```

对 btd-client：

```text
Source of truth:
D:\workspace\src\btd-client\design\KnowledgeBase

Nexus export/cache:
<nexus-local-data>\knowledge-exports\<projectId>\
```

要求：

- `btd-client/design/KnowledgeBase` 仍是唯一源。
- Nexus 生成的 export 只是展示缓存。
- 导入 btd-client 后自动生成 export。
- Knowledge Base 页面右上角提供 `Refresh Export`。
- 点击 `Refresh Export` 后重新扫描 btd-client 原始知识库并重建 export。
- 重建 export 不修改 btd-client 原始文件。
- export 可安全删除重建。

export 目录建议结构：

```text
knowledge-exports/<projectId>/
  manifest.json
  tree.json
  validation.json
  maintenance.json
  route-index.json
  docs/
    design_KnowledgeBase_index.md.json
    design_KnowledgeBase_project_routing.md.json
    design_KnowledgeBase_domains_ui_routing.md.json
```

`manifest.json` 示例：

```json
{
  "projectId": "btd-client",
  "projectRoot": "D:/workspace/src/btd-client",
  "sourceRoot": "design/KnowledgeBase",
  "exportRoot": "<nexus-local-data>/knowledge-exports/btd-client",
  "documentCount": 31,
  "sourceHash": "sha256:...",
  "exportedAt": "2026-07-07T18:31:00+08:00"
}
```

新增后端能力：

```go
func ExportProjectKnowledge(projectID string, projectRoot string, exportRoot string) (ExportManifest, error)
```

触发时机：

1. `ImportProject` 成功后，如果项目有 `design/KnowledgeBase`，自动导出。
2. `RescanProject` 成功后，可以刷新 summary，但不强制导出。
3. 前端点击 `Refresh Export` 时，强制重新导出。

新增 API：

```text
GET  /api/projects/{projectId}/knowledge/export
POST /api/projects/{projectId}/knowledge/export/refresh
```

说明：

- `GET export` 返回当前 manifest + tree + summary。
- `POST refresh` 重新生成 export 并返回新 manifest。

- [ ] **Step 1：参考 Kiso 后确认设计取舍**

Kiso 的核心取向是：

1. **OKF is the source of truth**：Markdown / OKF bundle 仍是可信源。
2. **Browse**：把 OKF bundle 转成结构化、可导航的站点。
3. **Inspect**：每个页面保留清晰的 metadata，并能回到原始 Markdown source。
4. **Publish**：输出 plain static pages，方便人和 agent 使用。
5. **Agent-friendly**：生成页面保持清晰路径、链接和可复用入口，例如 `llms.txt` / sitemap 这类索引。

映射到 Nexus Agents：

| Kiso 能力 | Nexus Agents 采用方式 |
|---|---|
| OKF source of truth | `btd-client/design/KnowledgeBase` 仍是源；Nexus 只生成可浏览 export |
| Browse | 导入项目后自动生成 Knowledge Base 前端页面所需 export |
| Inspect | 每篇文档显示 frontmatter、source path、raw markdown 入口 |
| Publish | 不发布外部站点，但在 Nexus 本地数据目录生成项目级 export，可手动刷新重建 |
| Agent-friendly | Overview 提供 agent entrypoints；Routing 提供 Loaded Knowledge 复制 |

不直接照搬 Kiso 的部分：

- 不发布到 GitHub Pages 或外部站点。
- 不把 Nexus 做成通用公开 OKF publisher。
- 不把 export 写回 btd-client 仓库。
- 不做完整 `llms.txt` 文件落盘；先在 UI 中提供可复制的 agent context。

- [ ] **Step 2：项目详情一级导航新增 Knowledge Base**

在 Nexus Agents 项目详情页的一级导航中增加：

```text
[Overview] [Agents] [Rules] [Skills] [Workflows] [Knowledge Base]
```

要求：

- `Knowledge Base` 是项目级页面，不是全局页面。
- 只有选中某个导入项目后才显示项目对应知识库。
- 对 btd-client 来说，读取路径是：

```text
D:\workspace\src\btd-client\design\KnowledgeBase
```

- 不新增单独顶层菜单，不放在 Workflows 子页面里。
- 不替代现有 Agents / Rules / Skills / Workflows 页面。

- [ ] **Step 3：Knowledge Base 页面二级 tab 设计**

`Knowledge Base` 页面内部包含 5 个 tab：

```text
Overview | Validation | Docs | Routing | Maintenance
```

职责划分：

| Tab | 作用 | 是否修改文件 |
|---|---|---|
| Overview | 总览项目知识库状态、文档数量、domain、最近更新时间、入口文件 | 否 |
| Validation | OKF frontmatter / 必要文件 / 断链 / 字段缺失校验 | 否 |
| Docs | Kiso 风格文档树 + Markdown 渲染阅读 | 否 |
| Routing | 输入任务描述，预览 workflow 应加载的知识文件 | 否 |
| Maintenance | 维护报告：过期、大文件、重复规则、建议动作 | 默认否 |

页面默认打开 `Overview`。

- [ ] **Step 4：Overview tab 设计**

展示内容：

```text
Knowledge Base Overview

Status: Detected / Missing / Invalid
Root: design/KnowledgeBase
Documents: 31
Domains: 5
Workflows: 4
Errors: 0
Warnings: 6
Last Updated: 2026-07-07 18:30
```

主要卡片：

1. **Status Card**
   - Detected：找到 `design/KnowledgeBase`
   - Missing：没找到知识库
   - Invalid：找到知识库，但存在 error 级问题

2. **Entry Files**
   - `design/KnowledgeBase/index.md`
   - `design/KnowledgeBase/log.md`
   - `design/KnowledgeBase/project/routing.md`

3. **Domain Summary**
   - `ui`
   - `uiarchitect`
   - `gameplay`
   - `network`
   - `behaviour_tree`

4. **Workflow Knowledge**
   - `unity-ui-feature`
   - `unity-ui-quick`
   - `unity-bugfix`
   - `unity-logic-mod`

5. **Agent Entrypoints（参考 Kiso 的 agent-friendly 入口）**
   - `index.md`
   - `project/routing.md`
   - `workflows/index.md`
   - 一键复制 “Agent Context Index”

“Agent Context Index” 复制内容格式：

```markdown
# btd-client Knowledge Base

Source root: `design/KnowledgeBase`

Start here:

1. `design/KnowledgeBase/index.md`
2. `design/KnowledgeBase/project/routing.md`
3. Select the relevant domain routing file.
4. Include `Loaded Knowledge` in workflow summaries.
```

当知识库不存在时，显示：

```text
No OKF-compatible knowledge base found.
Expected root: design/KnowledgeBase

[Initialize from Unity Client Profile]
```

但对 btd-client 默认不自动初始化覆盖，只提示用户确认。

- [ ] **Step 5：Validation tab 设计**

展示 OKF 校验结果。

顶部 summary：

```text
Validation Summary

Errors: 0
Warnings: 6
Checked At: 2026-07-07 18:30
```

问题列表字段：

| 字段 | 示例 |
|---|---|
| Severity | error / warning |
| Code | missing_frontmatter / broken_link / missing_timestamp |
| File | design/KnowledgeBase/domains/ui/routing.md |
| Line | 18 |
| Message | Markdown link target does not exist |

过滤器：

```text
[All] [Errors] [Warnings]
```

点击问题后：

- 如果能定位到文档，则切到 Docs tab 并打开对应文档。
- 如果有 line，则在右侧显示 line 提示。

- [ ] **Step 6：Docs tab 设计**

Docs 是 Kiso 风格展示页。

布局：

```text
左侧：文档树
右侧：文档内容
顶部：frontmatter metadata
```

左侧树示例：

```text
design/KnowledgeBase
├─ index.md
├─ log.md
├─ project
│  └─ routing.md
├─ domains
│  ├─ ui
│  │  ├─ README.md
│  │  ├─ routing.md
│  │  ├─ coding_rules.md
│  │  └─ data_flow_rules.md
│  └─ uiarchitect
│     ├─ README.md
│     └─ routing.md
└─ workflows
   ├─ index.md
   └─ unity-ui-feature.md
```

右侧顶部 metadata：

```text
Title: btd-client UI Routing
Type: Routing
Resource: design/KnowledgeBase/domains/ui/routing.md
Tags: btd-client, unity, ui, routing
Timestamp: 2026-07-07T00:00:00+08:00
Source: D:\workspace\src\btd-client\design\KnowledgeBase\domains\ui\routing.md
```

右侧顶部操作按钮：

```text
[Copy Path] [Copy Markdown Source] [Open Raw]
```

按钮说明：

| 按钮 | 行为 |
|---|---|
| Copy Path | 复制 repo 相对路径，例如 `design/KnowledgeBase/domains/ui/routing.md` |
| Copy Markdown Source | 复制原始 Markdown，包括 frontmatter |
| Open Raw | 在右侧切换为 raw Markdown 视图，不调用外部编辑器 |

右侧正文：

- 渲染 heading
- 渲染 paragraph
- 渲染 list
- 渲染 code block
- Markdown 链接可以点击
- HTML 必须 escape

不做：

- 不做在线编辑
- 不做双向图谱
- 不做向量检索
- 不做 QAnything 问答

- [ ] **Step 7：Routing tab 设计**

用途：在运行 workflow 前预览“这个任务应该读哪些知识”。

输入区：

```text
Task description:
[ 新增活动奖励弹窗 UI                         ]

[Preview Route]
```

输出区：

```text
Matched Domain: ui

Reason:
Task mentions UI / 弹窗, so UI domain knowledge is required.

Required Files:
✓ design/KnowledgeBase/project/routing.md
✓ design/KnowledgeBase/domains/ui/routing.md
✓ design/KnowledgeBase/domains/ui/coding_rules.md
✓ design/KnowledgeBase/domains/ui/data_flow_rules.md
✓ design/KnowledgeBase/domains/ui/development_workflow.md

Optional Files:
- design/KnowledgeBase/domains/ui/templates/mvvm-view.md
- design/KnowledgeBase/domains/ui/templates/mvvm-viewmodel.md

Missing Files:
none
```

交互：

- 点击文件名可跳转到 Docs tab 打开该文档。
- Missing Files 用红色标识。
- Optional Files 用弱强调展示。
- 提供一个复制按钮：

```text
[Copy Loaded Knowledge Section]
```

复制内容：

```markdown
## Loaded Knowledge

- `design/KnowledgeBase/project/routing.md`
- `design/KnowledgeBase/domains/ui/routing.md`
- `design/KnowledgeBase/domains/ui/coding_rules.md`
- `design/KnowledgeBase/domains/ui/data_flow_rules.md`
- `design/KnowledgeBase/domains/ui/development_workflow.md`
```

- [ ] **Step 8：Maintenance tab 设计**

用途：知识库定期整理报告。

展示区块：

1. **Stale Documents**

```text
domains/ui/feature_patterns.md
timestamp: 2026-03-01
reason: older than 90 days
```

2. **Large Documents**

```text
domains/ui/templates/mvvm-view.md
size: 128 KB
suggestion: keep as optional template, do not load by default
```

3. **Broken Links**

```text
domains/ui/README.md -> ./old_data_flow.md
```

4. **Duplicate Hard Rules**

```text
"Do not hand-edit generated *.Gen.cs files"
appears in:
- AGENTS.md
- design/KnowledgeBase/domains/ui/coding_rules.md
```

5. **Suggested Actions**

```text
1. Keep hard rule in AGENTS.md as project baseline.
2. In coding_rules.md, reference AGENTS.md instead of duplicating long wording.
3. Keep mvvm-view.md as optional template only.
```

默认行为：

- 只读报告。
- 不自动改文件。
- 不自动提交。
- 后续可以单独加 “Generate Patch Proposal”，但不在本阶段做自动应用。

- [ ] **Step 9：增加 Kiso 风格的 Export / Agent Context 辅助能力**

本阶段不生成完整静态站，但提供两个轻量导出能力：

1. **Copy Agent Context Index**

用于复制给 Codex / Claude 的入口摘要：

```markdown
# btd-client OKF Knowledge Context

Root: `design/KnowledgeBase`

Entry:
- `design/KnowledgeBase/index.md`
- `design/KnowledgeBase/project/routing.md`

Domains:
- `design/KnowledgeBase/domains/ui/routing.md`
- `design/KnowledgeBase/domains/uiarchitect/routing.md`

Workflow rule:
Always include `Loaded Knowledge` in the final summary.
```

2. **Export Document List**

导出当前知识库文档清单，格式类似：

```markdown
# btd-client Knowledge Documents

- `design/KnowledgeBase/index.md` — Index — btd-client Knowledge Base
- `design/KnowledgeBase/project/routing.md` — Routing — btd-client Project Knowledge Routing
- `design/KnowledgeBase/domains/ui/routing.md` — Routing — btd-client UI Routing
```

这两个能力对应 Kiso 的 agent-friendly / inspect 思路，但不生成实际 `llms.txt` 文件。

- [ ] **Step 10：实现文档树**

输出：

```json
{
  "root": "design/KnowledgeBase",
  "nodes": [
    {
      "path": "design/KnowledgeBase/index.md",
      "title": "btd-client Knowledge Base",
      "type": "Index"
    }
  ]
}
```

- [ ] **Step 11：实现 Markdown 基础渲染**

支持：

- heading
- paragraph
- list
- code fence
- escaped HTML

不做：

- 完整 Markdown 编辑器
- 双向链接图谱
- RAG 问答

- [ ] **Step 12：前端展示**

项目详情页增加 Knowledge Base 一级页面：

```text
[Overview] [Agents] [Rules] [Skills] [Workflows] [Knowledge Base]
```

Knowledge Base 页面内部增加：

```text
Overview | Validation | Docs | Routing | Maintenance
```

---

## Task 8：在 Nexus Agents 中加入 Route Preview

**文件：**
- 创建：`D:\workspace\src\nexus-agents\internal\knowledgebase\route.go`
- 修改：`D:\workspace\src\nexus-agents\internal\httpapi\server.go`
- 修改：`D:\workspace\src\nexus-agents\web\src\App.vue`

- [ ] **Step 1：实现 btd-client 路由匹配**

规则：

| 任务关键词 | domain |
|---|---|
| UI / 弹窗 / 界面 / ViewModel / prefab | ui |
| PSD / UIArchitect / 图层 / 命名 | uiarchitect |
| DataEvents / Service / Cache | ui |
| gameplay / 玩法 / 战斗 | gameplay |
| network / 协议 / socket | network |

- [ ] **Step 2：返回 required files**

例如输入：

```text
新增活动奖励弹窗 UI
```

返回：

```text
design/KnowledgeBase/project/routing.md
design/KnowledgeBase/domains/ui/routing.md
design/KnowledgeBase/domains/ui/coding_rules.md
design/KnowledgeBase/domains/ui/data_flow_rules.md
design/KnowledgeBase/domains/ui/development_workflow.md
```

- [ ] **Step 3：前端展示 route preview**

展示：

- 命中 domain
- required files
- optional files
- missing files
- reason

---

## Task 9：加入 Knowledge Maintenance Report

**文件：**
- 修改：`D:\workspace\src\nexus-agents\internal\knowledgebase\validate.go`
- 修改：`D:\workspace\src\nexus-agents\internal\httpapi\server.go`
- 修改：`D:\workspace\src\nexus-agents\web\src\App.vue`

- [ ] **Step 1：生成维护报告**

报告包括：

- 缺 frontmatter 文件
- 缺字段文件
- 断链
- stale docs
- large docs
- duplicate rules
- suggested actions

- [ ] **Step 2：默认只报告**

不自动修改：

- `AGENTS.md`
- workflow
- coding rules
- business rules

- [ ] **Step 3：前端展示维护建议**

展示：

```text
建议拆分 domains/ui/templates/mvvm-view.md，因为超过 120KB
建议补 frontmatter: domains/ui/routing.md
建议修复断链: domains/ui/README.md -> missing.md
```

---

## Task 10：加入 `$wf-kb-maintenance`

**文件：**
- 创建：`D:\workspace\src\nexus-agents\templates\workflows\kb-maintenance.md`
- 创建：`D:\workspace\src\nexus-agents\templates\workflows\kb-maintenance.graph.json`
- 创建：`D:\workspace\src\nexus-agents\templates\skills\codex\wf-kb-maintenance\SKILL.md`
- 创建：`D:\workspace\src\nexus-agents\templates\skills\codex\wf-kb-maintenance\agents\openai.yaml`

- [ ] **Step 1：创建维护 workflow**

workflow 内容：

```markdown
# Knowledge Base Maintenance Workflow

## Goal

Keep `design/KnowledgeBase` small, accurate, routable, and useful for AI workflows.

## Steps

1. Inventory knowledge files.
2. Validate OKF metadata.
3. Validate links.
4. Detect stale or oversized docs.
5. Detect duplicated hard rules.
6. Propose updates.
7. Wait for human review before applying hard-rule changes.
```

- [ ] **Step 2：创建 Codex skill**

skill 描述：

```markdown
---
name: wf-kb-maintenance
description: Maintain an OKF-compatible project knowledge base by validating metadata, links, routing, staleness, duplicate hard rules, and workflow evidence.
---
```

- [ ] **Step 3：确认 `/wf` 能看到入口**

运行 Nexus 后在 workflow UI 检查 `wf-kb-maintenance`。

---

## Task 11：加入 Loaded Knowledge eval 检查

**文件：**
- 修改：`D:\workspace\src\nexus-agents\internal\catalog\evaluation.go`
- 修改：`D:\workspace\src\nexus-agents\internal\catalog\evaluation_test.go`

- [ ] **Step 1：检测输出中的 Loaded Knowledge**

识别：

```markdown
## Loaded Knowledge

- `design/KnowledgeBase/project/routing.md`
- `design/KnowledgeBase/domains/ui/routing.md`
```

- [ ] **Step 2：作为 eval evidence**

初期规则：

- 有 Loaded Knowledge：加正向 evidence
- 没有 Loaded Knowledge：warning
- 不作为 hard fail

- [ ] **Step 3：测试**

运行：

```powershell
go test ./internal/catalog -run Knowledge -v
```

预期：PASS。

---

## Task 12：试点验证

**目标项目：**
- `D:\workspace\src\btd-client`

- [ ] **Step 1：运行 OKF 校验**

通过 Nexus UI 或 API 验证：

```text
GET /api/projects/{btd-client-id}/knowledge/validate
```

预期：

- 能识别文档数量
- 能列出缺失字段
- 能列出断链
- 能列出大文件

- [ ] **Step 2：运行 route preview**

输入：

```text
新增活动奖励弹窗 UI
```

预期 required files 包含：

```text
design/KnowledgeBase/project/routing.md
design/KnowledgeBase/domains/ui/routing.md
design/KnowledgeBase/domains/ui/coding_rules.md
design/KnowledgeBase/domains/ui/data_flow_rules.md
```

- [ ] **Step 3：运行一次 UI feature workflow**

要求最终输出包含：

```markdown
## Loaded Knowledge

- `design/KnowledgeBase/project/routing.md`
- `design/KnowledgeBase/domains/ui/routing.md`
```

- [ ] **Step 4：运行 maintenance report**

确认报告只给建议，不自动改硬规则。

---

## 暂缓事项

这些先不做：

1. btd-game-server 改造。
2. QAnything 集成。
3. 向量库 / embedding / semantic search。
4. 完整 Markdown 编辑器。
5. 自动改写知识库硬规则。
6. 自动修改 Unity prefab / scene / `.meta`。

等 btd-client 试点稳定后，再把同样模式复制到 btd-game-server。

---

## 验收标准

这次方案完成后，应满足：

1. `btd-client/design/KnowledgeBase` 是 OKF-compatible。
2. btd-client 的 UI workflow 会先按 routing 读知识。
3. Nexus Agents 能识别 btd-client 的知识库。
4. Nexus Agents 能展示知识库文档树。
5. Nexus Agents 能做 OKF 校验。
6. Nexus Agents 能做 route preview。
7. Nexus Agents 能做 maintenance report。
8. workflow eval 能识别 `Loaded Knowledge`。
9. 过程中没有修改 Unity 业务代码、prefab、scene、`.meta`。
10. 过程中没有修改全局 Codex 配置文件。

---

## 推荐执行顺序

1. Task 1：盘点 btd-client。
2. Task 2：补 OKF 根结构。
3. Task 3：补 frontmatter。
4. Task 4：补 workflow 知识索引。
5. Task 5：轻改 btd-client workflow。
6. Task 6：Nexus 加 OKF 校验。
7. Task 7：Nexus 加 Kiso 风格展示。
8. Task 8：Nexus 加 route preview。
9. Task 9：Nexus 加 maintenance report。
10. Task 10：Nexus 加 `$wf-kb-maintenance`。
11. Task 11：Nexus eval 加 Loaded Knowledge 检查。
12. Task 12：对 btd-client 试点验证。

---

## 自检

- 计划已改成中文。
- 计划优先改造 btd-client。
- btd-game-server 已暂缓。
- 说明了会改哪些文件、做哪些能力。
- 保留 OKF 主链路，QAnything 只作为未来可选项。
- 工作流是轻改，不是重写。
- 知识库支持定期维护，但默认 report-only。
