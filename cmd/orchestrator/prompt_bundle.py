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

logger = logging.getLogger(__name__)


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
    structured_output_path: Path | None = None

    def reference(self) -> dict[str, Any]:
        context_file = next(
            item for item in self.files if item.name == "context.json"
        )
        return {
            "mode": "prompt_file",
            "manifest_path": str(self.manifest_path.resolve()),
            "context_path": str(self.context_path.resolve()),
            "context_hash": context_file.sha256,
            "structured_output_path": (
                str(self.structured_output_path.resolve())
                if self.structured_output_path is not None
                else ""
            ),
            "result_path": str(self.root.joinpath("result.json").resolve()),
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
    ) -> PromptBundle:
        if not prompt:
            raise PromptBundleError("prompt must not be empty")
        root = self.root / _safe_component(task_id) / _safe_component(request_id)
        structured_output = (
            context.get("structured_output")
            if isinstance(context, Mapping)
            else None
        )
        structured_output_path: Path | None = None
        if (
            isinstance(structured_output, Mapping)
            and structured_output.get("mode") == "result_file"
        ):
            result_path = (root / "result.json").resolve()
            structured_output_path = (root / "structured-output.json").resolve()
            schema_hash = str(structured_output.get("schema_hash") or "")
            stable_fields = structured_output.get("stable_fields")
            stable_fields_text = ", ".join(
                str(item) for item in stable_fields if str(item).strip()
            ) if isinstance(stable_fields, list) else ""
            prompt = (
                f"{prompt.rstrip()}\n\n"
                "TRANSPORT CONTRACT (authoritative): the Orchestrator-owned "
                f"canonical result is {result_path}; the remote Agent must not "
                "write that path. Write exactly one complete business JSON "
                "result to relative result.json in the current Agent workspace "
                "as UTF-8 without BOM. Use a real JSON serializer so embedded "
                "quotes and control characters are escaped. "
                "Include the exact root field "
                f"structured_output_protocol={structured_output.get('protocol')}. "
                f"structured_output_schema_hash={schema_hash}. Always include "
                "contract_id, task_id, request_id, phase, state, role, and mode using the "
                "exact values from context.json. Read the canonical structured "
                f"output schema from {structured_output_path} before writing. You may create "
                "or replace only this local result file. For result_file mode, do not "
                "return the business JSON or a transport envelope inline. After the "
                "file is durably written, return exactly one compact "
                "nexus-agent-result-ref-v1 pointer containing the exact task_id, "
                "request_id, and actual local result.json path. Do not return "
                "./result.json or Markdown; keep the file until the response has "
                "been emitted, and do not return a second business result. "
                "ROLE PROTOCOL (authoritative): keep one fixed response shape for "
                f"phase={structured_output.get('phase')} role={structured_output.get('role')}. "
                f"Use state={structured_output.get('state')} and mode={structured_output.get('role_mode')} "
                f"as supplied. Always include these role fields: [{stable_fields_text}]. "
                "Use [] or null for fields not used by the current mode; do not omit fields "
                "or switch to another role's schema."
            )
        elif (
            isinstance(context, Mapping)
            and context.get("structured_output") is not None
            and not isinstance(structured_output, Mapping)
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

        files = [prompt_file, context_file]
        if structured_output_path is not None:
            structured_output_bytes = json.dumps(
                dict(structured_output), ensure_ascii=False, indent=2
            ).encode("utf-8")
            if len(structured_output_bytes) > self.max_file_bytes:
                raise PromptBundleError(
                    "structured output specification exceeds bundle file limit: "
                    f"{len(structured_output_bytes)} > {self.max_file_bytes}"
                )
            _atomic_write_bytes(structured_output_path, structured_output_bytes)
            structured_output_file = PromptFile(
                name="structured-output.json",
                purpose="canonical structured output schema and transport contract; read before prompt.txt",
                required=True,
                sha256=_sha256_bytes(structured_output_bytes),
                bytes=len(structured_output_bytes),
            )
            files.append(structured_output_file)
            logger.info(
                "PROMPT_BUNDLE_FILE_WRITTEN task_id=%s request_id=%s phase=%s role=%s "
                "file=%s purpose=structured_output bytes=%s sha256=%s encoding=utf-8 bom=false",
                task_id,
                request_id,
                phase,
                role,
                structured_output_path,
                structured_output_file.bytes,
                structured_output_file.sha256,
            )
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
            structured_output_path=structured_output_path,
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
