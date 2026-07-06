"""Content Manager server preset discovery.

A preset is a folder containing server_cfg.ini (+ entry_list.ini and any
extras like csp_extra_options.ini). CM keeps them under its data dir; the
exact path varies per install, so discovery tries known candidates unless an
explicit path is configured. Presets are treated as read-only — the bot
copies them into its staging folder and edits only the copies.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .ini import IniFile


def candidate_preset_dirs() -> list[Path]:
    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
    docs = Path(os.environ.get("USERPROFILE", str(home))) / "Documents"
    return [
        local / "AcTools Content Manager" / "Presets" / "Server Presets",
        docs / "Assetto Corsa" / "server presets",
    ]


def resolve_presets_dir(configured: str) -> Path | None:
    """Explicit path, or first auto-candidate that contains presets."""
    if configured and configured.lower() != "auto":
        p = Path(configured)
        return p if p.is_dir() else None
    for cand in candidate_preset_dirs():
        if cand.is_dir() and any(_is_preset(d) for d in cand.iterdir()):
            return cand
    return None


def _is_preset(d: Path) -> bool:
    return d.is_dir() and (d / "server_cfg.ini").is_file()


@dataclass
class Preset:
    name: str
    path: Path
    track: str = ""
    layout: str = ""
    max_clients: int = 0
    cars: list[str] = field(default_factory=list)  # unique models, file order

    @property
    def track_label(self) -> str:
        return f"{self.track} ({self.layout})" if self.layout else self.track


def read_preset(path: Path) -> Preset:
    cfg = IniFile.load(path / "server_cfg.ini")
    cars: list[str] = []
    entry = path / "entry_list.ini"
    if entry.is_file():
        el = IniFile.load(entry)
        for section in el.sections():
            if section.upper().startswith("CAR_"):
                model = el.get(section, "MODEL", "") or ""
                if model and model not in cars:
                    cars.append(model)
    return Preset(
        name=path.name,
        path=path,
        track=cfg.get("SERVER", "TRACK", "") or "",
        layout=cfg.get("SERVER", "CONFIG_TRACK", "") or "",
        max_clients=cfg.get_int("SERVER", "MAX_CLIENTS", 0) or 0,
        cars=cars,
    )


def list_presets(presets_dir: Path) -> list[Preset]:
    out = []
    for d in sorted(presets_dir.iterdir(), key=lambda p: p.name.lower()):
        if _is_preset(d):
            try:
                out.append(read_preset(d))
            except (OSError, UnicodeError):
                continue
    return out


def find_preset(presets_dir: Path, name: str) -> Preset | None:
    for p in list_presets(presets_dir):
        if p.name.lower() == name.lower():
            return p
    return None
