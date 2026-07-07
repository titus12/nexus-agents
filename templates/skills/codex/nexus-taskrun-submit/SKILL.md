---
name: nexus-taskrun-submit
description: Submit a completed workflow result to Nexus Task Runs. Use at workflow end after evidence is ready.
---

# nexus-taskrun-submit

## Purpose

Use this skill at the **end** of a Go or Unity workflow after you already have:

- final status
- verification evidence
- changed files summary
- remaining risks / skipped checks

This skill does **not** execute the workflow itself. It only packages the result and submits it to Nexus.

## When to call

Call this skill only when one of these is true:

1. The workflow has completed successfully.
2. The workflow partially succeeded and you still want evaluation coverage.
3. The workflow failed and you want the failure captured in Nexus.

Do **not** call it in the middle of implementation.

## Required input

Before calling, prepare a TaskRun payload with:

- `projectId`
- `workflowTemplateId`
- `workflowCopyId` if known
- `workflowType`
- `taskTitle`
- `submittedStatus`
- `context`
- `metrics`
- `evidence`

## Submission path

Preferred:

```text
go run .\cmd\nexus-agents submit-task-run --file <task-run-payload.json>
```

Fallback when Nexus HTTP API is unavailable but local direct write is acceptable:

```text
go run .\cmd\nexus-agents submit-task-run --file <task-run-payload.json> --use-store
```

## Included helper assets

This skill package includes:

- `task-run-template.json` — a minimal payload template you can copy and fill
- `submit-workflow-result.ps1` — a lightweight PowerShell wrapper around `submit-task-run`

Recommended usage:

```text
powershell -ExecutionPolicy Bypass -File .agents\skills\nexus-taskrun-submit\submit-workflow-result.ps1 -PayloadFile <task-run-payload.json>
```

## Execution notes

1. Write the TaskRun JSON payload to a local file first.
2. Run the submit command.
3. If submission succeeds, include the created TaskRun ID in the final report.
4. If submission fails, include:
   - the exact command used
   - the error
   - the full JSON payload in the final response so it can be submitted later

## Minimal final report addition

After submission, append one line like:

```text
Nexus TaskRun submitted: <task-run-id>
```

If submission failed:

```text
Nexus TaskRun submission failed; payload attached for manual submission.
```
