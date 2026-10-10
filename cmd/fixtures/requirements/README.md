# 需求工作流验收样本

每个 case 用两个独立真实模型任务验证。脚本测试验证机制，不能代替内容评审。singleflight 原文来自用户提供的 RTC 文档，source.json 记录来源与 SHA-256；测试不依赖外部仓库。

结果目录为 `<results>/<case_id>/run-1/` 和 `run-2/`，各自包含真实 journal、snapshot 和 `manual-review.json`。不要手工生成真实派发记录。模型任务使用显式 local 通信，停在确认等待；工程师评审后再由用户或指定业务 reviewer 确认内容。

人工评审记录示例：

```json
{
  "reviewer": "姓名或账号",
  "document_digest": "当前正文 SHA-256",
  "scores": {"intent": 3, "scenarios": 3, "consistency": 3, "acceptance": 3, "coherence": 3},
  "findings": [],
  "clause_evidence": ["具体章节及条款如何覆盖 case 中的语义，或具体扣分原因"],
  "business_confirmed": false
}
```

五个评分轴分别为意图忠实、场景完整、规则一致、验收可观察、阅读连贯，0–4 分。0 是不可用，1 是重大缺失，2 是仍需业务补充，3 是可交门下省实施，4 是覆盖充分且清楚。每轴至少 3 分、无 critical finding、真实 Critic 审查通过且业务确认，才通过内容门槛。关键词出现不等于语义正确；评审必须读正文与用户来源。

`evaluate_requirements_workflow.py --offline` 不调用模型或飞书；缺样本或人工评审时返回 INCOMPLETE。`--mechanisms-only` 只输出测量数据，不能作为内容或上线验证通过。

## 2026-10-10 新增场景（draft-first / 探针并发）

三个新验收场景以脚本化单元测试覆盖（机制层），暂未录制真实模型回放样本：

- `draft-first-explicit-goal`（目标可判定：首个 Solver 派发先于任何用户问题，问题带草案标记）→ `cmd/test_requirements_draft_first.py`；
- `draft-first-vague-goal`（目标不可判定：Solver 先返回 NEEDS_CLARIFICATION 问意图）→ `cmd/test_requirements_draft_first.py`；
- `scout-partial-failure`（非 intent 探针失败：汇总带 unavailable fact，流程照常到草案）→ `cmd/test_requirements_scouts.py`。

真实模型样本（含人工内容评审）仍按上文流程补充后才能作为内容门槛依据；`evaluate_requirements_workflow.py` 对缺样本场景返回 INCOMPLETE。
