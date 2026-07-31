"""Formatting helpers for operational timestamps.

The legacy inventory tables store timestamps as UTC-naive values.  They are
therefore treated as UTC at the display boundary and converted to the JI
Montadora operating timezone.  This deliberately does not mutate history.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


SAO_PAULO_TIMEZONE = ZoneInfo("America/Sao_Paulo")


def to_sao_paulo(value: datetime | None) -> datetime | None:
    """Return a value in America/Sao_Paulo without changing its instant.

    Existing inventory columns were created as ``timestamp without time
    zone`` and receive UTC values from ``now_utc``.  Naive values must thus be
    interpreted as UTC, while aware values keep their explicit offset.
    """

    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(SAO_PAULO_TIMEZONE)


def format_sao_paulo(value: datetime | None, pattern: str = "%d/%m/%Y %H:%M") -> str:
    """Format an operational timestamp in the local plant timezone."""

    local_value = to_sao_paulo(value)
    return local_value.strftime(pattern) if local_value else ""


def now_sao_paulo() -> datetime:
    """Current local timestamp for generated, user-facing documents."""

    return datetime.now(SAO_PAULO_TIMEZONE)
