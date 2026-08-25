# 03 门下省方案审议协议

## 1. 输入

门下省只接收中书省的冻结方案：

```json
{
  "frozen_plan": {
    "groups": [
      {
        "id": "group-000001",
        "items": [
          {
            "id": "item-000001",
            "title": "...",
            "description": "..."
          }
        ]
      }
    ]
  },
  "findings": [],
  "evidence_packet": {}
}
```

## 2. 逐任务顺序

门下省不并发跳过任务，固定按顺序处理：

```text
选择当前 group/item
  -> MENXIA_ITEM_SOLVER
  -> MENXIA_ITEM_ANALYST
  -> MENXIA_ITEM_CRITIC
  -> item 通过
  -> 下一个 item
  -> 组门禁
```

## 3. Item Solver

每个任务必须形成实现方案，至少包括：

- 要解决的具体问题；
- 预期修改的代码目录和文件；
- API/数据/状态变化；
- 技术实现步骤；
- 测试用例；
- 兼容性、性能和回滚策略；
- 不能修改的范围。

## 4. Item Analyst：正向审查

使用第一性原理追问：

1. 任务的真实目标是什么？
2. 方案是否直接解决目标？
3. 方案依赖的事实是否存在证据？
4. 修改范围是否足够但不过度？
5. 失败路径、边界条件和测试是否覆盖？

允许输出：

```text
FEASIBLE
EVIDENCE_SUFFICIENT
REQUEST_SOLVER_REVISION
HUMAN_GATE
```

## 5. Item Critic：反向审查

从对抗性角度检查：

- 如果实现只完成一半，哪里会漏？
- 是否扩大了不必要的文件和模块范围？
- 是否破坏既有状态、协议或兼容性？
- 是否会产生空 item、伪通过或无法验证的结论？
- 是否有回滚和失败处理？

P0/P1 发现必须把 item 打回 Solver；P2 可进入人工门禁，不能直接当作通过。

## 6. 组门禁

组门禁只在组内全部 item 通过后执行。以下任一情况禁止通过：

- item 未完成；
- item 级 P0/P1 active；
- Analyst/Critic 结果缺失；
- 组内存在 unresolved finding；
- 实现方案与中书省冻结方案不一致。
