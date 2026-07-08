"""Login gate for the web UI: one shared password, per-IP lockout, sessions.

Three failed logins from an IP get it banned for 24h (both configurable). Bans
live in a plain-text file in the data dir (`web_bans.txt`) so an admin can lift
one by deleting its line — no restart needed, the file is re-read on every check.

Loopback (127.0.0.1 / ::1) is *never* banned, so you can't lock yourself out
from the host. The ban decision keys off the real TCP peer address the caller
passes in (aiohttp's `request.remote`), never a client-supplied header like
X-Forwarded-For — otherwise anyone could spoof `127.0.0.1` to dodge the ban.
"""

from __future__ import annotations

import hmac
import ipaddress
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

_BAN_HEADER = (
    "# acbot web UI ban list.\n"
    "# One banned IP per line:  <ip>\\t<until ISO-8601 | forever>\\t# note\n"
    "# To LIFT a ban: delete its line (or set the time in the past), then save.\n"
    "# Loopback (127.0.0.1 / ::1) is never banned and won't appear here.\n"
)


def _normalize(ip: str) -> ipaddress._BaseAddress | None:
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return None
    # Treat IPv4-mapped IPv6 (::ffff:127.0.0.1) as its IPv4 form.
    mapped = getattr(addr, "ipv4_mapped", None)
    return mapped or addr


def is_loopback(ip: str) -> bool:
    addr = _normalize(ip)
    return bool(addr and addr.is_loopback)


@dataclass
class _Ban:
    until: datetime | None  # None => permanent
    note: str = ""

    def active(self, now: datetime) -> bool:
        return self.until is None or now < self.until


class BanList:
    """The on-disk, human-editable ban file. The file is the source of truth:
    every check re-reads it, so manual edits take effect immediately."""

    def __init__(self, path: Path, never_ban: list[str] | None = None):
        self.path = path
        # Pre-normalize the always-exempt IPs for stable comparison.
        self._exempt: set[str] = set()
        for raw in never_ban or []:
            addr = _normalize(raw)
            self._exempt.add(str(addr) if addr else raw.strip())

    # -- exemption -----------------------------------------------------------

    def is_exempt(self, ip: str) -> bool:
        if is_loopback(ip):
            return True
        addr = _normalize(ip)
        key = str(addr) if addr else ip.strip()
        return key in self._exempt

    # -- reads ---------------------------------------------------------------

    def _load(self) -> dict[str, _Ban]:
        out: dict[str, _Ban] = {}
        if not self.path.exists():
            return out
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as e:
            log.warning("could not read ban list %s: %s", self.path, e)
            return out
        for line in text.splitlines():
            body, _, note = line.partition("#")
            body = body.strip()
            if not body:
                continue
            parts = body.split()
            ip = parts[0]
            until: datetime | None = None
            if len(parts) >= 2 and parts[1].lower() != "forever":
                try:
                    until = datetime.fromisoformat(parts[1])
                except ValueError:
                    until = None  # unparseable => treat as permanent
            out[ip] = _Ban(until=until, note=note.strip())
        return out

    def is_banned(self, ip: str, now: datetime | None = None) -> bool:
        if self.is_exempt(ip):
            return False
        now = now or datetime.now()
        ban = self._load().get(ip)
        return bool(ban and ban.active(now))

    def banned_until(self, ip: str) -> datetime | None:
        """The active ban's expiry, or None if not banned / permanent."""
        if self.is_exempt(ip):
            return None
        ban = self._load().get(ip)
        if ban and ban.active(datetime.now()):
            return ban.until
        return None

    # -- writes --------------------------------------------------------------

    def ban(self, ip: str, hours: int, note: str = "") -> datetime | None:
        """Ban `ip` for `hours` (<=0 => permanent). No-op for exempt IPs.

        Returns the expiry (None for permanent), or None when nothing was done.
        """
        if self.is_exempt(ip):
            log.info("refusing to ban exempt IP %s", ip)
            return None
        bans = self._load()
        until = datetime.now() + timedelta(hours=hours) if hours > 0 else None
        bans[ip] = _Ban(until=until, note=note)
        self._write(bans)
        log.warning("banned %s until %s (%s)", ip, until or "forever", note)
        return until

    def _write(self, bans: dict[str, _Ban]) -> None:
        lines = [_BAN_HEADER.rstrip("\n")]
        for ip, ban in sorted(bans.items()):
            when = ban.until.isoformat(timespec="seconds") if ban.until else "forever"
            row = f"{ip}\t{when}"
            if ban.note:
                row += f"\t# {ban.note}"
            lines.append(row)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.replace(self.path)


class WebAuth:
    """Password check + failed-attempt lockout + in-memory login sessions."""

    def __init__(self, *, password: str | None, bans_path: Path,
                 max_attempts: int = 3, ban_hours: int = 24,
                 session_hours: int = 12, never_ban: list[str] | None = None):
        self._password = password
        self.bans = BanList(bans_path, never_ban)
        self.max_attempts = max(1, max_attempts)
        self.ban_hours = ban_hours
        self._session_ttl = timedelta(hours=max(1, session_hours))
        self._fails: dict[str, int] = {}
        # token -> (expiry, who) where `who` is a display label for the audit log.
        self._sessions: dict[str, tuple[datetime, str]] = {}

    # -- ban / attempt state -------------------------------------------------

    def is_banned(self, ip: str) -> bool:
        return self.bans.is_banned(ip)

    def banned_until(self, ip: str) -> datetime | None:
        return self.bans.banned_until(ip)

    def attempts_left(self, ip: str) -> int:
        return max(0, self.max_attempts - self._fails.get(ip, 0))

    # -- password ------------------------------------------------------------

    def check_password(self, candidate: str) -> bool:
        if not self._password:
            return False
        return hmac.compare_digest(candidate.encode("utf-8"),
                                   self._password.encode("utf-8"))

    def record_failure(self, ip: str) -> bool:
        """Count a bad login. Returns True if this one tripped a ban."""
        if self.bans.is_exempt(ip):
            return False
        count = self._fails.get(ip, 0) + 1
        self._fails[ip] = count
        if count >= self.max_attempts:
            self.bans.ban(ip, self.ban_hours,
                          note=f"{count} failed logins by {datetime.now():%Y-%m-%d %H:%M}")
            self._fails.pop(ip, None)
            return True
        return False

    # -- sessions ------------------------------------------------------------

    def start_session(self, ip: str, who: str = "") -> str:
        """Clear failures for `ip` and mint a session token tagged with `who`."""
        self._fails.pop(ip, None)
        token = secrets.token_urlsafe(32)
        self._sessions[token] = (datetime.now() + self._session_ttl, who)
        self._sweep()
        return token

    def valid_session(self, token: str | None) -> bool:
        if not token:
            return False
        entry = self._sessions.get(token)
        if entry is None:
            return False
        if datetime.now() >= entry[0]:
            self._sessions.pop(token, None)
            return False
        return True

    def session_who(self, token: str | None) -> str | None:
        """The display label a valid session was created with (for auditing)."""
        if not token:
            return None
        entry = self._sessions.get(token)
        if entry is None or datetime.now() >= entry[0]:
            return None
        return entry[1] or None

    def end_session(self, token: str | None) -> None:
        if token:
            self._sessions.pop(token, None)

    def _sweep(self) -> None:
        now = datetime.now()
        for tok in [t for t, (exp, _who) in self._sessions.items() if now >= exp]:
            self._sessions.pop(tok, None)
