# btd-client OKF 知识库盘点

## 现有入口

- `D:\workspace\src\btd-client\AGENTS.md`
- `D:\workspace\src\btd-client\CLAUDE.md`
- `D:\workspace\src\btd-client\.claude\workflows`
- `D:\workspace\src\btd-client\.agents\skills`
- `D:\workspace\src\btd-client\design\KnowledgeBase`

## 现有 KnowledgeBase 文件

当前 `design/KnowledgeBase` 有 31 个 Markdown 文件：

| 区域 | 文件数 | 说明 |
|---|---:|---|
| root | 1 | `README.md`，当前知识库入口 |
| domains root | 4 | domain 总览与 behaviour_tree/gameplay/network 占位入口 |
| domains/ui | 9 | UI routing、coding、data flow、workflow、patterns、prompt 等核心知识 |
| domains/ui/examples | 4 | UI 反例和示例 |
| domains/ui/templates | 4 | MVVM View/ViewModel/Test 模板 |
| domains/uiarchitect | 2 | UIArchitect 入口和 routing |
| generated | 1 | 生成内容说明 |
| project | 2 | project README 和 routing |
| schema | 4 | 文档模板、维护规则、同步 checklist |

## 缺 OKF frontmatter 的文件

盘点结果：31/31 个 Markdown 文件都缺少 OKF YAML frontmatter。后续需要补：

- `type`
- `title`
- `description`
- `resource`
- `tags`
- `timestamp`

## 现有 workflow / skill

### Claude workflow

- `subagent-driven-development.md`
- `uiarchitect-tool-development.md`
- `unity-bug-investigation.md`
- `unity-logic-modification.md`
- `unity-ui-feature-development.md`
- `unity-ui-quick.md`

### Codex workflow skills

- `wf-subagents`
- `wf-uiarchitect-tool`
- `wf-unity-bugfix`
- `wf-unity-logic-mod`
- `wf-unity-ui-feature`
- `wf-unity-ui-quick`

## 初步问题

1. `design/KnowledgeBase` 已有较完整内容，但目前不是 OKF-compatible。
2. 缺少根级 `index.md` 和 `log.md`。
3. 已有 `project/routing.md`，但需要升级成 OKF frontmatter + Nexus route preview 可消费格式。
4. UI 模板文件较大，但还没超过 120KB；应作为 optional template，不应 quick fix 默认加载。
5. workflow 尚未统一要求输出 `Loaded Knowledge`。
6. Nexus Agents 当前还没有导入项目后自动导出 Knowledge Base 的前端展示缓存。

## 改造建议

1. 保留现有 `design/KnowledgeBase`，不重建目录。
2. 增加 `index.md` / `log.md`，保留 `README.md` 作为人类入口或兼容入口。
3. 给所有现有 Markdown 补 OKF frontmatter。
4. 增加 `workflows/*.md`，描述不同 workflow 应加载的知识。
5. 轻改 `.claude/workflows/*.md`，加入 `Knowledge Loading`。
6. 在 Nexus Agents 中实现项目导入后的 Knowledge Base export：
   - 自动导出
   - 前端展示
   - Refresh Export 手动重建
   - Validation / Docs / Routing / Maintenance tabs

## 不做事项

- 不修改 Unity 业务代码。
- 不修改 prefab / scene / `.meta`。
- 不接 QAnything / RAG。
- 不自动重写 hard rules。
