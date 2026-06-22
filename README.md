# Nexus Agents

Nexus Agents 是一个 AI 开发配置管理台。设计原型在 `design/` 目录，生产实现基于 Go 后端 + Vue 3 + Vite + TypeScript 前端。

## 技术栈
- **Go 后端**：API 服务、项目配置管理、同步引擎、工作流运行时、模型代理
- **Vue 3 + Vite + TypeScript 前端**：管理控制台，遵循 `design/demo.html` 设计稿

## 当前 V1 功能范围
- `templates/` 目录下基于文件的 Agent、Rules、Skills、Workflows 模板配置
- 内存模型 API 目录，在 loader/sync 引擎建设期间对外暴露当前模板库
- 项目配置集：含来源追溯、本地版本、基础哈希、手动同步、解绑操作
- 工作流列表/画布：支持创建、编辑、节点选择和模拟运行抽屉
- Codex/Claude 模型路由表与路由解析
- **Codex 路由器（混合模式）**：Codex Desktop 本地代理——GPT 订阅模型走 ChatGPT Codex 后端，DeepSeek 模型走 Winky API 并做 Chat Completions 协议转换
- 项目导入写入本地 `.nexus` 元数据文件（已加入 .gitignore），工作流图存储在 `.claude/workflows/*.graph.json`

## 模板目录结构
```text
templates/
  agents/
    claude/    # .claude Agent Markdown 模板
    codex/     # .codex Agent TOML 模板
  rules/       # 规则 Markdown
  skills/      # 技能 Markdown
  workflows/   # 工作流 Markdown + .graph.json
```

Go 相关模板使用 `go-` 文件名前缀，例如 `templates/agents/claude/go-worker.md`、`templates/agents/codex/go-worker.toml`、`templates/workflows/go-bugfix.md`。

## 构建前端资源
Go 服务嵌入了 `web/dist`，前端代码修改后需重新构建：

```powershell
cd web
npm install
npm run build
cd ..
```

## 启动服务

推荐使用本地启动脚本，它会自动设置 Winky API Key 并清理端口：

```powershell
.\restart_local.ps1
```

`restart_local.ps1` 已加入 `.gitignore`，因为其中包含 `DEEPSEEK_API_KEY`。参照以下模板填写你的 key：

```powershell
# restart_local.ps1（不会提交到 git）
$env:DEEPSEEK_API_KEY = "你的-winky-key"
# ... 脚本其余部分
```

也可以手动启动：

```powershell
$env:DEEPSEEK_API_KEY = "你的-winky-key"
$env:NEXUS_ADDR = ":8766"
go run .\cmd\nexus-agents
```

Go 服务同时提供 Vue 控制台、API 和 Codex 代理：

```text
GET  http://127.0.0.1:8766/                              → Vue 控制台
GET  http://127.0.0.1:8766/api/health                    → 健康检查
GET  http://127.0.0.1:8766/proxy/codex/model-catalog.json → Codex 模型选择器
POST http://127.0.0.1:8766/proxy/codex/v1/responses       → Codex 请求入口
```

服务启动时还会自动将模型目录写入 `~/.codex/nexus-model-catalog.json`，供 Codex Desktop 读取本地文件路径。

---

## Codex 路由器：混合模式实现

`internal/codexrouter` 让 Codex Desktop 通过同一个本地 provider 同时使用 GPT 订阅模型和 DeepSeek API 模型。设计参考了 [codex-bridge](https://github.com/wangzhezbz/codex-bridge) 并移植到 Go。

### 整体流程

```
Codex Desktop
    │
    └─ POST /proxy/codex/v1/responses  （所有模型请求都到这里）
           │
           ▼
    codexrouter.Service  根据 model 字段分流
           │
           ├─ model = gpt-5.5 / gpt-5.4 / gpt-5.4-mini
           │      └─ proxyResponses()   ← 直接透传
           │           ├─ 替换 model 字段
           │           ├─ 透传 Codex 专属 Headers（Session-Id、X-Codex-* 等）
           │           ├─ 原样转发客户端的 OpenAI bearer token
           │           └─ 流式响应体直接回传给 Codex
           │
           └─ model = deepseek-v4-pro / deepseek-v4-flash
                  └─ proxyChatCompletions()   ← 协议转换
                       ├─ responsesToChatRequest()   请求方向转换
                       │    ├─ instructions → system message
                       │    ├─ input 条目 + previous_response_id 历史 → messages[]
                       │    └─ Responses tools → Chat Completions functions
                       ├─ POST /chat/completions → Winky DeepSeek（使用 DEEPSEEK_API_KEY）
                       └─ chatResponseToResponse() + responseToSSE()   响应方向转换
                            ├─ chat message → Responses output item
                            ├─ tool_calls → function_call / custom_tool_call 条目
                            └─ 完整 SSE 事件序列（见下文）
```

### 模型路由表

| Slug | 显示名称 | 后端地址 | 认证方式 |
|------|---------|---------|---------|
| `gpt-5.5` | GPT-5.5 | `chatgpt.com/backend-api/codex` | 订阅 token 透传 |
| `gpt-5.4` | GPT-5.4 | `chatgpt.com/backend-api/codex` | 订阅 token 透传 |
| `gpt-5.4-mini` | GPT-5.4 Mini | `chatgpt.com/backend-api/codex` | 订阅 token 透传 |
| `deepseek-v4-pro` | DeepSeek V4 Pro | `lumos.diandian.info/winky/deepseek/v1` | `DEEPSEEK_API_KEY` |
| `deepseek-v4-flash` | DeepSeek V4 Flash | `lumos.diandian.info/winky/deepseek/v1` | `DEEPSEEK_API_KEY` |
| `glm-5.2` | GLM-5.2 | `lumos.diandian.info/winky/glm/v1` | `DEEPSEEK_API_KEY`??? Winky key? |
| `glm-5.1` | GLM-5.1 | `lumos.diandian.info/winky/glm/v1` | `DEEPSEEK_API_KEY`??? Winky key? |

### 关键实现文件

```text
internal/codexrouter/
  router.go    # Service 主体、路由分发、proxyResponses、proxyChatCompletions、
               # 模型目录生成（ModelCatalog / serveModelCatalog + ETag 缓存）
  convert.go   # responsesToChatRequest（请求转换）、chatResponseToResponse（响应转换）、
               # responseToSSE（完整 SSE 事件序列）、responseHistory（多轮对话历史）
  tools.go     # Responses ↔ Chat Completions 工具定义和调用转换
               # 支持 function_call、custom_tool_call、apply_patch、tool_search
```

### Codex Desktop 配置（`~/.codex/config.toml`）

```toml
model_provider = "nexus-codex"
model = "gpt-5.5"
model_catalog_json = "C:/Users/<你的用户名>/.codex/nexus-model-catalog.json"

[model_providers.nexus-codex]
name = "Nexus Codex"
base_url = "http://127.0.0.1:8766/proxy/codex/v1"
wire_api = "responses"
requires_openai_auth = true

[windows]
sandbox = "unelevated"
```

**各配置项说明：**

- `requires_openai_auth = true`：让 Codex 把 ChatGPT 订阅 bearer token 带在请求头里。GPT 路由会原样转发这个 token 到 `chatgpt.com`；DeepSeek 路由忽略它，改用服务端的 `DEEPSEEK_API_KEY`。

- `model_catalog_json`：必须填**本地文件路径**，不能填 HTTP URL（Codex Desktop 不会请求远程 URL 拉取模型目录）。服务每次启动时自动写入该文件，修改路由配置后重启服务即可更新。

- `sandbox = "unelevated"`：Windows 下以 Administrator 账户运行时，`elevated` 模式因无法创建独立沙盒用户而失败，改为 `unelevated` 可绕过该限制。

### SSE 完整事件序列（关键）

Codex Desktop 期望收到完整的 Responses API SSE 事件流。**只发 `response.completed` 会导致 UI 一直转圈无响应**，这是 DeepSeek 路由"发消息没通"的根本原因。

`convert.go` 中的 `responseToSSE` 函数保证发出完整的 10 个事件：

```
event: response.created          ← 告知 Codex 响应已创建
event: response.in_progress      ← 标记进行中状态
event: response.output_item.added    ← 新增输出条目
event: response.content_part.added  ← 新增内容部分
event: response.output_text.delta   ← 实际文本内容
event: response.output_text.done    ← 文本输出完成
event: response.content_part.done   ← 内容部分完成
event: response.output_item.done    ← 输出条目完成
event: response.completed           ← 响应全部完成
data: [DONE]                         ← SSE 流结束标志
```

---

## 前端开发模式
迭代 Vue 代码时可以单独运行 Vite：

```powershell
cd web
npm run dev
```

Vite 开发服务器会将 `/api` 代理到 `http://127.0.0.1:8766`。

正常本地测试时，`npm run build` 后只启动 Go 服务即可。

## 验证
```powershell
.\scripts\verify_all.ps1
```

检查设计文档、应用脚手架、Go 测试，以及 `web/node_modules` 存在时的前端构建。

## 常用接口
```text
GET  /api/health
GET  /api/bootstrap
POST /api/projects/import
GET  /api/model-routes/resolve?client=codex&model=deepseek-v4-pro
GET  /proxy/codex/health
GET  /proxy/codex/model-catalog.json
GET  /proxy/codex/v1/models
```
