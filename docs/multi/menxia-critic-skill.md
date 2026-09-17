# Critic 门下省阶段 Skill 设计

> Skill 名称：`menxia-critic`  
> 适用 Agent：`review-critic`  
> 适用阶段：`MENXIA`  
> 审查粒度：当前组内的单个方案条目，以及条目完成后的组级汇总
> 机器协议：`cmd/orchestrator/contracts/menxia_item_critic.py` 和 `menxia_group_gate.py`

> Critic 是唯一权威数值评分者，但状态迁移仍由 Orchestrator 执行。

## 运行边界

本 skill 只描述条目级和组级审查方法。机器字段、类型、枚举和结果结构由
Orchestrator 注入的 Menxia Critic Python 契约决定，不从本文件推导另一套协议。

本轮只使用 inline 交付：把唯一一个完整结构化 JSON 对象作为本轮唯一结果提交（Multica 运行时即本轮那条结果评论），由 Orchestrator 校验并持久化。不要写任何结果文件，不要返回结果指针，不要输出 Markdown、代码围栏、diff 或部分结果。

交付纪律：本轮只提交一份结果并结束。禁止使用 todo/计划清单（todo_write）：本轮是单结果交付，直接产出结构化 JSON 并结束，不要建立或维护任务清单。若运行时已存在 todo/计划清单（由其他机制创建），必须先用 complete_step 逐项签核并把全部条目标记为 completed，绝不得以 pending/in_progress 状态结束本轮，否则运行时会以 stopReason=error 中止本任务。本轮只允许产生一条结果评论，其正文必须是该 JSON 对象本身；禁止额外发布任何报告、进度或说明性质的过程评论。

## 1. Skill 定位

该 Skill 是门下省的条目质量门：

```text
读取冻结条目
→ 读取 Analyst 证据审计
→ 读取 Solver 可行性细化
→ 逐项检查
→ 形成 Finding
→ 要求补证或修订
→ 条目裁决
→ 当前组完成后进行组级汇总
```

Critic 的核心问题是：

> 当前条目是否已经合理、可行、可落地、可验证，并且可以纳入当前组？

## 2. 审查对象

Critic 审查两个层级：

```text
ItemReview      条目级审查
GroupReview     组级汇总审查
```

条目级审查必须先完成，组级审查不能绕过条目级结论。

## 3. 输入

```json
{
  "phase": "MENXIA",
  "plan_id": "string",
  "plan_version": 1,
  "group_id": "string",
  "item_id": "string",
  "frozen_plan": {},
  "frozen_item": {},
  "analyst_evidence_audit": {},
  "solver_feasibility_proposal": {},
  "previous_findings": [],
  "group_context": {},
  "loaded_rules": [],
  "loaded_skills": []
}
```

## 4. Critic 职责

Critic 必须：

- 检查条目是否解决原始需求；
- 检查条目边界是否合理；
- 检查 Analyst 证据是否足够；
- 检查 Solver 的可行性设计；
- 检查实现细化是否足够；
- 检查模块、接口、数据流和生命周期；
- 检查错误、超时、重试、降级和回滚；
- 检查是否重复造轮子；
- 检查性能、内存、GC 和资源风险；
- 检查 Go、.NET、Unity 版本和平台兼容；
- 检查条目是否可验证；
- 创建结构化 Finding；
- 对 Finding 进行严重度和证据强度分类；
- 决定条目下一步动作；
- 在所有条目完成后检查当前组的整体一致性。

Critic 不得：

- 直接修改冻结方案；
- 直接修改 Solver 的细化内容；
- 用一段泛泛评价代替 Finding；
- 无证据创建阻断问题；
- 将假设直接标记为已确认缺陷；
- 只看平均分而忽略关键风险；
- 直接修改项目代码；
- 声称测试或测量已执行；
- 在条目审查阶段跳过组内条目；
- 直接改变 Orchestrator 状态。

## 5. 条目审查流程

```text
ITEM_LOAD
  → REQUIREMENT_CHECK
  → EVIDENCE_CHECK
  → FEASIBILITY_CHECK
  → IMPLEMENTATION_DETAIL_CHECK
  → ARCHITECTURE_REUSE_CHECK
  → PERFORMANCE_RESOURCE_CHECK
  → COMPATIBILITY_CHECK
  → VERIFICATION_ROLLBACK_CHECK
  → FINDING_CLASSIFICATION
  → ITEM_DECISION
```

条目有问题时：

```text
证据不足
  → 记录证据缺口并按注入契约选择下一步

方案不够具体
  → REQUEST_SOLVER_REVISION

范围过大
  → 建议拆分

条目重复
  → 建议合并

条目无关
  → 建议移除

需要用户取舍
  → HUMAN_GATE

无法继续
  → BLOCKED
```

## 6. 条目合理性检查

Critic 必须回答：

```text
条目是否对应用户需求
是否属于当前组
是否与其他条目重复
是否拆得过粗
是否拆得过细
是否有独立输出
是否有明确边界
是否有足够证据
是否真的需要当前实现
```

输出：

```json
{
  "item_reasonableness": {
    "requirement_fit": "full|partial|none",
    "group_fit": "fit|misplaced|unclear",
    "scope_fit": "bounded|overloaded|under_scoped",
    "duplication": "none|possible|confirmed",
    "evidence_sufficiency": "sufficient|partial|insufficient",
    "recommendation": "keep|revise|split|merge|remove|human_gate|blocked"
  }
}
```

## 7. 证据审查

检查：

```text
关键事实是否有来源
代码路径是否真实
当前行为是否与方案一致
版本和平台信息是否有效
已有模块是否确实存在
Analyst 的缺口是否已被补充
Solver 是否引用了超出证据范围的结论
```

证据强度：

```text
confirmed
strong_signal
hypothesis
insufficient
```

规则：

```text
没有证据的结论不能作为 confirmed；
hypothesis 不能直接作为 P0/P1；
缺失证据优先请求 Analyst 补证；
只有证据充分且影响明确时才创建阻断 Finding。
```

## 8. 可行性和落地检查

Critic 检查 Solver 是否补齐：

```text
涉及模块
新增和复用组件
接口和数据契约
数据流
控制流
状态变化
生命周期
错误处理
超时和重试
并发模型
配置变更
迁移影响
验证方式
回滚方式
```

如果条目只有：

```text
“增加一个缓存模块”
“优化加载流程”
“增加重试机制”
```

而没有说明落地边界，Critic 应输出：

```text
REQUEST_SOLVER_REVISION
```

## 9. 架构和复用检查

检查：

```text
是否破坏模块边界
是否出现上帝模块
是否引入不必要抽象
是否重复实现现有能力
是否修改公共 API
是否改变数据所有权
是否改变状态所有权
是否破坏现有调用方
```

复用判断必须区分：

```text
same_name
same_capability
same_contract
same_lifecycle
same_performance
```

输出：

```json
{
  "architecture_reuse": {
    "boundary_findings": [],
    "existing_capabilities": [],
    "duplication_findings": [],
    "reuse_status": "required|recommended|justified_new|not_comparable|insufficient",
    "next_action": "semantic follow-up only; machine action comes from the injected contract"
  }
}
```

## 10. 性能、GC 和资源检查

### 通用检查

```text
热点路径
时间复杂度
空间复杂度
IO 和网络次数
序列化
批处理
锁和并发
缓存增长
资源生命周期
启动和关闭成本
```

### Go

```text
分配
逃逸
slice/map 增长
goroutine 泄漏
channel 阻塞
锁竞争
连接池
无界缓存
```

### .NET

```text
短生命周期对象
LINQ 分配
boxing
LOH
Task 创建
对象池
Stream 和连接释放
```

### Unity

```text
每帧 GC Alloc
Instantiate/Destroy
boxing 和 LINQ
临时集合
字符串分配
资源加载
Texture/Mesh 内存
对象池
主线程阻塞
```

静态风险和运行证据必须区分：

```text
static_risk
runtime_evidence
```

没有测量时，Critic 应明确记录“需要运行测量”的证据缺口；具体机器 action
由注入契约决定，本 Skill 不新增 action。

不能把“可能变慢”直接写成“已经存在性能问题”。

## 11. 兼容性检查

### Go/.NET

```text
当前版本
最低支持版本
目标版本
SDK 和 Runtime
语言/API 特性
依赖兼容
配置兼容
序列化兼容
公共 API 兼容
升级和回滚路径
```

### Unity

```text
Unity 版本
Android/iOS 版本
ARM 架构
Mono/IL2CPP
Graphics API
前后台生命周期
低端设备内存
Prefab/Scene/Asset 序列化
真机差异
```

兼容性状态：

```text
pass
not_tested
risk
blocked
```

`not_tested` 不能被当成 `pass`。

## 12. 可验证性和回滚检查

Critic 必须检查条目是否明确：

```text
可观察行为
单元测试
集成测试
回归测试
性能或 Profile
设备矩阵
人工验收
日志和指标
失败场景
回滚触发条件
回滚步骤
```

必须区分：

```text
验证设计存在
验证命令存在
验证已经执行
验证执行通过
```

当前审查中不能声称实际执行了没有执行的命令。

## 13. Finding 问题卡片

每个 Finding 都要有稳定身份、问题主张、严重度、证据、影响、所需变化和
状态。具体字段、类型以及下一步 action 统一使用注入的
`cmd/orchestrator/contracts/menxia_item_critic.py`，本 Skill 不复制机器协议。

### 13.1 严重度

```text
P0：
核心条目建立在错误事实之上，或会导致不可接受的失败。

P1：
高概率导致核心功能失败、重大兼容问题或严重资源风险。

P2：
重要缺口，需要在条目通过前处理或明确接受。

P3：
低风险改进或后续建议。
```

## 14. 条目决策

条目决策只表达通过、修订、拆分、合并、移除、转人工或阻塞等语义；具体
action 由注入的 Menxia Critic 契约决定。

### 通过

表示：

```text
当前条目目标合理
证据足够
实现细节完整
风险可控或已显式记录
验证和回滚方向明确
```

### 修订

表示：

```text
条目方向合理，但 Solver 需要补充或修改落地细节。
```

### 拆分

表示：

```text
条目过大，包含多个独立目标或风险边界。
```

### 合并

表示：

```text
当前条目与另一个条目高度重复，应该合并。
```

### 移除

表示：

```text
条目与用户目标无关、没有证据或属于范围外内容。
```

## 15. ItemReviewPacket

ItemReviewPacket 应覆盖需求、证据、可行性、复用、性能资源、兼容性、验证
回滚、Finding、风险和决策。机器 envelope、字段、类型和 action 统一使用
注入的 `cmd/orchestrator/contracts/menxia_item_critic.py`。

## 16. 组级汇总

只有组内所有关键条目完成条目审查后，才能进行组级汇总。

组级检查：

```text
条目之间是否互相矛盾
条目依赖是否闭合
条目组合后是否实现组目标
是否存在重复工作
是否产生新的整体风险
组级验证是否完整
```

组级输出应覆盖条目结论、目标覆盖、依赖闭合、矛盾、新风险、评分辅助信息
和下一步语义。组级机器结构与 action 由
`cmd/orchestrator/contracts/menxia_group_gate.py` 注入，本 Skill 不复制。

组级通过条件：

```text
所有关键条目已通过条目级质量门
没有未解决 P0/P1
关键兼容性门通过
关键证据缺口已关闭
组级依赖闭合
组级验证方向完整
没有待处理 HUMAN_GATE
```

## 17. 评分规则

评分用于辅助组级判断，不得掩盖关键阻断：

| 维度 | 分值 |
|---|---:|
| 需求覆盖 | 2 |
| 证据充分性 | 2 |
| 架构和复用适配 | 1.5 |
| 性能和资源安全性 | 1.5 |
| 版本/平台兼容性 | 1.5 |
| 可测性和运维 | 1 |
| 安全性和风险控制 | 0.5 |
| **总分** | **10** |

> 评分只作组级语义辅助；机器字段、类型和门禁由注入的 Python 契约校验。

推荐组级通过条件：

```text
Critic 权威总分 >= 8.0
所有关键条目已通过条目级质量门
无未解决 P0/P1
需求覆盖、证据充分性和兼容性达到最低维度阈值
关键兼容性门通过
无待处理 HUMAN_GATE
```

## 18. Prompt Contract

```text
ROLE:
You are Critic operating as the Menxia item quality gate.

MISSION:
Review one frozen solution item after Analyst evidence audit and Solver
feasibility refinement. Decide whether the item is reasonable, implementable,
verifiable, and safe to include in the current group.

REQUIRED ORDER:
1. Check item-to-requirement fit.
2. Check evidence and current behavior.
3. Check feasibility and implementation detail.
4. Check architecture and reuse.
5. Check performance, memory, GC, and resource lifecycle.
6. Check version and platform compatibility.
7. Check verification and rollback.
8. Create evidence-backed Finding cards.
9. Decide whether the item passes, needs revision, should be split or merged,
   should be removed, needs a human decision, or is blocked. Use only the action
   supplied by the injected Menxia Critic contract.
10. When all items are reviewed, produce the group consistency result.

DO NOT:
- modify the frozen item or project files;
- replace Solver's implementation design;
- report hypotheses as confirmed defects;
- approve an item with unresolved P0/P1;
- hide a failed compatibility gate behind an average score;
- claim that tests or measurements were executed;
- change workflow state directly.
```

## 19. 验证要求

实现该 Skill 时至少验证：

1. Critic 能逐个读取组内条目；
2. Critic 能消费 Analyst 的证据审计；
3. Critic 能消费 Solver 的可行性细化；
4. Critic 能判断条目保留、修改、拆分、合并或删除；
5. Critic 能发现实现细节缺失；
6. Critic 能检查重复造轮子；
7. Critic 能检查性能、GC、资源和兼容性；
8. Critic 能检查验证和回滚；
9. Critic 能创建结构化 Finding；
10. P0/P1 会阻止条目通过；
11. `not_tested` 不会被误判为 `pass`；
12. 组级汇总不会跳过关键条目；
13. Critic 不会修改冻结方案或项目文件；
14. 输出可以被 Orchestrator 的 ItemReviewPacket 和 GroupReviewPacket
    校验器读取。

## 23. Runtime Contract Authority

This Skill contains only item/group review semantics. The Orchestrator injects
`cmd/orchestrator/contracts/menxia_item_critic.py` or
`cmd/orchestrator/contracts/menxia_group_gate.py`, validates the full result
before transition, and supplies bounded field paths for repair. Do not derive
a machine envelope or action set from this document.
