"""The admin web server: an aiohttp app whose handlers call straight into App.

Runs in the same process as everything else (started from App.startup, or
standalone via `acbot web`) so it shares the live server process, the ACSP
roster and the staged config — the web UI and the Discord bot are just two
front ends onto one App. Every request passes the auth middleware first
(see auth.py): banned IPs are refused, everything but /login needs a session.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING
from urllib.parse import quote

from aiohttp import web

from ..ac.backends.base import BackendError, NotSupportedError
from ..ac.process import CooldownError, ProcessError, StrayProcessError
from ..ac.staging import StagingError
from ..ac.uploads import UploadError
from ..config import Config
from ..leaderboard.queries import fmt_laptime, recent_laps
from ..util import fmt_duration, parse_hhmm
from .auth import WebAuth
from .pages import banned_page, dashboard_page, login_page
from .tls import build_ssl_context

if TYPE_CHECKING:
    from ..app import App

log = logging.getLogger(__name__)
audit_log = logging.getLogger("acbot.audit")

# Match the FileServer's per-upload cap so an admin can upload the same car zips.
UPLOAD_MAX_BYTES = 1024 * 1024 * 1024  # 1 GB


class _UploadTooLarge(Exception):
    pass


def _safe_unlink(path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


class WebServer:
    COOKIE = "acbot_session"

    def __init__(self, app: App, cfg: Config, password: str):
        self.app = app
        self.cfg = cfg
        self.auth = WebAuth(
            password=password,
            bans_path=cfg.web_bans_path,
            max_attempts=cfg.web.max_attempts,
            ban_hours=cfg.web.ban_hours,
            session_hours=cfg.web.session_hours,
            never_ban=cfg.web.never_ban,
        )
        self.web_app = web.Application(
            client_max_size=UPLOAD_MAX_BYTES + 16 * 1024 * 1024,
            middlewares=[self._make_middleware()],
        )
        self._add_routes()
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None

    # -- wiring --------------------------------------------------------------

    def _make_middleware(self):
        server = self

        @web.middleware
        async def _mw(request: web.Request, handler):
            ip = request.remote or ""
            if server.auth.is_banned(ip):
                return server._blocked(request)
            if request.path in ("/login", "/favicon.ico"):
                return await handler(request)
            if not server.auth.valid_session(request.cookies.get(server.COOKIE)):
                if request.path.startswith("/api/"):
                    return web.json_response({"ok": False, "error": "auth required"},
                                             status=401)
                return web.HTTPFound("/login")
            return await handler(request)

        return _mw

    def _add_routes(self) -> None:
        r = self.web_app.router
        r.add_get("/", self._dashboard)
        r.add_get("/login", self._login_get)
        r.add_post("/login", self._login_post)
        r.add_post("/logout", self._logout)
        r.add_get("/favicon.ico", lambda _req: web.Response(status=204))
        r.add_get("/api/status", self._api_status)
        r.add_get("/api/entries", self._api_entries)
        r.add_get("/api/presets", self._api_presets)
        r.add_get("/api/content", self._api_content)
        r.add_post("/api/server/start", self._api_start)
        r.add_post("/api/server/stop", self._api_stop)
        r.add_post("/api/server/restart", self._api_restart)
        r.add_post("/api/preset/apply", self._api_preset_apply)
        r.add_post("/api/entry/setcar", self._api_setcar)
        r.add_post("/api/entry/setskin", self._api_setskin)
        r.add_post("/api/settings/damage", self._api_damage)
        r.add_post("/api/settings/time", self._api_time)
        r.add_post("/api/settings/collisions", self._api_collisions)
        r.add_get("/api/uploads/pending", self._api_upload_pending)
        r.add_post("/api/uploads/approve", self._api_upload_approve)
        r.add_post("/api/uploads/reject", self._api_upload_reject)
        r.add_post("/api/upload", self._api_upload)
        r.add_get("/api/leaderboard/recent", self._api_recent)

    async def start(self) -> None:
        ssl_ctx = build_ssl_context(self.cfg)  # None => plain HTTP; raises on TLS misconfig
        self.runner = web.AppRunner(self.web_app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.cfg.web.host, self.cfg.web.port,
                                ssl_context=ssl_ctx)
        await self.site.start()
        scheme = "https" if ssl_ctx else "http"
        log.info("Web UI running on %s://%s:%d", scheme, self.cfg.web.host, self.cfg.web.port)

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()

    # -- helpers -------------------------------------------------------------

    def _err(self, message: str, status: int = 400) -> web.Response:
        return web.json_response({"ok": False, "error": message}, status=status)

    async def _json(self, request: web.Request) -> dict:
        try:
            data = await request.json()
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _audit(self, request: web.Request, action: str) -> None:
        audit_log.info("%s (web) | %s", request.remote or "?", action)

    def _download_base(self, request: web.Request) -> str:
        host = self.app.public_ip
        if not host:
            host = (request.host or "127.0.0.1").split(":")[0]
        port = getattr(getattr(self.app, "file_server", None), "port", 8082)
        return f"http://{host}:{port}/downloads"

    def _blocked(self, request: web.Request) -> web.Response:
        until = self.auth.banned_until(request.remote or "")
        if request.path.startswith("/api/"):
            return web.json_response({"ok": False, "error": "blocked"}, status=403)
        return web.Response(text=banned_page(until), content_type="text/html", status=403)

    # -- pages / auth --------------------------------------------------------

    async def _dashboard(self, _request: web.Request) -> web.Response:
        return web.Response(text=dashboard_page(), content_type="text/html")

    async def _login_get(self, request: web.Request) -> web.Response:
        if self.auth.valid_session(request.cookies.get(self.COOKIE)):
            return web.HTTPFound("/")
        return web.Response(text=login_page(), content_type="text/html")

    async def _login_post(self, request: web.Request) -> web.Response:
        ip = request.remote or ""
        form = await request.post()
        password = str(form.get("password") or "")
        if self.auth.check_password(password):
            token = self.auth.start_session(ip)
            resp = web.HTTPFound("/")
            resp.set_cookie(self.COOKIE, token, httponly=True, samesite="Lax",
                            secure=self.cfg.web.tls,
                            max_age=self.cfg.web.session_hours * 3600, path="/")
            self._audit(request, "signed in to the web UI")
            return resp
        # Wrong password: count it; a trip past the limit bans the IP.
        self.auth.record_failure(ip)
        if self.auth.is_banned(ip):
            return web.Response(
                text=login_page(banned_until=self.auth.banned_until(ip)),
                content_type="text/html", status=403)
        return web.Response(
            text=login_page(error="Incorrect password.",
                            attempts_left=self.auth.attempts_left(ip)),
            content_type="text/html", status=401)

    async def _logout(self, request: web.Request) -> web.Response:
        self.auth.end_session(request.cookies.get(self.COOKIE))
        resp = web.HTTPFound("/login")
        resp.del_cookie(self.COOKIE, path="/")
        return resp

    # -- status / reads ------------------------------------------------------

    async def _api_status(self, _request: web.Request) -> web.Response:
        app = self.app

        def safe(fn, default=None):
            try:
                return fn()
            except Exception:
                return default

        running = app.process.is_running
        track = safe(app.staging.track, ("", ""))
        t = safe(app.staging.get_time)
        out = {
            "ok": True,
            "running": running,
            "backend": app.cfg.server.backend,
            "server_name": safe(app.staging.server_name, "Assetto Corsa server"),
            "preset": safe(app.staging.preset_name),
            "track": (f"{track[0]} {track[1]}".strip() if track else ""),
            "damage": safe(app.staging.get_damage),
            "time": (f"{t[0]:02d}:{t[1]:02d}" if t else None),
        }
        if running:
            out["uptime"] = fmt_duration(app.process.uptime_s)
            app.listener.request_session_info()  # keep session/timeleft fresh
            info = await app.server_info()
            if info:
                out["clients"] = info.clients
                out["maxclients"] = info.maxclients
            session = app.listener.session
            if session:
                left = fmt_duration(info.timeleft) if info and info.timeleft else "—"
                out["session"] = f"{session.type_name} · {left} left"
            drivers = [d for d in app.listener.roster.values() if d.connected]
            out["drivers"] = [
                {"name": d.name, "model": d.model, "car_id": d.car_id}
                for d in sorted(drivers, key=lambda d: d.car_id)
            ]
            out["join_url"] = app.join_url()
        return web.json_response(out)

    async def _api_entries(self, _request: web.Request) -> web.Response:
        app = self.app
        try:
            entries = app.staging.entries()
        except Exception:
            return web.json_response({"ok": True, "entries": []})
        roster = {d.car_id: d for d in app.listener.roster.values() if d.connected}
        out = [
            {"slot": e.slot, "model": e.model, "skin": e.skin,
             "driver": (roster[e.slot].name if e.slot in roster else None)}
            for e in entries
        ]
        return web.json_response({"ok": True, "entries": out})

    async def _api_presets(self, _request: web.Request) -> web.Response:
        from ..ac.presets import list_presets
        app = self.app
        presets_dir = app.presets_dir()
        if presets_dir is None:
            return web.json_response({
                "ok": False, "presets": [],
                "error": "No CM presets folder found — set paths.cm_presets_dir.",
            })
        active = None
        try:
            active = app.staging.preset_name()
        except Exception:
            pass
        out = [
            {"name": p.name, "track": p.track_label, "max_clients": p.max_clients,
             "cars": p.cars, "active": p.name == active}
            for p in list_presets(presets_dir)
        ]
        return web.json_response({"ok": True, "presets": out})

    async def _api_content(self, request: web.Request) -> web.Response:
        app = self.app
        await app.content.ensure_loaded()
        base = self._download_base(request)
        cars = [
            {"id": c.car_id, "name": c.display_name,
             "url": f"{base}/cars/{quote(c.car_id)}"}
            for c in app.content.all_cars()
        ]
        return web.json_response({
            "ok": True, "download_base": base,
            "cars": cars, "tracks": app.content.all_tracks(),
        })

    # -- server control ------------------------------------------------------

    async def _api_start(self, request: web.Request) -> web.Response:
        from ..ac.presets import find_preset
        app = self.app
        data = await self._json(request)
        preset = (data.get("preset") or "").strip()
        take_over = bool(data.get("take_over"))
        if preset:
            presets_dir = app.presets_dir()
            if presets_dir is None:
                return self._err("No CM presets folder found.")
            found = find_preset(presets_dir, preset)
            if found is None:
                return self._err(f"Preset '{preset}' not found.")
            try:
                app.staging.apply_preset(found)
            except StagingError as e:
                return self._err(str(e))
            app.state.active_preset = found.name
            self._audit(request, f"applied preset '{found.name}'")
        try:
            await app.process.start(app.backend(), app.staging,
                                    take_over=take_over, skip_cooldown=take_over)
        except StrayProcessError as e:
            return web.json_response({"ok": False, "code": "stray", "error": str(e)},
                                     status=409)
        except CooldownError as e:
            return self._err(str(e), status=429)
        except (ProcessError, StagingError, BackendError) as e:
            return self._err(str(e))
        self._audit(request, f"started the server (preset {app.staging.preset_name()})")
        return web.json_response({"ok": True, "message": "🟢 Server started."})

    async def _api_stop(self, request: web.Request) -> web.Response:
        app = self.app
        if not app.process.is_running:
            return self._err("The server is not running.")
        try:
            await app.process.stop()
        except ProcessError as e:
            return self._err(str(e))
        self._audit(request, "stopped the server")
        return web.json_response({"ok": True, "message": "⚫ Server stopped."})

    async def _api_restart(self, request: web.Request) -> web.Response:
        app = self.app
        try:
            await app.process.restart(app.backend(), app.staging)
        except CooldownError as e:
            return self._err(str(e), status=429)
        except (ProcessError, StagingError, BackendError) as e:
            return self._err(str(e))
        self._audit(request, "restarted the server")
        return web.json_response({"ok": True, "message": "🔁 Server restarted."})

    async def _api_preset_apply(self, request: web.Request) -> web.Response:
        from ..ac.presets import find_preset
        app = self.app
        data = await self._json(request)
        name = (data.get("name") or "").strip()
        presets_dir = app.presets_dir()
        if presets_dir is None:
            return self._err("No CM presets folder found.")
        preset = find_preset(presets_dir, name)
        if preset is None:
            return self._err(f"Preset '{name}' not found.")
        try:
            app.staging.apply_preset(preset)
        except StagingError as e:
            return self._err(str(e))
        app.state.active_preset = preset.name
        self._audit(request, f"applied preset '{preset.name}'")
        tail = " Restart to apply." if app.process.is_running else " Start the server to use it."
        return web.json_response({"ok": True, "message": f"Staged preset {preset.name}.{tail}"})

    # -- entry edits ---------------------------------------------------------

    async def _api_setcar(self, request: web.Request) -> web.Response:
        app = self.app
        data = await self._json(request)
        try:
            slot = int(data.get("slot"))
        except (TypeError, ValueError):
            return self._err("Invalid slot.")
        car = (data.get("car") or "").strip()
        skin = (data.get("skin") or "").strip()
        if not car:
            return self._err("Car is required.")
        try:
            entry = app.staging.entry(slot)
        except StagingError as e:
            return self._err(str(e))
        if entry is None:
            return self._err(f"Slot {slot} does not exist.")
        known = app.content.get(car)
        if app.content.all_cars() and known is None:
            return self._err(f"Car '{car}' is not installed on the server.")
        skins = known.skins if known else []
        if skin:
            if skins and skin not in skins:
                return self._err(f"'{car}' has no skin '{skin}'. "
                                 f"Available: {', '.join(skins[:15]) or 'none'}")
        else:
            skin = entry.skin if entry.skin in skins else (skins[0] if skins else "")
        try:
            change = app.staging.set_entry_car(slot, car, skin or "")
        except StagingError as e:
            return self._err(str(e))
        self._audit(request, f"entry {change}")
        return web.json_response({"ok": True, "message": f"Staged: {change}"})

    async def _api_setskin(self, request: web.Request) -> web.Response:
        app = self.app
        data = await self._json(request)
        try:
            slot = int(data.get("slot"))
        except (TypeError, ValueError):
            return self._err("Invalid slot.")
        skin = (data.get("skin") or "").strip()
        try:
            entry = app.staging.entry(slot)
        except StagingError as e:
            return self._err(str(e))
        if entry is None:
            return self._err(f"Slot {slot} does not exist.")
        skins = app.content.skins_for(entry.model)
        if skins and skin not in skins:
            return self._err(f"'{entry.model}' has no skin '{skin}'. "
                             f"Available: {', '.join(skins[:15])}")
        try:
            change = app.staging.set_entry_skin(slot, skin)
        except StagingError as e:
            return self._err(str(e))
        self._audit(request, f"entry {change}")
        return web.json_response({"ok": True, "message": f"Staged: {change}"})

    # -- settings ------------------------------------------------------------

    async def _api_damage(self, request: web.Request) -> web.Response:
        app = self.app
        data = await self._json(request)
        try:
            percent = int(data.get("percent"))
        except (TypeError, ValueError):
            return self._err("Damage must be a number 0–100.")
        try:
            change = app.staging.set_damage(percent)
        except StagingError as e:
            return self._err(str(e))
        self._audit(request, change)
        return web.json_response({"ok": True, "message": f"Staged: {change}"})

    async def _api_time(self, request: web.Request) -> web.Response:
        app = self.app
        data = await self._json(request)
        try:
            hour, minute = parse_hhmm(str(data.get("value") or ""))
        except ValueError as e:
            return self._err(str(e))
        try:
            change = app.staging.set_time(hour, minute)
        except StagingError as e:
            return self._err(str(e))
        live_cmd = app.backend().live_time_command(hour, minute)
        if live_cmd and app.process.is_running and await app.process.send_console(live_cmd):
            self._audit(request, f"{change} (live)")
            return web.json_response(
                {"ok": True, "message": f"{change} — applied live and staged."})
        self._audit(request, change)
        return web.json_response({"ok": True, "message": f"Staged: {change}"})

    async def _api_collisions(self, request: web.Request) -> web.Response:
        app = self.app
        data = await self._json(request)
        state = str(data.get("state") or "").lower()
        try:
            change = app.backend().set_collisions(state == "on")
        except NotSupportedError as e:
            return self._err(str(e))
        except BackendError as e:
            return self._err(str(e))
        self._audit(request, change)
        return web.json_response({"ok": True, "message": f"Staged: {change}"})

    # -- uploads -------------------------------------------------------------

    async def _api_upload_pending(self, _request: web.Request) -> web.Response:
        pending = self.app.uploads.pending()
        if pending is None:
            return web.json_response({"ok": True, "pending": None})
        return web.json_response({"ok": True, "pending": {
            "label": pending.label, "filename": pending.filename, "cars": pending.cars}})

    async def _api_upload_approve(self, request: web.Request) -> web.Response:
        app = self.app
        try:
            installed = await asyncio.to_thread(app.uploads.install)
        except UploadError as e:
            return self._err(str(e))
        with contextlib.suppress(Exception):
            await app.content.ensure_loaded(force=True)
        cars = ", ".join(installed)
        self._audit(request, f"approved car install: {cars}")
        return web.json_response({"ok": True, "message": f"✅ Installed {cars}."})

    async def _api_upload_reject(self, request: web.Request) -> web.Response:
        await asyncio.to_thread(self.app.uploads.discard)
        self._audit(request, "rejected a pending car upload")
        return web.json_response({"ok": True})

    async def _api_upload(self, request: web.Request) -> web.Response:
        app = self.app
        try:
            reader = await request.multipart()
        except Exception:
            return self._err("Malformed upload.")
        field = await reader.next()
        while field is not None and not (field.name == "file" and field.filename):
            field = await reader.next()
        if field is None or not field.filename:
            return self._err("No file selected.")
        if not field.filename.lower().endswith(".zip"):
            return self._err("Please upload a .zip file.")

        app.uploads.dir.mkdir(parents=True, exist_ok=True)
        tmp = app.uploads.dir / f".web-incoming-{time.time_ns():x}.zip"
        try:
            size = 0
            with open(tmp, "wb") as f:
                while chunk := await field.read_chunk():
                    size += len(chunk)
                    if size > UPLOAD_MAX_BYTES:
                        raise _UploadTooLarge()
                    f.write(chunk)
            pending = await asyncio.to_thread(app.uploads.save, tmp, field.filename)
        except _UploadTooLarge:
            _safe_unlink(tmp)
            return self._err("File too large.")
        except UploadError as e:
            _safe_unlink(tmp)
            return self._err(str(e))
        finally:
            _safe_unlink(tmp)  # save() renamed it on success; no-op then

        # Mirror the /upload page: let the Discord approval flow see it too.
        with contextlib.suppress(Exception):
            await app.bus.emit("car_uploaded", pending=pending)
        self._audit(request, f"uploaded car zip {field.filename} (pending approval)")
        return web.json_response(
            {"ok": True, "message": f"Uploaded {pending.label} — approve it below."})

    # -- leaderboard ---------------------------------------------------------

    async def _api_recent(self, _request: web.Request) -> web.Response:
        try:
            rows = await recent_laps(self.app.db)
        except Exception:
            return web.json_response({"ok": True, "laps": []})
        laps = [
            {"laptime": fmt_laptime(r["laptime_ms"]), "name": r["name"],
             "car_model": r["car_model"], "track": r["track"], "layout": r["layout"],
             "clean": r["cuts"] == 0}
            for r in rows
        ]
        return web.json_response({"ok": True, "laps": laps})
