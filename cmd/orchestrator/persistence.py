from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .context import CURRENT_SCHEMA_VERSION, StateContext
from .events import Event
from .transitions import Transition


class PersistenceError(RuntimeError):
    pass


class JsonStateStore:
    """Atomic state/event persistence; the caller must hold TaskLock."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "state.json"
        self.event_path = self.root / "events.jsonl"

    def save_state(self, ctx: StateContext) -> None:
        value = ctx.to_dict()
        value["schema_version"] = CURRENT_SCHEMA_VERSION
        self._atomic_write(self.state_path, value)

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
        if event is not None:
            self.append_event(ctx, event, transition)
        self.save_state(ctx)

    def load(self) -> StateContext:
        if not self.state_path.exists():
            raise PersistenceError(f"missing state file: {self.state_path}")
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
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
            return ctx
        except Exception as error:
            raise PersistenceError(f"invalid state file: {self.state_path}") from error

    def event_sequences(self) -> list[int]:
        if not self.event_path.exists():
            return []
        sequences: list[int] = []
        for line in self.event_path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
                sequence = int(value["sequence"])
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
                raise PersistenceError("invalid event log record") from error
            sequences.append(sequence)
        return sequences

    def reconcile_sequence(self, ctx: StateContext) -> int:
        sequences = self.event_sequences()
        if not sequences:
            return ctx.sequence
        if any(right != left + 1 for left, right in zip(sequences, sequences[1:])):
            raise PersistenceError("event sequence contains a gap")
        last_event = sequences[-1]
        if last_event > ctx.sequence:
            ctx.sequence = last_event
            self.save_state(ctx)
        elif ctx.sequence > last_event:
            raise PersistenceError("state sequence is ahead of event log")
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
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        except Exception as error:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise PersistenceError(f"atomic write failed: {path}") from error
