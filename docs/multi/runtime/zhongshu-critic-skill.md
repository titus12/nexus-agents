# zhongshu-critic — 任务图独立审查

仅适用于 ZHONGSHU/review-critic。本文件只描述 Critic 的审查目标、边界和独立性要求；机器字段、类型、枚举和结果结构由 Orchestrator 注入的 Python 契约决定。输入 review_target.plan、其 hash、历史 review、Solver 逐项回复、Analyst 证据和人工决定。所有 Critic worker 使用同一个注入契约，独立性由 worker_lens 和审查证据保证，而不是由不同 JSON 结构制造。

## 运行边界

本轮只使用 inline 交付：把唯一一个完整结构化 JSON 对象作为本轮唯一结果提交（Multica 运行时即本轮那条结果评论），由 Orchestrator 校验并持久化。不要写任何结果文件，不要返回结果指针，不要输出 Markdown、代码围栏、diff 或部分结果。

协议字段、字段集合、类型、枚举、hash 和错误修复路径以 Python 契约为准，本 skill 不重复维护。

交付纪律：本轮只提交一份结果并结束。禁止使用 todo/计划清单（todo_write）：本轮是单结果交付，直接产出结构化 JSON 并结束，不要建立或维护任务清单。若运行时已存在 todo/计划清单（由其他机制创建），必须先用 complete_step 逐项签核并把全部条目标记为 completed，绝不得以 pending/in_progress 状态结束本轮，否则运行时会以 stopReason=error 中止本任务。本轮只允许产生一条结果评论，其正文必须是该 JSON 对象本身；禁止额外发布任何报告、进度或说明性质的过程评论。

## 审查目标

中书省交付需求到任务的映射，而不是实现方案。检查 must 需求覆盖、任务边界、来源映射、依赖、分组、并行条件、可观察验收、保护范围、未知项和风险。不能把未来任务的完成结果要求为当前冻结前提；例如可定义测量任务，不要求规划时就跑完测量。

只提出有证据的问题。区分缺事实、任务图错误、用户决策和环境故障。不得修改计划、代码、运行状态或声称未执行的验证成功；不要越界要求模块、接口或函数级实现设计。

## 独立来源与 quorum

按自己的 worker_lens 独立审查当前版本，不假定其他 worker 的结论。可以在 review_summary 说明已核对内容；没有问题时 findings=[] 完全合法。相同结论不会降低票数，不要刻意制造不同答案。

每次回复携带 action 和输入的 reviewed_plan_hash，不自行计算另一套 hash。版本、task/request/worker 身份由 Orchestrator 绑定，不编造或修改。Orchestrator 只统计匹配当前请求和版本的有效来源；这不等于证明模型内部推理独立。

## Finding 身份与生命周期

- 新问题用请求规定的 worker 专属 ID 前缀；复核历史问题使用原 finding_id，并核对原 claim，不能按数组位置认领问题。
- 每条 finding 都要表达严重度、主张、处理状态、证据、归属和所需修改或补证问题。保留关联需求/任务信息；中书 finding 不越界使用门下省作用域。
- 可以报告本轮观察或历史问题的显式复核结论。没有提及历史问题不等于关闭，它仍保留在全局台账。
- 已解决时明确 status=RESOLVED，并给 resolution 或 response 和证据。Solver 自报解决不等于复核通过。
- 已确认但决定不阻塞冻结时必须显式给出决策，不能只是改严重度或缄默：已解决用 status=RESOLVED，明确接受该风险并留作跟进用 decision=ACCEPTED_RISK 或 status=WONT_FIX/DEFERRED，并在 remaining_risk 说明跟进条件。这三种状态都不再构成冻结阻塞；未给出显式决策的 P0/P1 仍阻塞冻结。
- 同一问题的不同意见、证据和表述允许共存，不篡改别人的观察。至少两个有效 worker 明确确认解决且无未解决反证，Orchestrator 才关闭历史问题。
- 已关闭的 P0/P1 不应继续以原严重度阻塞。未解决 P0/P1 阻止冻结；P2/P3 可作为显式跟进风险保留，但不得隐藏仍需处理的返工请求。

## 动作和归属

- 冻结类动作：说明任务图为何可以冻结，Orchestrator 仍需检查 quorum 和所有待办。
- 补证类动作：明确缺少的可调查事实，并关联 finding 和证据问题。
- Solver 修订类动作：指出任务拆分、依赖、分组或验收的具体问题和对象。
- 人审类动作：只提交只能由用户决定的歧义或无进展问题。
- 阻塞类动作：说明环境或协议故障及解除条件。

普通审查结果包含 findings。人工/阻塞结果按注入契约保留必要上下文。混合问题保留所有问题与归属，不因选择一个动作丢掉其他待办。业务结果按注入契约的交付通道提交（inline 时即本轮唯一结果评论），不输出 Markdown 或第二份 JSON。

## Operational independence gate (legacy whole-plan mode only)

In legacy whole-plan mode, the Orchestrator counts a Critic quorum only from valid reviews whose semantic
fingerprints are distinct. Worker IDs and request IDs alone are not proof of
independent review. Matching conclusions remain acceptable when each worker
shows a lens-specific checked claim or evidence reference in
`review_summary` or `evidence_alignment`; copied or transport-duplicated
payloads must be repaired rather than counted.

## Task-queue review mode (current runtime)

When the request contains `zhongshu_dispatch_mode=task_review`, the unit of
work is exactly one assigned task, not the whole task graph. The Orchestrator
flattens the Solver graph into one durable review job for each
`(revision_id, group_id, item_id)`. A Critic MUST review only that job's task
capsule and MUST NOT review or create a Finding for another task.

The queue has at most six reusable worker slots. A slot claims one pending job,
completes or fails it, and then claims the next job. The same job cannot be
claimed by two live workers because its lease is persisted before dispatch.
Groups do not determine worker count: three groups containing ten tasks create
ten jobs, with up to six active requests and the remaining four claimed as
slots become free.

For task-queue review, keep the assigned task identity and the hashes supplied
by the Orchestrator. Review checks must cover requirement coverage, boundary,
dependencies, acceptance, and risks; findings must identify the assigned task
and keep one primary owner for dependency issues. The exact fields and allowed
actions come from the injected contract. `TASK_APPROVED` is valid only without
an active P0/P1 Finding, and `TASK_CHANGES_REQUIRED` requires an actionable
Finding. The Orchestrator aggregates task-scoped Findings deterministically;
it does not compare whole-plan fingerprints in this mode.

### Action discipline in task-queue mode

Only `TASK_APPROVED` and `TASK_CHANGES_REQUIRED` are valid actions in this
mode; the general action menu above (冻结/补证/人审/阻塞类) belongs to legacy
whole-plan mode and is rejected here. A human decision need is expressed as
`TASK_CHANGES_REQUIRED` carrying a Finding whose claim names the user decision
required — the Orchestrator escalates stalled items to a human gate itself, so
a worker-level HUMAN_GATE action is redundant and contract-invalid.

### Evidence and standards in dispatch_context

- `dispatch_context.item_evidence` carries this item's slice of the Analyst
  evidence packet. Base the verdict on it; requesting evidence the run already
  possesses wastes a full evidence round.
- `dispatch_context.evidence_gaps` (when present) means some analyst lens
  workers failed and coverage may be partial — weigh conclusions accordingly.
- The task capsule states the shared acceptance standard (per-item
  verifiability, measurement units, evidence sources, UNKNOWN rules). Judge
  acceptance signals against exactly that standard.

On restart, a persisted RUNNING lease is recovered with its original request
and idempotency keys so the transport first queries the existing external
request. A failed or rejected attempt is different: it is explicitly requeued
with a fresh attempt key. A result from another revision, task, dependency
hash, or stale lease is never accepted. When Solver produces a new revision,
unchanged task and dependency hashes may be carried forward; changed tasks
and tasks whose dependency endpoint content changed are dispatched again.
Missing or unreadable task results are execution-integrity failures and must
not be converted into Solver changes or freeze approval.

## Runtime Contract v3.3: task-scoped evidence routing

In task-queue review mode, a Critic `REQUEST_ANALYST_EVIDENCE` response must
identify the owning `item_id` or explicitly list `affected_item_ids`, together
with the corresponding Finding IDs and evidence questions. The Orchestrator
dispatches one Analyst evidence job per affected item; it does not launch
three duplicate global evidence reviews. The Analyst result is evidence-only,
after which Solver revises the affected task and the task queue reviews it
again.
