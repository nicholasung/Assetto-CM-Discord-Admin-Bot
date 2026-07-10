"""Discord OAuth2 for the web UI: log in with Discord, verify guild membership.

Flow (Authorization Code grant):
  1. /auth/discord/login  -> redirect the browser to Discord's authorize page
     with a random `state` (stored in a short-lived cookie for CSRF protection).
  2. Discord redirects back to /auth/discord/callback?code=…&state=… .
  3. We exchange the code for an access token, read the user + their guild list
     (scopes: identify, guilds), and only start a session if the configured
     guild (discord.guild_id) is among them.

Only the user's own consent is needed — no bot token or privileged intent — so
this works even for a web UI running without the Discord bot.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlencode

import aiohttp

log = logging.getLogger(__name__)

AUTHORIZE_URL = "https://discord.com/api/oauth2/authorize"
TOKEN_URL = "https://discord.com/api/oauth2/token"
API_BASE = "https://discord.com/api"
SCOPES = "identify guilds"
_TIMEOUT = aiohttp.ClientTimeout(total=10)


class DiscordAuthError(Exception):
    """A user-facing problem completing Discord login."""


@dataclass
class DiscordIdentity:
    user_id: str
    username: str  # display label for audit ("name" or "name#1234")
    guild_ids: set[str]

    def in_guild(self, guild_id: int | str) -> bool:
        return str(guild_id) in self.guild_ids


def build_authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    query = urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        # Skip the consent screen on repeat logins once the user has authorized.
        "prompt": "none",
    })
    return f"{AUTHORIZE_URL}?{query}"


async def exchange_and_identify(*, client_id: str, client_secret: str, code: str,
                                redirect_uri: str) -> DiscordIdentity:
    """Trade the OAuth code for a token, then read the user + their guilds."""
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        token = await _exchange_code(session, client_id, client_secret, code, redirect_uri)
        user = await _get(session, token, "/users/@me")
        guilds = await _get(session, token, "/users/@me/guilds")

    if not isinstance(guilds, list):
        raise DiscordAuthError("Discord returned an unexpected guild list.")
    guild_ids = {str(g.get("id")) for g in guilds if isinstance(g, dict) and g.get("id")}
    return DiscordIdentity(
        user_id=str(user.get("id") or ""),
        username=_label(user),
        guild_ids=guild_ids,
    )


async def _exchange_code(session: aiohttp.ClientSession, client_id: str,
                         client_secret: str, code: str, redirect_uri: str) -> str:
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    try:
        async with session.post(TOKEN_URL, data=data) as resp:
            body = await resp.json(content_type=None)
            if resp.status != 200:
                log.warning("Discord token exchange failed (%s): %s", resp.status, body)
                raise DiscordAuthError("Discord rejected the login (token exchange failed).")
    except aiohttp.ClientError as e:
        raise DiscordAuthError(f"Could not reach Discord: {e}") from e
    token = (body or {}).get("access_token")
    if not token:
        raise DiscordAuthError("Discord did not return an access token.")
    return str(token)


async def _get(session: aiohttp.ClientSession, token: str, path: str):
    try:
        async with session.get(f"{API_BASE}{path}",
                               headers={"Authorization": f"Bearer {token}"}) as resp:
            body = await resp.json(content_type=None)
            if resp.status != 200:
                log.warning("Discord GET %s failed (%s): %s", path, resp.status, body)
                raise DiscordAuthError("Discord API error while reading your account.")
            return body
    except aiohttp.ClientError as e:
        raise DiscordAuthError(f"Could not reach Discord: {e}") from e


def _label(user: dict) -> str:
    name = str(user.get("global_name") or user.get("username") or user.get("id") or "unknown")
    disc = str(user.get("discriminator") or "")
    if disc and disc != "0":  # legacy #1234 discriminators
        return f"{name}#{disc}"
    return name
