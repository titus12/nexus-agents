# 门下省组级流程对齐

用户授权梳理并对齐流程、门下省本地 skill 和 Multica skill。生产采用 MENXIA_GROUP_SOLVER → MENXIA_GROUP_ANALYST → MENXIA_GROUP_CRITIC → MENXIA_GROUP_GATE 的共享文档模式。此前 skill 仍以单条目流程为主，初始骨架缺少校验器要求的六个正文小节，格式错误直接被标记 BLOCKED。

统一正文标题定义，骨架、派发提示、Python 契约均引用该定义。首轮识别空骨架或仅含标题的骨架，填写保持 v1（兼容旧适配器首次升到 v2）；后续 Solver 修订精确 +1，reviewer 保持版本且只修改建议段，签核由编排器管理。空小节不能作为有效方案。

只对 Solver 的可修复正文结构错误提供组内自动纠错：缺/空小节、顺序、未声明 touched_scope 等。保存错误和被拒绝的草稿作为修复输入，权威正文保持原样。组级 reply_retry_count 不消耗内容 revision_round，默认最多三次自动纠错；耗尽仍明确阻塞。组身份、基线、历史账本和签核篡改不自动放行。成功组和冻结需求不丢失、不重跑。

首轮成功必须进入 Analyst，不能用 READY_FOR_CRITIC 跳过证据审查。三份本地 skill 以组级模式为主，按当前 target_state 和 mode 分流旧单条目及 Gate；保留真实证据、反证、数值纪律和单一批准需求文档要求。Multica 三个 agent 指令及三个门下省技能同步回读，模型/运行配置/技能绑定保持。

验证实际旧回复离线重放、骨架填写、空标题拒绝、组内预算与持久化、保护段篡改、多个组不重跑、脚本化应用收敛。使用 cmd/run_tests.py，禁止外部通知和在线模型验证，不修改当前运行的持久状态。

核对过程中补充：接通编排器签收并验证显式 reviewed_version/hash；当前版本 Analyst 未签通过时
Critic 批准回到补证。签收 parse/render 不重复状态词或因备注含“通过”误批准。原作者通过
confirmed_rejection_ids/reaffirmed_rejection_ids 结算自己的 pending 反驳，编排器记账。
真实组人工问题透传且不接受其草稿改动；Solver 空问题请求走相同有界回复纠错。
