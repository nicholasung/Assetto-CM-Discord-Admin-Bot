"""AssettoServer (https://assettoserver.org) — CSP-based acServer replacement.

Uses the same server_cfg.ini / entry_list.ini, plus cfg/extra_cfg.yml for its
own settings. Because AS is actively developed and option names move around,
the collision toggle and live time command are wired through config
(assettoserver.collisions_yaml_key / settime_console_template) rather than
hardcoded — `acbot doctor` and the README explain how to fill them in.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

from .base import BackendError, NotSupportedError, ServerBackend

log = logging.getLogger(__name__)


class AssettoServerBackend(ServerBackend):
    name = "assettoserver"
    process_names = ("assettoserver.exe", "assettoserver")

    def _as_dir(self) -> Path:
        d = self.cfg.paths.assettoserver_dir
        if not d:
            raise BackendError("paths.assettoserver_dir is not configured.")
        return d

    def exe_path(self) -> Path:
        d = self._as_dir()
        for name in ("AssettoServer.exe", "AssettoServer"):
            p = d / name
            if p.exists():
                return p
        raise BackendError(f"AssettoServer executable not found in {d}")

    def cwd(self) -> Path:
        return self._as_dir()

    def cfg_dir(self) -> Path:
        return self._as_dir() / "cfg"

    # -- collisions ----------------------------------------------------------

    @property
    def can_disable_collisions(self) -> bool:
        return bool(self.cfg.assettoserver.collisions_yaml_key)

    @property
    def extra_cfg_path(self) -> Path:
        return self.cfg_dir() / "extra_cfg.yml"

    def set_collisions(self, enabled: bool) -> str:
        """Writes the configured extra_cfg.yml key (true = collisions DISABLED)."""
        key = self.cfg.assettoserver.collisions_yaml_key
        if not key:
            raise NotSupportedError(
                "No collision switch configured for AssettoServer. Check your "
                "version's cfg/extra_cfg.yml for the relevant option and set "
                "`assettoserver.collisions_yaml_key` in config.yaml."
            )
        path = self.extra_cfg_path
        if not path.exists():
            raise BackendError(
                f"{path} not found — start AssettoServer once so it generates its config."
            )
        value = not enabled  # key semantics: true disables collisions
        self._patch_yaml_key(path, key, value)
        return f"collisions {'ON' if enabled else 'OFF'} ({key}: {str(value).lower()})"

    def _patch_yaml_key(self, path: Path, dot_key: str, value: bool) -> None:
        parts = dot_key.split(".")
        text = path.read_text(encoding="utf-8")
        if len(parts) == 1:
            # Top-level key: patch the line in place to keep AS's comments.
            pattern = re.compile(rf"^({re.escape(parts[0])}\s*:).*$", re.MULTILINE)
            new_text, n = pattern.subn(rf"\1 {str(value).lower()}", text)
            if n:
                path.write_text(new_text, encoding="utf-8")
                return
        # Nested (or missing) key: yaml round-trip. Comments are lost, which
        # is why the line patch above is preferred for top-level keys.
        data = yaml.safe_load(text) or {}
        node = data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise BackendError(f"extra_cfg.yml: {dot_key} path blocked at {part!r}")
        node[parts[-1]] = value
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        log.warning("extra_cfg.yml rewritten via YAML round-trip; comments were dropped")

    # -- live time -----------------------------------------------------------

    def live_time_command(self, hour: int, minute: int) -> str | None:
        template = self.cfg.assettoserver.settime_console_template
        if not template:
            return None
        return template.format(hour=hour, minute=minute, h=hour, m=minute)
