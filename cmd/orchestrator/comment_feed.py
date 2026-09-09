from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import threading
from typing import Any, Callable


def _comment_key(comment: dict[str, Any]) -> str:
    value = str(comment.get("id") or comment.get("comment_id") or "")
    if value:
        return value
    raw = "|".join(
        str(comment.get(key) or "")
        for key in ("created_at", "author_id", "creator_id", "content", "body")
    )
    return "synthetic:" + sha256(raw.encode("utf-8", errors="replace")).hexdigest()


def _overlap_timestamp(value: str, overlap_seconds: int) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (parsed - timedelta(seconds=overlap_seconds)).isoformat()


def _timestamp_key(value: str) -> tuple[int, float | str]:
    """Order ISO timestamps chronologically, including mixed timezone offsets."""
    if not value:
        return (0, 0.0)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (1, parsed.timestamp())
    except ValueError:
        return (2, value)


def comment_order_key(comment: dict[str, Any]) -> tuple[int, float | str]:
    return _timestamp_key(str(comment.get("created_at") or ""))


def timestamp_order_key(value: str) -> tuple[int, float | str]:
    return _timestamp_key(value)


@dataclass
class CommentCursor:
    """Timestamp cursor plus an ID fence for safe overlap re-reads."""

    last_created_at: str = ""
    seen_ids: set[str] = field(default_factory=set)
    seen_order: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.seen_order:
            self.seen_order = list(self.seen_ids)
        else:
            self.seen_order = list(dict.fromkeys(self.seen_order))
            self.seen_ids.update(self.seen_order)

    def query_since(self, overlap_seconds: int) -> str:
        return _overlap_timestamp(self.last_created_at, overlap_seconds)


class IncrementalCommentFeed:
    """Deduplicate an append-only comment stream per issue/task scope.

    The Multica CLI exposes a timestamp cursor rather than a numeric offset.
    Re-reading a small timestamp overlap protects against equal timestamps and
    eventual-consistency ordering; IDs prevent duplicate delivery to callers.
    """

    def __init__(
        self,
        overlap_seconds: int = 2,
        max_seen_ids: int = 4096,
        max_scopes: int = 512,
        state_path: str | Path | None = None,
    ) -> None:
        self.overlap_seconds = max(0, overlap_seconds)
        self.max_seen_ids = max(128, max_seen_ids)
        self.max_scopes = max(32, max_scopes)
        self.state_path = Path(state_path) if state_path else None
        self._lock = threading.Lock()
        self._cursors: dict[str, CommentCursor] = {}
        self._load()

    def read(
        self,
        scope: str,
        fetch: Callable[[str], Any],
        initial_since: str = "",
        *,
        persist: bool = True,
    ) -> list[dict[str, Any]]:
        with self._lock:
            cursor = self._cursors.setdefault(scope, CommentCursor())
            since = cursor.query_since(self.overlap_seconds) or initial_since
            value = fetch(since)
            comments = value if isinstance(value, list) else (
                value.get("comments", value.get("items", []))
                if isinstance(value, dict)
                else []
            )
            ordered = sorted(
                (item for item in comments if isinstance(item, dict)),
                key=comment_order_key,
            )
            fresh: list[dict[str, Any]] = []
            for comment in ordered:
                key = _comment_key(comment)
                if key in cursor.seen_ids:
                    continue
                cursor.seen_ids.add(key)
                cursor.seen_order.append(key)
                fresh.append(comment)
            if ordered:
                latest = str(ordered[-1].get("created_at") or "")
                if _timestamp_key(latest) > _timestamp_key(cursor.last_created_at):
                    cursor.last_created_at = latest
            if len(cursor.seen_ids) > self.max_seen_ids:
                cursor.seen_order = cursor.seen_order[-self.max_seen_ids:]
                cursor.seen_ids = set(cursor.seen_order)
            if len(self._cursors) > self.max_scopes:
                for stale_scope in list(self._cursors)[:-self.max_scopes]:
                    self._cursors.pop(stale_scope, None)
            if persist:
                self._save()
            return fresh

    def commit(self) -> None:
        """Persist the current cursors after a dependent side effect commits.

        Some consumers derive a second durable index from the returned
        comments.  They must commit that index before advancing the on-disk
        cursor, otherwise a crash can make the index miss a comment forever.
        """
        with self._lock:
            self._save()

    def snapshot(self, scope: str) -> dict[str, Any]:
        """Return a serializable cursor snapshot for a dependent write."""
        with self._lock:
            cursor = self._cursors.get(scope, CommentCursor())
            return {
                "last_created_at": cursor.last_created_at,
                "seen_order": list(cursor.seen_order),
            }

    def restore(self, scope: str, snapshot: dict[str, Any]) -> None:
        """Undo an uncommitted read when its dependent write fails."""
        with self._lock:
            self._cursors[scope] = CommentCursor(
                last_created_at=str(snapshot.get("last_created_at") or ""),
                seen_order=[
                    str(value)
                    for value in snapshot.get("seen_order", [])
                    if value
                ],
            )

    def _load(self) -> None:
        if self.state_path is None or not self.state_path.is_file():
            return
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        cursors = value.get("cursors", {}) if isinstance(value, dict) else {}
        if not isinstance(cursors, dict):
            return
        for scope, item in cursors.items():
            if not isinstance(item, dict):
                continue
            seen = item.get("seen_ids", [])
            seen_order = item.get("seen_order", seen)
            if not isinstance(seen_order, list):
                seen_order = seen
            self._cursors[str(scope)] = CommentCursor(
                last_created_at=str(item.get("last_created_at") or ""),
                seen_ids={str(entry) for entry in seen if entry},
                seen_order=[str(entry) for entry in seen_order if entry],
            )

    def _save(self) -> None:
        if self.state_path is None:
            return
        value = {
            "version": 2,
            "cursors": {
                scope: {
                    "last_created_at": cursor.last_created_at,
                    "seen_ids": list(cursor.seen_order),
                    "seen_order": list(cursor.seen_order),
                }
                for scope, cursor in self._cursors.items()
            },
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=self.state_path.name + ".",
            suffix=".tmp",
            dir=self.state_path.parent,
        )
        try:
            with open(fd, "w", encoding="utf-8", newline="") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
                handle.flush()
            Path(temporary).replace(self.state_path)
        finally:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass
