# 多 Agent 方案评审编排器（轮询版）— 部署与验收指南

## 文件结构

```
D:\workspace\src\nexus-agents\
├── review_orchestrator_v2.py   ← New immutable FSM entrypoint
├── README.md                ← 本文件
└── logs\                    ← 运行时自动创建
    ├── orchestrator_<timestamp>.log
    └── phase_<issue_id>_<phase>.json
```

---

## 第一步：确认 Python 环境

在 `D:\workspace\src\nexus-agents` 目录下执行：

```cmd
python --version
```

要求 Python 3.8+。无需额外依赖（只用标准库 + subprocess 调用 multica CLI）。

验收点：
- [ ] 输出 `Python 3.x.x`，版本 ≥ 3.8

---

## 第二步：确认 multica CLI 可用

```cmd
multica --version
multica workspace get --output json
```

验收点：
- [ ] CLI 正常运行，能返回 workspace 信息

---

## 第三步：在 Multica 中创建 3 个 Agent

按 `07-multi-agent-review-setup.md` 第一步操作，创建：

| 变量名 | Agent 名称 | 描述 |
|--------|-----------|------|
| `AGENT_ANALYST_ID` | `review-analyst` | 需求收敛 + 证据采集 |
| `AGENT_SOLVER_ID`  | `review-solver`  | 方案架构师 |
| `AGENT_CRITIC_ID`  | `review-critic`  | 对抗审员兼法官 |

创建后，在 Agent 列表页面复制每个 Agent 的 **ID（UUID）**。

验收点：
- [ ] 3 个 Agent 在 Dashboard 可见，状态 Online/Ready
- [ ] 各 Agent ID 已记录

---

## 第四步：配置环境变量

在 Windows 命令行（cmd 或 PowerShell）中设置：

**cmd：**
```cmd
set WORKSPACE_ID=0b9766ed-3f6b-47cf-832a-afa0b28b80dd
set AGENT_ANALYST_ID=<review-analyst 的 UUID>
set AGENT_SOLVER_ID=<review-solver 的 UUID>
set AGENT_CRITIC_ID=<review-critic 的 UUID>
set MULTICA_TOKEN=<你的 API Token>

REM 可选配置（不设置则用默认值）
set MAX_ROUNDS=3
set POLL_INTERVAL_SEC=8
set PHASE_TIMEOUT_SEC=300
```

**PowerShell：**
```powershell
$env:WORKSPACE_ID = "0b9766ed-3f6b-47cf-832a-afa0b28b80dd"
$env:AGENT_ANALYST_ID = "<review-analyst 的 UUID>"
$env:AGENT_SOLVER_ID  = "<review-solver 的 UUID>"
$env:AGENT_CRITIC_ID  = "<review-critic 的 UUID>"
$env:MULTICA_TOKEN     = "<你的 API Token>"
```

> **如何获取 MULTICA_TOKEN**：
> Multica Dashboard → Settings → API Tokens → 生成或复制

验收点：
- [ ] `set` 命令（或 `Get-ChildItem Env:AGENT*`）能看到变量已设置

---

## 第五步：干跑验证配置

```cmd
cd D:\workspace\src\nexus-agents
python review_orchestrator_v2.py --new "测试"
```

预期输出：
```
=== DRY RUN CONFIG ===
{
  "token": "your****",
  "workspace_id": "0b9766ed...",
  "agent_analyst": "...",
  ...
}
```

验收点：
- [ ] 无报错，能打印出配置（token 会脱敏显示为 `xxxx****`）
- [ ] 所有 agent ID 字段非空

---

## 第六步：冒烟测试（对已有 issue 启动评审）

先手动在 Multica 中创建一个测试 issue，内容为：

> 给订单列表增加按创建时间排序的功能

拿到 issue id（如 `SER-xxx` 对应的 UUID），然后：

```cmd
python review_orchestrator_v2.py --issue <issue-uuid>
```

**观察日志输出**（实时打印到终端 + 写入 `logs\orchestrator_*.log`）：

```
2026-08-10T... [INFO] orchestrator — review started: issue=...
2026-08-10T... [INFO] orchestrator — === Phase: intake (round 0) ===
2026-08-10T... [INFO] orchestrator — [intake] waiting for agent ... reply (timeout=300s)…
```

验收点：
- [ ] 编排器启动，打印 `review started`
- [ ] Phase `intake` 启动，日志显示 `waiting for agent reply`
- [ ] Multica 中对应 issue 出现了 `[TASK:INTAKE]` 评论
- [ ] issue 被分配给 `review-analyst` Agent

---

## 第七步：等待 Phase 完成

等待 Agent 回复（最多 5 分钟，可通过 `PHASE_TIMEOUT_SEC` 调整）。

日志会依次出现：
```
[INFO] [intake] agent reply received
[INFO] [intake] requirements parsed: N items
[INFO] === Phase: evidence (round 0) ===
[INFO] [evidence] evidence=N, blocked=M
[INFO] === Phase: solve v1 (round 1) ===
...
[INFO] [verdict] result=PASS/CONDITIONAL/REJECT score=XX
```

验收点：
- [ ] 全部 6 个 Phase 按顺序执行
- [ ] `logs\` 目录下生成 `phase_<issue_id>_*.json` artifact 文件
- [ ] 最终 issue 出现评审汇总评论

---

## 第八步：查看 Artifacts

```cmd
dir logs\
```

每个 Phase 都有对应的 JSON 文件：

| 文件 | 内容 |
|------|------|
| `phase_<id>_intake_output.json` | 结构化需求单 |
| `phase_<id>_evidence_output.json` | 证据列表 |
| `phase_<id>_solve_v1_output.json` | 方案 v1 |
| `phase_<id>_challenge_output.json` | 缺陷卡 |
| `phase_<id>_verdict_r1_output.json` | 裁决书 |

验收点：
- [ ] 文件存在且可正常 JSON 解析
- [ ] `verdict` 文件包含 `verdict` 字段（PASS/CONDITIONAL/REJECT）

---

## 常见问题

| 现象 | 原因 | 解决 |
|------|------|------|
| `缺少必填配置：token` | 环境变量未设置 | 重新 `set MULTICA_TOKEN=...` |
| `[intake] timeout waiting for agent reply` | Agent 未响应 | 检查 Agent 是否 Online，手动给 Agent 发一条测试消息 |
| `CLI failed: multica CLI not found` | multica 未在 PATH | 确认 multica.exe 路径，或用完整路径 |
| Phase 卡住不前进 | Agent 回复不含 JSON | 检查 Agent System Prompt 是否正确，手动测试 Agent |
| `assign agent failed` | issue 分配 API 参数问题 | 非致命警告，Agent 仍会因 comment @mention 触发 |

---

## 配置速查

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `MULTICA_TOKEN` | 无（必填）| API Token |
| `WORKSPACE_ID` | 已内置 | Workspace UUID |
| `AGENT_ANALYST_ID` | 无（必填）| review-analyst UUID |
| `AGENT_SOLVER_ID` | 无（必填）| review-solver UUID |
| `AGENT_CRITIC_ID` | 无（必填）| review-critic UUID |
| `REVIEW_PROJECT_ID` | 无（可选）| 新建 issue 归属 project |
| `MAX_ROUNDS` | 3 | 最大迭代轮次 |
| `POLL_INTERVAL_SEC` | 8 | 轮询间隔（秒） |
| `PHASE_TIMEOUT_SEC` | 300 | 每 phase 超时（秒） |

---

## 测试模式（不发送飞书消息）

专门用于联调/验证，任何外发飞书/webhook 消息都会被抑制，避免打扰真实群：

```powershell
# 方式一：命令行开关（推荐）
python review_orchestrator_v2.py --issue <issue-uuid> --no-external-notifications

# 方式二：环境变量（供服务/CI 使用）
$env:NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS = "1"
python review_orchestrator_v2.py --issue <issue-uuid>
```

- `--no-external-notifications` 等价于设置 `NEXUS_TEST_NO_EXTERNAL_NOTIFICATIONS=1`。
- 关闭时端口会被替换为 `NullNotificationPort`，不会发起任何 Feishu HTTP 请求。
- 也支持 `ENABLE_FEISHU_NOTIFICATIONS=0/false/no/off` 关闭。
- 如需与线上隔离，配合独立 runs 根目录：`--runs-root runs-test`（或 `ORCHESTRATOR_RUNS_ROOT=runs-test`）。
