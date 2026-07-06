"""Poller for the AC lobby HTTP endpoint the server exposes on HTTP_PORT.

GET /INFO returns coarse live state as JSON (both vanilla and AssettoServer
serve it — Content Manager itself relies on it to join).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import aiohttp

log = logging.getLogger(__name__)


@dataclass
class ServerInfo:
    name: str = ""
    clients: int = 0
    maxclients: int = 0
    track: str = ""
    session: int = 0
    timeleft: int = 0
    timeofday: int = 0
    pass_protected: bool = False
    cars: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


async def fetch_info(host: str, http_port: int, timeout_s: float = 4.0) -> ServerInfo | None:
    """None means the server didn't answer (down or still booting)."""
    url = f"http://{host}:{http_port}/INFO"
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout_s)
        ) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as e:
        log.debug("INFO poll failed (%s): %s", url, e)
        return None
    if not isinstance(data, dict):
        return None
    return ServerInfo(
        name=str(data.get("name") or ""),
        clients=int(data.get("clients") or 0),
        maxclients=int(data.get("maxclients") or 0),
        track=str(data.get("track") or ""),
        session=int(data.get("session") or 0),
        timeleft=int(data.get("timeleft") or 0),
        timeofday=int(data.get("timeofday") or 0),
        pass_protected=bool(data.get("pass")),
        cars=[str(c) for c in (data.get("cars") or [])],
        raw=data,
    )


async def resolve_public_ip(configured: str) -> str | None:
    """'auto' -> ask api.ipify.org once; anything else is used as-is."""
    if configured and configured.lower() != "auto":
        return configured
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=5)
        ) as session:
            async with session.get("https://api.ipify.org") as resp:
                if resp.status == 200:
                    ip = (await resp.text()).strip()
                    return ip or None
    except (aiohttp.ClientError, TimeoutError):
        pass
    log.warning("could not auto-resolve public IP; set server.public_ip in config.yaml")
    return None
