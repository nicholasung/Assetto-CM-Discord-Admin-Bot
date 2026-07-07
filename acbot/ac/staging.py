"""The bot's staged server configuration (data/active/).

`apply_preset` copies a CM preset here; every Discord edit (car swap, damage,
time of day, …) patches these copies. Backends deploy from here on start, so
presets themselves are never modified and changes take effect on restart.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from .ini import IniFile
from .presets import Preset

META_FILE = ".acbot_preset.json"

# Vanilla lighting: SUN_ANGLE = 16 * (hour - 13), drivable range ~08:00-18:00.
SUN_DEG_PER_HOUR = 16.0
SUN_HOUR_MIN, SUN_HOUR_MAX = 8.0, 18.0


class StagingError(Exception):
    pass


def time_to_sun_angle(hour: int, minute: int) -> float:
    return SUN_DEG_PER_HOUR * (hour + minute / 60.0 - 13.0)


def sun_angle_to_time(angle: float) -> tuple[int, int]:
    decimal = angle / SUN_DEG_PER_HOUR + 13.0
    h = int(decimal)
    m = int(round((decimal - h) * 60))
    if m == 60:
        h, m = h + 1, 0
    return h, m


@dataclass
class Entry:
    slot: int
    model: str
    skin: str
    guid: str = ""
    ballast: str = ""
    restrictor: str = ""

    @property
    def label(self) -> str:
        skin = self.skin or "<default skin>"
        return f"#{self.slot}: {self.model} [{skin}]"


class Staging:
    def __init__(self, staging_dir: Path):
        self.dir = staging_dir

    # -- lifecycle ---------------------------------------------------------

    @property
    def server_cfg_path(self) -> Path:
        return self.dir / "server_cfg.ini"

    @property
    def entry_list_path(self) -> Path:
        return self.dir / "entry_list.ini"

    def is_ready(self) -> bool:
        return self.server_cfg_path.is_file() and self.entry_list_path.is_file()

    def apply_preset(self, preset: Preset) -> None:
        if not (preset.path / "entry_list.ini").is_file():
            raise StagingError(f"Preset '{preset.name}' has no entry_list.ini")
        self.dir.mkdir(parents=True, exist_ok=True)
        for item in self.dir.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
        shutil.copytree(preset.path, self.dir, dirs_exist_ok=True)
        (self.dir / META_FILE).write_text(
            json.dumps({"preset": preset.name, "applied_at": time.time()}),
            encoding="utf-8",
        )

    def preset_name(self) -> str | None:
        meta = self.dir / META_FILE
        if meta.is_file():
            try:
                return json.loads(meta.read_text(encoding="utf-8")).get("preset")
            except (json.JSONDecodeError, OSError):
                return None
        return None

    # -- ini access --------------------------------------------------------

    def _require_ready(self) -> None:
        if not self.is_ready():
            raise StagingError("No preset staged yet — run /preset apply first.")

    def server_cfg(self) -> IniFile:
        self._require_ready()
        return IniFile.load(self.server_cfg_path)

    def entry_list(self) -> IniFile:
        self._require_ready()
        return IniFile.load(self.entry_list_path)

    # -- reads used all over -----------------------------------------------

    def server_name(self) -> str:
        return self.server_cfg().get("SERVER", "NAME", "AC Server") or "AC Server"

    def track(self) -> tuple[str, str]:
        cfg = self.server_cfg()
        return cfg.get("SERVER", "TRACK", "") or "", cfg.get("SERVER", "CONFIG_TRACK", "") or ""

    def http_port(self) -> int:
        return self.server_cfg().get_int("SERVER", "HTTP_PORT", 8081) or 8081

    def tcp_port(self) -> int:
        return self.server_cfg().get_int("SERVER", "TCP_PORT", 9600) or 9600

    def entries(self) -> list[Entry]:
        el = self.entry_list()
        out: list[Entry] = []
        for section in el.sections():
            s = section.upper()
            if not s.startswith("CAR_"):
                continue
            try:
                slot = int(s.split("_", 1)[1])
            except ValueError:
                continue
            out.append(Entry(
                slot=slot,
                model=el.get(section, "MODEL", "") or "",
                skin=el.get(section, "SKIN", "") or "",
                guid=el.get(section, "GUID", "") or "",
                ballast=el.get(section, "BALLAST", "") or "",
                restrictor=el.get(section, "RESTRICTOR", "") or "",
            ))
        return sorted(out, key=lambda e: e.slot)

    def entry(self, slot: int) -> Entry | None:
        for e in self.entries():
            if e.slot == slot:
                return e
        return None

    # -- edits (each returns a short old->new description for the audit log)

    def allowed_cars(self) -> list[str]:
        """Models in server_cfg.ini's [SERVER] CARS list (the server's allow-list)."""
        raw = self.server_cfg().get("SERVER", "CARS", "") or ""
        return [c.strip() for c in raw.split(";") if c.strip()]

    def ensure_car_allowed(self, model: str) -> bool:
        """Add `model` to the [SERVER] CARS allow-list if missing.

        The AC server validates every entry's MODEL against CARS and refuses to
        start ("car X is illegal") when one is absent — so a car swap has to
        touch server_cfg.ini too, not just entry_list.ini (Content Manager does
        both). Append-only: never drops a car another slot or pickup mode allows.
        Returns True when it had to add the model.
        """
        cfg = self.server_cfg()
        cars = [c.strip() for c in (cfg.get("SERVER", "CARS", "") or "").split(";") if c.strip()]
        if any(c.lower() == model.lower() for c in cars):
            return False
        cars.append(model)
        cfg.set("SERVER", "CARS", ";".join(cars))
        cfg.save(self.server_cfg_path)
        return True

    def set_entry_car(self, slot: int, model: str, skin: str) -> str:
        el = self.entry_list()
        section = f"CAR_{slot}"
        if not el.has_section(section):
            raise StagingError(f"Entry slot {slot} does not exist in the entry list.")
        old_model = el.get(section, "MODEL", "") or ""
        old_skin = el.get(section, "SKIN", "") or ""
        el.set(section, "MODEL", model)
        el.set(section, "SKIN", skin)
        el.save(self.entry_list_path)
        added = self.ensure_car_allowed(model)
        note = "  (added to allowed cars)" if added else ""
        return f"slot {slot}: {old_model} [{old_skin}] → {model} [{skin}]{note}"

    def set_entry_skin(self, slot: int, skin: str) -> str:
        el = self.entry_list()
        section = f"CAR_{slot}"
        if not el.has_section(section):
            raise StagingError(f"Entry slot {slot} does not exist in the entry list.")
        old_skin = el.get(section, "SKIN", "") or ""
        el.set(section, "SKIN", skin)
        el.save(self.entry_list_path)
        model = el.get(section, "MODEL", "") or ""
        return f"slot {slot} ({model}): skin {old_skin} → {skin}"

    def get_damage(self) -> int:
        return self.server_cfg().get_int("SERVER", "DAMAGE_MULTIPLIER", 100) or 0

    def set_damage(self, percent: int) -> str:
        if not 0 <= percent <= 100:
            raise StagingError("Damage must be 0–100%.")
        cfg = self.server_cfg()
        old = cfg.get_int("SERVER", "DAMAGE_MULTIPLIER", 100)
        cfg.set("SERVER", "DAMAGE_MULTIPLIER", percent)
        cfg.save(self.server_cfg_path)
        return f"damage {old}% → {percent}%"

    def get_time(self) -> tuple[int, int]:
        angle = self.server_cfg().get_float("LIGHTING", "SUN_ANGLE", 0.0) or 0.0
        return sun_angle_to_time(angle)

    def set_time(self, hour: int, minute: int) -> str:
        decimal = hour + minute / 60.0
        if not SUN_HOUR_MIN <= decimal <= SUN_HOUR_MAX:
            raise StagingError(
                f"Vanilla AC only supports {int(SUN_HOUR_MIN):02d}:00–{int(SUN_HOUR_MAX):02d}:00 "
                "(sun angle limit). CSP/AssettoServer WeatherFX is needed for night."
            )
        cfg = self.server_cfg()
        oh, om = sun_angle_to_time(cfg.get_float("LIGHTING", "SUN_ANGLE", 0.0) or 0.0)
        cfg.set("LIGHTING", "SUN_ANGLE", round(time_to_sun_angle(hour, minute), 2))
        cfg.save(self.server_cfg_path)
        return f"time {oh:02d}:{om:02d} → {hour:02d}:{minute:02d}"

    # -- leaderboard policy snapshot ----------------------------------------

    def policy_snapshot(self) -> dict:
        cfg = self.server_cfg()
        keys = (
            "ABS_ALLOWED", "TC_ALLOWED", "STABILITY_ALLOWED", "AUTOCLUTCH_ALLOWED",
            "TYRE_BLANKETS_ALLOWED", "TYRE_WEAR_RATE", "FUEL_RATE",
            "DAMAGE_MULTIPLIER", "FORCE_VIRTUAL_MIRROR",
        )
        snap = {k.lower(): cfg.get("SERVER", k) for k in keys}
        snap["dynamic_track"] = cfg.items("DYNAMIC_TRACK") or None
        return {k: v for k, v in snap.items() if v is not None}
