from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Union


def parse_duration(duration: str) -> timedelta:
    """
    Parse: 1mois, 3mois, 7j, 14d, 2w, 6h, 1an, 1y, 30min...
    """
    raw = duration.strip().lower().replace(" ", "")
    m = re.fullmatch(r"(\d+)([a-z]+)", raw)
    if not m:
        raise ValueError(f"Format de duree invalide: {duration}")

    n, unit = int(m.group(1)), m.group(2)

    if unit in {"min", "minute", "minutes"}:
        return timedelta(minutes=n)
    if unit in {"h", "hour", "hours"}:
        return timedelta(hours=n)
    if unit in {"j", "d", "day", "days"}:
        return timedelta(days=n)
    if unit in {"w", "week", "weeks"}:
        return timedelta(weeks=n)
    if unit in {"mois", "month", "months"}:
        return timedelta(days=30 * n)
    if unit in {"an", "ans", "y", "year", "years"}:
        return timedelta(days=365 * n)
    raise ValueError(f"Unite non supportee: {unit}")


def to_ms(value: Union[str, int, float, datetime]) -> int:
    """Convertit n'importe quelle representation de date en ms epoch UTC."""
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    if isinstance(value, (int, float)):
        # < 10^10 = secondes, sinon ms.
        return int(value * 1000) if value < 10_000_000_000 else int(value)
    if isinstance(value, str):
        import pandas as pd
        dt = pd.to_datetime(value, utc=True)
        return int(dt.timestamp() * 1000)
    raise TypeError(f"Type de date non supporte: {type(value)}")