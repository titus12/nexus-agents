# 中书省效率优化与协议对齐：2026-10-06

后续真实测试发现本批新增的组审身份校验与现有契约冲突，导致合法批准被误拒。
已修正并补充真实 schema 到 join 的测试；详见
[本轮兼容性回归记录](zhongshu-group-identity-repair-20261006.md)。下面的 1314 项通过为当时验证记录，不能据此认为已经覆盖全部真实输出路径。

本批针对 `task-20261006-18194e` 中已证明的证据丢失与重复执行机制。保存有效工作，不减少审查标准。

| 问题 | 修复 | 品质保护 |
| --- | --- | --- |
| Analyst 补证包覆盖旧证据 | 同一权威需求契约下增量合并；持久化历史与单轮新增预算分离 | 契约变化新建证据包；不改变正式任务图；门下省直接包不走中书合并 |
| 相同 evidence_id 被当作不同事实 | 按 evidence_id 加 finding/item/requirement 作用域合并；新证据 ID 使用 worker 前缀 | 保留来源和 observed_variants；Critic 切片显示至多三条冲突摘要及总数，完整观测保存在证据包 |
| 单个 Analyst 需求 ID 错误导致整批重跑 | 契约模式先逐 worker 验证，保存有效同伴，逐 worker 反馈错误；接入既有只重调缺失 worker 的机制 | 不放宽需求 ID 校验；合法 BLOCKED/HUMAN_GATE 保留原优先级；旧协议保留原错误分类 |
| Critic 失败波次丢弃有效批准 | 失败汇聚保存通过身份、组归属、finding 归属校验的分组结果；已有批准进入既有 ratchet | 整轮仍为 FAIL，不假装完成；表面改变会重新审；P0/P1、文档、依赖与冻结门槛继续检查 |
| 修格式重新做调查 | 修复提示带有界失败草稿及原始草稿路径/摘要，明确只修所报缺陷 | 草稿是结果数据，不能覆盖当前指令；重试次数不增加；完整结构化结果仍须通过当前契约 |

范围边界：本批复用的是通过机械验证的分组批准。`REVISE_GROUP` 保存其发现但不会冒充批准，也没有新建跨阶段并行 FSM。进一步让一组补证与另一组 Solver 修订同时进行属于后续机制设计，不能把其潜在收益算作本批已实现收益。

人工只需审一份权威、带版本与哈希的需求文档。精确回复“通过”才进入门下省；其它非空反馈由 Solver 修订、机械校验后再次人工审查，不增派 Critic。以上既有行为维持。

## 日志离线重放

读取保存的 sequence 3 与 6 证据包。原包 15 条、补证包 12 条；旧机制丢失了 13 个旧“ID＋作用域”记录。新机制保存 25 条唯一作用域记录，所有旧证据及新增证据均可追溯，缺失数量为 0，包版本递增到 2。

证据：`runs/maintenance/20261006-evidence-efficiency/replay-verification.json`。
重放仅写自己的维护报告；活跃任务的 state 和 events 文件在重放期间哈希一致，没有回写生产状态。

## Multica 对齐

三 agent：Analyst-reasonix-v4、Solver-opencode-v4、Critic-codex-luna。
更新三份 Zhongshu skill 及三个共享 agent 指令；回读同时核对三份 Menxia skill 和附属文件。

`runs/maintenance/20261006-evidence-efficiency/multica-sync/verification.json`：
`ok=true`；三 agent 的 `drift=[]`；三份 Menxia skill 与本地一致；模型、runtime、配置、绑定、Menxia 附件及公共语言 skill 未变。

协议核对：当前 dispatch 的 phase/mode/schema 为权威；正常交付唯一完整 inline JSON；结果文件只用于明确的平台输出超限兜底。没有引入另一套交付协议。Zhongshu 的批准/补证规则不覆盖 Menxia 的 group 文档协议。

## 验证与生效

新增八个回归用例覆盖证据累积、冲突可见性、契约变化、阶段隔离、Analyst 错误同伴保存、分组错误哈希拒收、批准折叠及表面变化重审、草稿修复预算。完整安全测试包含真实 OrchestratorApp 脚本适配器流程与技能绑定测试；结果保存于 `runs/maintenance/20261006-evidence-efficiency/full-tests.log`。

最终全量 **1314 项测试全部通过，48.229 秒**。`git diff --check` 通过。未启动真实模型测试，也未发送测试通知。

Multica 静态内容已发布，对下一次 dispatch 生效；已创建的 prompt bundle 固定。运行中的 Python 进程不会因为磁盘代码修改自动获得新机制，需要在当前测试安全结束后按正常流程重新加载 Orchestrator。此次没有重启 8766，没有取消、恢复或迁移当前任务。

时间目标：前文的 50–60 分钟与 23–36% 缩短是基于已消耗约 78 分钟的失败轮次作出的模型估算，原轮次当时尚未完成，不能视作完整轮次基准，更不是本批实测结果。当前已验证机制与品质门槛；真实模型耗时须在加载新代码后的同类新轮次测量，人工等待单独计算。
