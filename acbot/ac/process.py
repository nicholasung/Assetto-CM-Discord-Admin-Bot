"""Owns the single AC server process.

Guarantees exactly one server: an asyncio lock serializes start/stop, and
before any launch the whole machine is scanned (psutil) for stray
acServer/AssettoServer processes — e.g. one started by hand in Content
Manager — which must be explicitly taken over. A cooldown stops restart spam.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import datetime
from pathlib import Path

import psutil

from ..config import Config
from ..events import EventBus
from .backends.base import ServerBackend
from .staging import Staging

log = logging.getLogger(__name__)

STRAY_NAMES = {"acserver.exe", "acserver", "assettoserver.exe", "assettoserver"}


class ProcessError(Exception):
    """User-facing process control problem."""


class CooldownError(ProcessError):
    def __init__(self, remaining: float):
        self.remaining = remaining
        super().__init__(f"Cooldown: wait {int(remaining) + 1}s before the next start/stop.")


class StrayProcessError(ProcessError):
    def __init__(self, procs: list[psutil.Process]):
        self.procs = procs
        names = ", ".join(f"{p.info.get('name', '?')} (pid {p.pid})" for p in procs)
        super().__init__(
            f"Another AC server is already running outside my control: {names}. "
            "Stop it (or take over) before starting."
        )


class ServerProcess:
    def __init__(self, cfg: Config, bus: EventBus):
        self.cfg = cfg
        self.bus = bus
        self._lock = asyncio.Lock()
        self._proc: asyncio.subprocess.Process | None = None
        self._watch_task: asyncio.Task | None = None
        self._stopping = False
        self._last_action = 0.0
        self.backend: ServerBackend | None = None
        self.started_at: float | None = None
        self.log_path: Path | None = None

    # -- inspection ----------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self.is_running and self._proc else None

    @property
    def uptime_s(self) -> float | None:
        if self.is_running and self.started_at:
            return time.time() - self.started_at
        return None

    def find_strays(self) -> list[psutil.Process]:
        """AC server processes on the machine that aren't our child."""
        strays = []
        own_pid = self.pid
        for p in psutil.process_iter(["name", "pid"]):
            try:
                name = (p.info.get("name") or "").lower()
                if name in STRAY_NAMES and p.pid != own_pid:
                    strays.append(p)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return strays

    def _check_cooldown(self) -> None:
        elapsed = time.time() - self._last_action
        cooldown = self.cfg.server.restart_cooldown_s
        if elapsed < cooldown:
            raise CooldownError(cooldown - elapsed)

    # -- control -------------------------------------------------------------

    async def start(self, backend: ServerBackend, staging: Staging,
                    take_over: bool = False, skip_cooldown: bool = False) -> None:
        async with self._lock:
            if self.is_running:
                raise ProcessError("The server is already running (use /server restart).")
            if not skip_cooldown:
                self._check_cooldown()
            strays = self.find_strays()
            if strays:
                if not take_over:
                    raise StrayProcessError(strays)
                await self._kill_procs(strays)

            backend.deploy(staging)
            exe = backend.exe_path()
            self.cfg.logs_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            self.log_path = self.cfg.logs_dir / f"server-{stamp}.log"

            log.info("starting %s: %s", backend.name, exe)
            self._stopping = False
            # Send the server's output straight to the session log file via the
            # OS. Pumping every line through the event loop (with a per-line
            # flush) let a chatty live server starve Discord's heartbeat and the
            # 3s slash-command ack window -> "Unknown interaction" on everything.
            log_file = open(self.log_path, "ab")
            try:
                self._proc = await asyncio.create_subprocess_exec(
                    str(exe), *backend.args(),
                    cwd=str(backend.cwd()),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=log_file,
                    stderr=asyncio.subprocess.STDOUT,
                )
            finally:
                log_file.close()  # the child keeps its own inherited handle
            self.backend = backend
            self.started_at = time.time()
            self._last_action = time.time()
            self._watch_task = asyncio.create_task(self._watch(self._proc))
        await self.bus.emit("server_started", backend=backend.name)

    async def stop(self, skip_cooldown: bool = True) -> int | None:
        async with self._lock:
            if not self.is_running or not self._proc:
                raise ProcessError("The server is not running.")
            if not skip_cooldown:
                self._check_cooldown()
            self._stopping = True
            code = await self._terminate(self._proc)
            self._last_action = time.time()
            self._proc = None
            self.started_at = None
        await self.bus.emit("server_stopped", code=code)
        return code

    async def restart(self, backend: ServerBackend, staging: Staging) -> None:
        # One cooldown check for the whole operation.
        self._check_cooldown()
        if self.is_running:
            await self.stop()
        await self.start(backend, staging, skip_cooldown=True)

    async def send_console(self, line: str) -> bool:
        """Write a line to the server's stdin (AssettoServer console)."""
        if not self.is_running or not self._proc or not self._proc.stdin:
            return False
        try:
            self._proc.stdin.write((line.rstrip("\n") + "\n").encode("utf-8"))
            await self._proc.stdin.drain()
            return True
        except (ConnectionResetError, BrokenPipeError, OSError):
            return False

    # -- internals -----------------------------------------------------------

    async def _terminate(self, proc: asyncio.subprocess.Process) -> int | None:
        # Kill the whole tree: AssettoServer may have children.
        with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
            ps = psutil.Process(proc.pid)
            children = ps.children(recursive=True)
            for c in children:
                with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                    c.terminate()
        proc.terminate()
        try:
            return await asyncio.wait_for(proc.wait(), timeout=10)
        except TimeoutError:
            log.warning("server did not exit after terminate; killing")
            proc.kill()
            return await proc.wait()

    async def _kill_procs(self, procs: list[psutil.Process]) -> None:
        for p in procs:
            with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                p.terminate()
        _gone, alive = await asyncio.get_running_loop().run_in_executor(
            None, lambda: psutil.wait_procs(procs, timeout=5)
        )
        for p in alive:
            with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                p.kill()

    async def _watch(self, proc: asyncio.subprocess.Process) -> None:
        """Wait for the server to exit; if it wasn't asked to stop, surface why.

        The reason the server printed (bad config, port in use, missing content,
        …) is read from the tail of the session log so it shows up instead of a
        bare exit code.
        """
        code = await proc.wait()
        if not self._stopping and self._proc is proc:
            tail = self._read_log_tail()
            log.error(
                "server exited unexpectedly with code %s (see %s)%s",
                code, self.log_path,
                f"\n--- last output ---\n{tail}" if tail else " — no output captured",
            )
            self._proc = None
            self.started_at = None
            await self.bus.emit(
                "server_exited", code=code, tail=tail, log_path=str(self.log_path)
            )

    def _read_log_tail(self, n_lines: int = 25, max_bytes: int = 65536) -> str:
        """Last few non-blank lines of the current session log."""
        if not self.log_path:
            return ""
        try:
            with open(self.log_path, "rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - max_bytes))
                chunk = f.read()
        except OSError:
            return ""
        lines = [ln for ln in chunk.decode("utf-8", "replace").splitlines() if ln.strip()]
        return "\n".join(lines[-n_lines:])
