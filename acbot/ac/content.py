"""Index of installed cars/skins from the full AC install's content folder.

The dedicated server package only ships physics data; skins only exist in the
full game install (paths.ac_root), which is present on the VM because Content
Manager runs there.
"""

from __future__ import annotations

import asyncio
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
    """mtime-cached view of <ac_root>/content cars + tracks.

    The disk scan (hundreds of cars, each with a ui_car.json parse) is far too
    heavy for Discord's ~3s autocomplete budget, so it never runs on the event
    loop: `ensure_loaded()` warms the caches in a worker thread and queries just
    read the in-memory result. `acbot doctor` (no bot loop) scans inline.
    """

    def __init__(self, ac_root: Path | None):
        self.ac_root = ac_root
        self._cars: dict[str, Car] = {}
        self._cars_mtime: float | None = None
        self._tracks: list[str] = []
        self._tracks_mtime: float | None = None
        self._lock = asyncio.Lock()
        self._loading = False

    @property
    def cars_dir(self) -> Path | None:
        return self.ac_root / "content" / "cars" if self.ac_root else None

    @property
    def tracks_dir(self) -> Path | None:
        return self.ac_root / "content" / "tracks" if self.ac_root else None

    # -- scanning (heavy; kept off the event loop via ensure_loaded) ---------

    def _scan_cars(self, force: bool = False) -> None:
        cars_dir = self.cars_dir
        if cars_dir is None or not cars_dir.is_dir():
            self._cars = {}
            return
        mtime = cars_dir.stat().st_mtime
        if not force and self._cars_mtime == mtime and self._cars:
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
        self._cars_mtime = mtime
        log.info("content index: %d cars under %s", len(cars), cars_dir)

    def _scan_tracks(self, force: bool = False) -> None:
        tracks_dir = self.tracks_dir
        if tracks_dir is None or not tracks_dir.is_dir():
            self._tracks = []
            return
        mtime = tracks_dir.stat().st_mtime
        if not force and self._tracks_mtime == mtime and self._tracks:
            return
        names = sorted(
            d.name for d in tracks_dir.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        )
        self._tracks = names
        self._tracks_mtime = mtime
        log.info("content index: %d tracks under %s", len(names), tracks_dir)

    async def ensure_loaded(self, force: bool = False) -> None:
        """Warm the car + track caches in a worker thread (mtime-cached)."""
        async with self._lock:
            self._loading = True
            try:
                await asyncio.to_thread(self._scan_cars, force)
                await asyncio.to_thread(self._scan_tracks, force)
            finally:
                self._loading = False

    def refresh(self, force: bool = False) -> None:
        # Query-facing: while a warm holds the cache, don't scan on the event
        # loop — serve what's cached. `acbot doctor` has no warm, so it scans.
        if self._loading and not force:
            return
        self._scan_cars(force)

    # -- queries (cheap: read the warmed cache) ------------------------------

    def all_cars(self) -> list[Car]:
        self.refresh()
        return list(self._cars.values())

    def all_tracks(self) -> list[str]:
        if not self._loading:
            self._scan_tracks()
        return list(self._tracks)

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
