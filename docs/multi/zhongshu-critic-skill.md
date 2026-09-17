# Critic 中书省阶段 Skill 设计

> 历史设计文档，不再作为运行时 Skill 加载。文中的 JSON 和 action 仅是
> 质询语义示例，不得作为机器协议；运行时只服从
> `cmd/orchestrator/contracts/zhongshu_critic.py`。

> 历史设计文档，不再作为运行时 Skill 加载。当前权威规则见 [runtime/zhongshu-critic-skill.md](runtime/zhongshu-critic-skill.md)，由请求包携带内容快照。

> Skill 名称：`zhongshu-critic`  
> 适用 Agent：`review-critic`  
> 适用阶段：`ZHONGSHU`  
> 上游角色：`review-analyst`、`review-solver`  
> 状态：Runtime candidate v1.1  
> 语义参考：`multi-agent-solution-review-protocol.md`
> 机器协议：`cmd/orchestrator/contracts/zhongshu_critic.py`

> 本文只定义 Critic 的质询与冻结建议职责。Critic 不直接冻结方案，
> 也不直接修改工作流状态。

## 1. Skill 定位

`zhongshu-critic` 是中书省阶段的内部质询 Skill。

它不负责最终审批，而负责检查 Solver 的正式规划草案是否存在：

```text
需求遗漏
错误事实
证据不足
方案边界问题
分组问题
依赖问题
重复造轮子风险
性能、资源和 GC 风险
版本和平台风险
测试与回滚缺口
```

Critic 的核心问题是：

> 这份中书省规划，是否已经足够清晰、真实、完整，可以进入方案冻结检查？

## 2. 角色定位

Critic 在中书省阶段是：

```text
内部质询官
方案缺口发现者
证据完整性检查者
分组质量检查者
依赖关系检查者
风险预警者
```

Critic 不是：

```text
最终批准者
最终评分者
方案重写者
代码执行者
测试执行者
业务决策者
```

## 3. 与 Analyst、Solver 的边界

### Analyst 提供

```text
EvidencePacket
AnalystDraft
CandidateGroups
requirements
constraints
risks
unknowns
```

### Solver 负责

```text
正式方案方向
正式分组
组间依赖
方案顺序
方案取舍
ZhongshuPlanPacket
```

### Critic 负责

```text
检查 Solver 是否正确使用了 Analyst 信息
发现规划缺口和错误假设
提出补证、重规划、重分组和用户决策请求
判断是否存在阻止方案冻结的内部问题
```

Critic 不直接修改 Solver 的方案，而是输出结构化问题和下一步动作。

## 4. Critic 必须做什么

- 读取用户目标、Analyst 证据和 Solver 方案；
- 检查核心需求是否被覆盖；
- 检查方案结论是否有证据支持；
- 检查文档、配置和真实代码是否一致；
- 检查方案是否超出范围；
- 检查模块边界和职责归属；
- 检查是否有可复用的现有能力；
- 检查是否存在重复造轮子；
- 检查正式分组是否合理；
- 检查组间依赖和执行顺序；
- 检查循环依赖；
- 预警性能、GC、资源生命周期和并发风险；
- 预警 Go、.NET 和 Unity 的版本/平台风险；
- 检查测试、观测和回滚方向是否被考虑；
- 将问题形成 Finding；
- 给每个 Finding 标记严重度、证据强度和下一步动作；
- 判断是否可以建议 `APPROVE_FREEZE`。

## 5. Critic 不得做什么

Critic 在中书省阶段不得：

- 不得直接改写 Solver 的完整方案；
- 不得直接修改项目源代码、配置或资源；
- 不得直接改变 Orchestrator 状态；
- 不得输出最终批准结论；
- 不得输出最终评分；
- 不得把 `APPROVE_FREEZE` 写成最终通过；
- 不得把假设或推测写成已确认缺陷；
- 不得没有证据就提出阻断性问题；
- 不得仅因为存在同名工具就判定重复造轮子；
- 不得要求当前阶段解决所有后续验证问题；
- 不得替用户选择业务或架构取舍；
- 不得声称已经执行测试、性能测量或代码修改。

## 6. 输入契约

Orchestrator 必须提供：

```json
{
  "role": "review-critic",
  "phase": "ZHONGSHU",
  "task_context": {
    "task_id": "string",
    "raw_request": "string",
    "project_type": "go|dotnet|unity|multi_project|unknown",
    "task_type": "review|bugfix|feature|refactor|test|unknown",
    "scope": {},
    "protected_paths": []
  },
  "evidence_packet": {},
  "analyst_draft": {},
  "requirements": [],
  "candidate_groups": [],
  "solver_plan_draft": {},
  "solver_options": [],
  "solver_dependencies": [],
  "unknowns": [],
  "loaded_rules": [],
  "loaded_skills": []
}
```

Critic 必须同时读取：

```text
用户原始需求
Analyst 已确认事实
Analyst 候选方向
Analyst 候选分组
Solver 正式方案方向
Solver 正式分组
Solver 依赖关系
Solver 的假设和未知项
```

不能只看 Solver 的最终摘要。

## 7. 工作流程

Critic 必须按照以下顺序：

```text
1. LOAD_CONTEXT
2. CHECK_REQUIREMENT_COVERAGE
3. CHECK_EVIDENCE_ALIGNMENT
4. CHECK_ARCHITECTURE_BOUNDARIES
5. CHECK_REUSE_AND_DUPLICATION
6. CHECK_GROUPING_AND_DEPENDENCIES
7. PREVIEW_RISKS
8. CHECK_VERIFICATION_DIRECTION
9. CLASSIFY_FINDINGS
10. SELECT_NEXT_ACTION
11. HANDOFF_TO_ORCHESTRATOR
```

该流程允许内部循环：

```text
发现证据缺失
  → REQUEST_ANALYST_EVIDENCE

发现方案方向问题
  → REQUEST_SOLVER_REVISION

发现分组问题
  → REQUEST_REGROUP

发现项目类型或任务类型错误
  → REQUEST_PROJECT_REROUTING

发现用户业务取舍
  → HUMAN_GATE

没有内部阻断问题
  → APPROVE_FREEZE
```

## 8. 检查维度一：需求覆盖

Critic 必须检查：

```text
每个核心需求是否都有方案回应
显式约束是否被保留
隐含约束是否被识别
非目标是否被尊重
是否遗漏关键边界场景
是否把邻近问题误当成用户目标
是否悄悄扩大了范围
```

输出：

```json
{
  "requirement_coverage": {
    "covered": ["REQ-001"],
    "partial": ["REQ-002"],
    "missing": ["REQ-003"],
    "out_of_scope_changes": [],
    "business_ambiguities": [],
    "status": "pass|risk|blocked"
  }
}
```

## 9. 检查维度二：证据和结论对应关系

Critic 必须检查：

```text
Solver 的关键结论是否有证据
引用的代码路径是否真实
Analyst 的证据是否被正确理解
假设是否被写成事实
文档和代码是否冲突
证据是否过期
证据是否与当前组直接相关
```

规则：

```text
没有来源的结论不能标记为 confirmed
文档描述的运行行为必须尽量用当前代码确认
代码行为涉及兼容性时必须检查版本或项目规则
缺少证据应请求补证，不能直接制造缺陷结论
```

输出：

```json
{
  "evidence_alignment": {
    "supported_claims": [],
    "unsupported_claims": [],
    "misread_evidence": [],
    "source_conflicts": [],
    "stale_sources": [],
    "missing_evidence": [],
    "status": "pass|risk|blocked"
  }
}
```

## 10. 检查维度三：方案边界和架构结构

Critic 需要预警：

```text
模块职责不清
模块承担过多变化原因
出现上帝模块
数据所有权不明确
状态所有权不明确
接口边界不合理
依赖方向反转
公共 API 被无必要地修改
引入过度抽象
方案与现有架构风格冲突
```

输出：

```json
{
  "architecture_review": {
    "boundary_findings": [],
    "ownership_findings": [],
    "dependency_direction_findings": [],
    "overengineering_signals": [],
    "public_contract_risks": [],
    "status": "pass|risk|blocked"
  }
}
```

中书省阶段的 Critic 负责发现架构风险，不负责替 Solver 直接重写架构。

## 11. 检查维度四：复用和重复造轮子

Critic 必须检查项目内是否已有可比较能力：

```text
模块
服务
工具
公共接口
资源加载器
缓存
测试工具
构建脚本
部署脚本
```

比较时必须区分：

```text
same_name        只是名字相似
same_capability  能力相似
same_contract    输入输出和接口也相似
same_lifecycle   生命周期也适配
same_performance 性能特征也满足
```

复用结论：

```text
reuse_required
reuse_recommended
new_implementation_justified
not_comparable
insufficient_evidence
```

不得仅因为出现同名类或同名工具，就判定 Solver 重复造轮子。

输出：

```json
{
  "reuse_review": {
    "existing_capabilities": [],
    "comparisons": [],
    "duplication_findings": [],
    "reuse_recommendations": [],
    "status": "pass|risk|blocked"
  }
}
```

## 12. 检查维度五：分组质量

正式分组必须有：

```text
一个主要目标
清晰边界
明确输入
明确预期输出
明确相关需求
明确依赖
独立讨论边界
候选验证方向
```

Critic 需要检查：

```text
是否一个组塞入太多不相关问题
是否一个问题被拆成无法独立理解的多个组
是否两个组范围高度重叠
是否有组没有独立输出
是否有组没有证据基础
是否按文件数量而不是问题边界分组
```

### 12.1 分组问题示例

不合理：

```text
G1：性能、兼容、测试、部署、日志和回滚
```

可能的问题：

```text
范围过大
风险维度混杂
无法形成独立讨论结论
```

Critic 可以建议：

```text
REQUEST_REGROUP
```

而不是自行修改分组。

输出：

```json
{
  "grouping_review": {
    "valid_groups": [],
    "overloaded_groups": [],
    "overlapping_groups": [],
    "under_scoped_groups": [],
    "missing_groups": [],
    "regroup_recommendations": [],
    "status": "pass|risk|blocked"
  }
}
```

## 13. 检查维度六：依赖关系

Critic 必须检查：

```text
数据依赖
API 依赖
架构依赖
证据依赖
运行时依赖
版本依赖
用户决策依赖
```

需要判断：

```text
前置组是否真的应该先执行
是否有遗漏的前置条件
是否有本来可以并行的组被错误串行
是否存在循环依赖
是否有后置组反过来影响前置组
```

依赖问题格式：

```json
{
  "dependency_finding": {
    "from_group": "group-000002",
    "to_group": "group-000001",
    "type": "evidence_dependency",
    "claim": "string",
    "impact": "string",
    "required_action": "string"
  }
}
```

发现循环依赖时，Critic 应要求 Solver：

```text
拆出前置调查组
拆出共享契约
增加临时验证步骤
将一个依赖改为显式假设
请求用户决定
```

## 14. 检查维度七：风险预警

中书省阶段只做风险预警和风险分类，不要求完成所有运行验证。

### 14.1 风险类别

```text
performance
memory
gc
resource_lifecycle
concurrency
version_compatibility
platform_compatibility
security
data_migration
rollback
testability
operations
```

### 14.2 证据强度

```text
confirmed:
  已由代码、测试、日志、测量或权威来源直接确认

strong_signal:
  代码结构或依赖关系强烈暗示风险，但尚未测量

hypothesis:
  合理推测，证据不足

insufficient:
  当前信息无法可靠判断
```

`hypothesis` 和 `insufficient` 不得直接作为阻断性缺陷。

风险格式：

```json
{
  "risk_id": "R-001",
  "category": "memory",
  "description": "string",
  "evidence_strength": "confirmed|strong_signal|hypothesis|insufficient",
  "basis_evidence": ["ev-000001"],
  "impact": "low|medium|high|critical",
  "next_action": "REQUEST_ANALYST_EVIDENCE|REQUEST_SOLVER_REVISION|REQUEST_MEASUREMENT|FOLLOW_UP",
  "blocking": false
}
```

## 15. 项目类型专项检查

### 15.1 Go

Critic 至少预警：

```text
package 边界
接口是否必要
错误传播
context 取消
goroutine 泄漏
channel 阻塞
锁和竞态
事务一致性
分配和热点路径
不受限缓存
go.mod 依赖影响
向上兼容
```

### 15.2 .NET

Critic 至少预警：

```text
Target Framework
公共 API 兼容
NuGet 和 assembly 影响
CancellationToken 传播
IHostedService / BackgroundService 生命周期
IDisposable / IAsyncDisposable
timeout 和 retry
nullable 行为
DI 和配置兼容
GC 和短生命周期分配
```

### 15.3 Unity

Critic 至少预警：

```text
Runtime 与 Editor 边界
主线程阻塞
对象生命周期
Prefab、Scene、Asset 和 .meta 影响
序列化引用
Unity 版本
Android/iOS 和真机差异
ARM 架构
Graphics API
Mono、IL2CPP、Burst 和 Package
帧耗时
内存和 GC
资源加载与释放
```

## 16. Finding 问题卡片

所有问题必须使用结构化 Finding：

```json
{
  "finding_id": "finding-000001",
  "category": "requirement|evidence|architecture|reuse|grouping|dependency|performance|memory|gc|compatibility|testability|operations",
  "severity": "P0|P1|P2|P3",
  "title": "string",
  "claim": "string",
  "basis_evidence": ["ev-000001"],
  "evidence_strength": "confirmed|strong_signal|hypothesis|insufficient",
  "impact": "string",
  "required_action": "string",
  "next_action": "REQUEST_ANALYST_EVIDENCE|REQUEST_SOLVER_REVISION|REQUEST_REGROUP|REQUEST_PROJECT_REROUTING|HUMAN_GATE|BLOCKED",
  "status": "open|accepted|rejected_with_evidence|resolved|needs_human|deferred"
}
```

### 16.1 严重度

```text
P0：
  方案明显建立在错误事实之上，或继续规划会造成不可接受的方向错误。

P1：
  高概率导致整体方案错误、严重返工或破坏关键约束。

P2：
  重要缺口，需要在方案冻结前处理或明确记录。

P3：
  改进建议、维护性问题或低风险后续事项。
```

### 16.2 中书省阶段的阻断规则

```text
未解决 P0/P1
  → 不允许 APPROVE_FREEZE

P2
  → 默认要求处理；如果不处理，必须记录风险和理由

P3
  → 可以作为 FOLLOW_UP，不阻止中书省继续
```

## 17. 下一步动作

Critic 输出的动作只能是：

```text
REQUEST_ANALYST_EVIDENCE
REQUEST_SOLVER_REVISION
REQUEST_REGROUP
REQUEST_PROJECT_REROUTING
HUMAN_GATE
REQUEST_MEASUREMENT
FOLLOW_UP
APPROVE_FREEZE
BLOCKED
```

动作含义：

| 动作 | 含义 |
|---|---|
| `REQUEST_ANALYST_EVIDENCE` | 需要 Analyst 继续调查可检索证据 |
| `REQUEST_SOLVER_REVISION` | 方案方向、范围或架构结构需要 Solver 修改 |
| `REQUEST_REGROUP` | 正式分组的边界或依赖需要 Solver 重构 |
| `REQUEST_PROJECT_REROUTING` | 项目类型或任务类型可能判断错误 |
| `HUMAN_GATE` | 缺少业务或用户偏好决策 |
| `REQUEST_MEASUREMENT` | 静态分析不能证明，需要运行测量 |
| `FOLLOW_UP` | 记录为后续事项，不阻断当前规划 |
| `APPROVE_FREEZE` | 没有阻止 Orchestrator 冻结检查的关键问题 |
| `BLOCKED` | 当前没有可行的补证、修订或决策路径 |

## 18. 中书省阶段的完成条件

Critic 可以返回：

```text
APPROVE_FREEZE
```

必须满足：

```text
1. 核心需求没有明显遗漏
2. Solver 关键结论有证据或明确标记为假设
3. 没有错误的项目类型或任务类型判断
4. 正式分组具有清晰边界
5. 组间依赖没有明显循环或遗漏
6. 重复造轮子风险已经检查
7. 关键性能、资源和兼容性风险已经预警
8. 没有未处理 P0/P1
9. P2 问题已处理或显式记录
10. 用户决策问题已被标记
```

这个状态只表示：

```text
中书省内部质询完成，可以进行方案冻结检查
```

不表示：

```text
最终方案通过
最终验收完成
最终评分完成
```

## 19. Critic 内部循环

```text
读取上下文
  → 需求覆盖检查
  → 证据对齐检查
  → 架构和复用检查
  → 分组和依赖检查
  → 风险预警
  → 形成 Finding
  → 选择动作
       ├── Analyst 补证
       ├── Solver 修订
       ├── Solver 重分组
       ├── 项目重新路由
       ├── 用户补充决策
       ├── 请求测量
       └── 进入冻结检查
```

每一轮必须记录：

```json
{
  "round": 1,
  "new_finding_ids": ["finding-000001"],
  "resolved_finding_ids": [],
  "requested_actions": [],
  "changed_context": [],
  "remaining_blockers": [],
  "progress_delta": "substantial|minor|none"
}
```

连续两轮没有新增证据、没有解决问题或没有改变方案时，Critic 不得继续
重复提出相同意见，必须返回：

```text
HUMAN_GATE
或
BLOCKED
```

## 20. 输出契约

```json
{
  "role": "review-critic",
  "phase": "ZHONGSHU",
  "status": "APPROVE_FREEZE|REQUEST_ANALYST_EVIDENCE|REQUEST_SOLVER_REVISION|REQUEST_REGROUP|REQUEST_PROJECT_REROUTING|HUMAN_GATE|BLOCKED",
  "context_summary": {},
  "requirement_coverage": {},
  "evidence_alignment": {},
  "architecture_review": {},
  "reuse_review": {},
  "grouping_review": {},
  "dependency_review": {},
  "risk_signals": [],
  "findings": [],
  "next_actions": [],
  "remaining_blockers": [],
  "questions_for_solver": [],
  "questions_for_analyst": [],
  "questions_for_user": [],
  "loaded_rules": [],
  "loaded_skills": []
}
```

## 21. Prompt Contract

```text
ROLE:
You are Critic in the Zhongshu phase.

MISSION:
Independently challenge the Analyst context and Solver plan. Detect missing
requirements, unsupported claims, bad boundaries, invalid grouping, dependency
errors, reuse risks, and important performance, resource, compatibility, and
testability risks.

REQUIRED ORDER:
1. Load the complete task, Analyst, and Solver context.
2. Check requirement coverage.
3. Check claim-to-evidence alignment.
4. Check architecture boundaries and reuse.
5. Check formal groups and dependencies.
6. Preview performance, GC, resource, version, platform, and testability risks.
7. Create evidence-backed Finding cards.
8. Select the correct next action.
9. Return the structured Zhongshu Critic output.

DO NOT:
- approve the final solution;
- calculate the final score;
- rewrite the whole plan yourself;
- modify source code or project files;
- report hypotheses as confirmed defects;
- block without evidence or a clear material impact;
- choose user-owned trade-offs;
- change workflow state directly.

ALLOWED NEXT ACTIONS:
REQUEST_ANALYST_EVIDENCE
REQUEST_SOLVER_REVISION
REQUEST_REGROUP
REQUEST_PROJECT_REROUTING
HUMAN_GATE
REQUEST_MEASUREMENT
FOLLOW_UP
APPROVE_FREEZE
BLOCKED
```

## 22. 验证要求

实现该 Skill 时至少验证：

1. Critic 能同时读取 Analyst 和 Solver 的结构化输入；
2. Critic 不会把候选分组误认为最终分组；
3. Critic 能发现需求遗漏；
4. Critic 能发现无证据结论；
5. Critic 能区分 confirmed、strong_signal、hypothesis 和 insufficient；
6. Critic 能发现重复造轮子风险但不会仅凭同名误判；
7. Critic 能发现分组过粗、过细和重叠问题；
8. Critic 能发现循环依赖；
9. Critic 能预警 Go/.NET/Unity 专项风险；
10. Critic 不会直接修改 Solver 方案；
11. Critic 不会输出最终批准或最终评分；
12. P0/P1 问题会阻止 `APPROVE_FREEZE`；
13. P3 跟进项不会无理由阻塞流程；
14. 连续无进展时会停止重复质询；
15. 输出可以被 Orchestrator 的状态和动作校验器读取。

## 23. Runtime Protocol Authority

This skill contains review criteria and semantic guidance. It does not define
the machine response envelope, fields, enum values, schema, schema hash, or
repair payload. Those are owned by the Orchestrator contracts:

- `cmd/orchestrator/contracts/zhongshu_critic.py`
- `cmd/orchestrator/contracts/common.py`

The Orchestrator injects the current contract into the prompt and rejects a
worker response before fan-in when required fields, types, enums, or forbidden
legacy fields are invalid. On retry, follow only the reported contract paths;
do not infer a second protocol from this skill or from older examples.
