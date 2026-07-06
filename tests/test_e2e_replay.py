"""End-to-end without the game: fabricated ACSP packets go through a real UDP
socket into the listener, laps land in the leaderboard DB, results JSON
backfills — the same wiring tools/replay_udp.py exercises against a live bot.
"""

import asyncio
import json
import socket
from pathlib import Path

import pytest

from acbot.ac import udp
from acbot.ac.presets import read_preset
from acbot.ac.staging import Staging
from acbot.events import EventBus
from acbot.leaderboard.db import LeaderboardDB
from acbot.leaderboard.ingest import LapIngest

GUID = "76561198000000009"


@pytest.fixture
def staging(tmp_path: Path, presets_dir: Path) -> Staging:
    s = Staging(tmp_path / "active")
    s.apply_preset(read_preset(presets_dir / "Race Night"))
    return s


async def _wait_for(predicate, timeout=5.0):
    async def poll():
        while not await predicate():
            await asyncio.sleep(0.05)
    await asyncio.wait_for(poll(), timeout)


async def test_full_session_replay(tmp_path: Path, staging: Staging):
    bus = EventBus()
    db = LeaderboardDB(tmp_path / "lb.sqlite3")
    await db.open()
    LapIngest(db, staging, bus, results_base=tmp_path)

    listener = udp.AcspListener(bus, "127.0.0.1", 0, server_port=54999,
                                entry_count_hint=lambda: 3)
    await listener.start()
    assert listener.listen_port != 0

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    target = ("127.0.0.1", listener.listen_port)

    def send(payload: bytes) -> None:
        sock.sendto(payload, target)

    session = udp.SessionInfo(
        protocol_version=4, server_name="Test", track="ks_brands_hatch",
        track_config="gp", name="Practice", session_type=1,
        ambient_temp=22, road_temp=29, weather_graphics="3_clear",
    )
    alice = udp.ConnectionEvent(driver_name="Alice", driver_guid=GUID,
                                car_id=0, car_model="ks_mazda_mx5_cup", car_skin="red")

    send(udp.build_version(4))
    send(udp.build_session_info(session, packet_id=udp.ACSP_NEW_SESSION))
    send(udp.build_connection(alice))
    send(udp.build_client_loaded(0))
    send(udp.build_lap_completed(udp.LapCompleted(car_id=0, laptime_ms=92123, cuts=0)))
    send(udp.build_lap_completed(udp.LapCompleted(car_id=0, laptime_ms=91500, cuts=0)))
    send(udp.build_lap_completed(udp.LapCompleted(car_id=0, laptime_ms=99999, cuts=4)))

    async def three_laps():
        async with db.db.execute("SELECT COUNT(*) AS n FROM laps") as cur:
            return (await cur.fetchone())["n"] == 3
    await _wait_for(three_laps)

    # Session ends -> results JSON enriches tyre + backfills a missed lap.
    results = tmp_path / "results" / "SESSION.json"
    results.parent.mkdir()
    results.write_text(json.dumps({
        "TrackName": "ks_brands_hatch", "TrackConfig": "gp", "Type": "PRACTICE",
        "Laps": [
            {"DriverName": "Alice", "DriverGuid": GUID, "CarModel": "ks_mazda_mx5_cup",
             "CarSkin": "red", "LapTime": 91500, "Cuts": 0, "Tyre": "SM"},
            {"DriverName": "Alice", "DriverGuid": GUID, "CarModel": "ks_mazda_mx5_cup",
             "CarSkin": "red", "LapTime": 93777, "Cuts": 0, "Tyre": "SM"},
        ],
    }), encoding="utf-8")
    send(udp.build_connection(alice, packet_id=udp.ACSP_CONNECTION_CLOSED))
    send(udp.build_end_session(str(results)))

    async def four_laps():
        async with db.db.execute("SELECT COUNT(*) AS n FROM laps") as cur:
            return (await cur.fetchone())["n"] == 4
    await _wait_for(four_laps)

    async with db.db.execute(
        "SELECT laptime_ms, cuts, tyre, source FROM laps ORDER BY laptime_ms"
    ) as cur:
        rows = [tuple(r) for r in await cur.fetchall()]
    assert rows == [
        (91500, 0, "SM", "udp"),        # tyre enriched by backfill
        (92123, 0, None, "udp"),
        (93777, 0, "SM", "results"),    # missed lap backfilled
        (99999, 4, None, "udp"),        # cut lap kept, never a PB
    ]
    best = await db.best_for_combo("ks_brands_hatch", "gp", "ks_mazda_mx5_cup", GUID)
    assert best == 91500

    sock.close()
    listener.close()
    await db.close()
