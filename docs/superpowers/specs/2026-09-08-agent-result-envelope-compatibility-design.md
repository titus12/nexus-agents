# Agent 结果传输封套兼容与 Schema Bundle 设计

## 1. 背景

Multica 线上 `zhongshu-analyst` Skill 已经要求 Analyst 在所有 mode 下输出统一的
transport envelope，包括 `task_id`、`request_id`、`phase`、`state`、`role`、`mode`、
`structured_output_protocol` 和 `structured_output_schema_hash`。

但最新运行中，`EVIDENCE_PACKET_READY` 的 12 份 `result.json` 都只输出了业务字段和
部分请求信息，缺少整组 envelope；`REQUIREMENT_CONTRACT_READY` 则能输出完整 envelope。
同时 Skill 要求 Agent 读取 `structured-output.json`，而当前 Prompt Bundle 没有下发该文件。

## 2. 目标与非目标

目标：

- 每个 Prompt Bundle 都提供与请求完全一致的 `structured-output.json`，并由 manifest 校验。
- 让旧的、已经写入 request-scoped 目录但缺少 transport 字段的结果可以安全恢复。
- 保留对错误 request、错误 revision、错误 hash 和错误业务字段的拒绝能力。
- 让日志能区分“Agent 原始结果缺字段”和“结果内容/绑定值错误”。

非目标：

- 不放宽业务字段校验，不把自然语言评论当作业务结果。
- 不自动修复 Agent 的业务内容，不覆盖 Agent 原始 `result.json`。
- 不修改 Multica 线上 Skill 文本；已核对的线上 Skill 配置已经包含正确要求。
- 不改变并发调度、quorum 或状态机推进规则。

## 3. 方案决策

### 3.1 Prompt Bundle 提供完整 Schema 文件

`PromptBundleBuilder` 使用当前 `StructuredOutputSpec.to_dict()` 生成
`structured-output.json`，内容包含：

- 当前角色的完整 JSON Schema；
- `schema_hash`、`protocol`、`phase`、`role`、`state`、`role_mode`；
- 当前角色固定字段和允许的 mode。

文件以 UTF-8 无 BOM 原子写入，并作为 required 文件加入 manifest，manifest 记录字节数和
SHA-256。Prompt reference 增加该文件路径，并明确它与 `context.json` 中的 schema 必须一致。
这样 `EVIDENCE_PACKET_READY` 不再依赖 Agent 的旧 mode-specific 示例或隐式默认模板。

### 3.2 仅对 request-scoped 文件恢复做受控传输字段补全

`read_agent_result_file` 增加兼容参数，由 `_recover_result_file` 在扫描当前请求的精确结果路径
时启用。处理顺序：

1. 先验证路径位于允许目录内，并且是当前 `task_id/request_id` 派生的精确路径。
2. 结果必须是合法 UTF-8 JSON 对象。
3. `request_id` 必须存在且与当前请求完全一致；缺失或不一致直接拒绝。
4. 对缺失的不可变传输字段，从当前请求补齐到内存中的 payload：
   `task_id`、`phase`、`state`、`role`、`mode`、`structured_output_protocol`、
   `structured_output_schema_hash`。
5. 这些字段如果存在但不一致，仍然直接拒绝，不能被覆盖。
6. `action` 和所有角色业务字段不补全，继续执行现有结构及业务校验。

补全只发生在内存中，不回写原始文件；payload 增加
`result_transport_backfilled_fields`，并记录 warning，便于追查 Agent/Multica 的旧模板。

### 3.3 诊断日志

新增或统一以下诊断信息：

- bundle 写入：`PROMPT_BUNDLE_FILE_WRITTEN`，标明 `structured-output.json` 的 hash/bytes；
- 兼容恢复：`AGENT_REPLY_FILE_TRANSPORT_BACKFILLED`，记录补全字段；
- 存在值冲突：继续记录明确的 mismatch，不记录为 backfill；
- 业务校验失败：保留原有 `STRUCTURED_ROLE_FIELDS_MISSING` 等错误。

## 4. 数据流

```text
StructuredOutputSpec
      │
      ├── context.json
      ├── structured-output.json  ← 新增，完整 schema/协议快照
      ├── active-skill.md
      └── prompt.txt + manifest.json
              │
              ▼
         Multica Analyst
              │
       result.json + pointer/comment
              │
              ▼
  精确 request-scoped 文件恢复
      │  request_id 校验
      │  缺失 envelope 受控补全
      │  完整 role/schema/业务校验
      ▼
       Analyst fan-in
```

## 5. 安全边界与故障处理

- 不能通过路径、文件名或缺失字段单独证明结果归属；`request_id` 必须存在且精确匹配。
- 不能接受另一个请求、revision 或 schema hash 的结果。
- 不能补全 `action`、`requirements`、`evidence_updates` 等业务字段。
- 兼容补全只用于恢复旧 Agent 输出，不能阻止后续继续修复 Multica/Agent 的真实输出模板。
- 评论仍然只允许合法结果指针；评论为自然语言或非 JSON 时，优先扫描当前请求的结果文件，
  扫描不到或校验失败则按原流程拒绝。

## 6. 验证范围

本次只做静态验证：Python AST 解析、diff whitespace 检查、manifest/schema 代码路径审查。
不启动服务、不执行业务测试、不发送飞书通知。

