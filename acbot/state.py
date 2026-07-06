"""Small persisted bot state (data/state.json), written atomically."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class BotState:
    def __init__(self, path: Path):
        self.path = path
        self._data: dict[str, Any] = {}
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self._save()

    # Convenience accessors ------------------------------------------------

    @property
    def active_preset(self) -> str | None:
        return self.get("active_preset")

    @active_preset.setter
    def active_preset(self, name: str | None) -> None:
        self.set("active_preset", name)

    @property
    def status_message(self) -> tuple[int, int] | None:
        v = self.get("status_message")
        if isinstance(v, list) and len(v) == 2:
            return int(v[0]), int(v[1])
        return None

    @status_message.setter
    def status_message(self, value: tuple[int, int] | None) -> None:
        self.set("status_message", list(value) if value else None)
