"""Shared fixture builders: fake AC content tree + CM presets on tmp_path."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

SERVER_CFG = """\
[SERVER]
NAME=Test Server ;x
CARS=ks_mazda_mx5_cup;abarth500
TRACK=ks_brands_hatch
CONFIG_TRACK=gp
MAX_CLIENTS=4
HTTP_PORT=8081
TCP_PORT=9600
UDP_PORT=9600
ABS_ALLOWED=1
TC_ALLOWED=1
STABILITY_ALLOWED=0
AUTOCLUTCH_ALLOWED=1
TYRE_BLANKETS_ALLOWED=0
TYRE_WEAR_RATE=100
FUEL_RATE=100
DAMAGE_MULTIPLIER=50

[LIGHTING]
SUN_ANGLE=0
; midday-ish

[DYNAMIC_TRACK]
SESSION_START=96
RANDOMNESS=2
"""

ENTRY_LIST = """\
[CAR_0]
MODEL=ks_mazda_mx5_cup
SKIN=red
SPECTATOR_MODE=0
DRIVERNAME=
TEAM=
GUID=
BALLAST=0
RESTRICTOR=0

[CAR_1]
MODEL=ks_mazda_mx5_cup
SKIN=blue
GUID=
BALLAST=0

[CAR_2]
MODEL=abarth500
SKIN=stripes
GUID=
"""


def make_content_tree(root: Path) -> Path:
    """<root>/content/cars with two cars + skins; one has a broken ui json."""
    cars = root / "content" / "cars"
    mx5 = cars / "ks_mazda_mx5_cup"
    (mx5 / "skins" / "red").mkdir(parents=True)
    (mx5 / "skins" / "blue").mkdir(parents=True)
    (mx5 / "ui").mkdir(parents=True)
    (mx5 / "ui" / "ui_car.json").write_text(
        json.dumps({"name": "Mazda MX5 Cup", "brand": "Mazda"}), encoding="utf-8"
    )
    abarth = cars / "abarth500"
    (abarth / "skins" / "stripes").mkdir(parents=True)
    (abarth / "ui").mkdir(parents=True)
    # Deliberately malformed (BOM + trailing comma) like plenty of mods.
    (abarth / "ui" / "ui_car.json").write_bytes(
        b"\xef\xbb\xbf" + b'{"name": "Abarth 500",}'
    )
    return root


def make_preset(presets_dir: Path, name: str, server_cfg: str = SERVER_CFG,
                entry_list: str = ENTRY_LIST) -> Path:
    p = presets_dir / name
    p.mkdir(parents=True)
    (p / "server_cfg.ini").write_text(server_cfg, encoding="utf-8")
    (p / "entry_list.ini").write_text(entry_list, encoding="utf-8")
    return p


@pytest.fixture
def content_root(tmp_path: Path) -> Path:
    return make_content_tree(tmp_path / "ac")


@pytest.fixture
def presets_dir(tmp_path: Path) -> Path:
    d = tmp_path / "presets"
    make_preset(d, "Race Night")
    make_preset(d, "Practice Day")
    (d / "not_a_preset").mkdir()  # junk dir without server_cfg.ini
    (d / "loose_file.txt").write_text("junk", encoding="utf-8")
    return d
