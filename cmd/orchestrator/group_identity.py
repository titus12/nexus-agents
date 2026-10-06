"""Shared lexical contract for stable group identities, without a fixed prefix."""

GROUP_ID_TOKEN = r"[A-Za-z0-9][A-Za-z0-9_.-]*"
GROUP_ID_SCHEMA_PATTERN = rf"^{GROUP_ID_TOKEN}$"
GROUP_ID_DESCRIPTION = (
    "Stable, case-sensitive ID: ASCII letter/digit first, then letters, digits, "
    "underscore, dot or hyphen; e.g. GRP-001 or group-00001. "
    "Use the identical value in plan.groups and the requirement document title."
)
