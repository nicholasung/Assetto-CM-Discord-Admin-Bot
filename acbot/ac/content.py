"""Index of installed cars/skins from the full AC install's content folder.

The dedicated server package only ships physics data; skins only exist in the
full game install (paths.ac_root), which is present on the VM because Content
Manager runs there.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class Car:
    car_id: str
    display_name: str
    skins: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        if self.display_name and self.display_name != self.car_id:
            return f"{self.display_name} ({self.car_id})"
        return self.car_id


def _read_display_name(car_dir: Path) -> str:
    ui = car_dir / "ui" / "ui_car.json"
    if not ui.exists():
        return car_dir.name
    try:
        # Mod ui_car.json files are frequently malformed (BOM, control chars,
        # trailing commas) — salvage the name or fall back to the folder name.
        text = ui.read_text(encoding="utf-8-sig", errors="replace")
        data = json.loads(text, strict=False)
        name = str(data.get("name") or "").strip()
        return name or car_dir.name
    except (json.JSONDecodeError, OSError):
        return car_dir.name


class ContentIndex:
    """Lazy, mtime-cached view of <ac_root>/content/cars."""

    def __init__(self, ac_root: Path | None):
        self.ac_root = ac_root
        self._cars: dict[str, Car] = {}
        self._mtime: float | None = None

    @property
    def cars_dir(self) -> Path | None:
        return self.ac_root / "content" / "cars" if self.ac_root else None

    def refresh(self, force: bool = False) -> None:
        cars_dir = self.cars_dir
        if cars_dir is None or not cars_dir.is_dir():
            self._cars = {}
            return
        mtime = cars_dir.stat().st_mtime
        if not force and self._mtime == mtime and self._cars:
            return
        cars: dict[str, Car] = {}
        for car_dir in sorted(cars_dir.iterdir()):
            if not car_dir.is_dir() or car_dir.name.startswith("."):
                continue
            skins_dir = car_dir / "skins"
            skins = []
            if skins_dir.is_dir():
                skins = sorted(
                    d.name for d in skins_dir.iterdir()
                    if d.is_dir() and not d.name.startswith(".")
                )
            cars[car_dir.name.lower()] = Car(
                car_id=car_dir.name,
                display_name=_read_display_name(car_dir),
                skins=skins,
            )
        self._cars = cars
        self._mtime = mtime
        log.info("content index: %d cars under %s", len(cars), cars_dir)

    # -- queries -----------------------------------------------------------

    def all_cars(self) -> list[Car]:
        self.refresh()
        return list(self._cars.values())

    def get(self, car_id: str) -> Car | None:
        self.refresh()
        return self._cars.get(car_id.lower())

    def skins_for(self, car_id: str) -> list[str]:
        car = self.get(car_id)
        return car.skins if car else []

    def search(self, query: str, limit: int = 25) -> list[Car]:
        """Substring search over id + display name (for autocomplete)."""
        self.refresh()
        q = query.lower().strip()
        if not q:
            return list(self._cars.values())[:limit]
        starts, contains = [], []
        for car in self._cars.values():
            hay_id = car.car_id.lower()
            hay_name = car.display_name.lower()
            if hay_id.startswith(q) or hay_name.startswith(q):
                starts.append(car)
            elif q in hay_id or q in hay_name:
                contains.append(car)
        return (starts + contains)[:limit]
