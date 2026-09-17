"""DispatchEnvelope: the declared ingredient list of one agent dispatch.

The cooking-model contract between the data layer and the behavior layer: a
dispatch must declare which data it consumes (``ingredients``, each with a
store key, a transport source, a lifetime and an optional slice expression),
which mutation boundaries it carries (``tools``), and which typed product it
must return (``product``).  The declaration is written into the bundle
manifest so every decision is auditable ("which data produced this verdict?")
instead of being reconstructed from a full-context dump.

The envelope rides inside ``dispatch_context``; the runner flattens that into
``request.context`` and :class:`PromptBundleBuilder` lifts it into
``manifest.json``.
"""

from __future__ import annotations

from typing import Any, Mapping

_SOURCES = frozenset({"prompt.txt", "context.json", "active-skill.md"})
_LIFETIMES = frozenset({"persisted", "session", "transient"})


def build_envelope(
    *,
    ingredients: list[dict[str, Any]],
    tools: dict[str, Any],
    product: dict[str, Any],
) -> dict[str, Any]:
    """Build one envelope declaration; validates while building."""

    envelope = {"ingredients": ingredients, "tools": tools, "product": product}
    return validate_envelope(envelope)


def validate_envelope(value: Mapping[str, Any]) -> dict[str, Any]:
    """Fail fast on a malformed declaration: it is a domain bug, not data."""

    if not isinstance(value, Mapping):
        raise ValueError("envelope must be an object")
    ingredients = value.get("ingredients")
    if not isinstance(ingredients, list) or not ingredients:
        raise ValueError("envelope.ingredients must be a non-empty array")
    normalized: list[dict[str, Any]] = []
    for item in ingredients:
        if not isinstance(item, Mapping):
            raise ValueError("envelope ingredient must be an object")
        key = str(item.get("key") or "").strip()
        source = str(item.get("source") or "").strip()
        lifetime = str(item.get("lifetime") or "").strip()
        if not key:
            raise ValueError("envelope ingredient requires a key")
        if source not in _SOURCES:
            raise ValueError(f"envelope ingredient {key!r} has unknown source {source!r}")
        if lifetime not in _LIFETIMES:
            raise ValueError(
                f"envelope ingredient {key!r} has unknown lifetime {lifetime!r}"
            )
        entry: dict[str, Any] = {"key": key, "source": source, "lifetime": lifetime}
        slice_expr = str(item.get("slice") or "").strip()
        if slice_expr:
            entry["slice"] = slice_expr
        normalized.append(entry)
    tools = value.get("tools")
    if not isinstance(tools, Mapping) or not str(tools.get("editable") or "").strip():
        raise ValueError("envelope.tools requires an editable boundary")
    product = value.get("product")
    if not isinstance(product, Mapping) or not str(product.get("type") or "").strip():
        raise ValueError("envelope.product requires a type")
    return {
        "ingredients": normalized,
        "tools": dict(tools),
        "product": dict(product),
    }


__all__ = ["build_envelope", "validate_envelope"]
