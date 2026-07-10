"""Composition root: builds and wires all services the cogs use."""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import quote_plus

from .ac.backends.assettoserver import AssettoServerBackend
from .ac.backends.base import BackendError, ServerBackend
from .ac.backends.vanilla import VanillaBackend
from .ac.content import ContentIndex
from .ac.http_info import fetch_info, resolve_public_ip
from .ac.presets import resolve_presets_dir
from .ac.process import ProcessError, ServerProcess, StrayProcessError
from .ac.staging import Staging
from .ac.udp import AcspListener
from .ac.uploads import PendingUpload, UploadError, UploadStore
from .config import ASSETTOSERVER, Config
from .events import EventBus
from .leaderboard.db import LeaderboardDB
from .leaderboard.ingest import LapIngest
from .state import BotState

log = logging.getLogger(__name__)


from aiohttp import web

# Cap per uploaded car zip; the request body limit adds headroom for multipart.
UPLOAD_MAX_BYTES = 1024 * 1024 * 1024  # 1 GB


class _UploadTooLarge(Exception):
    pass


class FileServer:
    """Serves AC content over HTTP. Folders are zipped on demand; only the
    most recently requested zip is kept on disk so the cache dir never grows.

    When an `upload_store` is provided it also hosts a static upload page
    (GET/POST /upload) that parks a car zip for admin approval (see UploadStore);
    each accepted upload fires `on_upload` so the bot can prompt in Discord."""

    def __init__(self, host: str, port: int, root_dir: Path, cache_dir: Path,
                 upload_store: UploadStore | None = None,
                 on_upload: Callable[[PendingUpload], Awaitable[None]] | None = None,
                 max_upload_bytes: int = UPLOAD_MAX_BYTES):
        self.uploads = upload_store
        self.on_upload = on_upload
        self.max_upload_bytes = max_upload_bytes
        self.app = web.Application(client_max_size=max_upload_bytes + 16 * 1024 * 1024)
        self.app.router.add_get('/downloads/{filename:.+}', self._serve_file)
        # Landing page + its polling endpoint (see _download_page). The Discord
        # links point at /get/... so the browser never hangs on a cold zip build.
        self.app.router.add_get('/get/{path:.+}', self._download_page)
        self.app.router.add_get('/prepare/{path:.+}', self._prepare_status)
        if upload_store is not None:
            self.app.router.add_get('/upload', self._upload_form)
            self.app.router.add_post('/upload', self._handle_upload)
        self.runner = None
        self.site = None
        self.root = root_dir.resolve()
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.host = host
        self.port = port
        self._zip_lock = asyncio.Lock()
        # Folder path -> in-flight background zip build, so the landing page can
        # poll build status without kicking off a duplicate build each time.
        self._builds: dict[str, asyncio.Task] = {}

    def _resolve(self, path: str) -> Path:
        """Resolve a content-relative path, refusing to escape the content root."""
        fpath = (self.root / path).resolve()
        try:
            fpath.relative_to(self.root)
        except ValueError:
            raise web.HTTPForbidden()
        return fpath

    async def _serve_file(self, request: web.Request) -> web.FileResponse:
        fpath = self._resolve(request.match_info['filename'])

        if not fpath.exists():
            raise web.HTTPNotFound()

        if fpath.is_dir():
            fpath = await self._package(fpath)

        return web.FileResponse(fpath)

    # -- download landing page ------------------------------------------------

    async def _download_page(self, request: web.Request) -> web.Response:
        """HTML page shown by the Discord link: names the content, shows a
        "preparing…" spinner, and reveals a download button once the zip is
        built. The heavy lifting happens on /prepare, which the page polls."""
        path = request.match_info['path']
        fpath = self._resolve(path)
        if not fpath.exists():
            raise web.HTTPNotFound(text="That content is no longer available.")
        name = request.query.get("name") or fpath.name
        return web.Response(text=_download_page(name, path), content_type="text/html")

    async def _prepare_status(self, request: web.Request) -> web.Response:
        """Report (and, on first call, kick off) the zip build for `path`.

        Returns JSON {status: ready|building|error|missing}. A single file needs
        no packaging and is ready immediately; a folder is zipped in the
        background so the poll returns quickly while the build runs."""
        path = request.match_info['path']
        fpath = self._resolve(path)
        if not fpath.exists():
            return web.json_response({"status": "missing"}, status=404)

        if fpath.is_file():
            return web.json_response(
                {"status": "ready", "size": _human_size(fpath.stat().st_size)})

        target = self.cache_dir / f"{fpath.name}.zip"
        if self._zip_is_fresh(target, fpath):
            return web.json_response(
                {"status": "ready", "size": _human_size(target.stat().st_size)})

        key = str(fpath)
        task = self._builds.get(key)
        if task is not None and task.done():
            self._builds.pop(key, None)
            if (exc := task.exception()) is not None:
                log.error("failed to package %s for download: %r", fpath, exc)
                return web.json_response({"status": "error"})
            # Built successfully — the fresh-zip check above catches this on the
            # next poll, but report ready now that the archive exists.
            return web.json_response(
                {"status": "ready", "size": _human_size(task.result().stat().st_size)})
        if task is None:
            self._builds[key] = asyncio.create_task(self._package(fpath))
        return web.json_response({"status": "building"})

    async def _package(self, folder: Path) -> Path:
        """Zip `folder` on demand, reusing a cached zip when one is already built.

        A browser download of one file opens several parallel / range requests;
        without this each would re-zip the whole folder and hammer the loop. We
        build a folder's zip once, reuse it for every follow-up request, and keep
        only the most recent folder's zip so the cache dir never grows.
        """
        target = self.cache_dir / f"{folder.name}.zip"
        if self._zip_is_fresh(target, folder):
            return target
        async with self._zip_lock:
            # Re-check inside the lock: a concurrent request may have built it.
            if self._zip_is_fresh(target, folder):
                return target
            self._clear_cache(keep=target)
            tmp_base = self.cache_dir / f".{folder.name}.building"
            archive = await asyncio.to_thread(
                shutil.make_archive, str(tmp_base), "zip", root_dir=folder
            )
            try:
                Path(archive).replace(target)
            except OSError:
                # Target is locked (an earlier zip is still downloading) — serve
                # the fresh build directly; it gets cleaned on the next request.
                return Path(archive)
            return target

    @staticmethod
    def _zip_is_fresh(zip_path: Path, folder: Path) -> bool:
        try:
            return zip_path.stat().st_mtime >= folder.stat().st_mtime
        except OSError:
            return False

    def _clear_cache(self, keep: Path | None = None) -> None:
        for stale in self.cache_dir.glob("*.zip"):
            if keep is not None and stale == keep:
                continue
            try:
                stale.unlink()
            except OSError:
                log.warning("could not remove stale zip %s (still being downloaded?)", stale)

    # -- uploads (parked for admin approval) ---------------------------------

    async def _upload_form(self, request: web.Request) -> web.Response:
        return web.Response(
            text=_upload_page(ok=request.query.get("ok"), err=request.query.get("err")),
            content_type="text/html",
        )

    async def _handle_upload(self, request: web.Request) -> web.StreamResponse:
        assert self.uploads is not None
        try:
            reader = await request.multipart()
        except Exception:
            return web.HTTPFound("/upload?err=Malformed+upload.")

        field = await reader.next()
        while field is not None and not (field.name == "file" and field.filename):
            field = await reader.next()
        if field is None or not field.filename:
            return web.HTTPFound("/upload?err=No+file+selected.")
        if not field.filename.lower().endswith(".zip"):
            return web.HTTPFound("/upload?err=Please+upload+a+.zip+file.")

        self.uploads.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.uploads.dir / f".incoming-{time.time_ns():x}.zip"
        try:
            size = 0
            with open(tmp, "wb") as f:
                while chunk := await field.read_chunk():
                    size += len(chunk)
                    if size > self.max_upload_bytes:
                        raise _UploadTooLarge()
                    f.write(chunk)
            pending = await asyncio.to_thread(self.uploads.save, tmp, field.filename)
        except _UploadTooLarge:
            self._safe_unlink(tmp)
            return web.HTTPFound("/upload?err=File+too+large.")
        except UploadError as e:
            self._safe_unlink(tmp)
            return web.HTTPFound(f"/upload?err={quote_plus(str(e))}")
        finally:
            self._safe_unlink(tmp)  # save() renamed it on success; no-op then

        if self.on_upload is not None:
            try:
                await self.on_upload(pending)
            except Exception:
                log.exception("failed to notify Discord of the pending upload")
        return web.HTTPFound("/upload?ok=1")

    @staticmethod
    def _safe_unlink(path: Path) -> None:
        try:
            path.unlink()
        except OSError:
            pass

    async def start(self) -> None:
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()
        log.info(f"Download server running on {self.host}:{self.port}")

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()
        self._clear_cache()


def _human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"  # unreachable, keeps type checkers happy


def _download_page(name: str, path: str) -> str:
    import json
    from html import escape

    safe_name = escape(name)
    # json.dumps yields a safe JS string literal for the raw (un-encoded) path;
    # the page URL-encodes each segment before fetching.
    path_json = json.dumps(path)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Download · {safe_name}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: system-ui, sans-serif; margin: 0; min-height: 100vh;
         display: grid; place-items: center; background: #14161a; color: #e8eaed; }}
  .card {{ width: min(92vw, 460px); background: #1e2127; border: 1px solid #2c313a;
          border-radius: 14px; padding: 28px; box-shadow: 0 8px 30px rgba(0,0,0,.35);
          text-align: center; }}
  h1 {{ font-size: 1.15rem; margin: 0 0 6px; color: #a5abb5; font-weight: 600; }}
  .name {{ font-size: 1.25rem; font-weight: 700; margin: 0 0 24px; word-break: break-word; }}
  .status {{ display: flex; align-items: center; justify-content: center; gap: 12px;
            color: #a5abb5; font-size: .95rem; }}
  .spinner {{ width: 20px; height: 20px; border: 3px solid #2c313a; border-top-color: #3b82f6;
            border-radius: 50%; animation: spin .8s linear infinite; }}
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
  .btn {{ display: block; text-decoration: none; padding: 14px; border-radius: 10px;
          background: #3b82f6; color: #fff; font-size: 1rem; font-weight: 600; }}
  .btn:hover {{ background: #2f6fe0; }}
  .note {{ padding: 12px 14px; border-radius: 10px; font-size: .9rem;
          background: #33161a; border: 1px solid #6b2530; }}
</style></head>
<body><div class="card">
  <h1>Preparing your download</h1>
  <p class="name">{safe_name}</p>
  <div id="status" class="status"><div class="spinner"></div><span>Packaging files…</span></div>
  <a id="dl" class="btn" download style="display:none">Download</a>
  <div id="err" class="note" style="display:none">Something went wrong preparing this
    download. Refresh the page to try again.</div>
</div>
<script>
  const path = {path_json};
  const encoded = path.split('/').map(encodeURIComponent).join('/');
  const statusEl = document.getElementById('status');
  const dl = document.getElementById('dl');
  const err = document.getElementById('err');
  async function poll() {{
    try {{
      const data = await (await fetch('/prepare/' + encoded)).json();
      if (data.status === 'ready') {{
        statusEl.style.display = 'none';
        dl.href = '/downloads/' + encoded;
        dl.textContent = data.size ? 'Download (' + data.size + ')' : 'Download';
        dl.style.display = 'block';
        return;
      }}
      if (data.status === 'error' || data.status === 'missing') {{
        statusEl.style.display = 'none';
        err.style.display = 'block';
        return;
      }}
    }} catch (e) {{ /* transient — keep polling */ }}
    setTimeout(poll, 1500);
  }}
  poll();
</script>
</body></html>"""


def _upload_page(ok: str | None = None, err: str | None = None) -> str:
    if ok:
        banner = ('<div class="note ok">✅ Upload received. An admin has been asked '
                  'to approve the install in Discord.</div>')
    elif err:
        from html import escape
        banner = f'<div class="note err">⚠️ {escape(err)}</div>'
    else:
        banner = ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Upload a car</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: system-ui, sans-serif; margin: 0; min-height: 100vh;
         display: grid; place-items: center; background: #14161a; color: #e8eaed; }}
  .card {{ width: min(92vw, 460px); background: #1e2127; border: 1px solid #2c313a;
          border-radius: 14px; padding: 28px; box-shadow: 0 8px 30px rgba(0,0,0,.35); }}
  h1 {{ font-size: 1.3rem; margin: 0 0 4px; }}
  p.sub {{ margin: 0 0 20px; color: #a5abb5; font-size: .92rem; line-height: 1.4; }}
  input[type=file] {{ width: 100%; padding: 14px; border: 1px dashed #3a4150;
          border-radius: 10px; background: #171a20; color: #e8eaed; box-sizing: border-box; }}
  button {{ margin-top: 16px; width: 100%; padding: 12px; border: 0; border-radius: 10px;
          background: #3b82f6; color: #fff; font-size: 1rem; font-weight: 600; cursor: pointer; }}
  button:hover {{ background: #2f6fe0; }}
  .note {{ padding: 12px 14px; border-radius: 10px; margin-bottom: 18px; font-size: .9rem; }}
  .note.ok {{ background: #12331f; border: 1px solid #1f6b3a; }}
  .note.err {{ background: #33161a; border: 1px solid #6b2530; }}
</style></head>
<body><form class="card" method="post" action="/upload" enctype="multipart/form-data">
  {banner}
  <h1>Upload a car</h1>
  <p class="sub">Pick a car mod <code>.zip</code>. It won't go live until an admin
    approves it in Discord — until then only the most recent upload is kept.</p>
  <input type="file" name="file" accept=".zip" required>
  <button type="submit">Upload for approval</button>
</form></body></html>"""


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        cfg.ensure_dirs()
        self.bus = EventBus()
        self.state = BotState(cfg.state_path)
        self.staging = Staging(cfg.staging_dir)
        self.content = ContentIndex(cfg.paths.ac_root)
        self.uploads = UploadStore(
            cfg.pending_upload_dir,
            (cfg.paths.ac_root / "content" / "cars") if cfg.paths.ac_root else None,
        )
        self.process = ServerProcess(cfg, self.bus)
        self.db = LeaderboardDB(cfg.db_path)
        self.listener = AcspListener(
            self.bus,
            listen_host=cfg.server.udp_listen_host,
            listen_port=cfg.server.udp_listen_port,
            server_port=cfg.server.udp_plugin_server_port,
            entry_count_hint=self._entry_count,
        )
        self._backend: ServerBackend | None = None
        self.ingest: LapIngest | None = None
        self.public_ip: str | None = None
        self.web_server = None  # started in startup() when web is enabled
        self.bus.subscribe("server_started", self._on_server_started)

    # -- services ------------------------------------------------------------

    def backend(self) -> ServerBackend:
        if self._backend is None:
            if self.cfg.server.backend == ASSETTOSERVER:
                self._backend = AssettoServerBackend(self.cfg)
            else:
                self._backend = VanillaBackend(self.cfg)
        return self._backend

    def presets_dir(self) -> Path | None:
        return resolve_presets_dir(self.cfg.paths.cm_presets_dir)

    def _entry_count(self) -> int:
        try:
            return len(self.staging.entries())
        except Exception:
            return 24

    def _results_base(self) -> Path | None:
        try:
            return self.backend().cwd()
        except Exception:
            return None

    # -- live info -------------------------------------------------------------

    async def server_info(self):
        try:
            port = self.staging.http_port()
        except Exception:
            return None
        return await fetch_info("127.0.0.1", port)

    def join_url(self) -> str | None:
        if not self.public_ip:
            return None
        try:
            port = self.staging.http_port()
        except Exception:
            return None
        return f"https://acstuff.club/s/q:race/online/join?ip={self.public_ip}&httpPort={port}"

    # -- lifecycle ---------------------------------------------------------------

    async def startup(self) -> None:
        await self.db.open()
        self.ingest = LapIngest(self.db, self.staging, self.bus,
                                results_base=self._results_base())
        await self.listener.start()
        # A server might already be up (e.g. bot restarted): ask it who's on.
        self.listener.request_session_info()
        self.listener.request_all_car_info()
        self.public_ip = await resolve_public_ip(self.cfg.server.public_ip)
        if self.public_ip:
            log.info("public IP: %s", self.public_ip)
        self.file_server = FileServer(
            host="0.0.0.0",
            port=8082,
            root_dir=self.cfg.paths.ac_root / "content",
            cache_dir=self.cfg.downloads_cache_dir,
            upload_store=self.uploads,
            on_upload=self._on_car_uploaded,
        )
        await self.file_server.start()
        await self._start_web()
        # Warm the car/track index off the loop so the first autocomplete is
        # instant instead of triggering a multi-second scan on the event loop.
        self._content_warm = asyncio.create_task(self._warm_content())

    async def _start_web(self) -> None:
        """Start the admin web UI when enabled and its login method is ready."""
        if not self.cfg.web.enabled:
            return
        if not self.cfg.web_auth_ready():
            if self.cfg.web.auth == "discord":
                log.warning("web UI enabled but Discord OAuth isn't configured — skipping it "
                            "(need discord.guild_id, web.discord_client_id and "
                            "ACBOT_WEB_DISCORD_SECRET/web.discord_client_secret)")
            else:
                log.warning("web UI enabled but no password set — skipping it "
                            "(set ACBOT_WEB_PASSWORD or web.password to enable)")
            return
        from .web.server import WebServer
        from .web.tls import WebTLSError
        server = WebServer(self, self.cfg)
        try:
            await server.start()
        except WebTLSError:
            # Never serve the admin UI in the clear when HTTPS was requested but
            # is misconfigured — leave it off (bot keeps running) and log why.
            log.exception("web UI not started: TLS is misconfigured")
            return
        self.web_server = server

    async def _warm_content(self) -> None:
        try:
            await self.content.ensure_loaded()
        except Exception:
            log.exception("content index warm-up failed")

    async def _on_car_uploaded(self, pending: PendingUpload) -> None:
        # Fan the HTTP upload out to the bot (UploadsCog posts the approval prompt).
        await self.bus.emit("car_uploaded", pending=pending)

    async def shutdown(self) -> None:
        self.listener.close()
        await self.db.close()
        await self.file_server.stop()
        if self.web_server is not None:
            await self.web_server.stop()

    async def autostart_if_configured(self) -> None:
        """Launch the AC server on bot boot if server.autostart is set.

        Uses whatever config is already staged in data/active/ (persists
        across bot restarts from the last /preset apply) — never reaches
        into Content Manager itself. Any problem is logged, not raised: a
        bad autostart must never prevent the bot from coming up.
        """
        if not self.cfg.server.autostart:
            return
        if self.process.is_running:
            return
        if not self.staging.is_ready():
            log.warning("server.autostart is on but no preset is staged yet — "
                       "run /preset apply once, then restart the bot")
            return
        try:
            await self.process.start(self.backend(), self.staging, skip_cooldown=True)
        except StrayProcessError as e:
            log.warning("autostart skipped: %s", e)
        except (ProcessError, BackendError):
            log.exception("autostart failed")
        else:
            log.info("autostarted the AC server (preset %s)", self.staging.preset_name())

    async def _on_server_started(self, **_: object) -> None:
        self.listener.reset()
