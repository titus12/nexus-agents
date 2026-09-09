"""Normalize raw transport replies into one canonical workflow input."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TypeVar

from ..domain.errors import ReplyBindingError, ReplyValidationError
from .replies import RawTransportReply, ReplyBinding, ReplyEnvelope


TPayload = TypeVar("TPayload")


class ReplyNormalizer:
    """Validate transport correlation and decode one role payload.

    Both direct polling and parallel polling are expected to call this same
    entry point.  Backfill is intentionally limited to transport metadata; the
    normalizer never overwrites an explicitly supplied value.
    """

    _BINDING_FIELDS = ("task_id", "request_id", "phase", "role", "target_state")

    def normalize(
        self,
        raw: RawTransportReply,
        binding: ReplyBinding,
        role_decoder: Callable[[Mapping[str, object]], TPayload],
    ) -> ReplyEnvelope[TPayload]:
        """Return a validated, typed envelope or raise a typed domain error."""

        if not isinstance(raw, RawTransportReply):
            raise ReplyValidationError("raw must be a RawTransportReply")
        if not isinstance(binding, ReplyBinding):
            raise ReplyValidationError("binding must be a ReplyBinding")
        if not callable(role_decoder):
            raise ReplyValidationError("role_decoder must be callable")

        self._validate_binding(binding)
        self._require_author(raw, binding)
        values = {
            field: self._resolve_explicit_value(raw, field)
            for field in self._BINDING_FIELDS
        }
        resolved = {
            field: self._backfill_or_validate(field, values[field], getattr(binding, field))
            for field in self._BINDING_FIELDS
        }

        try:
            typed_payload = role_decoder(raw.payload)
        except Exception as error:
            raise ReplyValidationError(
                f"role payload decoding failed: {type(error).__name__}: {error}"
            ) from error

        if typed_payload is None:
            raise ReplyValidationError("role_decoder returned None")

        return ReplyEnvelope(
            task_id=resolved["task_id"],
            request_id=resolved["request_id"],
            author_id=raw.author_id,
            external_message_id=raw.external_message_id,
            phase=resolved["phase"],
            role=resolved["role"],
            target_state=resolved["target_state"],
            payload=typed_payload,
            received_at=raw.received_at,
            source=raw.source,
        )

    @staticmethod
    def _validate_binding(binding: ReplyBinding) -> None:
        fields = ("task_id", "request_id", "author_id", "phase", "role", "target_state")
        for field in fields:
            value = getattr(binding, field)
            if not isinstance(value, str) or not value:
                raise ReplyValidationError(
                    f"binding field {field} must be a non-empty string"
                )

    @staticmethod
    def _require_author(raw: RawTransportReply, binding: ReplyBinding) -> None:
        if raw.author_id != binding.author_id:
            raise ReplyBindingError(
                "reply author_id does not match binding: "
                f"actual={raw.author_id!r}, expected={binding.author_id!r}"
            )

    @staticmethod
    def _resolve_explicit_value(raw: RawTransportReply, field: str) -> object | None:
        """Read top-level and payload metadata without hiding conflicts."""

        top_level = getattr(raw, field)
        payload = raw.payload
        payload_has_field = field in payload
        payload_value = payload.get(field) if payload_has_field else None

        if top_level is not None and payload_has_field and payload_value != top_level:
            raise ReplyBindingError(
                f"reply has conflicting explicit {field}: "
                f"top_level={top_level!r}, payload={payload_value!r}"
            )
        if top_level is not None:
            return top_level
        if payload_has_field:
            return payload_value
        return None

    @staticmethod
    def _backfill_or_validate(
        field: str,
        explicit_value: object | None,
        expected_value: str,
    ) -> str:
        if explicit_value is None:
            return expected_value
        if not isinstance(explicit_value, str):
            raise ReplyValidationError(
                f"reply binding field {field} must be a string, "
                f"got {type(explicit_value).__name__}"
            )
        if not explicit_value:
            raise ReplyValidationError(
                f"reply binding field {field} cannot be an empty string"
            )
        if explicit_value != expected_value:
            raise ReplyBindingError(
                f"reply {field} does not match binding: "
                f"actual={explicit_value!r}, expected={expected_value!r}"
            )
        return explicit_value


__all__ = ["ReplyNormalizer"]
