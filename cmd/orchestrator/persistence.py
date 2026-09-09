from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .context import CURRENT_SCHEMA_VERSION, StateContext
from .events import Event
from .transitions import Transition


logger = logging.getLogger("review_orchestrator_fsm")


class PersistenceError(RuntimeError):
    pass


class PersistenceConflict(PersistenceError):
    pass


class JsonStateStore:
    """Atomic state/event persistence; the caller must hold TaskLock."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "state.json"
        self.event_path = self.root / "events.jsonl"

    def save_state(self, ctx: StateContext, expected_version: int | None = None) -> None:
        value = ctx.to_dict()
        current_version = int(value.get("state_version", 0) or 0)
        if expected_version is not None and current_version != expected_version:
            raise PersistenceConflict(f"state version conflict: expected {expected_version}, got {current_version}")
        next_version = current_version + 1
        value["state_version"] = next_version
        value["schema_version"] = CURRENT_SCHEMA_VERSION
        self._atomic_write(self.state_path, value)
        # Do not advance the in-memory version until the complete document is
        # committed. A failed write must remain retryable with the old version.
        ctx.state_version = next_version

    def append_event(
        self,
        ctx: StateContext,
        event: Event,
        transition: Transition | None,
    ) -> None:
        record: dict[str, Any] = {
            "sequence": ctx.sequence,
            "task_id": ctx.task_id,
            "event": event.name,
            "payload": event.payload,
            "created_at": event.created_at,
            # Keep only recovery-critical fields here.  Full business
            # payloads already live in state/artifacts and must not be copied
            # into every event record.
            "state_after": {
                "workflow_state": ctx.workflow_state,
                "current_phase": ctx.current_phase,
                "current_role": ctx.current_role,
                "expected_agent_id": ctx.expected_agent_id,
                "active_request_id": ctx.active_request_id,
                "dispatch_status": ctx.dispatch_status,
                "dispatch_operation_id": ctx.dispatch_operation_id,
                "dispatch_external_message_id": ctx.dispatch_external_message_id,
                "dispatch_idempotency_key": ctx.dispatch_idempotency_key,
                "entered_at": ctx.entered_at,
                "resume_state": ctx.resume_state,
            },
        }
        if transition:
            record.update({
                "from_state": transition.from_state,
                "to_state": transition.to_state,
                "transition_reason": transition.reason,
            })
        self.event_path.parent.mkdir(parents=True, exist_ok=True)
        with self.event_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def persist(
        self,
        ctx: StateContext,
        event: Event | None,
        transition: Transition | None,
    ) -> None:
        # State is the recovery source of truth.  Save the fully prepared
        # state first, then append the audit record.  This makes an interrupted
        # transition resumable even when the event append never completes.
        # ``reconcile_sequence`` tolerates that one-record audit lag.
        self.save_state(ctx)
        if event is not None:
            try:
                self.append_event(ctx, event, transition)
            except Exception:
                # The state commit is already durable.  An audit append
                # failure must not roll a valid workflow into STATE_CORRUPTED;
                # the next load will treat the event log as lagging.
                logger.exception(
                    "PERSISTENCE_EVENT_APPEND_DEFERRED task_id=%s sequence=%s event=%s",
                    ctx.task_id,
                    ctx.sequence,
                    event.name,
                )

    def load(self) -> StateContext:
        if not self.state_path.exists() and not self._backup_path(self.state_path).exists():
            raise PersistenceError(f"missing state file: {self.state_path}")
        recovered_from_backup = False
        try:
            value = self._read_state_file(self.state_path)
        except Exception as primary_error:
            backup_path = self._backup_path(self.state_path)
            if not backup_path.exists():
                raise PersistenceError(f"invalid state file: {self.state_path}") from primary_error
            try:
                value = self._read_state_file(backup_path)
                self._copy_atomic(backup_path, self.state_path)
                recovered_from_backup = True
                logger.error(
                    "STATE_PRIMARY_RECOVERED_FROM_BACKUP path=%s backup=%s error=%s",
                    self.state_path,
                    backup_path,
                    str(primary_error)[:300],
                )
            except Exception as backup_error:
                raise PersistenceError(f"invalid state file: {self.state_path}") from backup_error
        try:
            version = str(value.get("schema_version", "3.0"))
            if version > CURRENT_SCHEMA_VERSION:
                raise PersistenceError(
                    f"state schema {version} is newer than supported {CURRENT_SCHEMA_VERSION}"
                )
            if version < CURRENT_SCHEMA_VERSION:
                value = self._migrate(value, version)
                self._backup_state()
                self._atomic_write(self.state_path, value)
            ctx = StateContext.from_dict(value)
            self.reconcile_sequence(ctx)
            if recovered_from_backup:
                ctx.last_error = {
                    "code": "STATE_RECOVERED_FROM_BACKUP",
                    "message": "primary state.json was invalid and restored from state.json.bak",
                }
            return ctx
        except Exception as error:
            raise PersistenceError(f"invalid state file: {self.state_path}") from error

    @staticmethod
    def _backup_path(path: Path) -> Path:
        return path.with_name(path.name + ".bak")

    @staticmethod
    def _read_state_file(path: Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        try:
            StateContext.validate_serialized(value)
        except (TypeError, ValueError) as error:
            raise PersistenceError(f"state file has invalid field types: {path}") from error
        return value

    def event_sequences(self) -> list[int]:
        return [int(value["sequence"]) for value in self._event_records()]

    def _event_records(self) -> list[dict[str, Any]]:
        if not self.event_path.exists():
            return []
        records: list[dict[str, Any]] = []
        lines = self.event_path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise TypeError("event record is not an object")
                int(value["sequence"])
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
                # A process can be terminated after opening the append file
                # but before the final newline is durable.  Ignore only that
                # incomplete tail record; corruption in the middle remains a
                # hard error because it invalidates audit ordering.
                if index == len(lines) - 1:
                    logger.warning(
                        "PERSISTENCE_PARTIAL_EVENT_IGNORED path=%s line=%s error=%s",
                        self.event_path,
                        index + 1,
                        str(error)[:200],
                    )
                    break
                raise PersistenceError("invalid event log record") from error
            records.append(value)
        return records

    def reconcile_sequence(self, ctx: StateContext) -> int:
        records = self._event_records()
        if not records:
            return ctx.sequence
        sequences = [int(value["sequence"]) for value in records]
        if any(right != left + 1 for left, right in zip(sequences, sequences[1:])):
            raise PersistenceError("event sequence contains a gap")
        last_event = sequences[-1]
        if last_event > ctx.sequence:
            # This is only expected for state files written by the older
            # event-first protocol, or for a crash between event append and
            # state save.  Recover the destination state from the durable
            # transition and let the normal state enter/dispatch path rebuild
            # its request on the next startup.
            if last_event != ctx.sequence + 1:
                raise PersistenceError("event log is ahead of state by more than one transition")
            record = records[-1]
            target = str(record.get("to_state") or "").strip()
            source = str(record.get("from_state") or "").strip()
            state_after = record.get("state_after")
            recorded_state = (
                str(state_after.get("workflow_state") or "").strip()
                if isinstance(state_after, dict)
                else ""
            )
            if (
                str(record.get("task_id") or "") != ctx.task_id
                or not target
                or (source and source != ctx.workflow_state)
                or (recorded_state and recorded_state != target)
            ):
                raise PersistenceError("event/state transition binding mismatch")
            ctx.workflow_state = target
            ctx.sequence = last_event
            if isinstance(state_after, dict):
                for name in (
                    "current_phase", "current_role", "expected_agent_id",
                    "active_request_id", "dispatch_status", "dispatch_operation_id",
                    "dispatch_external_message_id", "dispatch_idempotency_key",
                    "entered_at", "resume_state",
                ):
                    if name in state_after:
                        setattr(ctx, name, state_after[name])
            else:
                ctx.active_request_id = ""
                ctx.dispatch_status = "none"
                ctx.dispatch_operation_id = None
                ctx.dispatch_external_message_id = None
                ctx.dispatch_idempotency_key = None
            self.save_state(ctx)
        elif ctx.sequence > last_event:
            # With state-first persistence the last audit record may be
            # missing after a crash or a transient append failure.  The state
            # already contains the complete recovery data, so do not mark the
            # workflow corrupted merely because its audit trail lags by one
            # or more records.
            logger.warning(
                "PERSISTENCE_EVENT_LOG_BEHIND state_sequence=%s last_event_sequence=%s path=%s",
                ctx.sequence,
                last_event,
                self.event_path,
            )
        return ctx.sequence

    @staticmethod
    def _migrate(value: dict[str, Any], version: str) -> dict[str, Any]:
        if version not in {"3.0", "3.1"}:
            raise PersistenceError(f"unsupported schema version: {version}")
        migrated = dict(value)
        migrated.setdefault("dispatch_idempotency_key", None)
        migrated.setdefault("findings", [])
        migrated.setdefault("blocked_reason", None)
        migrated.setdefault("max_external_retries", 3)
        migrated.setdefault("max_reply_retries", 3)
        migrated["schema_version"] = CURRENT_SCHEMA_VERSION
        return migrated

    def _backup_state(self) -> None:
        if self.state_path.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            shutil.copy2(self.state_path, self.state_path.with_suffix(f".{stamp}.bak"))

    @staticmethod
    def _atomic_write(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            ).encode("utf-8")
            decoded = json.loads(encoded.decode("utf-8"))
            if not isinstance(decoded, dict):
                raise ValueError("state JSON root must be an object")
        except (TypeError, ValueError, UnicodeError) as error:
            raise PersistenceError(f"state JSON serialization failed: {path}") from error
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            if path.exists():
                if JsonStateStore._is_valid_json_object(path):
                    JsonStateStore._copy_atomic(path, JsonStateStore._backup_path(path))
                else:
                    # Never replace the last-good backup with the same
                    # corrupted primary file we are trying to overwrite.
                    logger.warning(
                        "STATE_BACKUP_PRESERVED_PRIMARY_INVALID path=%s backup=%s",
                        path,
                        JsonStateStore._backup_path(path),
                    )
            os.replace(tmp_name, path)
        except Exception as error:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise PersistenceError(f"atomic write failed: {path}") from error

    @staticmethod
    def _copy_atomic(source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
        try:
            with source.open("rb") as source_handle, os.fdopen(fd, "wb") as target_handle:
                shutil.copyfileobj(source_handle, target_handle)
                target_handle.flush()
                os.fsync(target_handle.fileno())
            os.replace(tmp_name, target)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    @staticmethod
    def _is_valid_json_object(path: Path) -> bool:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, TypeError, ValueError):
            return False
        return isinstance(value, dict)
