from pathlib import Path

import pytest

from acbot.ac.presets import read_preset
from acbot.app import App
from acbot.config import Config
from tests.test_process import DummyBackend


class FakeStray:
    """Stand-in for a psutil.Process, as used by ServerProcess.find_strays()."""

    def __init__(self, pid: int = 1234, name: str = "acServer.exe"):
        self.pid = pid
        self.info = {"name": name}


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    c = Config()
    c.paths.data_dir = tmp_path / "data"
    c.server.restart_cooldown_s = 0
    return c


async def test_disabled_by_default_does_nothing(cfg: Config):
    app = App(cfg)
    await app.autostart_if_configured()
    assert not app.process.is_running


async def test_skips_without_a_staged_preset(cfg: Config):
    cfg.server.autostart = True
    app = App(cfg)
    assert not app.staging.is_ready()
    await app.autostart_if_configured()
    assert not app.process.is_running


async def test_launches_the_previously_staged_preset(cfg: Config, presets_dir: Path):
    cfg.server.autostart = True
    app = App(cfg)
    app.staging.apply_preset(read_preset(presets_dir / "Race Night"))
    app._backend = DummyBackend(cfg)
    try:
        await app.autostart_if_configured()
        assert app.process.is_running
    finally:
        if app.process.is_running:
            await app.process.stop()


async def test_noop_when_already_running(cfg: Config, presets_dir: Path):
    cfg.server.autostart = True
    app = App(cfg)
    app.staging.apply_preset(read_preset(presets_dir / "Race Night"))
    app._backend = DummyBackend(cfg)
    await app.process.start(app._backend, app.staging)
    try:
        await app.autostart_if_configured()  # must not raise "already running"
        assert app.process.is_running
    finally:
        await app.process.stop()


async def test_backs_off_when_a_stray_server_is_already_up(
        cfg: Config, presets_dir: Path, monkeypatch):
    cfg.server.autostart = True
    app = App(cfg)
    app.staging.apply_preset(read_preset(presets_dir / "Race Night"))
    app._backend = DummyBackend(cfg)
    monkeypatch.setattr(app.process, "find_strays", lambda: [FakeStray()])
    await app.autostart_if_configured()
    assert not app.process.is_running  # never auto-kills someone's live server
