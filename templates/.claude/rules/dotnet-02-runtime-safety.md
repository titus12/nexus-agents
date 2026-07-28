# .NET Runtime Safety

For hosted/background services and asynchronous library code:

1. Propagate the caller or host `CancellationToken`; do not replace it with
   `CancellationToken.None` without an explicit reason.
2. Respect `IHostedService` / `BackgroundService` start-stop lifecycle and
   shutdown behavior.
3. Dispose `IDisposable` and `IAsyncDisposable` resources at their ownership
   boundary.
4. Preserve timeout, retry, idempotency, and exception behavior unless the
   requested contract changes them.
5. Keep secrets out of source and logs; use existing configuration and
   structured logging conventions.

Validate the changed execution path with actual build/test or documented
manual evidence. Do not report an unrun command as passing.
