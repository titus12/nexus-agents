from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .dispatch_envelope import validate_envelope

# The service log file only carries the canonical "review_orchestrator_fsm"
# logger (logging_setup.configure_logging attaches handlers to it alone), so
# every module must log under that name for ops visibility.
logger = logging.getLogger("review_orchestrator_fsm")

_PROMPT_SECTION_MARKER_RE = re.compile(r"^\[([^\[\]]{1,80})\]\s*$", re.MULTILINE)
_CONTEXT_SECTION_MIN_BYTES = 1024


def _prompt_sections(prompt: str) -> dict[str, int]:
    """Split prompt.txt into its named ``[Block]`` sections, bytes each.

    The composed prompts use bracketed block markers (``[Contract rules]``,
    ``[Retry feedback]``, ...); the text before the first marker is the
    ``header``.  Sections are what role slicing can cut independently.
    """

    matches = list(_PROMPT_SECTION_MARKER_RE.finditer(prompt))
    sections: dict[str, int] = {}
    header_end = matches[0].start() if matches else len(prompt)
    if prompt[:header_end].strip():
        sections["header"] = len(prompt[:header_end].encode("utf-8"))
    for index, match in enumerate(matches):
        name = match.group(1).strip() or f"section-{index}"
        end = matches[index + 1].start() if index + 1 < len(matches) else len(prompt)
        sections[name] = len(prompt[match.start():end].encode("utf-8"))
    return sections


def _context_sections(context: Mapping[str, Any]) -> dict[str, int]:
    """Per-key byte weight of context.json (two levels, small keys bucketed).

    context.json carries the heavy review material (plan, evidence, capsules,
    dispatch contexts); its top-level keys and — for mappings — their biggest
    sub-keys are the slicing candidates the section report is for.
    """

    def _size(value: Any) -> int:
        try:
            payload = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            payload = str(value)
        return len(payload.encode("utf-8"))

    sections: dict[str, int] = {}
    small_total = 0
    for key, value in context.items():
        size = _size(value)
        name = str(key)
        if size < _CONTEXT_SECTION_MIN_BYTES:
            small_total += size
            continue
        sections[f"context#/{name}"] = size
        if isinstance(value, Mapping):
            for sub_key, sub_value in value.items():
                sub_size = _size(sub_value)
                if sub_size >= _CONTEXT_SECTION_MIN_BYTES:
                    sections[f"context#/{name}.{sub_key}"] = sub_size
    if small_total:
        sections["context#/other"] = small_total
    return sections


class PromptBundleMeter:
    """In-process hop meter feeding the PROMPT_BUNDLE_REPORT aggregation.

    Records one entry per dispatched bundle (task, state, sections); the
    report groups hops by state with byte averages and the heaviest sections.
    Process-local by design: a restart starts a fresh measurement window.
    """

    def __init__(self) -> None:
        self._hops: dict[str, list[dict[str, Any]]] = {}

    def record(
        self,
        task_id: str,
        state: str,
        request_id: str,
        sections: Mapping[str, int],
    ) -> None:
        self._hops.setdefault(task_id, []).append(
            {
                "state": state,
                "request_id": request_id,
                "sections": dict(sections),
            }
        )

    def report(self, task_id: str) -> dict[str, dict[str, Any]]:
        by_state: dict[str, dict[str, Any]] = {}
        for hop in self._hops.get(task_id, ()):
            entry = by_state.setdefault(hop["state"], {"hops": 0, "bytes_total": 0})
            entry["hops"] += 1
            entry["bytes_total"] += sum(hop["sections"].values())
            totals = entry.setdefault("section_totals", {})
            for name, size in hop["sections"].items():
                totals[name] = totals.get(name, 0) + size
        for entry in by_state.values():
            hops = max(1, entry["hops"])
            entry["bytes_avg"] = entry["bytes_total"] // hops
            totals = entry.pop("section_totals")
            entry["top_sections"] = dict(
                sorted(totals.items(), key=lambda item: -item[1])[:5]
            )
        return by_state


BUNDLE_METER = PromptBundleMeter()


def emit_prompt_bundle_report(task_id: str, *, reason: str) -> None:
    """Log the aggregated PROMPT_BUNDLE_REPORT for one task (if any hops)."""

    report = BUNDLE_METER.report(task_id)
    if not report:
        return
    logger.info(
        "PROMPT_BUNDLE_REPORT task_id=%s reason=%s by_state=%s",
        task_id,
        reason,
        json.dumps(report, ensure_ascii=False, sort_keys=True),
    )


class PromptBundleError(RuntimeError):
    pass


def _utf8_no_bom() -> Any:
    return "utf-8"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_component(value: str) -> str:
    component = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip(" .")
    return component or "unnamed"


def _atomic_write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception as error:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise PromptBundleError(f"atomic prompt bundle write failed: {path}") from error


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    _atomic_write_bytes(path, payload)


@dataclass(frozen=True)
class PromptFile:
    name: str
    purpose: str
    required: bool
    sha256: str
    bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "purpose": self.purpose,
            "required": self.required,
            "sha256": self.sha256,
            "bytes": self.bytes,
            "encoding": "utf-8",
            "bom": False,
        }


@dataclass(frozen=True)
class PromptBundle:
    root: Path
    manifest_path: Path
    manifest_hash: str
    total_bytes: int
    files: tuple[PromptFile, ...]
    context_path: Path

    def reference(self) -> dict[str, Any]:
        context_file = next(
            item for item in self.files if item.name == "context.json"
        )
        return {
            "mode": "prompt_file",
            "manifest_path": str(self.manifest_path.resolve()),
            "context_path": str(self.context_path.resolve()),
            "context_hash": context_file.sha256,
            "manifest_hash": self.manifest_hash,
            "total_bytes": self.total_bytes,
            "encoding": "utf-8",
            "bom": False,
        }


class PromptBundleBuilder:
    """Persist one Agent request as an immutable, UTF-8, hash-addressed bundle."""

    def __init__(self, root: str | Path, max_file_bytes: int = 8 * 1024 * 1024) -> None:
        self.root = Path(root)
        self.max_file_bytes = max_file_bytes

    def result_path(self, task_id: str, request_id: str) -> Path:
        return (
            self.root
            / _safe_component(task_id)
            / _safe_component(request_id)
            / "result.json"
        )

    def build(
        self,
        *,
        task_id: str,
        request_id: str,
        phase: str,
        role: str,
        revision_id: str = "",
        context: Mapping[str, Any] | None = None,
        prompt: str,
        state: str = "",
    ) -> PromptBundle:
        if not prompt:
            raise PromptBundleError("prompt must not be empty")
        root = self.root / _safe_component(task_id) / _safe_component(request_id)
        if (
            isinstance(context, Mapping)
            and context.get("structured_output") is not None
            and not isinstance(context.get("structured_output"), Mapping)
        ):
            raise PromptBundleError(
                "structured output specification must be a JSON object"
            )
        prompt_bytes = prompt.encode("utf-8")
        if len(prompt_bytes) > self.max_file_bytes:
            raise PromptBundleError(
                f"prompt exceeds bundle file limit: {len(prompt_bytes)} > {self.max_file_bytes}"
            )

        prompt_path = root / "prompt.txt"
        _atomic_write_bytes(prompt_path, prompt_bytes)
        prompt_file = PromptFile(
            name="prompt.txt",
            purpose="complete Agent instruction; read as UTF-8",
            required=True,
            sha256=_sha256_bytes(prompt_bytes),
            bytes=len(prompt_bytes),
        )
        logger.info(
            "PROMPT_BUNDLE_FILE_WRITTEN task_id=%s request_id=%s phase=%s role=%s "
            "file=%s purpose=prompt bytes=%s sha256=%s encoding=utf-8 bom=false",
            task_id,
            request_id,
            phase,
            role,
            prompt_path,
            prompt_file.bytes,
            prompt_file.sha256,
        )

        context_value: Mapping[str, Any] = context or {
            "task_id": task_id,
            "request_id": request_id,
            "phase": phase,
            "role": role,
            "revision_id": revision_id,
            "prompt_sha256": prompt_file.sha256,
            "prompt_bytes": prompt_file.bytes,
        }
        context_path = root / "context.json"
        _atomic_write_json(context_path, context_value)
        context_bytes = context_path.read_bytes()
        context_file = PromptFile(
            name="context.json",
            purpose="request context and stable transport metadata; read as UTF-8",
            required=True,
            sha256=_sha256_bytes(context_bytes),
            bytes=len(context_bytes),
        )
        logger.info(
            "PROMPT_BUNDLE_SCOPE task_id=%s request_id=%s phase=%s role=%s "
            "dispatch_mode=%s review_job_id=%s revision_id=%s group_id=%s "
            "item_id=%s task_hash=%s dependency_hash=%s prompt_bytes=%s",
            task_id,
            request_id,
            phase,
            role,
            str(context_value.get("zhongshu_dispatch_mode") or ""),
            str(context_value.get("review_job_id") or ""),
            str(context_value.get("revision_id") or revision_id),
            str(context_value.get("group_id") or ""),
            str(context_value.get("item_id") or ""),
            str(context_value.get("task_hash") or ""),
            str(context_value.get("dependency_hash") or ""),
            len(prompt_bytes),
        )
        logger.info(
            "PROMPT_BUNDLE_FILE_WRITTEN task_id=%s request_id=%s phase=%s role=%s "
            "file=%s purpose=context bytes=%s sha256=%s encoding=utf-8 bom=false",
            task_id,
            request_id,
            phase,
            role,
            context_path,
            context_file.bytes,
            context_file.sha256,
        )
        # T3.1 (2026-09-28): per-section byte weight of the bundle — the
        # prompt's named blocks plus context.json's heaviest keys — so the
        # role-slicing cut is decided on data instead of guesses.
        bundle_sections: dict[str, int] = {
            "prompt.txt": len(prompt_bytes),
            "context.json": len(context_bytes),
        }
        bundle_sections.update(
            {f"prompt#/{name}": size for name, size in _prompt_sections(prompt).items()}
        )
        bundle_sections.update(_context_sections(context_value))
        meter_state = state or str(context_value.get("target_state") or "") or phase
        logger.info(
            "PROMPT_BUNDLE_SECTIONS task_id=%s request_id=%s state=%s sections=%s",
            task_id,
            request_id,
            meter_state,
            json.dumps(bundle_sections, ensure_ascii=False, sort_keys=True),
        )
        BUNDLE_METER.record(task_id, meter_state, request_id, bundle_sections)

        files = [prompt_file, context_file]
        skill_lock = context_value.get("active_runtime_skill_lock")
        if isinstance(skill_lock, Mapping) and skill_lock.get("source"):
            try:
                skill_bytes = Path(str(skill_lock.get("source") or "")).read_bytes()
            except OSError as error:
                raise PromptBundleError("active runtime Skill source is unavailable") from error
            if _sha256_bytes(skill_bytes) != skill_lock.get("sha256"):
                raise PromptBundleError("active runtime Skill changed after request construction")
            if len(skill_bytes) > self.max_file_bytes or skill_bytes.startswith(b"\xef\xbb\xbf"):
                raise PromptBundleError("active runtime Skill encoding or size is invalid")
            _atomic_write_bytes(root / "active-skill.md", skill_bytes)
            files.append(PromptFile(name="active-skill.md", purpose="authoritative runtime Skill snapshot; read before prompt.txt", required=True, sha256=_sha256_bytes(skill_bytes), bytes=len(skill_bytes)))
        manifest = {
            "protocol": "nexus-prompt-bundle-v1",
            "task_id": task_id,
            "request_id": request_id,
            "phase": phase,
            "role": role,
            "revision_id": revision_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "encoding": "utf-8",
            "bom": False,
            "active_runtime_skill_lock": (
                dict(context_value.get("active_runtime_skill_lock"))
                if isinstance(context_value.get("active_runtime_skill_lock"), Mapping)
                else {}
            ),
            "files": [item.to_dict() for item in files],
        }
        envelope_value = context_value.get("envelope")
        if envelope_value is not None:
            try:
                manifest["envelope"] = validate_envelope(envelope_value)
            except ValueError as error:
                raise PromptBundleError(f"dispatch envelope invalid: {error}") from error
            logger.info(
                "PROMPT_BUNDLE_ENVELOPE task_id=%s request_id=%s role=%s "
                "ingredients=%s tools=%s product=%s",
                task_id,
                request_id,
                role,
                len(manifest["envelope"]["ingredients"]),
                str(manifest["envelope"]["tools"].get("editable") or ""),
                str(manifest["envelope"]["product"].get("type") or ""),
            )
        else:
            manifest["envelope"] = {}
        manifest_path = root / "manifest.json"
        _atomic_write_json(manifest_path, manifest)
        manifest_bytes = manifest_path.read_bytes()
        manifest_hash = _sha256_bytes(manifest_bytes)
        total_bytes = sum(item.bytes for item in files) + len(manifest_bytes)
        logger.info(
            "PROMPT_BUNDLE_FILE_WRITTEN task_id=%s request_id=%s phase=%s role=%s "
            "file=%s purpose=manifest bytes=%s sha256=%s encoding=utf-8 bom=false",
            task_id,
            request_id,
            phase,
            role,
            manifest_path,
            len(manifest_bytes),
            manifest_hash,
        )

        logger.info(
            "PROMPT_BUNDLE_CREATED task_id=%s request_id=%s phase=%s role=%s "
            "manifest_path=%s manifest_hash=%s prompt_bytes=%s total_bytes=%s",
            task_id,
            request_id,
            phase,
            role,
            manifest_path,
            manifest_hash,
            len(prompt_bytes),
            total_bytes,
        )
        logger.info(
            "PROMPT_BUNDLE_REFERENCE task_id=%s request_id=%s manifest_path=%s manifest_hash=%s",
            task_id,
            request_id,
            manifest_path,
            manifest_hash,
        )
        return PromptBundle(
            root=root,
            manifest_path=manifest_path,
            manifest_hash=manifest_hash,
            total_bytes=total_bytes,
            files=tuple(files),
            context_path=context_path,
        )

    @staticmethod
    def verify(bundle: PromptBundle) -> None:
        if not bundle.manifest_path.exists():
            raise PromptBundleError(f"manifest missing: {bundle.manifest_path}")
        manifest_bytes = bundle.manifest_path.read_bytes()
        actual_manifest_hash = _sha256_bytes(manifest_bytes)
        if actual_manifest_hash != bundle.manifest_hash:
            logger.error(
                "PROMPT_BUNDLE_HASH_MISMATCH manifest_path=%s expected=%s actual=%s",
                bundle.manifest_path,
                bundle.manifest_hash,
                actual_manifest_hash,
            )
            raise PromptBundleError("prompt bundle manifest hash mismatch")
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        for file_info in manifest.get("files", []):
            if not isinstance(file_info, dict):
                raise PromptBundleError("manifest file entry must be an object")
            path = bundle.root / str(file_info.get("name") or "")
            if not path.is_file():
                raise PromptBundleError(f"bundle file missing: {path}")
            data = path.read_bytes()
            if data.startswith(b"\xef\xbb\xbf"):
                raise PromptBundleError(f"bundle file has BOM: {path}")
            expected_bytes = int(file_info.get("bytes") or -1)
            expected_hash = str(file_info.get("sha256") or "").lower()
            if len(data) != expected_bytes or _sha256_bytes(data) != expected_hash:
                logger.error(
                    "PROMPT_BUNDLE_FILE_HASH_MISMATCH path=%s expected_bytes=%s "
                    "actual_bytes=%s expected_sha256=%s actual_sha256=%s",
                    path,
                    expected_bytes,
                    len(data),
                    expected_hash,
                    _sha256_bytes(data),
                )
                raise PromptBundleError(f"prompt bundle file hash mismatch: {path}")
