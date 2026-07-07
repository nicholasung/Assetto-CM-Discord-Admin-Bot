"""Holds a single pending car-zip upload awaiting admin approval, then installs
it into the AC content/cars folder once an admin accepts it.

Uploads arrive over HTTP from anyone with the link (see app.FileServer), so
nothing is trusted enough to install on its own: the zip sits in a single-slot
holding area — a new upload overwrites the last un-accepted one — until an admin
approves it from Discord.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

ZIP_FILE = "pending.zip"
META_FILE = "pending.json"

# Guard against zip bombs (total uncompressed size). Kept well above the 1 GB
# upload cap so a legitimate large car mod isn't rejected on its expanded size.
MAX_UNCOMPRESSED_BYTES = 6 * 1024 * 1024 * 1024  # 6 GiB

# A member has to look like real car content or we refuse the whole zip.
_CAR_MARKERS = ("data.acd", "ui_car.json")
_CAR_SUFFIXES = (".kn5",)
_JUNK_TOPS = ("__macosx",)


class UploadError(Exception):
    """A user-facing problem with an uploaded zip (bad format, not a car, …)."""


@dataclass
class PendingUpload:
    filename: str
    uploaded_at: float
    cars: list[str] = field(default_factory=list)
    token: str = ""

    @property
    def label(self) -> str:
        return ", ".join(self.cars) if self.cars else self.filename


def _analyze(zip_path: Path) -> list[tuple[zipfile.ZipInfo, str]]:
    """Map the zip's car content onto paths under content/cars/.

    Returns ``[(member, dest_rel_path)]``; raises ``UploadError`` when the file
    isn't a usable car zip. Handles both a CM-style archive that carries the
    whole ``content/cars/<id>/…`` tree and a bare ``<id>/…`` car folder.
    """
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as e:
        raise UploadError("That file isn't a valid .zip archive.") from e

    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if not infos:
            raise UploadError("The zip is empty.")
        if sum(i.file_size for i in infos) > MAX_UNCOMPRESSED_BYTES:
            raise UploadError("The zip is too large to install.")

        members: list[tuple[zipfile.ZipInfo, str]] = []
        # Case 1: an archive that includes the full content/cars/<id>/… tree.
        for info in infos:
            low = info.filename.replace("\\", "/").lower()
            idx = low.find("content/cars/")
            if idx == -1:
                members = []
                break
            rel = info.filename.replace("\\", "/")[idx + len("content/cars/"):].lstrip("/")
            if rel:
                members.append((info, rel))

        # Case 2: the zip's top-level folders are the car folders themselves.
        if not members:
            for info in infos:
                name = info.filename.replace("\\", "/").lstrip("/")
                top = name.split("/", 1)[0]
                if not top or top.lower() in _JUNK_TOPS or top.startswith("."):
                    continue
                members.append((info, name))

        if not members:
            raise UploadError("Couldn't find any car folder inside the zip.")

        looks_like_car = any(
            Path(rel).name.lower() in _CAR_MARKERS or rel.lower().endswith(_CAR_SUFFIXES)
            for _, rel in members
        )
        if not looks_like_car:
            raise UploadError(
                "That doesn't look like a car mod (no data.acd, .kn5 or ui_car.json).")
        return members


def _car_ids(members: list[tuple[zipfile.ZipInfo, str]]) -> list[str]:
    return sorted({rel.split("/", 1)[0] for _, rel in members})


class UploadStore:
    def __init__(self, pending_dir: Path, cars_dir: Path | None):
        self.dir = pending_dir
        self.cars_dir = cars_dir
        self._zip = pending_dir / ZIP_FILE
        self._meta = pending_dir / META_FILE

    # -- holding the single pending zip ------------------------------------

    def save(self, tmp_zip: Path, filename: str) -> PendingUpload:
        """Adopt a freshly-uploaded zip as *the* pending upload, replacing any
        previously held (never-accepted) one. Raises UploadError on junk."""
        members = _analyze(tmp_zip)
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp_zip.replace(self._zip)  # single slot: overwrite the last upload
        pending = PendingUpload(
            filename=filename,
            uploaded_at=time.time(),
            cars=_car_ids(members),
            token=f"{time.time_ns():x}",
        )
        self._meta.write_text(json.dumps(asdict(pending)), encoding="utf-8")
        log.info("held pending car upload %s (cars: %s)", filename, pending.cars)
        return pending

    def pending(self) -> PendingUpload | None:
        if not (self._zip.is_file() and self._meta.is_file()):
            return None
        try:
            return PendingUpload(**json.loads(self._meta.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError, TypeError):
            return None

    def discard(self) -> None:
        for p in (self._zip, self._meta):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                log.warning("could not remove pending upload file %s", p)

    # -- install (admin-approved) ------------------------------------------

    def install(self) -> list[str]:
        if self.pending() is None:
            raise UploadError("There's no pending upload to install.")
        if self.cars_dir is None:
            raise UploadError("No AC content/cars folder is configured on this host.")
        members = _analyze(self._zip)
        cars_root = self.cars_dir.resolve()
        cars_root.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(self._zip) as zf:
            for info, rel in members:
                dest = (cars_root / rel).resolve()
                if not dest.is_relative_to(cars_root):  # zip-slip guard
                    raise UploadError(f"Refusing unsafe path in zip: {rel}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
        installed = _car_ids(members)
        log.info("installed cars from upload: %s", installed)
        self.discard()
        return installed
