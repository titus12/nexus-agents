# Nexus 知识检索规范

访问项目 `KnowledgeBase` 前，必须先使用 Nexus 知识检索；不得先在本地浏览 `KnowledgeBase`。

## 必须调用

使用仓库根目录名作为 `{id}`，使用 `context` 模式、`6000` 个最大 Token，并将任务标题或检索问题作为 `q`。

```text
GET /api/projects/{id}/knowledge/retrieve?q={query}&mode=context&maxTokens=6000
```

仓库提供 Nexus 检索客户端时，必须使用该客户端。

## 前置门禁

- 不得因智能体已了解项目而跳过检索。
- 不得在检索前手动浏览 `KnowledgeBase` 并猜测相关文件。
- 不得以 grep、CodeGraph 或源码搜索替代检索；检索完成后，才可将它们用于核实代码事实。
- 对实施类工作流，检索成功或已记录回退前，不得规划或实现。

## 检索结果

将 `loadedKnowledgeMarkdown` 作为权威知识上下文。

对于工作流，记录 `knowledgeRetrieval`：

- `query`
- `matchedDomain`
- `requiredPaths`
- `usedTokens`
- `loadedKnowledgeMarkdown`
- 适用时记录 `fallbackUsed` / `error`

工作流最终摘要必须包含返回的 `Loaded Knowledge` 区块。

## 回退

检索不可用时，记录 `knowledgeRetrieval.error`、设置 `fallbackUsed=true`，然后以本地知识库路由作为回退，并向用户报告。
