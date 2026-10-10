# 重试协议对齐修复验收（2026-10-07）

## 修复

在纯结果预检查中独立核对假设接受权限，使用分派时 consensus 的 assumption_id 定位原记录。模型不能把新假设或未接受假设设为 accepted；已由人接受的记录可原样回传或省略，改写仍被拒绝。领域提交保护保留，未自动改写模型输出。

该权限检查在文件证据校验前执行，错误与所有源码引文错误合并反馈。因此不会因引文失败跳过这个已知权限错误。多个非法假设分别报告 analysis.assumptions[index].disposition，明确要求 set disposition="proposed"，并说明增加/更换 ID 不能获得接受权限。

分派 schema 的 disposition 描述、角色协议 prompt rules 和锁定的 requirements-analyst skill 都明确条件写入权限。状态枚举仍保留 accepted，避免破坏已有人工决定的读取和原样回传。源码引文仍严格匹配连续原文，未扩大重试预算。

## 回归

cmd/fixtures/requirements/retry-alignment-20261007.json 从最新失败任务 9d7ff3 第一次返回提取，保留全部十条无效引文和错误 accepted 假设；源码路径转换为文件名，源码文本固定在 fixture 中。

定向边界/协议测试 15 项通过：真实失败首次同时反馈十处引文及假设权限错误，一次修正同份返回通过；原输入不被改写；现有 accepted 可原样保留，改写或换 ID 被拒绝；多处假设错误同时定位。真实 OrchestratorApp 脚本一次修复后进入待审批且没有人工审批记录。

完整 Python 回归通过 742 项，114.807 秒；随后补验实际分派指令和锁定 skill 的 5 项测试通过。统一使用 cmd/run_tests.py 禁止外部测试通知。git diff --check 通过。

## 实际生效

18:42:20 左右核验 task-20261007-9d7ff3 处于 REQ_ENGINE_ERROR:6 且无待执行效果、已记录 runtime-code 失败指纹确已变化。通过正式恢复事件重启为 PID 128504，保留任务、日志和旧返回；恢复到 REQ_ANALYZE:7，work_epoch=2，新请求 task-20261007-9d7ff3:e2:7:REQ_ANALYZE:attempt-1 于 18:42:24 成功分派。

实际 dispatch JSON 第 275 行包含条件写入权限、第 478 行包含新增假设 proposed 规则，锁定 skill 指纹为 003d0ec2d066a8bebfce05fb1fae43749457eeaef2fb41f1b4f0f5ac2833193b。prompt 明确读取 active-skill.md 并以锁定 skill 为准；未运行旧的远端中书批量同步脚本。

另四个等待任务在无待执行效果的边界重载加载相同修复。未停止 Nexus 服务，未修复历史 d5080d 的独立文档授权阻塞。代码/协议及脚本验收不保证所有模型返回首次合格，也不表示完整方案已生成。

## 真实恢复返回

18:43:26，恢复后的第一次真实模型返回通过全部校验，进入 REQ_CLARIFY_PUBLISH:8；18:43:28 卡片投递后进入 REQ_CLARIFY_WAIT:9，18:43:29 独立阶段通知成功。恢复 epoch 只调用模型一次，没有修复重试。共 14 条 observed 源码事实通过逐字引文校验并拥有运行时计算的 SHA-256；3 条假设均为 proposed，并非靠丢弃全部事实或把它们设为 unavailable 通过。

当前同批 5 个澄清问题，尚无人工回答和审批，待执行效果为零。证明文件 runs/task-20261007-9d7ff3/retry-alignment-acceptance.json；原事件日志第 42、49、54 行分别为恢复、分析通过与卡片投递。这次验收证明已知失败在真实返回中得到纠正，完整草案生成/发布仍需后续回答和验收。
