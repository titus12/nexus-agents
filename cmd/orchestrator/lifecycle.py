from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LifecyclePaths:
    """Stable request/stage layout for one orchestrator task."""

    task_root: Path

    @property
    def requests(self) -> Path:
        return self.task_root / "requests"

    @property
    def zhongshu(self) -> Path:
        return self.task_root / "zhongshu"

    @property
    def menxia(self) -> Path:
        return self.task_root / "menxia"

    @property
    def delivery(self) -> Path:
        return self.task_root / "delivery"

    def request(self, request_id: str) -> Path:
        return self.requests / _safe(request_id)

    def stage(self, phase: str, revision: str = "current") -> Path:
        return (self.zhongshu if phase.upper() == "ZHONGSHU" else self.menxia) / _safe(revision)

    def stage_result(
        self,
        *,
        phase: str,
        revision: str,
        state: str,
        group_id: str = "",
        item_id: str = "",
        worker_id: str = "",
        payload: dict[str, Any] | None = None,
    ) -> Path:
        """Return the stable path used for one immutable worker result."""
        base = self.stage(phase, revision)
        if phase.upper() == "ZHONGSHU":
            role = _safe(state).lower()
            worker = _safe(worker_id or (payload or {}).get("worker_id") or role)
            if group_id and item_id:
                attempt = _safe(str((payload or {}).get("attempt") or "1"))
                return (
                    base
                    / "tasks"
                    / _safe(group_id)
                    / "items"
                    / _safe(item_id)
                    / f"attempt-{attempt}"
                    / "result.json"
                )
            return base / "workers" / worker / "result.json"
        return (
            base
            / "groups"
            / _safe(group_id)
            / "items"
            / _safe(item_id)
            / f"{_safe(state).lower()}.json"
        )

    def ensure(self) -> None:
        for path in (self.task_root, self.requests, self.zhongshu, self.menxia, self.delivery):
            path.mkdir(parents=True, exist_ok=True)


def _safe(value: str) -> str:
    text = str(value or "unknown")
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in text)[:160]


def atomic_json(path: Path, value: dict[str, Any]) -> str:
    """Write UTF-8 JSON without BOM and return the byte SHA-256."""
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return hashlib.sha256(encoded).hexdigest()


def write_context(paths: LifecyclePaths, ctx: Any) -> Path:
    path = paths.task_root / "context.json"
    atomic_json(path, ctx.to_dict() if hasattr(ctx, "to_dict") else dict(ctx))
    return path


def write_stage_result(
    paths: LifecyclePaths,
    *,
    phase: str,
    revision: str,
    state: str,
    payload: dict[str, Any],
    group_id: str = "",
    item_id: str = "",
    worker_id: str = "",
) -> Path:
    """Persist one immutable worker result; only the orchestrator calls this."""
    path = paths.stage_result(
        phase=phase,
        revision=revision,
        state=state,
        group_id=group_id,
        item_id=item_id,
        worker_id=worker_id,
        payload=payload,
    )
    envelope = {
        "schema": "nexus-orchestrator-stage-result-v1",
        "phase": phase,
        "revision": revision,
        "state": state,
        "group_id": group_id,
        "item_id": item_id,
        "worker_id": worker_id or str(payload.get("worker_id") or ""),
        "attempt": payload.get("attempt"),
        "payload": payload,
    }
    atomic_json(path, envelope)
    return path


def write_stage_verdict(paths: LifecyclePaths, *, phase: str, revision: str, verdict: dict[str, Any]) -> Path:
    path = paths.stage(phase, revision) / "verdict.json"
    atomic_json(path, verdict)
    return path
