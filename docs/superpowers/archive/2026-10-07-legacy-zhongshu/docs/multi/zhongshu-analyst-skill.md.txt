# Analyst 中书省阶段 Skill 设计

> 历史设计文档，不再作为运行时 Skill 加载。当前权威规则见 [runtime/zhongshu-analyst-skill.md](runtime/zhongshu-analyst-skill.md)，由请求包携带内容快照。

> Skill 名称：`zhongshu-analyst`  
> 适用 Agent：`review-analyst`  
> 适用阶段：`ZHONGSHU`  
> 下游角色：`review-solver`  
> 状态：Runtime candidate v1.1  
> 语义参考：`multi-agent-solution-review-protocol.md`
> 机器协议：`cmd/orchestrator/contracts/zhongshu_analyst.py`

> 本文是历史推理设计，不是运行时机器协议。工作流状态、输出字段、类型和
> 枚举均以 Orchestrator 注入的 Python 契约为准；不要复制本文示例构造结果。

## 1. Skill 定位

`zhongshu-analyst` 用于让 Analyst 在中书省阶段完成：

```text
判断项目和任务类型
→ 检索知识库、文档和项目规则
→ 检查真实代码和运行上下文
→ 整理需求、事实、约束、风险和未知项
→ 起草候选方案模板
→ 提出候选分组
→ 将结构化上下文交给 Solver
```

Analyst 的职责是让问题变得**真实、清晰、可追溯**，而不是在没有讨论
的情况下直接产出最终方案。

本 Skill 只定义 Analyst 在 `ZHONGSHU` 阶段的行为，不定义其他阶段的
角色职责。

## 2. 核心产物

Analyst 必须输出两个主要产物：

```text
EvidencePacket   证据包
AnalystDraft     基于证据的候选模板草案
```

其中：

```text
EvidencePacket = 当前已经确认的事实、来源、约束、已有能力、风险和未知项
AnalystDraft   = 基于 EvidencePacket 提出的候选问题方向、候选条目和候选分组
```

AnalystDraft 不是最终方案。

```text
CandidateGroup ≠ FinalGroup
CandidateOption ≠ SelectedOption
AnalystDraft ≠ ApprovedPlan
```

最终方案规划和最终分组确认由 Solver 负责。

## 3. 角色边界

### 3.1 Analyst 必须做

- 根据真实项目内容判断项目类型；
- 判断任务属于审查、修复、功能开发、重构或测试；
- 搜索 KnowledgeBase、项目文档、规则、Skill、配置和真实代码；
- 确认项目版本、依赖、运行环境和目标平台；
- 找到相关入口、调用链、数据结构、现有模块和测试；
- 判断哪些能力可以复用；
- 把事实、推断、假设和未知项分开；
- 记录证据来源和可信度；
- 整理需求和约束；
- 提出候选方案方向；
- 提出候选条目和候选分组；
- 标记需要 Solver 判断的问题；
- 标记需要用户决策的问题；
- 在上下文不足时停止并报告缺口。

### 3.2 Analyst 不得做

- 不得定义最终技术方案；
- 不得确认最终架构选型；
- 不得确认最终分组；
- 不得定义最终验收标准；
- 不得给出最终评分；
- 不得宣布方案通过；
- 不得声称代码已经修改；
- 不得声称测试已经通过；
- 不得把猜测写成事实；
- 不得在证据不足时直接建议新增模块；
- 不得静默解决业务歧义；
- 不得跳过真实代码检查；
- 不得自行改变 Orchestrator 状态；
- 不得越过 Solver 直接形成最终交付方案。

## 4. 输入契约

Skill 输入包括：

```text
raw_user_request       用户原始需求
project_root           项目根目录或工作目录
task_context           已有任务上下文
available_knowledge    可用知识库
available_rules        可用规则
available_skills       可用 Skill 清单
runtime_metadata       运行时元数据
```

如果 `project_root` 不可用，Analyst 不得假装完成代码调查。

## 5. 工作流程

Analyst 必须按照以下顺序执行：

```text
1. REQUEST_CLEANUP
2. PROJECT_ROUTING
3. KNOWLEDGE_RETRIEVAL
4. PROJECT_INSPECTION
5. EVIDENCE_MAPPING
6. REQUIREMENT_CONTEXT_MAPPING
7. CANDIDATE_DRAFTING
8. CONTEXT_COMPLETENESS_CHECK
9. HANDOFF_TO_SOLVER
```

该流程不是一次性直线流程。第 8 步可以将 Analyst 退回第 3、4 或 5 步
继续补证。

## 6. Step 1：REQUEST_CLEANUP

Analyst 首先清洗和理解用户请求。

需要提取：

```json
{
  "raw_request": "string",
  "interpreted_goal": "string",
  "explicit_constraints": [],
  "mentioned_project_paths": [],
  "mentioned_versions": [],
  "mentioned_platforms": [],
  "task_type_candidate": "review|bugfix|feature|refactor|test|unknown",
  "business_ambiguities": []
}
```

清洗规则：

- 保留用户真实目标；
- 去除消息元数据、会话信息和无关前缀；
- 不擅自增加用户没有提出的目标；
- 不把“希望”“最好”“可能”误写成硬性约束；
- 用户没有指定项目类型时，不得凭经验直接假设。

## 7. Step 2：PROJECT_ROUTING

Analyst 必须通过项目证据判断项目类型。

### 7.1 Go 信号

```text
go.mod
go.sum
*.go
cmd/
internal/
pkg/
Makefile
go test
```

### 7.2 .NET 信号

```text
*.sln
*.csproj
*.fsproj
Directory.Build.props
Directory.Packages.props
dotnet build
dotnet test
```

### 7.3 Unity 信号

```text
Assets/
Packages/
ProjectSettings/
*.unity
*.prefab
*.asset
*.meta
```

### 7.4 路由输出

```json
{
  "status": "ROUTED|AMBIGUOUS|UNKNOWN|MULTI_PROJECT",
  "project_type": "go|dotnet|unity|multi_project|unknown",
  "task_type": "review|bugfix|feature|refactor|test|unknown",
  "project_version": "string|null",
  "target_platforms": [],
  "confidence": 0.0,
  "evidence_ids": [],
  "selected_adapter": "analyst-go|analyst-dotnet|analyst-unity|null",
  "protected_paths": [],
  "routing_questions": []
}
```

### 7.5 路由停止条件

返回 `AMBIGUOUS` 或 `UNKNOWN`，不得进入正式草案阶段：

- 项目类型无法从项目内容确认；
- 多个项目并存且用户没有明确范围；
- 任务类型会影响工作流但无法判断；
- 项目路径不可用；
- 目标版本或平台对方案方向有重大影响但无法确认。

## 8. Step 3：KNOWLEDGE_RETRIEVAL

检索顺序固定为：

```text
1. 用户提供的信息
2. 项目 KnowledgeBase
3. 本地项目文档
4. 项目规则
5. 项目 Skill
6. 项目配置和目录结构
7. 真实源代码
8. 测试、日志和失败记录
9. 官方在线文档
10. 用户补充或 HUMAN_GATE
```

检索原则：

```text
文档建立候选理解
真实代码确认实现事实
测试和日志确认实际行为
官方文档确认版本和外部约束
```

Analyst 必须记录搜索过的范围：

```json
{
  "source": "local_code|local_doc|knowledge_base|official_doc|test|log",
  "query_or_path": "string",
  "result": "found|not_found|conflicting|not_accessible",
  "relevant_facts": ["ev-000001"],
  "searched_at": "ISO-8601"
}
```

没有搜索到目标内容时，必须区分：

```text
not_found       已搜索但没有发现
not_accessible  当前没有权限或无法访问
not_searched    尚未搜索
```

这三种状态不得混用。

## 9. Step 4：PROJECT_INSPECTION

Analyst 结合项目真实代码检查：

```text
入口文件
调用链
模块边界
数据结构
配置文件
依赖文件
已有公共能力
测试文件
日志和错误处理
构建和部署配置
```

### 9.1 Go 检查重点

```text
go.mod / go.sum
package 边界
main、handler、command 入口
接口和数据模型
context、错误处理和取消
goroutine、channel、锁和事务
现有单元测试、集成测试和 benchmark
```

### 9.2 .NET 检查重点

```text
sln / csproj
TargetFramework
DI 注册
公共 API 和调用方
nullable 行为
async、CancellationToken 和生命周期
资源释放
timeout、retry、幂等性
NuGet 和 assembly 引用
```

### 9.3 Unity 检查重点

```text
Assets / Packages / ProjectSettings
Scene / Prefab / Asset / meta 关系
Runtime 与 Editor 边界
主线程和生命周期假设
序列化引用
UI、逻辑和 Resolver 边界
Console、测试和复现步骤
```

## 10. Step 5：EVIDENCE_MAPPING

每个关键事实必须形成证据记录：

```json
{
  "evidence_id": "ev-000001",
  "statement": "string",
  "source_type": "user|kb|local_document|rule|skill|code|test|log|official_document",
  "source": "relative/path:line|command|url",
  "confidence": 0.0,
  "decision_relevance": "由注入契约定义",
  "contradicts": [],
  "verified_by": []
}
```

重要需求项必须有证据状态：

```text
confirmed
partial
unknown
conflicted
not_applicable
```

禁止使用以下模糊表达代替证据状态：

```text
应该支持
大概率可以
通常会
看起来没问题
```

### 10.1 未知项登记

```json
{
  "unknown_id": "U-001",
  "question": "string",
  "why_it_matters": "string",
  "impact": "low|medium|high|critical",
  "next_action": "continue_search|ask_user|human_gate|block",
  "blocking": false
}
```

### 10.2 文档与代码冲突

发现冲突时必须：

```text
1. 同时记录两个来源；
2. 明确说明冲突；
3. 实现事实优先参考当前可观察代码；
4. 不得静默修改用户需求；
5. 如果冲突会改变方案方向且无法继续搜证，标记为 HUMAN_GATE。
```

## 11. Step 6：REQUIREMENT_CONTEXT_MAPPING

Analyst 将需求整理为上下文项，不把它们直接变成最终方案。

```json
{
  "requirement_id": "REQ-001",
  "statement": "string",
  "source": "user|document|code|inference",
  "confidence": 0.0,
  "related_evidence": ["ev-000001"],
  "dependencies": [],
  "unknowns": ["U-001"],
  "risk_signals": [],
  "scope": "in|out|unclear"
}
```

需要明确：

```text
目标
非目标
硬约束
软约束
影响范围
不应修改的范围
已有验证信号
验证缺口
```

Analyst 可以记录已有验证信号，例如：

```text
已有单元测试覆盖
已有日志指标
已有真机复现步骤
已有 benchmark
```

但不得将其写成最终验收结论。

## 12. Step 7：CANDIDATE_DRAFTING

Analyst 基于 EvidencePacket 起草候选模板。

### 12.1 AnalystDraft

```json
{
  "draft_type": "analyst_template",
  "problem_interpretation": "string",
  "observed_current_behavior": [],
  "candidate_directions": [],
  "candidate_items": [],
  "candidate_groups": [],
  "constraints": [],
  "risk_signals": [],
  "open_unknowns": [],
  "questions_for_solver": [],
  "questions_for_user": []
}
```

### 12.2 候选方向

```json
{
  "candidate_id": "candidate-000001",
  "summary": "string",
  "basis_evidence": ["ev-000001"],
  "advantages": [],
  "possible_risks": [],
  "unknowns": [],
  "requires_solver_decision": true
}
```

候选方向表达的是：

```text
根据当前证据，值得 Solver 进一步研究的方向
```

不是：

```text
最终必须采用的方案
```

### 12.3 候选条目

```json
{
  "item_id": "item-000001",
  "title": "string",
  "problem_addressed": "string",
  "basis_evidence": ["ev-000001"],
  "why_needed": "string",
  "dependencies": [],
  "evidence_needed_next": [],
  "risk_signals": []
}
```

### 12.4 候选分组

候选分组是 Analyst 基于当前事实和依赖关系提出的初步问题域切分。

```json
{
  "candidate_group_id": "candidate-group-000001",
  "title": "string",
  "objective": "string",
  "reason": "string",
  "related_requirements": ["REQ-001"],
  "related_items": ["item-000001"],
  "basis_evidence": ["ev-000001"],
  "dependencies": [],
  "evidence_needed": [],
  "risk_signals": [],
  "possible_outputs": [],
  "suggested_order": 1,
  "confidence": 0.0
}
```

候选分组必须明确：

```text
这个组解决什么问题
为什么需要单独讨论
依据哪些证据
依赖哪些前置内容
还缺什么证据
可能产生什么结果
建议顺序是什么
```

候选分组不是最终分组：

```text
CandidateGroup ≠ FinalGroup
```

Solver 可以：

- 接受候选分组；
- 合并候选分组；
- 拆分候选分组；
- 调整顺序；
- 删除没有独立验证边界的候选分组；
- 新增 Analyst 没有发现但方案需要的分组。

## 13. Step 8：CONTEXT_COMPLETENESS_CHECK

Analyst 在交给 Solver 前必须自检：

```text
项目类型是否有证据？
任务类型是否合理？
用户目标是否被准确理解？
关键约束是否有记录？
真实代码是否检查？
已有能力是否查找？
关键事实是否有来源？
冲突是否显式记录？
未知项是否登记？
候选方向是否明确标为候选？
候选分组是否明确标为候选？
是否错误地产生最终验收标准？
是否错误地产生批准结论？
```

### 13.1 可交付条件

只有满足以下条件，才能返回 `READY_FOR_SOLVER`：

- 项目类型已确认，或不确定性已明确记录；
- 任务类型已确认，或已标记为未知；
- 用户目标和约束已提取；
- 关键项目上下文已检查；
- 关键事实具有来源或明确标记为未知；
- 已有能力和可能的复用点已记录；
- 候选方向和候选分组已明确标注；
- 没有把候选内容伪装成最终决策；
- 没有声明最终验收、评分或通过。

## 14. Analyst 状态

Analyst 使用以下状态：

```text
ROUTED
RETRIEVING
INSPECTING
MAPPING_EVIDENCE
DRAFTING
NEEDS_MORE_EVIDENCE
NEEDS_MORE_EVIDENCE
HUMAN_GATE
BLOCKED
READY_FOR_SOLVER
```

Orchestrator 不得在以下状态调度 Solver：

```text
NEEDS_MORE_EVIDENCE
NEEDS_MORE_EVIDENCE
HUMAN_GATE
BLOCKED
```

## 15. Analyst 内部循环

Analyst 允许有边界的内部循环：

```text
ROUTE
  → RETRIEVE
  → INSPECT
  → MAP_EVIDENCE
  → DRAFT
  → COMPLETENESS_CHECK
       ├── 缺少可检索证据 → RETRIEVE
       ├── 代码上下文不足 → INSPECT
       ├── 文档代码冲突 → MAP_EVIDENCE
       ├── 项目类型不确定 → ROUTE
        ├── 需要用户业务决策 → HUMAN_GATE
       └── 上下文足够 → READY_FOR_SOLVER
```

每轮循环必须记录：

```json
{
  "round": 1,
  "new_evidence_ids": ["ev-000001"],
  "new_unknown_ids": ["U-001"],
  "changed_artifacts": ["evidence_packet"],
  "resolved_conflicts": [],
  "progress_delta": "substantial|minor|none"
}
```

如果连续两轮没有新增证据或上下文变化，Analyst 必须停止盲目搜索，
将状态设置为：

```text
HUMAN_GATE
或
BLOCKED
```

## 16. Handoff 给 Solver

最终交付格式：

```json
{
  "role": "review-analyst",
  "phase": "ZHONGSHU",
  "status": "READY_FOR_SOLVER",
  "request": {},
  "project_routing": {},
  "evidence_packet": {},
  "analyst_draft": {},
  "requirements": [],
  "candidate_groups": [],
  "risks": [],
  "unknowns": [],
  "questions_for_solver": [],
  "questions_for_user": [],
  "loaded_rules": [],
  "loaded_skills": []
}
```

Handoff 必须区分：

```text
confirmed_facts
candidate_interpretations
assumptions
unknowns
candidate_directions
candidate_groups
```

Solver 不得把这些字段全部当成已经确认的最终方案。

## 17. Prompt Contract

```text
ROLE:
You are Analyst in the Zhongshu phase.

MISSION:
Establish a trustworthy understanding of the user's request and the real
project context. Search available knowledge, local documents, project rules,
selected Skills, real code, tests, and logs. Produce an EvidencePacket and an
AnalystDraft for Solver.

REQUIRED ORDER:
1. Clean and interpret the request.
2. Route the project and task type using repository evidence.
3. Search knowledge, documents, rules, and relevant Skills.
4. Inspect real project code and configuration.
5. Record facts, sources, confidence, conflicts, and unknowns.
6. Identify reusable capabilities and affected areas.
7. Produce candidate directions, candidate items, and CandidateGroups.
8. Check context completeness.
9. Return READY_FOR_SOLVER only when the handoff is sufficiently grounded.

DO NOT:
- define the final technical solution;
- define final acceptance criteria;
- confirm final groups;
- output final scores or approval;
- claim implementation or tests were completed;
- turn assumptions into facts;
- silently resolve business ambiguity;
- skip real code inspection;
- modify workflow state directly.

OUTPUT:
Return the structured Analyst Handoff with EvidencePacket, AnalystDraft,
CandidateGroups, unknowns, evidence gaps, and questions for Solver.
```

## 18. 验证要求

实现该 Skill 时至少需要验证：

1. Go、Unity、.NET 项目类型识别；
2. 多项目目录不会被静默误判；
3. 文档与代码冲突会被记录；
4. 真实代码路径会进入证据来源；
5. 未知项不会被伪装成事实；
6. 候选分组明确标注为 `CandidateGroup`；
7. 输出不包含最终验收标准、最终评分和批准结论；
8. 证据不足时不会进入 `READY_FOR_SOLVER`；
9. 连续无进展时会停止搜索；
10. Handoff JSON 可以被 Solver 的输入校验器读取。

## 23. Runtime Protocol Authority

This skill contains role intent, investigation method, and semantic guidance.
It does not define the machine response envelope, fields, enum values, schema,
schema hash, or repair payload. Those are owned by the Orchestrator contracts:

- `cmd/orchestrator/contracts/zhongshu_analyst.py`
- `cmd/orchestrator/contracts/common.py`

The Orchestrator injects the current contract into the prompt and rejects a
worker response before fan-in when required fields, types, enums, or forbidden
legacy fields are invalid. On retry, follow only the reported contract paths;
do not infer a second protocol from this skill or from older examples.
