"""Feeds the leaderboard from live UDP events + session results JSON.

Primary source: ACSP LAP_COMPLETED (real-time, has cuts). Backfill: the
results/*.json file the server names in END_SESSION — used to catch laps the
bot missed (e.g. it was restarted) and to enrich tyre compound when present.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..ac.staging import Staging
from ..ac.udp import Driver, SessionInfo
from ..events import EventBus
from .db import LeaderboardDB

log = logging.getLogger(__name__)


class LapIngest:
    def __init__(self, db: LeaderboardDB, staging: Staging, bus: EventBus,
                 results_base: Path | None = None):
        self.db = db
        self.staging = staging
        self.bus = bus
        self.results_base = results_base
        bus.subscribe("driver_joined", self.on_driver_joined)
        bus.subscribe("lap_completed", self.on_lap_completed)
        bus.subscribe("end_session", self.on_end_session)

    # -- helpers -------------------------------------------------------------

    def _track_layout(self, session: SessionInfo | None) -> tuple[str, str]:
        if session and session.track:
            return session.track, session.track_config
        try:
            return self.staging.track()
        except Exception:
            return "", ""

    def _policy(self, session: SessionInfo | None, grip: float | None) -> dict:
        try:
            policy = self.staging.policy_snapshot()
        except Exception:
            policy = {}
        if session:
            policy["ambient_temp"] = session.ambient_temp
            policy["road_temp"] = session.road_temp
            policy["weather"] = session.weather_graphics
        if grip is not None:
            policy["grip"] = round(grip, 4)
        return policy

    # -- event handlers --------------------------------------------------------

    async def on_driver_joined(self, driver: Driver, **_: object) -> None:
        if driver.guid:
            await self.db.upsert_driver(driver.guid, driver.name)

    async def on_lap_completed(self, driver: Driver, laptime_ms: int, cuts: int,
                               grip: float | None, session: SessionInfo | None,
                               **_: object) -> None:
        if not driver.guid or laptime_ms <= 0:
            return
        track, layout = self._track_layout(session)
        if not track:
            log.warning("lap without track context dropped (guid=%s)", driver.guid)
            return
        lap_id, is_pb = await self.db.record_lap(
            track=track, layout=layout,
            car_model=driver.model, skin=driver.skin,
            driver_guid=driver.guid, driver_name=driver.name,
            laptime_ms=laptime_ms, cuts=cuts,
            session_type=session.type_name if session else "",
            grip=grip,
            policy=self._policy(session, grip),
            source="udp",
        )
        await self.bus.emit(
            "lap_recorded",
            lap_id=lap_id, is_pb=is_pb, driver=driver,
            laptime_ms=laptime_ms, cuts=cuts, track=track, layout=layout,
        )

    async def on_end_session(self, report_file: str, **_: object) -> None:
        path = self._resolve_report(report_file)
        if path is None:
            return
        try:
            await self.backfill_results(path)
        except Exception:
            log.exception("results backfill failed for %s", path)

    def _resolve_report(self, report_file: str) -> Path | None:
        if not report_file:
            return None
        p = Path(report_file)
        candidates = [p]
        if not p.is_absolute() and self.results_base:
            candidates.append(self.results_base / p)
        for c in candidates:
            if c.is_file():
                return c
        log.warning("session report not found: %s", report_file)
        return None

    # -- results JSON ----------------------------------------------------------

    async def backfill_results(self, path: Path) -> int:
        """Returns number of laps inserted from the results file."""
        data = json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
        track = str(data.get("TrackName") or "")
        layout = str(data.get("TrackConfig") or "")
        session_type = str(data.get("Type") or "").lower()
        if not track:
            track, layout = self._track_layout(None)
        laps = data.get("Laps") or []
        inserted = 0
        for lap in laps:
            guid = str(lap.get("DriverGuid") or "")
            laptime = int(lap.get("LapTime") or 0)
            model = str(lap.get("CarModel") or "")
            if not guid or laptime <= 0 or not model:
                continue
            cuts = int(lap.get("Cuts") or 0)
            tyre = str(lap.get("Tyre") or "") or None
            existing = await self.db.find_matching_lap(guid, track, layout, model, laptime)
            if existing is not None:
                if tyre:
                    await self.db.enrich_lap_tyre(existing["id"], tyre)
                continue
            await self.db.record_lap(
                track=track, layout=layout, car_model=model,
                skin=str(lap.get("CarSkin") or ""),
                driver_guid=guid, driver_name=str(lap.get("DriverName") or ""),
                laptime_ms=laptime, cuts=cuts, session_type=session_type,
                policy=self._policy(None, None), tyre=tyre, source="results",
            )
            inserted += 1
        if inserted:
            log.info("results backfill: %d laps from %s", inserted, path.name)
        return inserted
