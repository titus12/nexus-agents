---
name: skill-standard
description: "Skill 创建规范：文件结构、编写原则、触发规则、映射表。创建新 skill 时加载。"
---

# Skill 创建规范

> 触发：创建新 skill / 维护已有 skill
>
> §1 定位 §2 标准结构 §3 编写原则 §4 何时创建 §5 触发规则 §6 映射表

## §1 定位

| 类型 | 职责 | 加载时机 |
|------|------|----------|
| Rule | 全局约束 | 每次自动 |
| Agent | 角色定义 | --agent 或 subagent 派发 |
| Skill | 领域知识 | 涉及该模块时按需加载 |

文件位置：`.claude/skills/{module-name}.md`，命名用 kebab-case。

## §2 标准结构

```markdown
---
name: {module-name}
description: "{一句话描述模块和何时使用此 skill}"
---

# {模块名} Skill

> 模块: ... | 核心文件: ... | 入口: ...
>
> §1 架构 §2 核心接口 §3 数据结构 §4 扩展点 §5 配置 §6 踩坑 §7 测试 §8 变更检查

## §1 架构概览
## §2 核心接口
## §3 数据结构
## §4 扩展点
## §5 配置依赖
## §6 踩坑记录
## §7 测试方法
## §8 变更检查清单
```

## §3 编写原则

1. 每章节 ≤ 20 行，只写 agent 需要的信息
2. 扩展点要写具体到"在哪个文件的什么位置加什么"
3. 不重复 rules 中的通用规范
4. 发现新坑时追加到 §6

## §4 何时创建

| 信号 | 行动 |
|------|------|
| 某模块被修改 3+ 次 | 考虑创建 |
| 修改时经常踩坑 | 必须创建 |
| 模块有复杂的扩展模式 | 建议创建 |

## §5 触发规则

| 触发条件 | 行为 |
|----------|------|
| 代码修改涉及某模块 | 动手前 Read 对应 skill |
| bug 排查涉及某模块 | Read skill §1§6 |
| 方案设计涉及某模块 | Read skill §1§2§4 |
| 代码审核涉及某模块 | Read skill §6§8 |
| 无对应 skill | 完成后提示用户是否创建 |

## §6 关键词→Skill 映射表

| 关键词/文件路径 | 对应 Skill |
|----------------|------------|
| quest/任务/成就/目标 | `quest-system` |
| gate/网关/连接/鉴权/转发 | `cross-gate` |
| social/好友/聊天/跨服 | `cross-social` |
| proto/配置表/rawdata/xbean | `cross-config` |
| 客户端/unity/前端/联调 | `cross-client` |
| item/背包/物品/道具 | `item-system` (待创建) |
| club/公会/社团 | `club-system` (待创建) |
| shop/商店/购买 | `shop-system` (待创建) |
| island/岛屿 | `island-system` (待创建) |

此映射表随 skill 文件增加而更新。
