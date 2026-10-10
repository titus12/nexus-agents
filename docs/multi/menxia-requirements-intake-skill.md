# requirements-v1 门下省入口

ACTIVE_RUNTIME_SKILL: menxia-requirements-intake

只从已批准的需求正文、决定和验收条目推导实施任务，不改需求基线。
读取 `approved_requirements` 的同一正文与审批信息，输出 canonical 实施 DAG。
`requirements` 必须保留全部 requirement_id 和对应 description；每个需求至少被一项任务覆盖。
一个审查组可以包含任意数量的任务；保留真正的依赖、兼容、验证和回退要求。
items 的字段包括 item_id、group_id、title、objective、dependencies、source_requirement_ids、
acceptance_signals、unknowns、risks、parallelizable。groups 使用 group_id 和 item_ids。
业务行为需要变化时返回 REQUIREMENTS_CHANGE_REQUESTED 和完整澄清问题；不得先改正文或批准记录。
按当前状态的结构化契约输出。如果出现 "too large to post safely"，使用结果文件，
返回绑定目录内的 result_path 与 result_sha256，不用未绑定的聊天摘要代替业务结果。
