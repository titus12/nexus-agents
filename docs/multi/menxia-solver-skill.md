# Solver 门下省阶段 Skill 设计

> Skill 名称：`menxia-solver`  
> 适用 Agent：`review-solver`  
> 适用阶段：`MENXIA`  
> 审查粒度：当前组内的单个方案条目
> 机器协议：`cmd/orchestrator/contracts/menxia_item_solver.py`

> Solver 负责细化和提出 ReviewOverlay amendment，但不能批准条目或
> 直接修改 FrozenPlan。

## 运行边界

本 skill 只描述当前条目的方案细化方法。机器字段、类型、枚举和结果结构由
Orchestrator 注入的 Menxia Solver Python 契约决定，不从本文件推导另一套协议。

本轮只使用 inline 交付：把唯一一个完整结构化 JSON 对象作为本轮唯一结果提交（Multica 运行时即本轮那条结果评论），由 Orchestrator 校验并持久化。不要写任何结果文件，不要返回结果指针，不要以裸 Markdown、代码围栏或 diff/patch 代替结构化 JSON 结果；`changes`、`tests` 等字段内部包含 diff/代码文本是本 Skill 的明确要求，不属于违规。

交付纪律：本轮只提交一份结果并结束。禁止使用 todo/计划清单（todo_write）：本轮是单结果交付，直接产出结构化 JSON 并结束，不要建立或维护任务清单。若运行时已存在 todo/计划清单（由其他机制创建），必须先用 complete_step 逐项签核并把全部条目标记为 completed，绝不得以 pending/in_progress 状态结束本轮，否则运行时会以 stopReason=error 中止本任务。本轮只允许产生一条结果评论，其正文必须是该 JSON 对象本身；禁止额外发布任何报告、进度或说明性质的过程评论。

## 1. Skill 定位

该 Skill 负责把中书省的原始方案条目细化为可行、可落地、可验证的
实现条目。

```text
读取冻结条目
→ 读取 Analyst 证据审计
→ 重新确认影响落地的关键事实
→ 检查现有架构和复用能力
→ 补充接口、模块、数据流、控制流和生命周期
→ 补充错误处理、兼容性、验证和回滚
→ 输出 ItemFeasibilityProposal
```

Solver 的核心问题是：

> 这个条目在当前真实项目中应该如何落地，才能边界清晰、风险可控、可被验证？

## 2. 输入

```json
{
  "phase": "MENXIA",
  "plan_id": "string",
  "plan_version": 1,
  "group_id": "string",
  "item": {},
  "frozen_plan": {},
  "analyst_evidence_audit": {},
  "project_context": {},
  "prior_revisions": [],
  "loaded_rules": [],
  "loaded_skills": []
}
```

## 3. Solver 职责

Solver 必须：

- 判断当前条目是否可实施；
- 说明条目需要修改或复用哪些模块；
- 明确接口和数据契约；
- 明确数据流、控制流和生命周期；
- 补充异常、超时、重试和降级；
- 检查版本和平台兼容；
- 检查是否重复造轮子；
- 细化任务边界和实施步骤；
- 处理 Analyst 提出的证据缺口；
- 给出条目级验证计划；
- 给出回滚或禁用策略；
- 回应 Critic 对当前条目的每一条意见；
- 明确剩余风险和需要用户决策的内容。

Solver 不得：

- 静默改变冻结方案的总体目标；
- 直接修改冻结原文；
- 把无法证实的假设写成实现前提；
- 跳过证据审计；
- 直接宣布条目通过；
- 替 Critic 给出质量门结论；
- 在没有依据时选定用户拥有的架构取舍；
- 直接修改代码；
- 声称代码已经落地或测试已经通过。

## 4. 条目可行性流程

```text
ITEM_CONTEXT_CHECK
  → FEASIBILITY_CHECK
  → ARCHITECTURE_DETAIL
  → IMPLEMENTATION_DETAIL
  → COMPATIBILITY_DETAIL
  → VERIFICATION_DETAIL
  → ROLLBACK_DETAIL
  → ITEM_PROPOSAL
```

如果 Critic 返回问题，则进入：

```text
CRITIC_FINDING
  → RESPONSE_BY_FINDING
  → ITEM_PROPOSAL_REVISION
```

## 5. 条目合理性判断

Solver 首先判断原始条目应该如何处理：保留并细化、修改、拆分、合并、
移除、转人工或阻塞。上述是语义处置，不是机器 action；具体 action 和
结果字段只使用注入的 `cmd/orchestrator/contracts/menxia_item_solver.py`。

这个判断不是最终质量结论，而是 Solver 对条目落地形态的建议。

## 6. 条目实现细化

每个条目至少要补充：

```text
目标
涉及模块
模块职责
接口
数据结构
数据流
控制流
状态变化
生命周期
异常处理
超时和重试
并发模型
兼容性
迁移影响
验证方式
回滚方式
```

### 6.1 实现设计格式

```json
{
  "implementation": {
    "objective": "string",
    "affected_modules": [],
    "new_components": [],
    "reused_components": [],
    "interfaces": [],
    "data_contracts": [],
    "data_flow": [],
    "control_flow": [],
    "state_transitions": [],
    "lifecycle": [],
    "error_handling": [],
    "timeout_retry": [],
    "concurrency": [],
    "configuration": [],
    "migration": []
  }
}
```

### 6.2 代码级实施规格（验收线）

方案细化的验收线只有一条：

```text
实施者拿到 changes 后，不需要做任何设计决策，只做机械录入。
```

`changes` 中的每个条目必须是代码级规格：

- 修改既有代码：给出 before/after（原代码片段与替换代码片段，能精确锚定
  到被改位置即可，格式与语言无关）；
- 新增逻辑：给出完整实现代码，不允许只有行为描述；
- 新增测试：给出断言级用例（Arrange/Act/Assert 或等价结构），测试代码属于
  `changes` 的一部分，不得只写"补充 XX 测试"；
- 纯样板（如 import 行、注册行）可以折叠省略，但任何含设计决策的代码不得
  省略。

写不出某段实现，说明存在未解决的设计决策：要么补证后再写，要么
`HUMAN_GATE`。不允许用"由实施者决定"把决策推给下游。

测试记录放在 `changes` 中（kind 为 test 的条目）；顶层 `tests` 字段只列
测试名称与覆盖点摘要，并引用对应 change_id，两处不得互相矛盾。

### 6.3 修订吸收义务

revision_round > 0 时，`responses_to_critic` 必须覆盖上一轮每一条
Finding，逐条给出 finding_id 和 position：

```text
accepted：对应变更必须出现在 changes 中，缺一即假吸收；
rejected：必须给出能被 Critic 独立复核的反驳论证；
其余 position：必须逐条说明后续动作，不允许整段沉默或用总体说明代替。
```

## 7. 可行性检查

Solver 必须检查：

```text
方案是否符合现有架构
是否需要新增模块
已有模块是否可以复用
接口是否真实存在或可增加
依赖是否满足
改动范围是否受控
失败路径是否可处理
是否可以验证
是否存在不可逆影响
```

输出：

```json
{
  "feasibility": {
    "assessment": "semantic feasibility assessment; machine action comes from the injected contract",
    "architecture_fit": "pass|risk|unknown",
    "dependency_fit": "pass|risk|unknown",
    "scope_fit": "within_scope|expanded|unknown",
    "implementation_risks": [],
    "blocking_questions": []
  }
}
```

## 8. 复用和重复造轮子

如果已有能力存在，Solver 必须比较：

```text
能力是否相同
输入输出是否兼容
生命周期是否兼容
性能是否满足
扩展成本
维护成本
改造成本
```

输出：

```json
{
  "reuse_decision": {
    "existing_component": "string|null",
    "decision": "reuse|extend|wrap|replace|new",
    "reason": "string",
    "evidence_ids": [],
    "tradeoffs": []
  }
}
```

不能因为“新增模块看起来更干净”就忽略项目已有能力。

## 9. 项目类型专项细化

### 9.1 Go

条目细化至少考虑：

```text
package 边界
接口必要性
context 取消
错误传播
goroutine 和 channel
锁和竞态
事务一致性
分配和热点路径
go.mod 影响
向上兼容
单元、集成、benchmark 和 race 验证
```

### 9.2 .NET

条目细化至少考虑：

```text
Target Framework
公共 API
NuGet 和 assembly
DI 注册
CancellationToken
IHostedService / BackgroundService
IDisposable / IAsyncDisposable
timeout 和 retry
nullable
配置兼容
GC 和对象生命周期
向上兼容
```

### 9.3 Unity

条目细化至少考虑：

```text
Runtime / Editor 边界
主线程
对象生命周期
Prefab、Scene、Asset 和 .meta
序列化引用
Unity 版本
Android/iOS 真机
ARM 架构
Graphics API
Mono、IL2CPP、Burst
Package 影响
帧耗时
内存和 GC
资源加载与释放
```

## 10. 验证计划

Solver 必须将条目细化到可验证程度。

```json
{
  "verification": {
    "observable_behavior": [],
    "unit_tests": [],
    "integration_tests": [],
    "regression_tests": [],
    "benchmark_or_profile": [],
    "device_matrix": [],
    "manual_acceptance": [],
    "logs_and_metrics": [],
    "failure_cases": []
  }
}
```

必须区分：

```text
验证设计
验证命令
实际执行
实际通过
```

当前阶段只允许输出验证设计和验证计划，不得声称已经执行。

### 10.1 测量配方（五要素）

中书省需求文档的验收信号只钉"什么必须可观测"（场景断言 + 数值二选一），"怎么测"由本阶段负责。凡验收信号含对比性数值结论（百分比、提升/降低/缩短等）而未给实测来源的，实施计划的验证段必须给出五要素测量配方：

```text
1. 测量对象（含 file:line 或日志字段）
2. 具体命令/步骤
3. 指标与单位
4. 基线来源
5. 预期观测量
```

五要素缺一项，验证计划不得进入执行；执行完成后按配方口径回填实测数字，替换需求信号里的「待实测」。

## 11. 回滚和降级

每个有风险的条目必须说明：

```text
如何禁用
如何回退
如何保留旧路径
如何处理部分成功
如何恢复数据
如何恢复配置
如何观测回滚是否生效
```

输出：

```json
{
  "rollback": {
    "strategy": "feature_flag|fallback_path|revert_change|migration_rollback|manual",
    "steps": [],
    "trigger_conditions": [],
    "data_safety": [],
    "verification": []
  }
}
```

## 12. 条目级可行性输出

输出应覆盖目标、证据基础、可行性、实现细化、复用、兼容性、验证、回滚、
剩余风险和 Critic 意见响应。机器 envelope、字段、类型和 action 不在本节
重复定义，统一使用注入的 `cmd/orchestrator/contracts/menxia_item_solver.py`。

`amendment` 只描述对 `ReviewOverlay` 的建议。Solver 不得直接覆盖
FrozenPlan。涉及跨组依赖、公共契约、主要架构方向或组边界的修改，
必须返回中书省并生成新的 FrozenPlan 版本。

## 13. Critic 意见响应

Critic 对条目提出问题后，Solver 必须逐条回应：

```json
{
  "responses_to_critic": [
    {
      "finding_id": "finding-000001",
      "position": "accepted|rejected|partially_accepted|needs_more_evidence|needs_user",
      "reason": "string",
      "proposal_change": "string",
      "evidence_ids": [],
      "verification_change": [],
      "remaining_risk": "string"
    }
  ]
}
```

不得用一段总体说明替代逐条响应。

## 14. 典型细化示例

### 原始条目

```text
增加核心资源预加载。
```

### Solver 细化后

```text
在现有 AddressablesLoader 之上增加预加载调度接口：

1. 增加 IPreloadScheduler；
2. 资源分为 Critical、SceneWarmup、Deferred；
3. Critical 在进入主流程前加载；
4. SceneWarmup 在场景切换前异步加载；
5. Deferred 进入场景后按需加载；
6. 每类资源拥有独立 Handle 生命周期；
7. Critical 失败时阻止进入主流程；
8. SceneWarmup 失败时降级为按需加载；
9. Deferred 失败时支持重试；
10. 不修改现有资源发布流程；
11. 提供内存峰值和首帧耗时验证方案；
12. 通过配置开关支持关闭预加载并恢复旧路径。
```

这才是可以交给质量门继续检查的落地条目。

### 代码级变更记录（changes 条目示例）

```json
{
  "change_id": "chg-000001",
  "file": "Assets/Scripts/Loading/AddressablesLoader.cs",
  "kind": "edit",
  "before": "public Task LoadAsync(string key) { return LoadAsync(key, default); }",
  "after": "public Task LoadAsync(string key) { return LoadAsync(key, _preloadScheduler.TokenFor(key)); }",
  "anchor": "AddressablesLoader.LoadAsync(string)",
  "why": "Critical 资源必须在进入主流程前挂到预加载调度器上 (finding-000003, accepted)"
}
```

```json
{
  "change_id": "chg-000002",
  "file": "Assets/Tests/PreloadSchedulerTests.cs",
  "kind": "test",
  "code": "Assert.Throws<InvalidOperationException>(() => _scheduler.Preload(critical));",
  "why": "证伪'Critical 失败不阻止主流程'：若预加载失败未阻断主流程，该断言不成立"
}
```

对照第 14 节开头的原始条目："增加核心资源预加载"这种粒度不满足验收线；
上面的记录粒度才满足。

## 15. Prompt Contract

```text
ROLE:
You are Solver refining one frozen solution item in the Menxia phase.

MISSION:
Turn the current item into a feasible, bounded, implementation-ready, and
verifiable item using Analyst's evidence audit and the frozen plan.

REQUIRED ORDER:
1. Read the frozen item and Analyst evidence audit.
2. Decide whether the item should be kept, revised, split, merged, removed,
   or sent to HUMAN_GATE.
3. Revalidate facts that affect implementation direction.
4. Detail modules, interfaces, data flow, lifecycle, errors, compatibility,
   verification, and rollback.
5. Compare reuse and new-implementation choices.
6. Produce code-level changes: before/after for edits, complete code for new
   logic, assertion-level tests. The implementer must make zero design
   decisions from your changes alone.
7. Respond to every Critic finding by finding ID; each accepted finding maps
   to a concrete change.
8. Return ItemFeasibilityProposal.

DO NOT:
- silently change the overall plan objective;
- modify the frozen source artifact;
- claim implementation or tests were completed;
- declare the item approved;
- choose user-owned trade-offs without HUMAN_GATE;
- invent missing facts;
- defer design decisions to the implementer with "TBD" or behavior-only
  placeholders.
```

## 16. 验证要求

实现该 Skill 时至少验证：

1. Solver 能消费 Analyst 的条目证据审计；
2. Solver 能判断条目保留、修改、拆分、合并或删除；
3. Solver 能补充具体模块、接口和数据流；
4. Solver 能补充错误处理、重试、降级和回滚；
5. Solver 能检查复用和重复造轮子；
6. Solver 能检查 Go/.NET/Unity 专项兼容性；
7. Solver 能输出可验证的条目；
8. Solver 能逐条回应 Critic Finding；
9. accepted Finding 与 changes 一一对应，不存在假吸收；
10. changes 达到代码级：实施者仅凭 changes 无需再做设计决策；
11. Solver 不会直接宣布条目通过；
12. Solver 不会声称已经修改代码或执行测试；
13. Solver 能在缺少关键证据时返回 `NEEDS_MORE_EVIDENCE`；
14. 输出可以被 Critic 的 ItemReviewPacket 校验器读取。

## 23. Runtime Contract Authority

This Skill contains only item-feasibility semantics. The Orchestrator injects
`cmd/orchestrator/contracts/menxia_item_solver.py`, validates the full result
before transition, and supplies bounded field paths for repair. Do not derive
a machine envelope or action set from this document.
