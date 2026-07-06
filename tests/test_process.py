import asyncio
import sys
from pathlib import Path

import pytest

from acbot.ac.backends.base import ServerBackend
from acbot.ac.process import CooldownError, ProcessError, ServerProcess
from acbot.config import Config
from acbot.events import EventBus


class DummyBackend(ServerBackend):
    """Runs a python sleeper instead of acServer.exe."""
    name = "dummy"
    process_names = ()

    def __init__(self, cfg: Config, script: str = "import time; time.sleep(30)"):
        super().__init__(cfg)
        self.script = script

    def exe_path(self) -> Path:
        return Path(sys.executable)

    def cwd(self) -> Path:
        return Path.cwd()

    def cfg_dir(self) -> Path:
        return self.cfg.data_dir / "dummy_cfg"

    def args(self) -> list[str]:
        return ["-c", self.script]

    def deploy(self, staging) -> None:  # no real config to deploy
        pass


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    c = Config()
    c.paths.data_dir = tmp_path / "data"
    c.server.restart_cooldown_s = 0
    return c


async def test_start_stop_lifecycle(cfg: Config):
    bus = EventBus()
    events: list[str] = []
    bus.subscribe("server_started", _record(events, "started"))
    bus.subscribe("server_stopped", _record(events, "stopped"))
    proc = ServerProcess(cfg, bus)
    backend = DummyBackend(cfg)

    assert not proc.is_running
    with pytest.raises(ProcessError):
        await proc.stop()

    await proc.start(backend, staging=None)
    assert proc.is_running
    assert proc.uptime_s is not None
    assert proc.log_path and proc.log_path.parent.exists()

    with pytest.raises(ProcessError):  # single instance: no double start
        await proc.start(backend, staging=None)

    await proc.stop()
    assert not proc.is_running
    assert events == ["started", "stopped"]


async def test_cooldown_blocks_rapid_restart(cfg: Config):
    cfg.server.restart_cooldown_s = 60
    proc = ServerProcess(cfg, EventBus())
    backend = DummyBackend(cfg)
    await proc.start(backend, staging=None)  # first action: no prior timestamp
    try:
        with pytest.raises(CooldownError):
            await proc.restart(backend, staging=None)
    finally:
        await proc.stop()


async def test_unexpected_exit_emits_event(cfg: Config):
    bus = EventBus()
    exited = asyncio.Event()

    async def on_exit(**_):
        exited.set()
    bus.subscribe("server_exited", on_exit)

    proc = ServerProcess(cfg, bus)
    await proc.start(DummyBackend(cfg, script="pass"), staging=None)
    await asyncio.wait_for(exited.wait(), timeout=10)
    assert not proc.is_running


async def test_console_write(cfg: Config):
    proc = ServerProcess(cfg, EventBus())
    assert await proc.send_console("hello") is False  # not running yet
    await proc.start(DummyBackend(cfg), staging=None)
    assert await proc.send_console("/settime 12:00") is True
    await proc.stop()


def _record(events: list[str], name: str):
    async def handler(**_):
        events.append(name)
    return handler
