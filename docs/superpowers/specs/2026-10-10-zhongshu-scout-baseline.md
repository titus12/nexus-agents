# 中书省 draft-first / 侦察并发化：T0 基线记录

- 日期：2026-10-10（实施前基线，对应修改文档 `2026-10-10-zhongshu-scout-draft-first-modification.md` §5 T0）
- 测试基线：`python -X utf8 cmd/run_core_tests.py` → **213 通过**（20.1s）；全量 `cmd/run_tests.py` → **775 通过**（115.5s）。改前无红。
- fixture 机制评估：`evaluate_requirements_workflow.py --offline --mechanisms-only` → 3 个场景（evolving-scope / singleflight / vague-goal）均为 INCOMPLETE（缺独立真实模型样本）。离线回放无法产出内容分数；内容门槛仍依赖真实运行 + 人工评审（见 fixtures/requirements/README.md）。

## 真实运行的墙钟基线（机器人自动验收，`multica/bot-reports/rounds.jsonl`）

说明：无法在实施前再跑 3×2 个真实 fixture 任务（成本高），按 T0 第 4 条的回退，用既有真实机器人轮次作墙钟基线，并标注"来自机器人实测，非 fixture 回放"。时间从 rounds.jsonl 的 started_at/finished_at 与引擎日志（multica/orchestrator-*.log）取整。

| 指标 | 基线值 | 来源 |
| --- | --- | --- |
| 任务总时长（成功） | 5644s（task-20261009-d673e8，reject）；3957s（task-20261009-0b2d2f，recommend） | rounds.jsonl |
| 首个 DRAFT_READY 前墙钟 | ≈1488s（d673e8）；≈1596s（0b2d2f） | 引擎日志状态时间戳 |
| 首个 DRAFT_READY 前 REQ_ANALYZE 次数 | 4（d673e8：分析→澄清×3→再分析）；2（0b2d2f，缓存命中后） | rounds.jsonl states 序列 |
| 首轮问题数 | 逐题批量澄清（d673e8 三轮各约 9/4/3 题；0d7b78 三轮阶梯式提问） | 引擎日志 |
| 首次审查就绪（REQ_REVIEW） | ≈3228s（d673e8）；≈2286s（0b2d2f，40 分钟目标达成） | 引擎日志 |
| 失败轮次（改前一周） | 0d7b78 超时 7210s（4 次 REQ_ANALYZE，含 18.3/9.7/5.1/2.9 分钟再分析递减）；fb9af2 输出体积护栏丢弃；4160e2 已答题重发被拒 | rounds.jsonl |
| 机械失败特征 | 模型逐字复制引用差一个空白即整轮重来；`REQUIREMENTS_RESULT_VALIDATION_FAILED` 为主要失败事件（requirements_agent.py:234） | 代码 + 实测 |

## 判读

1. 到首版草案的耗时主要消耗在"分析→批量提问→等待→再分析"的循环（d673e8 为 4 次 REQ_ANALYZE）；draft-first 预期直接砍掉首轮批量提问。
2. 机械门槛（逐字引用）是超时与重试的主因之一；引用回填（补充篇 T7）与格式修复免费（T8）针对此项。
3. `first_complete_draft_seconds` 指标已在 `requirements_metrics.measure()` 存在，但其完整性判定硬编码旧 8 章 kind（含 `rules`），T1 改章节集后必须同步（修改文档 V3）。

## 改后（2026-10-10，T1–T8 实施后）

- 测试：`cmd/run_core_tests.py` → **290 通过**（36.9s，基线 213）；全量 `cmd/run_tests.py` → **848 通过**（基线 775）。新增测试集：`test_requirements_document_template`、`test_requirements_draft_first`、`test_requirements_lint`、`test_requirements_provenance`、`test_requirements_quotes`、`test_requirements_budget`、`test_requirements_scouts`。
- 机制评估：`evaluate_requirements_workflow.py --offline --mechanisms-only` → MECHANISMS_ONLY，3 个既有场景仍 INCOMPLETE（缺独立真实模型样本，与基线一致）；新增三个场景（draft-first-explicit-goal / draft-first-vague-goal / scout-partial-failure）按修改文档 §6.2 的回退条款以脚本化单元测试覆盖（见 `cmd/fixtures/requirements/README.md`）。
- 机制层验收（§10 A1–A8、A10）：全部由上述测试集证明通过；A4/A5/A8/A10 见 document_template/lint/provenance 测试，A6/A7 见 scouts 测试 #8/#9。
- 墙钟对比（A9）：待下一次真实机器人轮次（R34，`--scenario recommend`）产生改后真实任务后回填本栏；脚本化运行零模型 token，不产生可比墙钟数据。`measure()` 已新增 `scout` 探针计数（fanouts/probes/probe_failures）供对比使用。
