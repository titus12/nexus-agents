from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import threading
import time
import uuid
from typing import Any
import logging


logger = logging.getLogger("review_orchestrator_fsm")


class ConcurrencyError(RuntimeError):
    pass


class DuplicateLeaseError(ConcurrencyError):
    pass


@dataclass(frozen=True)
class ConcurrencyLimits:
    global_max: int = 6
    per_task_max: int = 3
    analyst_max: int = 3
    critic_max: int = 3
    lease_ttl_seconds: int = 900

    def validate(self) -> None:
        for name in (
            "global_max",
            "per_task_max",
            "analyst_max",
            "critic_max",
            "lease_ttl_seconds",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be >= 1")


@dataclass(frozen=True)
class ConcurrencyLease:
    lease_id: str
    task_id: str
    phase: str
    worker_id: str
    revision_id: str
    acquired_at: str
    expires_at: str


@dataclass(frozen=True)
class ConcurrencySnapshot:
    global_inflight: int
    task_inflight: dict[str, int]
    phase_inflight: dict[str, int]
    active_leases: tuple[ConcurrencyLease, ...]


class ConcurrencyAdmission:
    """Thread-safe admission gate for all parallel LLM work.

    The gate is deliberately synchronous because the current orchestrator is
    synchronous. Callers must acquire a lease before dispatch and release it
    only after the remote request is terminal or explicitly reconciled.
    """

    def __init__(
        self,
        limits: ConcurrencyLimits | None = None,
        clock: Any | None = None,
    ) -> None:
        self.limits = limits or ConcurrencyLimits()
        self.limits.validate()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._leases: dict[str, ConcurrencyLease] = {}
        self._logical_keys: dict[tuple[str, str, str, str], str] = {}
        self._lock = threading.RLock()

    def try_acquire(
        self,
        task_id: str,
        phase: str,
        worker_id: str,
        revision_id: str,
        *,
        log_rejection: bool = True,
    ) -> ConcurrencyLease | None:
        key = (task_id, phase, worker_id, revision_id)
        with self._lock:
            self._expire_locked(self._now())
            if key in self._logical_keys:
                raise DuplicateLeaseError(
                    f"active lease already exists for {task_id}/{phase}/{worker_id}/{revision_id}"
                )
            snapshot = self._snapshot_locked()
            phase_limit = self._phase_limit(phase)
            if snapshot.global_inflight >= self.limits.global_max:
                if log_rejection:
                    logger.info(
                        "CONCURRENCY_LIMIT_REJECTED task_id=%s phase=%s worker_id=%s "
                        "revision_id=%s reason=global_limit global_inflight=%s limit=%s",
                        task_id,
                        phase,
                        worker_id,
                        revision_id,
                        snapshot.global_inflight,
                        self.limits.global_max,
                    )
                return None
            if snapshot.task_inflight.get(task_id, 0) >= self.limits.per_task_max:
                if log_rejection:
                    logger.info(
                        "CONCURRENCY_LIMIT_REJECTED task_id=%s phase=%s worker_id=%s "
                        "revision_id=%s reason=task_limit task_inflight=%s limit=%s",
                        task_id,
                        phase,
                        worker_id,
                        revision_id,
                        snapshot.task_inflight.get(task_id, 0),
                        self.limits.per_task_max,
                    )
                return None
            if snapshot.phase_inflight.get(phase, 0) >= phase_limit:
                if log_rejection:
                    logger.info(
                        "CONCURRENCY_LIMIT_REJECTED task_id=%s phase=%s worker_id=%s "
                        "revision_id=%s reason=phase_limit phase_inflight=%s limit=%s",
                        task_id,
                        phase,
                        worker_id,
                        revision_id,
                        snapshot.phase_inflight.get(phase, 0),
                        phase_limit,
                    )
                return None
            now = self._now()
            lease = ConcurrencyLease(
                lease_id=f"lease-{uuid.uuid4().hex}",
                task_id=task_id,
                phase=phase,
                worker_id=worker_id,
                revision_id=revision_id,
                acquired_at=now.isoformat(),
                expires_at=(
                    now.timestamp() + self.limits.lease_ttl_seconds
                ).__str__(),
            )
            self._leases[lease.lease_id] = lease
            self._logical_keys[key] = lease.lease_id
            snapshot = self._snapshot_locked()
            logger.info(
                "CONCURRENCY_LEASE_ACQUIRED lease_id=%s task_id=%s phase=%s "
                "worker_id=%s revision_id=%s global_inflight=%s task_inflight=%s "
                "phase_inflight=%s",
                lease.lease_id,
                task_id,
                phase,
                worker_id,
                revision_id,
                snapshot.global_inflight,
                snapshot.task_inflight.get(task_id, 0),
                snapshot.phase_inflight.get(phase, 0),
            )
            return lease

    def wait_acquire(
        self,
        task_id: str,
        phase: str,
        worker_id: str,
        revision_id: str,
        *,
        timeout_seconds: float,
        poll_interval: float = 0.5,
        stop_event: threading.Event | None = None,
        heartbeat: Any | None = None,
    ) -> ConcurrencyLease | None:
        """Wait for capacity without rejecting a valid queued worker.

        The caller owns the worker timeout.  A quiet retry loop avoids filling
        the logs with identical limit messages while another worker releases
        its lease.  ``None`` means the deadline or stop event was reached.
        """
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        interval = max(0.05, float(poll_interval))
        while True:
            if heartbeat is not None:
                heartbeat()
            lease = self.try_acquire(
                task_id,
                phase,
                worker_id,
                revision_id,
                log_rejection=False,
            )
            if lease is not None:
                return lease
            if stop_event is not None and stop_event.is_set():
                return None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning(
                    "CONCURRENCY_WAIT_TIMEOUT task_id=%s phase=%s worker_id=%s "
                    "revision_id=%s timeout_seconds=%s",
                    task_id,
                    phase,
                    worker_id,
                    revision_id,
                    timeout_seconds,
                )
                return None
            wait_seconds = min(interval, remaining)
            if stop_event is not None:
                stop_event.wait(wait_seconds)
            else:
                time.sleep(wait_seconds)

    def release(self, lease_id: str) -> bool:
        with self._lock:
            lease = self._leases.pop(lease_id, None)
            if lease is None:
                return False
            self._logical_keys.pop(
                (lease.task_id, lease.phase, lease.worker_id, lease.revision_id),
                None,
            )
            logger.info(
                "CONCURRENCY_LEASE_RELEASED lease_id=%s task_id=%s phase=%s "
                "worker_id=%s revision_id=%s",
                lease.lease_id,
                lease.task_id,
                lease.phase,
                lease.worker_id,
                lease.revision_id,
            )
            return True

    def refresh(self, lease_id: str) -> bool:
        """Extend a live lease without changing its logical ownership."""
        with self._lock:
            self._expire_locked(self._now())
            lease = self._leases.get(lease_id)
            if lease is None:
                return False
            now = self._now()
            refreshed = replace(
                lease,
                expires_at=str(now.timestamp() + self.limits.lease_ttl_seconds),
            )
            self._leases[lease_id] = refreshed
            logger.debug(
                "CONCURRENCY_LEASE_REFRESHED lease_id=%s task_id=%s phase=%s "
                "worker_id=%s revision_id=%s",
                refreshed.lease_id,
                refreshed.task_id,
                refreshed.phase,
                refreshed.worker_id,
                refreshed.revision_id,
            )
            return True

    def expire(self, now: datetime | None = None) -> list[ConcurrencyLease]:
        with self._lock:
            return self._expire_locked(now or self._now())

    def snapshot(self) -> ConcurrencySnapshot:
        with self._lock:
            self._expire_locked(self._now())
            return self._snapshot_locked()

    def _phase_limit(self, phase: str) -> int:
        normalized = phase.upper()
        if "ANALYST" in normalized:
            return self.limits.analyst_max
        if "CRITIC" in normalized:
            return self.limits.critic_max
        return self.limits.per_task_max

    def _expire_locked(self, now: datetime) -> list[ConcurrencyLease]:
        expired: list[ConcurrencyLease] = []
        timestamp = now.timestamp()
        for lease_id, lease in list(self._leases.items()):
            if float(lease.expires_at) <= timestamp:
                expired.append(lease)
                self._leases.pop(lease_id, None)
                self._logical_keys.pop(
                    (lease.task_id, lease.phase, lease.worker_id, lease.revision_id),
                    None,
                )
                logger.warning(
                    "CONCURRENCY_LEASE_EXPIRED lease_id=%s task_id=%s phase=%s "
                    "worker_id=%s revision_id=%s",
                    lease.lease_id,
                    lease.task_id,
                    lease.phase,
                    lease.worker_id,
                    lease.revision_id,
                )
        return expired

    def _snapshot_locked(self) -> ConcurrencySnapshot:
        task_inflight: dict[str, int] = {}
        phase_inflight: dict[str, int] = {}
        for lease in self._leases.values():
            task_inflight[lease.task_id] = task_inflight.get(lease.task_id, 0) + 1
            phase_inflight[lease.phase] = phase_inflight.get(lease.phase, 0) + 1
        return ConcurrencySnapshot(
            global_inflight=len(self._leases),
            task_inflight=task_inflight,
            phase_inflight=phase_inflight,
            active_leases=tuple(self._leases.values()),
        )

    def _now(self) -> datetime:
        value = self._clock()
        return value if isinstance(value, datetime) else datetime.now(timezone.utc)
