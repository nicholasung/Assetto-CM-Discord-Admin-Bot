"""Tiny formatting/parsing helpers shared by cogs."""

from __future__ import annotations

import re

_HHMM = re.compile(r"^(\d{1,2}):(\d{2})$")


def parse_hhmm(value: str) -> tuple[int, int]:
    m = _HHMM.match(value.strip())
    if not m:
        raise ValueError("Time must look like HH:MM, e.g. 14:30")
    hour, minute = int(m.group(1)), int(m.group(2))
    if hour > 23 or minute > 59:
        raise ValueError("Time must look like HH:MM, e.g. 14:30")
    return hour, minute


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def truncate(text: str, limit: int = 100) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
