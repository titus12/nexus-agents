# requirements-v1 操作说明

本工作流以完整需求正文为核心。Analyst 维护解释与可核验事实，Solver 是正文唯一作者，Critic 独立审查；编排器保存用户来源、决定、问题、预算与批准。需求批准后，门下省接收同一份正文，再建立实施 DAG。

2026-10-07 按用户明确指示完成单一工作流收口：新任务默认 requirements-v1，旧中书执行代码与专用协议已退役。历史任务只读查看、显式迁移；旧批准不继承。真实内容样本、业务评审和飞书往返仍未完成，切换本身不证明品质或效率提升。

2026-10-07 用户明确指定沿用此前同步消息的飞书群，并确认群内只有本人和机器人。新任务默认接受该群内经 Gateway 核验的真人回调，不要求填写负责人 open_id。需要限定指定人员时可选用 `--decision-owner`。

## 创建与观察

本地通信不发飞书通知，适合先验证机制和内容：

```powershell
rtk proxy python -X utf8 cmd/review_orchestrator_v2.py --new "本次需求" --communication local --project <project-id>
rtk proxy python -X utf8 cmd/review_orchestrator_v2.py --inspect-task <task-id>
```

创建命令持续运行以接收回答。另一个终端提交输入时，输入先进入持久 inbox；已有运行进程处理它。进程退出后用 `--resume <task-id>` 继续，不清空决定、答案、正文或预算。`--fast-track` 已删除；旧中书环境开关不能切换回旧流程。

`inspect-task` 只读提供持久快照、完整聚合、当前阶段、outcome、问题与回答、正文 SHA、审查依据、预算和 recovery 解除条件，不启动运行器。`WAITING_CLARIFICATION` 和 `WAITING_CONFIRMATION` 是正常人工等待；`PREREQUISITE_BLOCKED` 需要补前提；`ENGINE_ERROR` 表示契约/机制/预算故障。中书省交接后 outcome 为 `REQUIREMENTS_READY`；后续门下省的阶段结果独立。

## 回答、修改与批准

当前轮次的问题见 `runs/<task-id>/requirements/questions/<round-id>.json`。每页最多 5 个问题，可以部分提交；已提交答案不会因为刷新失败而丢失。问题依赖未完成时，不应提前替用户决定。

回答文件为 UTF-8 JSON；`actor_id` 由 CLI 强制记录为 local-operator，不接受文件伪造飞书身份：

```json
{
  "round_id": "round-000001",
  "answers": [{"question_id": "q-000001", "question_revision": 1,
    "option_ids": ["manual"], "free_text": "", "disposition": "answered"}]
}
```

```powershell
rtk proxy python -X utf8 cmd/review_orchestrator_v2.py --resume <task-id> --requirements-answer answers.json
rtk proxy python -X utf8 cmd/review_orchestrator_v2.py --resume <task-id> --requirements-feedback feedback.md
rtk proxy python -X utf8 cmd/review_orchestrator_v2.py --resume <task-id> --approve-requirements <current-document-sha256>
rtk proxy python -X utf8 cmd/review_orchestrator_v2.py --cancel <task-id>
```

`--answer-file` / `--feedback-file` 是对应别名。回答只解决问题，不代表批准正文。文本回答最多 1000 字；选择“其他”需要解释。非阻断问题可显式 `accept_default` 或 `defer`，系统不会自动接受推荐值。旧卡片提交不同答案会请求确认决定变更，不能覆盖已有决定。

批准必须匹配当前文档 SHA、当前确认 gate 和有效审查。飞书批准还核验云端读回与已收消息屏障；用户反馈、事实来源变化或云端编辑会使批准失效。旧批准不能批准新正文。门下省请求业务变更时，返回澄清与重新审查。

## 飞书 gateway

独立环境固定 Python 3.12，SDK 为 `lark-oapi==1.7.3`；业务核心继续使用原 Python。当前工作区已准备便携 Python 与独立 venv，重建时可用以下命令：

```powershell
rtk proxy uv python install 3.12 --install-dir .tools/requirements-python --no-bin --no-registry --cache-dir .tools/uv-cache --no-config
rtk proxy uv venv --python .tools/requirements-python/cpython-3.12.13-windows-x86_64-none/python.exe .venv-requirements-gateway --cache-dir .tools/uv-cache --no-config
rtk proxy uv pip install --python .venv-requirements-gateway/Scripts/python.exe -r requirements-gateway-lock.txt --cache-dir .tools/uv-cache --no-config
rtk proxy .venv-requirements-gateway/Scripts/python.exe -X utf8 cmd/requirements_card_gateway.py --database runs/requirements-gateway.sqlite --check
rtk proxy .venv-requirements-gateway/Scripts/python.exe -X utf8 cmd/requirements_card_gateway.py --database runs/requirements-gateway.sqlite
```

配置由运行环境提供 `FEISHU_GATE_APP_ID`、`FEISHU_GATE_APP_SECRET`、`HUMAN_GATE_CHAT_ID`；文档 CLI 使用现有独立文档身份。不要将凭据写入任务文件、日志或 Git。Gateway 与任务必须指向相同 runs-root 中的 SQLite 文件。官方应用需要开启长连接，并订阅 `card.action.trigger`；卡片机器人需要发送/读取/更新交互消息权限，文档身份需要创建、读取与分享目标文档权限。

`--check` 只检查 SDK、DB、进程锁、连接心跳和凭据是否存在，不连接服务；没有实际连接时返回非零。它不能证明订阅权限有效。上线前在用户指定的现有群进行选择、多选、自由文本、部分回答、重复提交、越权拒绝、反馈、批准、取消与旧卡片测试。实际 gateway 连接和发卡结果以验证记录为准。

```powershell
rtk proxy python -X utf8 cmd/review_orchestrator_v2.py --new "本次需求" --communication feishu --project <project-id>
```

`--decision-owner` 为可选项，可重复指定。未指定时，新任务采用持久化的 `chat-members` 授权，受理当前群内通过 app、gate token、page 和消息回执核验的真人操作；无需额外成员查询权限。指定时仍只受理指定人员。历史任务保持已保存的权限策略。ready gateway 缺失时启动检查拒绝创建远端任务。Windows `Start-Process` 必须加 `-WindowStyle Hidden`。不停止当前 Nexus 的 8766 服务，HTTP 验证使用 18766。

Gateway 在返回成功 toast 前，先持久保存经过 app、chat、owner、gate token、page 校验的回调。domain commit 与 inbox ACK 分离且幂等。模型正在运行时，gateway 仍可收输入；下一周期先处理人类消息，再应用模型结果。早到回调在回执核验前不会丢弃。

未知卡片发送结果会保留 `OUTCOME_UNKNOWN`，不能盲目创建第二张卡片。核对实际消息后按下节绑定回执。已批准输入之后的反馈保存为 follow-up，不改已交接正文。

## 前提恢复

无内容 `resume` 不解除需求阻塞。恢复只能使用两种可核验输入：

```json
{"kind":"prerequisite_changed","source_ref":"communication",
 "expected_old_fingerprint":"失败记录中的指纹","new_fingerprint":"修复后实际指纹"}
```

```json
{"kind":"bind_card_receipt","gate_id":"当前 gate",
 "page_id":"review","message_id":"实际消息 ID"}
```

```powershell
rtk proxy python -X utf8 cmd/review_orchestrator_v2.py --resume <task-id> --recovery-note recovery.json
```

前提变更必须匹配记录中的 source_ref 与旧指纹，并由 runtime 重读核验变化；任意修改另一个文件或写“已修复”不算证据。`runtime-code` 表示需求实现与协议代码的指纹，`communication` 表示不含 secret 的通信配置与 ready 状态，`cloud:<version>:<document-id>` 表示实际云端正文。绑定回执必须经消息 GET 核对发送者、chat、gate token 和 page。

## 产物、预算与回退

送审在 `requirements/drafts/`，批准后在 `requirements/approved/`，分别不可变。最终目录包含同一份 `requirements.md`、共识、追踪、审查、批准和 manifest；未批准目录不生成 approval.json。handoff 保存完整正文及批准依据。相同逻辑重放保持字节不变，同路径不同内容拒绝覆盖。

每个机器工作周期最多 12 次派发、3 次语义修复、2 次补证；单派发最多 2 次格式修复和 2 次外部重试，单发布最多 2 次重试。两次无有效进展停止。相同答案、单纯重启不刷新预算；有效新用户输入或已核验前提变化开启新周期，累计调用保持。

```powershell
rtk proxy python -X utf8 cmd/review_orchestrator_v2.py --inspect-task <historical-task-id>
rtk proxy python -X utf8 cmd/review_orchestrator_v2.py --migrate-task <historical-task-id> --communication local
rtk proxy python -X utf8 cmd/review_orchestrator_v2.py --resume <existing-task-id>
rtk proxy python -X utf8 cmd/run_tests.py
```

历史 resume 会拒绝旧执行并提示迁移。迁移要求旧任务没有活动运行锁，保留原快照、用户请求与明确反馈，生成新任务，不派发模型，不继承旧计划或批准。不要直接修改旧 snapshot 的 workflow。旧专用模块与测试按原字节归档为 .txt 并登记 SHA-256，不参与执行或测试发现。canonical model/compiler/render 与历史 DTO 仅保留共享数据能力。

## 品质与效率验收

脚本 adapter 验证机制，不消耗模型 token。三类 fixture 各两份真实模型样本独立执行；使用 local 通信，停在确认等待，不自动批准。模糊目标的回答使用 case.json 的用户语义，按实际问题绑定，不能把缺少的答案编出来。

```powershell
rtk proxy python -X utf8 cmd/sample_requirements_workflow.py --case singleflight --sample 1
rtk proxy python -X utf8 cmd/sample_requirements_workflow.py --case vague-goal --sample 1 --answer-file fixture-answers.json
rtk proxy python -X utf8 cmd/evaluate_requirements_workflow.py --fixtures cmd/fixtures/requirements --results runs/requirements-quality-20261007 --offline --output runs/requirements-quality-report.json
```

结果容器为 `<case>/run-N/<task-id>/`。人工评审记录放在任务目录的 `manual-review.json`，格式见 fixture README。五轴各至少 3/4、没有关键缺陷、Critic 通过、业务 reviewer 确认后才 PASS。缺六份样本或缺评审返回 INCOMPLETE；`--mechanisms-only` 不声明内容质量。

指标从 journal 持久时间戳与实际 effect dispatch 提取：首份完整草案、首次合格送审、机器 effect 时间、已经结束的人工等待、角色调用、澄清、重试、门下退回与 bundle bytes。当前 provider usage 未持久提供时为 null，不能把 bytes 换算成实测 token。没有相同输入、模型与里程碑的旧归档，不宣称收益百分比。

## 与旧版的职责和机制差异

| 维度 | 旧版中书省 | 当前唯一新版 | 改善依据 |
|---|---|---|---|
| Analyst | 建立需求契约，再按视角/任务补证 | 解释用户意图、区分原话/假设/待定项，维护可核验事实；按缺口定向补证 | 把用户决定与模型推断分开，减少重复分析 |
| Solver | 建立任务图、组需求文档，再按问题批次修订 | 完整需求正文唯一作者，只按有效决定与审查缺陷更新正文 | 单一正文减少结构与文档同时变更 |
| Critic | 任务/组级审查、投票、冻结检查 | 独立审查需求正文，维护稳定缺陷与审查覆盖；区分补证、改正文、问用户 | 修复路由明确，已通过结论按依据复用 |
| 编排器 | 控制组队列、修订轮次与 freeze | 持久保存来源、决定、问题、预算、缺陷与批准；机械校验后进入确认 | 授权与流程状态有单一归属 |
| 澄清 | 人工 gate 后以文本恢复修订 | 批量问题、部分回答、依赖、旧回答变更确认 | 减少来回问答，决定不因重启丢失 |
| 审查返工 | 围绕任务/组反复返工 | 纯事实补证可走 Critic→Analyst→Critic，正文不变；内容缺陷交 Solver | 有机会减少全量重写，收益待真实样本测量 |
| 批准 | 旧方案 gate 与组冻结 | 当前正文摘要、当前 gate、有效审查及来源验证绑定批准 | 旧回复、旧版本不能批准新正文 |
| 收敛 | 多层组/任务修订预算、冻结 | 机器周期调用/修复/补证有界，两次无有效进展停止 | 停滞可解释，避免持续消耗 |
| 门下交接 | 中书已经建立实施任务图 | 中书交完整批准需求，门下再建立实施 DAG 与实施文档 | 需求承诺与实施细化各有归属 |
| 运行版本 | 可选旧流程与试用新版 | 只有 requirements-v1；历史数据只读与显式迁移 | 一套中书执行代码与一套协议 |

以上是设计与脚本机制层面的比较；稳定内容品质、实际耗时和 token 收益须由真实样本与人工评审确认。
# 批量问答与进度展示补充（2026-10-07）

首轮分析中已知但未关联问题的阻塞假设，会在同批卡片中补齐确认问题；问题按主题归并，保留依赖与分页。卡片显示整批问题数、已答数，部分提交保存后等待剩余问题。后续补问的角色协议要求说明新增依据。

启动及每个推进事件分别发送独立进度消息，已发送的进度消息不再 PATCH。发送回执与平台 UUID 按原事件键持久化，重启或重复消费同一事件复用原消息；发送结果未知时拒绝盲目重发。历史阶段补发读取工作流日志中的真实时间。问答卡片仍更新回答状态，审批和故障通知单独发布。机制与真实消息验收见 [进度历史验收](progress-history-validation-20261007.md)。

