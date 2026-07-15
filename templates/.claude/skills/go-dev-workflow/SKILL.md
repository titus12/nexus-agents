---
name: dev-workflow
description: "新功能开发九步流程。新需求/较大重构时加载，确保设计→实现→验证→提交的完整流程。"
---

# 新功能开发流程

> 适用：新功能 / 较大重构 | 方案归档：`docs/dev-plans/`
>
> §1 九步流程 §2 铁律 §3 Agent 分工 §4 方案文件

## §1 九步流程

| # | 步骤 | 主理 agent | 产出 | 进入下一步前提 |
|---|------|-----------|------|----------------|
| 1 | 阅读文档、拆分任务 | sisyphus + librarian（飞书文档触发 `feishu` skill） | 任务拆解 | 用户审核通过 |
| 2 | 设计 xlsx 配置表 | prometheus | Excel 表结构 | 用户审核通过 |
| 3 | 设计数据表 | prometheus | dbstate / xbean 字段 | 用户审核通过 |
| 4 | 设计协议 | prometheus | proto 消息 / service 接口 | 用户审核通过 |
| 5 | 写方案 | prometheus → sisyphus 落盘 | `docs/dev-plans/<功能名>.md` | 用户审核通过 |
| 6 | 实际开发 | hephaestus / worker / quick | 代码实现 | 审核通过 |
| 7 | 测试验证 | 开发 agent + debugger | 编译通过 + 测试结果 | 用户审核通过 |
| 8 | 提交（不 push） | gatekeeper | 本地 commit | 用户审核通过 |
| 9 | 标记完成 | sisyphus | 方案状态「已完成」 | — |

## §2 铁律

1. **每一步都要审核**：完成一步 → 汇报 → 等待审核 → 通过后进入下一步
2. **第 1–5 步禁止改代码**：只做阅读、设计、写文档
3. **第 8 步只 commit 不 push**：等用户决定何时 push
4. **顺序不可跳步**：调整需用户明确同意

## §3 Agent 分工

| Agent | 职责 | 步骤 |
|-------|------|------|
| sisyphus | 拆解/协调/落盘/收尾 | 1, 5, 9 |
| librarian | 文档/API 查询 | 1 |
| prometheus | 配置/数据/协议/方案设计 | 2, 3, 4, 5 |
| hephaestus | 端到端实现 | 6 |
| worker | 明确子任务执行 | 6 |
| reviewer-* | 并行审核 | 6 |
| debugger | 排查失败 | 7 |
| gatekeeper | 提交门禁 | 8 |

## §4 方案文件

- 放 `docs/dev-plans/<功能名>.md`，格式见 `docs/dev-plans/README.md`
- 必须含 **Manager 接口文档**：包级公开 API 签名 + 入参/返回/错误码 + 一句话语义
- 完成后状态改为「已完成」，更新索引表
