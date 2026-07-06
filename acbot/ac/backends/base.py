"""Backend abstraction: vanilla acServer.exe vs AssettoServer.exe.

A backend knows where its executable lives, how to deploy the staged config
before launch, and which extras it supports (collision toggle, live time).
The Discord command surface never changes — unsupported features explain
themselves via NotSupportedError.
"""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from pathlib import Path

from ...config import Config
from ..ini import IniFile
from ..staging import Staging


class BackendError(Exception):
    pass


class NotSupportedError(BackendError):
    """Feature not available on this backend (message is user-facing)."""


class ServerBackend(ABC):
    name: str = "?"
    process_names: tuple[str, ...] = ()

    def __init__(self, cfg: Config):
        self.cfg = cfg

    # -- launch ------------------------------------------------------------

    @abstractmethod
    def exe_path(self) -> Path: ...

    @abstractmethod
    def cwd(self) -> Path: ...

    @abstractmethod
    def cfg_dir(self) -> Path: ...

    def args(self) -> list[str]:
        return []

    def deploy(self, staging: Staging) -> None:
        """Copy staged config into the server's cfg dir + enforce plugin wiring."""
        if not staging.is_ready():
            raise BackendError("No preset staged — run /preset apply first.")
        dest = self.cfg_dir()
        dest.mkdir(parents=True, exist_ok=True)
        for item in staging.dir.glob("*.ini"):
            shutil.copy2(item, dest / item.name)
        self._enforce_udp_plugin(dest / "server_cfg.ini")

    def _enforce_udp_plugin(self, server_cfg: Path) -> None:
        ini = IniFile.load(server_cfg)
        ini.set("SERVER", "UDP_PLUGIN_LOCAL_PORT", self.cfg.server.udp_plugin_server_port)
        ini.set(
            "SERVER", "UDP_PLUGIN_ADDRESS",
            f"127.0.0.1:{self.cfg.server.udp_listen_port}",
        )
        ini.save(server_cfg)

    # -- capabilities --------------------------------------------------------

    @property
    def can_disable_collisions(self) -> bool:
        return False

    def set_collisions(self, enabled: bool) -> str:
        raise NotSupportedError(
            "The vanilla AC server cannot disable car collisions — that needs the "
            "AssettoServer backend. You can set `/settings damage 0` so contacts "
            "at least cause no damage."
        )

    def live_time_command(self, hour: int, minute: int) -> str | None:
        """Console line to change time on a running server, if supported."""
        return None
