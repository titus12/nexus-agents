---
name: nexus-taskrun-submit
description: Initialize and submit compact local Nexus TaskRun payloads at workflow start and end.
---

# nexus-taskrun-submit

## Lifecycle

This skill owns one local payload for one workflow:

```text
start: create .nexus/task-run-<workflow>.json with sessionId and startedAt
submit: complete the same payload and POST it exactly once to Nexus
```

The local start stage does not call `POST /api/workflow-runs/start`. Existing workflow-run headers may still be used for model-routing telemetry, but `workflowRunId` and workflow IDs must not appear in the final TaskRun payload.

## Start

Before knowledge retrieval, planning, subagent dispatch, or edits, initialize a local payload. Use an ASCII task title for shell-safe JSON.

Cross-platform:

```text
node .agents/skills/nexus-taskrun-submit/taskrun.mjs start --projectId <projectId> --workflowType <workflowType> --taskTitle "<ascii task title>" --payloadFile .nexus/task-run-<workflowType>.json --contextFile .nexus/workflow-context-<workflowType>.json
```

Windows PowerShell:

```text
powershell -ExecutionPolicy Bypass -File .agents\skills\nexus-taskrun-submit\start-workflow-run.ps1 -ProjectId <projectId> -WorkflowType <workflowType> -TaskTitle "<ascii task title>" -PayloadFile .nexus\task-run-<workflowType>.json -ContextFile .nexus\workflow-context-<workflowType>.json
```

Continue only after the payload exists and contains `sessionId` and `startedAt`. If initialization fails, stop and report the exact error.

## Submit

Before submission, update the same payload with:

```text
submittedStatus
endedAt
metrics.filesChangedCount
metrics.testRunCount
evidence.summary
evidence.changedFiles
evidence.planCompliance
evidence.verification
evidence.skippedChecks
evidence.remainingRisks
```

The body must include:

```text
projectId, workflowType, taskTitle, submittedStatus,
sessionId, startedAt, endedAt, metrics, evidence
```

Do not include:

```text
workflowId, workflowRunId, workflowTemplateId, workflowCopyId,
X-Nexus-Workflow-Run-Id
```

Submit exactly once:

```text
node .agents/skills/nexus-taskrun-submit/taskrun.mjs submit --payloadFile .nexus/task-run-<workflowType>.json --contextFile .nexus/workflow-context-<workflowType>.json
```

or:

```text
powershell -ExecutionPolicy Bypass -File .agents\skills\nexus-taskrun-submit\submit-workflow-result.ps1 -PayloadFile .nexus\task-run-<workflowType>.json -ContextFile .nexus\workflow-context-<workflowType>.json
```

Keep the `.nexus/task-run-*.json` payload and any context/snapshot JSON after either success or failure. The final response must report the retained paths.

## Compact evidence rules

- Keep `summary` to one short sentence.
- Use project-relative `changedFiles`.
- For implementation workflows, record `planCompliance.items` with plan item ID, status, and short implementation or verification evidence; record unapproved scope additions in `planCompliance.unexpectedChanges`.
- Omit empty and unknown fields.
- Use concise command labels; do not embed long logs or full diffs.
- Record skipped checks and remaining risks with short reasons.
- Do not submit token, route, duration, or model metrics; Nexus attributes those from `sessionId + startedAt + endedAt`.

## Included files

- `taskrun.mjs` — cross-platform start/submit helper.
- `start-workflow-run.ps1` — Windows start helper.
- `submit-workflow-result.ps1` — Windows submit helper.
- `task-run-template.json` — minimal local payload example.
