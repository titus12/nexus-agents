"""Bounded worker admission for the immutable workflow runtime."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import logging
import threading
import time
import uuid

from .ports import AdmissionKey, LeaseReceipt


logger = logging.getLogger("review_orchestrator_fsm")


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


class ConcurrencyError(RuntimeError):
    """Base class for admission failures."""


class DuplicateLeaseError(ConcurrencyError):
    """The same logical worker already owns a live lease."""


@dataclass(frozen=True)
class ConcurrencySnapshot:
    global_inflight: int
    task_inflight: dict[str, int]
    phase_inflight: dict[str, int]
    active_leases: tuple[LeaseReceipt, ...]


class RuntimeConcurrencyAdmission:
    """Thread-safe global/task/phase admission for external workers."""

    def __init__(
        self,
        limits: ConcurrencyLimits | None = None,
        clock: object | None = None,
    ) -> None:
        self.limits = limits or ConcurrencyLimits()
        self.limits.validate()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._leases: dict[str, LeaseReceipt] = {}
        self._logical_keys: dict[AdmissionKey, str] = {}
        self._lock = threading.RLock()

    def try_acquire(
        self,
        key: AdmissionKey,
        *,
        log_rejection: bool = True,
    ) -> LeaseReceipt | None:
        self._validate_key(key)
        with self._lock:
            self._expire_locked(self._now())
            if key in self._logical_keys:
                raise DuplicateLeaseError(f"active lease already exists for {key}")
            snapshot = self._snapshot_locked()
            if key.external_target_key:
                external_target_inflight = sum(
                    1
                    for lease in snapshot.active_leases
                    if lease.key.external_target_key == key.external_target_key
                )
                if external_target_inflight:
                    self._log_rejection(
                        key,
                        "external_target_limit",
                        external_target_inflight,
                        1,
                        log_rejection,
                    )
                    return None
            phase_limit = self._phase_limit(key.phase)
            if snapshot.global_inflight >= self.limits.global_max:
                self._log_rejection(key, "global_limit", snapshot.global_inflight, self.limits.global_max, log_rejection)
                return None
            if snapshot.task_inflight.get(key.task_id, 0) >= self.limits.per_task_max:
                self._log_rejection(key, "task_limit", snapshot.task_inflight.get(key.task_id, 0), self.limits.per_task_max, log_rejection)
                return None
            if snapshot.phase_inflight.get(key.phase, 0) >= phase_limit:
                self._log_rejection(key, "phase_limit", snapshot.phase_inflight.get(key.phase, 0), phase_limit, log_rejection)
                return None
            now = self._now()
            lease = LeaseReceipt(
                lease_id=f"lease-{uuid.uuid4().hex}",
                key=key,
                acquired_at=now.isoformat(),
                expires_at=str(now.timestamp() + self.limits.lease_ttl_seconds),
            )
            self._leases[lease.lease_id] = lease
            self._logical_keys[key] = lease.lease_id
            logger.info(
                "CONCURRENCY_LEASE_ACQUIRED lease_id=%s task_id=%s phase=%s worker_id=%s revision_id=%s",
                lease.lease_id,
                key.task_id,
                key.phase,
                key.worker_id,
                key.revision_id,
            )
            return lease

    def acquire(
        self,
        key: AdmissionKey,
        deadline: float,
        *,
        poll_interval: float = 0.5,
        stop_event: threading.Event | None = None,
        heartbeat: object | None = None,
    ) -> LeaseReceipt | None:
        """Wait for capacity until the supplied monotonic deadline."""

        interval = max(0.05, float(poll_interval))
        while True:
            if callable(heartbeat):
                heartbeat()
            lease = self.try_acquire(key, log_rejection=False)
            if lease is not None:
                return lease
            if stop_event is not None and stop_event.is_set():
                return None
            remaining = float(deadline) - time.monotonic()
            if remaining <= 0:
                logger.warning(
                    "CONCURRENCY_WAIT_TIMEOUT task_id=%s phase=%s worker_id=%s revision_id=%s",
                    key.task_id,
                    key.phase,
                    key.worker_id,
                    key.revision_id,
                )
                return None
            wait_seconds = min(interval, remaining)
            if stop_event is not None:
                stop_event.wait(wait_seconds)
            else:
                time.sleep(wait_seconds)

    def refresh(self, lease_id: str) -> bool:
        with self._lock:
            self._expire_locked(self._now())
            lease = self._leases.get(lease_id)
            if lease is None:
                return False
            now = self._now()
            self._leases[lease_id] = replace(
                lease,
                expires_at=str(now.timestamp() + self.limits.lease_ttl_seconds),
            )
            return True

    def release(self, lease_id: str) -> bool:
        with self._lock:
            lease = self._leases.pop(lease_id, None)
            if lease is None:
                return False
            self._logical_keys.pop(lease.key, None)
            logger.info("CONCURRENCY_LEASE_RELEASED lease_id=%s", lease_id)
            return True

    def expire(self, now: datetime | None = None) -> list[LeaseReceipt]:
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

    def _expire_locked(self, now: datetime) -> list[LeaseReceipt]:
        expired: list[LeaseReceipt] = []
        timestamp = now.timestamp()
        for lease_id, lease in list(self._leases.items()):
            if float(lease.expires_at) <= timestamp:
                expired.append(lease)
                self._leases.pop(lease_id, None)
                self._logical_keys.pop(lease.key, None)
                logger.warning("CONCURRENCY_LEASE_EXPIRED lease_id=%s", lease_id)
        return expired

    def _snapshot_locked(self) -> ConcurrencySnapshot:
        task_inflight: dict[str, int] = {}
        phase_inflight: dict[str, int] = {}
        for lease in self._leases.values():
            task_inflight[lease.key.task_id] = task_inflight.get(lease.key.task_id, 0) + 1
            phase_inflight[lease.key.phase] = phase_inflight.get(lease.key.phase, 0) + 1
        return ConcurrencySnapshot(
            global_inflight=len(self._leases),
            task_inflight=task_inflight,
            phase_inflight=phase_inflight,
            active_leases=tuple(self._leases.values()),
        )

    def _now(self) -> datetime:
        value = self._clock() if callable(self._clock) else None
        return value if isinstance(value, datetime) else datetime.now(timezone.utc)

    @staticmethod
    def _validate_key(key: AdmissionKey) -> None:
        if not isinstance(key, AdmissionKey):
            raise TypeError("admission requires an AdmissionKey")
        for name in ("task_id", "phase", "worker_id", "revision_id"):
            if not getattr(key, name):
                raise ValueError(f"admission key {name} must not be empty")

    @staticmethod
    def _log_rejection(
        key: AdmissionKey,
        reason: str,
        inflight: int,
        limit: int,
        enabled: bool,
    ) -> None:
        if enabled:
            logger.info(
                "CONCURRENCY_LIMIT_REJECTED task_id=%s phase=%s worker_id=%s revision_id=%s reason=%s inflight=%s limit=%s",
                key.task_id,
                key.phase,
                key.worker_id,
                key.revision_id,
                reason,
                inflight,
                limit,
            )


ConcurrencyAdmission = RuntimeConcurrencyAdmission


__all__ = [
    "ConcurrencyAdmission",
    "ConcurrencyError",
    "ConcurrencyLimits",
    "ConcurrencySnapshot",
    "DuplicateLeaseError",
    "RuntimeConcurrencyAdmission",
]
