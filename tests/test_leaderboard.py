import json
from pathlib import Path

import pytest

from acbot.ac.presets import read_preset
from acbot.ac.staging import Staging
from acbot.ac.udp import Driver, SessionInfo
from acbot.events import EventBus
from acbot.leaderboard.db import LeaderboardDB
from acbot.leaderboard.ingest import LapIngest
from acbot.leaderboard.queries import (
    fmt_laptime,
    known_combos,
    personal_bests,
    recent_laps,
    top_for_combo,
)

GUID_A = "76561198000000001"
GUID_B = "76561198000000002"
COMBO = dict(track="ks_brands_hatch", layout="gp", car_model="ks_mazda_mx5_cup")


@pytest.fixture
async def db(tmp_path: Path):
    d = LeaderboardDB(tmp_path / "lb.sqlite3")
    await d.open()
    yield d
    await d.close()


async def test_pb_detection_and_skin_agnostic_identity(db: LeaderboardDB):
    _, pb1 = await db.record_lap(**COMBO, skin="red", driver_guid=GUID_A,
                                 driver_name="Alice", laptime_ms=93000, cuts=0)
    assert pb1 is True
    _, pb2 = await db.record_lap(**COMBO, skin="blue", driver_guid=GUID_A,
                                 driver_name="Alice", laptime_ms=94000, cuts=0)
    assert pb2 is False  # slower
    _, pb3 = await db.record_lap(**COMBO, skin="blue", driver_guid=GUID_A,
                                 driver_name="Alice", laptime_ms=92000, cuts=0)
    assert pb3 is True  # faster, different skin — same driver record
    _, pb4 = await db.record_lap(**COMBO, skin="red", driver_guid=GUID_A,
                                 driver_name="Alice", laptime_ms=80000, cuts=3)
    assert pb4 is False  # cut lap can never be a PB

    rows = await top_for_combo(db, COMBO["track"], COMBO["layout"], COMBO["car_model"])
    assert len(rows) == 1  # one driver despite two skins
    assert rows[0].laptime_ms == 92000
    assert rows[0].driver_name == "Alice"
    assert rows[0].lap_count == 3  # clean laps only


async def test_top_ordering_multiple_drivers(db: LeaderboardDB):
    await db.record_lap(**COMBO, skin="", driver_guid=GUID_A, driver_name="Alice",
                        laptime_ms=92000, cuts=0)
    await db.record_lap(**COMBO, skin="", driver_guid=GUID_B, driver_name="Bob",
                        laptime_ms=91000, cuts=0)
    rows = await top_for_combo(db, COMBO["track"], COMBO["layout"], COMBO["car_model"])
    assert [r.driver_name for r in rows] == ["Bob", "Alice"]
    assert [r.rank for r in rows] == [1, 2]


async def test_link_discord_and_personal(db: LeaderboardDB):
    await db.record_lap(**COMBO, skin="", driver_guid=GUID_A, driver_name="Alice",
                        laptime_ms=92000, cuts=0)
    await db.link_discord(GUID_A, 555)
    assert await db.guid_for_discord(555) == GUID_A
    # Relinking to another guid releases the old one.
    await db.link_discord(GUID_B, 555)
    assert await db.guid_for_discord(555) == GUID_B
    async_rows = await personal_bests(db, GUID_A)
    assert len(async_rows) == 1
    assert async_rows[0].laptime_ms == 92000


async def test_queries_misc(db: LeaderboardDB):
    await db.record_lap(**COMBO, skin="", driver_guid=GUID_A, driver_name="Alice",
                        laptime_ms=92000, cuts=0)
    await db.record_lap(track="spa", layout="", car_model="abarth500", skin="",
                        driver_guid=GUID_A, driver_name="Alice",
                        laptime_ms=150000, cuts=1)
    recents = await recent_laps(db)
    assert len(recents) == 2
    combos = await known_combos(db)  # clean laps only
    assert combos == [("ks_brands_hatch", "gp", "ks_mazda_mx5_cup")]
    assert fmt_laptime(92123) == "1:32.123"
    assert fmt_laptime(None) == "—"


@pytest.fixture
def staging(tmp_path: Path, presets_dir: Path) -> Staging:
    s = Staging(tmp_path / "active")
    s.apply_preset(read_preset(presets_dir / "Race Night"))
    return s


def _driver(guid=GUID_A, name="Alice", model="ks_mazda_mx5_cup", skin="red",
            car_id=0) -> Driver:
    return Driver(car_id=car_id, name=name, guid=guid, model=model, skin=skin)


def _session() -> SessionInfo:
    return SessionInfo(track="ks_brands_hatch", track_config="gp", session_type=1,
                       ambient_temp=24, road_temp=31, weather_graphics="3_clear")


async def test_ingest_lap_records_policy(db: LeaderboardDB, staging: Staging):
    bus = EventBus()
    LapIngest(db, staging, bus)
    await bus.emit("lap_completed", driver=_driver(), laptime_ms=93500, cuts=0,
                   grip=0.97, session=_session())
    async with db.db.execute("SELECT * FROM laps") as cur:
        rows = await cur.fetchall()
    assert len(rows) == 1
    lap = rows[0]
    assert lap["track"] == "ks_brands_hatch"
    assert lap["session_type"] == "practice"
    policy = json.loads(lap["policy_json"])
    assert policy["damage_multiplier"] == "50"
    assert policy["ambient_temp"] == 24
    assert policy["grip"] == pytest.approx(0.97)
    assert lap["tc"] is None  # client setup is unobtainable -> stays NULL


async def test_results_backfill_dedupes_and_enriches_tyre(
        db: LeaderboardDB, staging: Staging, tmp_path: Path):
    bus = EventBus()
    ingest = LapIngest(db, staging, bus, results_base=tmp_path)
    # A UDP lap already recorded for Alice.
    await bus.emit("lap_completed", driver=_driver(), laptime_ms=93500, cuts=0,
                   grip=0.97, session=_session())
    report = {
        "TrackName": "ks_brands_hatch",
        "TrackConfig": "gp",
        "Type": "PRACTICE",
        "Laps": [
            {   # duplicate of Alice's UDP lap, but with tyre info
                "DriverName": "Alice", "DriverGuid": GUID_A,
                "CarModel": "ks_mazda_mx5_cup", "CarSkin": "red",
                "LapTime": 93500, "Cuts": 0, "Tyre": "SM",
            },
            {   # missed lap by Bob (bot was down)
                "DriverName": "Bob", "DriverGuid": GUID_B,
                "CarModel": "ks_mazda_mx5_cup", "CarSkin": "blue",
                "LapTime": 95111, "Cuts": 1, "Tyre": "SM",
            },
            {"DriverName": "junk", "DriverGuid": "", "CarModel": "x", "LapTime": 0},
        ],
    }
    results = tmp_path / "results" / "2026_7_5_PRACTICE.json"
    results.parent.mkdir()
    results.write_text(json.dumps(report), encoding="utf-8")

    await bus.emit("end_session", report_file="results/2026_7_5_PRACTICE.json")

    async with db.db.execute("SELECT * FROM laps ORDER BY id") as cur:
        rows = await cur.fetchall()
    assert len(rows) == 2  # Alice deduped, Bob inserted, junk skipped
    assert rows[0]["tyre"] == "SM"        # enriched from results
    assert rows[0]["source"] == "udp"
    assert rows[1]["driver_guid"] == GUID_B
    assert rows[1]["source"] == "results"

    # Running the same backfill again inserts nothing new.
    inserted = await ingest.backfill_results(results)
    assert inserted == 0
