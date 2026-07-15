---
type: Rules
title: External Source Reading
description: OKF-compatible rules for reading user-provided external requirement sources before making inferences.
resource: KnowledgeBase/framework/external-source-reading.md
tags: [knowledge-base, framework, routing, external-source, feishu, lark]
timestamp: 2026-07-13T00:00:00+08:00
---

# External Source Reading

<!-- AI:RULES - Load when a user provides or refers to an external requirement source, especially a Feishu/Lark resource. -->

## Scope

Treat user-provided external sources as authoritative input when the task depends on their content. This applies in and
outside workflows, including implementation, investigation, review, planning, and research tasks.

Do not infer source content from a URL, token, title, screenshot filename, or local code candidate.

## Feishu / Lark Routing

When the user provides a Feishu/Lark URL or token, or says that information is in Feishu/Lark, use the `feishu` entry
skill and route to the smallest matching `lark-*` skill before summarizing or using the source as a requirement.

| Resource or intent | Route |
|---|---|
| Docx document, document content, document Wiki page | `$lark-doc` |
| Wiki space, node hierarchy, or Wiki node management | `$lark-wiki` |
| Spreadsheet content | `$lark-sheets` |
| Bitable / Base content | `$lark-base` |
| Drive file, attachment, media, document comments, or file discovery | `$lark-drive` |
| Whiteboard content | `$lark-whiteboard` |
| Minutes, transcript, chapters, or AI meeting outputs | `$lark-minutes` |
| A resource embedded by a fetched document | Route again to the matching skill for that embedded resource |

For document reads, follow `$lark-doc` as the command-level source of truth. Its normal path uses the session-scoped
document-task wrapper. Use the official `docs +fetch --api-version v2 --as user` path only when `$lark-doc` directs a
fallback or manual diagnosis after the normal document-task path fails.

## Read, Diagnose, and Report

1. Read the smallest source section sufficient for the task.
2. If the source embeds a sheet, Base, whiteboard, file, or synced document, extract its token and read the embedded
   resource through the matching `lark-*` skill when its contents matter.
3. On an authorization, scope, identity, or connector failure, preserve the exact error, use `$lark-shared` or the
   applicable skill's diagnostic path, and retry only with the required escalation.
4. State the read status in the task result: successfully read, unavailable with cause, or intentionally not required.

## Degraded Handling

If an authoritative source cannot be read, do not present guessed source content as fact. Report:

- the unread source and why it could not be read;
- the user action or authorization needed;
- which conclusions are blocked or uncertain;
- any local code/config facts used separately from the unread source.

A task-specific workflow may impose a stricter gate, such as requiring successful reading before its first formal plan.
