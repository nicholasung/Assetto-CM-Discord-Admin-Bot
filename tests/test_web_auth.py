"""Login gate: password check, per-IP lockout, loopback exemption, ban file."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from acbot.web.auth import BanList, WebAuth, is_loopback


@pytest.fixture
def bans_path(tmp_path):
    return tmp_path / "web_bans.txt"


# -- loopback / exemption ----------------------------------------------------

def test_loopback_detection():
    assert is_loopback("127.0.0.1")
    assert is_loopback("::1")
    assert is_loopback("::ffff:127.0.0.1")  # IPv4-mapped IPv6
    assert not is_loopback("203.0.113.5")
    assert not is_loopback("not-an-ip")


def test_loopback_is_never_banned(bans_path):
    bans = BanList(bans_path)
    assert bans.ban("127.0.0.1", 24) is None
    assert not bans.is_banned("127.0.0.1")
    # ban() refused to write anything for the exempt IP.
    assert not bans_path.exists()


def test_never_ban_list_covers_extra_ips(bans_path):
    bans = BanList(bans_path, never_ban=["203.0.113.9"])
    assert bans.is_exempt("203.0.113.9")
    assert bans.ban("203.0.113.9", 24) is None
    assert not bans.is_banned("203.0.113.9")


# -- ban lifecycle -----------------------------------------------------------

def test_ban_and_expiry(bans_path):
    bans = BanList(bans_path)
    until = bans.ban("203.0.113.5", 24)
    assert until is not None
    assert bans.is_banned("203.0.113.5")
    # A past expiry (as if the clock advanced) is no longer an active ban.
    assert not bans.is_banned("203.0.113.5", now=until + timedelta(minutes=1))


def test_permanent_ban(bans_path):
    bans = BanList(bans_path)
    assert bans.ban("203.0.113.7", hours=0) is None  # None expiry = forever
    assert bans.is_banned("203.0.113.7")
    assert bans.is_banned("203.0.113.7", now=datetime.now() + timedelta(days=3650))


def test_manual_edit_lifts_ban(bans_path):
    """Deleting the IP's line in the file lifts the ban with no restart."""
    bans = BanList(bans_path)
    bans.ban("203.0.113.5", 24)
    assert bans.is_banned("203.0.113.5")
    bans_path.write_text("# emptied by an admin\n", encoding="utf-8")
    assert not bans.is_banned("203.0.113.5")


def test_ban_file_is_reread_each_check(bans_path):
    """A hand-written entry is honoured (the file is the source of truth)."""
    bans = BanList(bans_path)
    future = (datetime.now() + timedelta(hours=1)).isoformat(timespec="seconds")
    bans_path.write_text(f"198.51.100.2\t{future}\t# by hand\n", encoding="utf-8")
    assert bans.is_banned("198.51.100.2")


def test_unparseable_until_is_permanent(bans_path):
    bans = BanList(bans_path)
    bans_path.write_text("198.51.100.3  garbage-date\n", encoding="utf-8")
    assert bans.is_banned("198.51.100.3")


# -- WebAuth: attempts, sessions, password -----------------------------------

def _auth(bans_path, **kw):
    return WebAuth(password="hunter2", bans_path=bans_path,
                   max_attempts=3, ban_hours=24, **kw)


def test_password_check(bans_path):
    auth = _auth(bans_path)
    assert auth.check_password("hunter2")
    assert not auth.check_password("wrong")
    assert not auth.check_password("")


def test_three_strikes_bans_the_ip(bans_path):
    auth = _auth(bans_path)
    ip = "203.0.113.20"
    assert auth.record_failure(ip) is False  # 1
    assert auth.attempts_left(ip) == 2
    assert auth.record_failure(ip) is False  # 2
    assert auth.attempts_left(ip) == 1
    assert auth.record_failure(ip) is True   # 3 -> banned
    assert auth.is_banned(ip)


def test_exempt_ip_never_banned_however_many_failures(bans_path):
    auth = _auth(bans_path)
    for _ in range(10):
        assert auth.record_failure("127.0.0.1") is False
    assert not auth.is_banned("127.0.0.1")


def test_success_clears_failures_and_starts_session(bans_path):
    auth = _auth(bans_path)
    ip = "203.0.113.21"
    auth.record_failure(ip)
    auth.record_failure(ip)  # 2 of 3
    token = auth.start_session(ip)
    assert auth.valid_session(token)
    # Counter was reset, so it takes a fresh 3 to ban again.
    assert auth.attempts_left(ip) == 3


def test_session_invalid_and_logout(bans_path):
    auth = _auth(bans_path)
    assert not auth.valid_session(None)
    assert not auth.valid_session("bogus")
    token = auth.start_session("203.0.113.22")
    auth.end_session(token)
    assert not auth.valid_session(token)


def test_session_expiry(bans_path):
    auth = WebAuth(password="pw", bans_path=bans_path, session_hours=1)
    token = auth.start_session("203.0.113.23", who="ada")
    assert auth.session_who(token) == "ada"
    # Force the stored expiry into the past.
    auth._sessions[token] = (datetime.now() - timedelta(seconds=1), "ada")
    assert not auth.valid_session(token)
    assert auth.session_who(token) is None
