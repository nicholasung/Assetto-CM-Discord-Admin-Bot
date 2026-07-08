"""Discord OAuth login for the web UI: config, helper, and the callback flow."""

from __future__ import annotations

from http.cookies import SimpleCookie

import aiohttp.web_request as wr
import pytest
from aiohttp.test_utils import TestClient, TestServer

import acbot.web.server as server_mod
from acbot.config import AUTH_DISCORD, Config, WebConfig, load_config, validate_for_web
from acbot.web.discord_auth import (
    AUTHORIZE_URL,
    DiscordIdentity,
    build_authorize_url,
)
from acbot.web.server import WebServer

GUILD = 424242424242424242


# -- helper unit tests -------------------------------------------------------

def test_authorize_url_has_expected_params():
    url = build_authorize_url("clientid", "https://h/auth/discord/callback", "st8")
    assert url.startswith(AUTHORIZE_URL + "?")
    assert "client_id=clientid" in url
    assert "response_type=code" in url
    assert "state=st8" in url
    assert "scope=identify+guilds" in url
    assert "redirect_uri=https%3A%2F%2Fh%2Fauth%2Fdiscord%2Fcallback" in url


def test_identity_membership():
    ident = DiscordIdentity(user_id="1", username="ada", guild_ids={"10", "20"})
    assert ident.in_guild(10) and ident.in_guild("20")
    assert not ident.in_guild(30)


# -- config ------------------------------------------------------------------

def test_secret_env_overrides_config(tmp_path, monkeypatch):
    cfg = Config(base_dir=tmp_path)
    cfg.web = WebConfig(discord_client_secret="from-file")
    monkeypatch.setenv("ACBOT_WEB_DISCORD_SECRET", "from-env")
    assert cfg.web_discord_secret() == "from-env"
    monkeypatch.delenv("ACBOT_WEB_DISCORD_SECRET")
    assert cfg.web_discord_secret() == "from-file"


def test_web_auth_ready_and_validation(tmp_path):
    cfg = Config(base_dir=tmp_path)
    cfg.discord.guild_id = GUILD
    cfg.paths.ac_root = tmp_path
    cfg.web = WebConfig(auth=AUTH_DISCORD)
    assert not cfg.web_auth_ready()
    problems = validate_for_web(cfg)
    assert any("client_id" in p for p in problems)
    assert any("secret" in p.lower() for p in problems)

    cfg.web.discord_client_id = "cid"
    cfg.web.discord_client_secret = "secret"
    assert cfg.web_auth_ready()


def test_invalid_auth_mode_rejected(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("web:\n  auth: carrierpigeon\n", encoding="utf-8")
    with pytest.raises(Exception, match="web.auth"):
        load_config(p)


# -- full callback flow ------------------------------------------------------

@pytest.fixture
def discord_cfg(tmp_path):
    cfg = Config(base_dir=tmp_path)
    cfg.discord.guild_id = GUILD
    cfg.web = WebConfig(auth=AUTH_DISCORD, host="127.0.0.1", port=0,
                        discord_client_id="cid", discord_client_secret="secret")
    cfg.ensure_dirs()
    return cfg


async def _client(cfg, remote="203.0.113.7", monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setattr(wr.BaseRequest, "remote",
                            property(lambda self: remote), raising=False)
    ws = WebServer(app=None, cfg=cfg)
    client = TestClient(TestServer(ws.web_app))
    await client.start_server()
    return ws, client


def _stub_identity(monkeypatch, guild_ids):
    async def fake(**_kw):
        return DiscordIdentity(user_id="1", username="racer", guild_ids=guild_ids)
    monkeypatch.setattr(server_mod, "exchange_and_identify", fake)


async def test_login_page_shows_discord_button(discord_cfg, monkeypatch):
    _ws, client = await _client(discord_cfg, remote="127.0.0.1", monkeypatch=monkeypatch)
    r = await client.get("/login")
    body = await r.text()
    assert "Log in with Discord" in body
    assert "member of the Discord server" in body or "in the Discord server" in body
    # Unauthenticated dashboard is gated -> redirect to /login.
    r = await client.get("/", allow_redirects=False)
    assert r.status == 302 and r.headers["Location"] == "/login"
    await client.close()


async def test_member_completes_login(discord_cfg, monkeypatch):
    _ws, client = await _client(discord_cfg, remote="127.0.0.1", monkeypatch=monkeypatch)
    _stub_identity(monkeypatch, {str(GUILD)})

    # Start OAuth: grab the state from the cookie the redirect sets.
    r = await client.get("/auth/discord/login", allow_redirects=False)
    assert r.status == 302 and r.headers["Location"].startswith(AUTHORIZE_URL)
    jar = SimpleCookie(r.headers["Set-Cookie"])
    state = jar["acbot_oauth_state"].value

    # Discord redirects back; membership check passes -> session + redirect to /.
    r = await client.get(f"/auth/discord/callback?code=abc&state={state}",
                         allow_redirects=False)
    assert r.status == 302 and r.headers["Location"] == "/", await r.text()
    assert "acbot_session=" in r.headers.get("Set-Cookie", "")

    # Session now grants access to the gated dashboard + API.
    r = await client.get("/", allow_redirects=False)
    assert r.status == 200
    await client.close()


async def test_non_member_is_denied_and_counts_as_failure(discord_cfg, monkeypatch):
    ws, client = await _client(discord_cfg, remote="203.0.113.9", monkeypatch=monkeypatch)
    _stub_identity(monkeypatch, {"999"})  # not in GUILD

    r = await client.get("/auth/discord/login", allow_redirects=False)
    state = SimpleCookie(r.headers["Set-Cookie"])["acbot_oauth_state"].value
    r = await client.get(f"/auth/discord/callback?code=abc&state={state}",
                         allow_redirects=False)
    body = await r.text()
    assert r.status == 403 and "not a member" in body
    assert "acbot_session=" not in r.headers.get("Set-Cookie", "")
    assert ws.auth.attempts_left("203.0.113.9") == 2  # one strike recorded
    await client.close()


async def test_state_mismatch_rejected(discord_cfg, monkeypatch):
    _ws, client = await _client(discord_cfg, remote="127.0.0.1", monkeypatch=monkeypatch)
    _stub_identity(monkeypatch, {str(GUILD)})
    await client.get("/auth/discord/login", allow_redirects=False)  # sets a real state
    r = await client.get("/auth/discord/callback?code=abc&state=WRONG",
                         allow_redirects=False)
    assert r.status == 400
    await client.close()
