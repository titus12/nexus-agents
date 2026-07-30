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

## Risky APIs and Synchronization

Risky APIs are lower-priority options. Prefer existing project abstractions,
simple cancellable async flows, and designs that avoid shared mutable state.

Do not introduce or change a risky API without the user's explicit approval.
This includes adding a call, changing its arguments, trigger condition, scope,
or code executed under it. Do not treat approval for one use as approval for
another use.

For synchronization, this includes adding, removing, or replacing a `lock`;
changing its lock object, critical-section scope, acquisition order, nesting,
or code executed while the lock is held.

Risky APIs include:
- synchronization: `lock`, `Monitor`, `Mutex`, `SemaphoreSlim`,
  `ReaderWriterLockSlim`, `WaitHandle`, explicit `Thread` creation, and
  blocking waits;
- host or process control: `StopApplication`, `Environment.Exit`,
  `Environment.FailFast`, `Process.Start`, and `Process.Kill`;
- destructive file, database, cache, registry, or external-service operations;
- native/unsafe interop, dynamic assembly/code loading, global runtime changes,
  security or authorization changes, and network exposure changes.

Before requesting approval, state:
1. the exact API and intended call site;
2. the protected/shared state or affected external resource;
3. lock ordering, nesting, blocking/callback risks, and cancellation/timeout
   behavior when synchronization is involved;
4. safer alternatives considered and why they are insufficient;
5. the smallest behavior-based validation plan.

Without explicit approval, stop before editing the production code. Do not
silently replace a risky API with an equivalent risky API to avoid approval.

Do not use `Thread.Abort()` or uncancellable, unbounded blocking waits such as
`.Wait()`, `.Result`, or `WaitOne()` in production code unless the user
explicitly requests them.
