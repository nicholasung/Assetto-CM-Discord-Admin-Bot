"""Tiny async pub/sub bus wiring the live layer to cogs and the leaderboard.

Event names used across the app:
    server_started, server_stopped, server_exited (unexpected),
    session_info, new_session, end_session (result file name),
    driver_joined, driver_left, lap_completed, chat, client_event
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)

Handler = Callable[..., Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, event: str, handler: Handler) -> None:
        self._subs[event].append(handler)

    async def emit(self, event: str, **kwargs: Any) -> None:
        for handler in self._subs.get(event, []):
            try:
                await handler(**kwargs)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("event handler for %r failed", event)
