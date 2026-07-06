"""SQLite persistence for drivers + laps (aiosqlite, WAL, single connection)."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

import aiosqlite


class LeaderboardDB:
    def __init__(self, path: Path):
        self.path = path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        schema = resources.files("acbot.leaderboard").joinpath("schema.sql").read_text()
        await self._db.executescript(schema)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "LeaderboardDB not opened"
        return self._db

    # -- drivers -------------------------------------------------------------

    async def upsert_driver(self, guid: str, name: str) -> None:
        await self.db.execute(
            """INSERT INTO drivers (guid, last_name, last_seen)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(guid) DO UPDATE SET
                 last_name = excluded.last_name,
                 last_seen = excluded.last_seen""",
            (guid, name),
        )
        await self.db.commit()

    async def link_discord(self, guid: str, discord_id: int) -> None:
        # A Discord account claims one GUID; release the guid from anyone else.
        await self.db.execute(
            "UPDATE drivers SET discord_id = NULL WHERE discord_id = ?", (discord_id,)
        )
        await self.db.execute(
            """INSERT INTO drivers (guid, discord_id) VALUES (?, ?)
               ON CONFLICT(guid) DO UPDATE SET discord_id = excluded.discord_id""",
            (guid, discord_id),
        )
        await self.db.commit()

    async def guid_for_discord(self, discord_id: int) -> str | None:
        async with self.db.execute(
            "SELECT guid FROM drivers WHERE discord_id = ?", (discord_id,)
        ) as cur:
            row = await cur.fetchone()
        return row["guid"] if row else None

    async def driver_name(self, guid: str) -> str:
        async with self.db.execute(
            "SELECT last_name FROM drivers WHERE guid = ?", (guid,)
        ) as cur:
            row = await cur.fetchone()
        return (row["last_name"] if row else "") or guid

    # -- laps ----------------------------------------------------------------

    async def best_for_combo(self, track: str, layout: str, car_model: str,
                             guid: str) -> int | None:
        async with self.db.execute(
            """SELECT MIN(laptime_ms) AS best FROM laps
               WHERE track=? AND layout=? AND car_model=? AND driver_guid=? AND cuts=0""",
            (track, layout, car_model, guid),
        ) as cur:
            row = await cur.fetchone()
        return row["best"] if row and row["best"] is not None else None

    async def record_lap(
        self, *, track: str, layout: str, car_model: str, skin: str,
        driver_guid: str, driver_name: str = "", laptime_ms: int, cuts: int,
        session_type: str = "", grip: float | None = None,
        policy: dict | None = None, tyre: str | None = None,
        source: str = "udp",
    ) -> tuple[int, bool]:
        """Insert a lap. Returns (lap_id, is_new_personal_best)."""
        if driver_name:
            await self.upsert_driver(driver_guid, driver_name)
        else:
            await self.db.execute(
                "INSERT OR IGNORE INTO drivers (guid) VALUES (?)", (driver_guid,)
            )
        prev_best = await self.best_for_combo(track, layout, car_model, driver_guid)
        is_pb = cuts == 0 and (prev_best is None or laptime_ms < prev_best)
        cur = await self.db.execute(
            """INSERT INTO laps (track, layout, car_model, skin, driver_guid,
                                 laptime_ms, cuts, session_type, grip, policy_json,
                                 tyre, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (track, layout, car_model, skin or "", driver_guid, laptime_ms, cuts,
             session_type, grip, json.dumps(policy) if policy else None, tyre, source),
        )
        await self.db.commit()
        return cur.lastrowid, is_pb

    async def find_matching_lap(self, driver_guid: str, track: str, layout: str,
                                car_model: str, laptime_ms: int,
                                within_hours: float = 12.0) -> aiosqlite.Row | None:
        """Dedupe helper for results-JSON backfill vs already-ingested UDP laps."""
        async with self.db.execute(
            f"""SELECT * FROM laps
                WHERE driver_guid=? AND track=? AND layout=? AND car_model=?
                  AND laptime_ms=?
                  AND ts >= datetime('now', '-{float(within_hours)} hours')
                ORDER BY id DESC LIMIT 1""",
            (driver_guid, track, layout, car_model, laptime_ms),
        ) as cur:
            return await cur.fetchone()

    async def enrich_lap_tyre(self, lap_id: int, tyre: str) -> None:
        await self.db.execute(
            "UPDATE laps SET tyre = ? WHERE id = ? AND (tyre IS NULL OR tyre = '')",
            (tyre, lap_id),
        )
        await self.db.commit()
