"""Composition root: builds and wires all services the cogs use."""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from .ac.backends.assettoserver import AssettoServerBackend
from .ac.backends.base import BackendError, ServerBackend
from .ac.backends.vanilla import VanillaBackend
from .ac.content import ContentIndex
from .ac.http_info import fetch_info, resolve_public_ip
from .ac.presets import resolve_presets_dir
from .ac.process import ProcessError, ServerProcess, StrayProcessError
from .ac.staging import Staging
from .ac.udp import AcspListener
from .config import ASSETTOSERVER, Config
from .events import EventBus
from .leaderboard.db import LeaderboardDB
from .leaderboard.ingest import LapIngest
from .state import BotState

log = logging.getLogger(__name__)


from aiohttp import web

class FileServer:
    """Serves AC content over HTTP. Folders are zipped on demand; only the
    most recently requested zip is kept on disk so the cache dir never grows."""

    def __init__(self, host: str, port: int, root_dir: Path, cache_dir: Path):
        self.app = web.Application()
        self.app.router.add_get('/downloads/{filename:.+}', self._serve_file)
        self.runner = None
        self.site = None
        self.root = root_dir.resolve()
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.host = host
        self.port = port
        self._zip_lock = asyncio.Lock()

    async def _serve_file(self, request: web.Request) -> web.FileResponse:
        filename = request.match_info['filename']
        fpath = (self.root / filename).resolve()

        try:
            fpath.relative_to(self.root)
        except ValueError:
            raise web.HTTPForbidden()

        if not fpath.exists():
            raise web.HTTPNotFound()

        if fpath.is_dir():
            fpath = await self._package(fpath)

        return web.FileResponse(fpath)

    async def _package(self, folder: Path) -> Path:
        """Zip `folder` into cache_dir, clearing any previously hosted zip first."""
        async with self._zip_lock:
            self._clear_cache()
            archive = await asyncio.to_thread(
                shutil.make_archive, str(self.cache_dir / folder.name), "zip", root_dir=folder
            )
            return Path(archive)

    def _clear_cache(self) -> None:
        for stale in self.cache_dir.glob("*.zip"):
            try:
                stale.unlink()
            except OSError:
                log.warning("could not remove stale zip %s (still being downloaded?)", stale)

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

class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        cfg.ensure_dirs()
        self.bus = EventBus()
        self.state = BotState(cfg.state_path)
        self.staging = Staging(cfg.staging_dir)
        self.content = ContentIndex(cfg.paths.ac_root)
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
        )
        await self.file_server.start()

    async def shutdown(self) -> None:
        self.listener.close()
        await self.db.close()
        await self.file_server.stop()

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
