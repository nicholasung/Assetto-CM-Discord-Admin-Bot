"""Stock Kunos dedicated server (acServer.exe)."""

from __future__ import annotations

from pathlib import Path

from .base import BackendError, ServerBackend


class VanillaBackend(ServerBackend):
    name = "vanilla"
    process_names = ("acserver.exe", "acserver")

    def _server_dir(self) -> Path:
        d = self.cfg.paths.server_dir
        if not d:
            raise BackendError("paths.server_dir is not configured.")
        return d

    def exe_path(self) -> Path:
        d = self._server_dir()
        for name in ("acServer.exe", "acServer"):
            p = d / name
            if p.exists():
                return p
        raise BackendError(f"acServer executable not found in {d}")

    def cwd(self) -> Path:
        return self._server_dir()

    def cfg_dir(self) -> Path:
        return self._server_dir() / "cfg"
