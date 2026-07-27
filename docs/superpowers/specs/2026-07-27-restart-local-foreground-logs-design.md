# Restart local service with foreground logs

## Goal

Make `restart_local.ps1` restart Nexus Agents and keep the service attached to the
current PowerShell console so the operator can watch startup and runtime logs.

## Current behavior

`restart_local.ps1` stops the process listening on the selected port, then starts
`scripts/start_dev.ps1` through a hidden background PowerShell process. It polls
the health endpoint and exits once the service reports healthy. Because the
launcher is hidden and detached, logs are not visible in the invoking console.

## Chosen approach

After stopping the existing listener, invoke `scripts/start_dev.ps1` directly in
the current PowerShell process using the existing `-Port` and `-SkipWebBuild`
arguments.

This preserves the environment variables established by `restart_local.ps1` and
lets `scripts/start_dev.ps1` run `go run .\cmd\nexus-agents` in the foreground.
Standard output and standard error therefore remain visible in the current
console.

## Scope

- Keep the current port-release logic and its timeout/error behavior.
- Keep the `Port` and `SkipWebBuild` parameters.
- Remove hidden background process creation.
- Remove the background-only health polling and launcher PID reporting.
- Print a concise handoff message before the foreground service starts.
- Preserve Ctrl+C behavior: interrupting the foreground process stops the service.

## Error handling

- Missing `scripts/start_dev.ps1` remains a terminating error.
- Failure to release the selected port remains a terminating error.
- Startup/build/runtime failures are allowed to surface directly from
  `scripts/start_dev.ps1` and `go run`, with their logs visible in the console.

## Verification

1. Run PowerShell's parser against `restart_local.ps1`.
2. Review the script diff to confirm it directly invokes `scripts/start_dev.ps1`
   and no longer starts a hidden process.
3. Optionally run the script manually and confirm that Go service logs appear in
   the same window until Ctrl+C is pressed.
