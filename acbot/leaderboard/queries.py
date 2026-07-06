"""Read-side leaderboard queries feeding the /lb commands."""

from __future__ import annotations

from dataclasses import dataclass

from .db import LeaderboardDB


@dataclass
class BoardRow:
    rank: int
    driver_guid: str
    driver_name: str
    laptime_ms: int
    skin: str
    tyre: str | None
    set_at: str
    lap_count: int


@dataclass
class PersonalRow:
    track: str
    layout: str
    car_model: str
    laptime_ms: int
    set_at: str
    lap_count: int


def fmt_laptime(ms: int | None) -> str:
    if not ms or ms <= 0:
        return "—"
    m, rem = divmod(int(ms), 60_000)
    s, milli = divmod(rem, 1000)
    return f"{m}:{s:02d}.{milli:03d}"


async def top_for_combo(db: LeaderboardDB, track: str, layout: str, car_model: str,
                        limit: int = 10) -> list[BoardRow]:
    """Best clean lap per driver for a track/layout/car combo."""
    sql = """
        SELECT l.driver_guid,
               COALESCE(NULLIF(d.last_name, ''), l.driver_guid) AS name,
               MIN(l.laptime_ms) AS best,
               COUNT(*) AS lap_count
        FROM laps l
        LEFT JOIN drivers d ON d.guid = l.driver_guid
        WHERE l.track = ? AND l.layout = ? AND l.car_model = ? AND l.cuts = 0
        GROUP BY l.driver_guid
        ORDER BY best ASC
        LIMIT ?
    """
    rows: list[BoardRow] = []
    async with db.db.execute(sql, (track, layout, car_model, limit)) as cur:
        raw = await cur.fetchall()
    for i, r in enumerate(raw, start=1):
        # Pull skin/tyre/time of the actual best lap.
        async with db.db.execute(
            """SELECT skin, tyre, ts FROM laps
               WHERE driver_guid=? AND track=? AND layout=? AND car_model=?
                 AND laptime_ms=? AND cuts=0 LIMIT 1""",
            (r["driver_guid"], track, layout, car_model, r["best"]),
        ) as cur:
            detail = await cur.fetchone()
        rows.append(BoardRow(
            rank=i,
            driver_guid=r["driver_guid"],
            driver_name=r["name"],
            laptime_ms=r["best"],
            skin=(detail["skin"] if detail else "") or "",
            tyre=detail["tyre"] if detail else None,
            set_at=(detail["ts"] if detail else "") or "",
            lap_count=r["lap_count"],
        ))
    return rows


async def personal_bests(db: LeaderboardDB, guid: str, limit: int = 15) -> list[PersonalRow]:
    sql = """
        SELECT track, layout, car_model, MIN(laptime_ms) AS best,
               COUNT(*) AS lap_count, MAX(ts) AS last_ts
        FROM laps
        WHERE driver_guid = ? AND cuts = 0
        GROUP BY track, layout, car_model
        ORDER BY last_ts DESC
        LIMIT ?
    """
    out: list[PersonalRow] = []
    async with db.db.execute(sql, (guid, limit)) as cur:
        for r in await cur.fetchall():
            out.append(PersonalRow(
                track=r["track"], layout=r["layout"], car_model=r["car_model"],
                laptime_ms=r["best"], set_at=r["last_ts"], lap_count=r["lap_count"],
            ))
    return out


async def recent_laps(db: LeaderboardDB, limit: int = 10) -> list[dict]:
    sql = """
        SELECT l.ts, l.track, l.layout, l.car_model, l.laptime_ms, l.cuts,
               COALESCE(NULLIF(d.last_name, ''), l.driver_guid) AS name
        FROM laps l LEFT JOIN drivers d ON d.guid = l.driver_guid
        ORDER BY l.id DESC LIMIT ?
    """
    async with db.db.execute(sql, (limit,)) as cur:
        return [dict(r) for r in await cur.fetchall()]


async def known_combos(db: LeaderboardDB) -> list[tuple[str, str, str]]:
    """(track, layout, car_model) combos that have clean laps — for autocomplete."""
    sql = """
        SELECT DISTINCT track, layout, car_model FROM laps WHERE cuts = 0
        ORDER BY track, layout, car_model
    """
    async with db.db.execute(sql) as cur:
        return [(r["track"], r["layout"], r["car_model"]) for r in await cur.fetchall()]
