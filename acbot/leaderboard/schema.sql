CREATE TABLE IF NOT EXISTS drivers (
    guid        TEXT PRIMARY KEY,
    last_name   TEXT NOT NULL DEFAULT '',
    discord_id  INTEGER,
    first_seen  TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen   TEXT
);

-- Leaderboard identity is (track, layout, car_model, driver_guid): the entry
-- slot and skin a driver happened to occupy never split their record.
CREATE TABLE IF NOT EXISTS laps (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL DEFAULT (datetime('now')),
    track         TEXT NOT NULL,
    layout        TEXT NOT NULL DEFAULT '',
    car_model     TEXT NOT NULL,
    skin          TEXT DEFAULT '',
    driver_guid   TEXT NOT NULL REFERENCES drivers(guid),
    laptime_ms    INTEGER NOT NULL,
    cuts          INTEGER NOT NULL DEFAULT 0,
    session_type  TEXT DEFAULT '',
    grip          REAL,
    -- What the server enforced when the lap was set (assist policy, wear
    -- rates, damage, temps). Client-side setup fields below stay NULL unless
    -- a richer source (stracker/ptracker, AssettoServer plugin) fills them.
    policy_json     TEXT,
    tyre            TEXT,
    tc              TEXT,
    abs             TEXT,
    pressures_json  TEXT,
    alignment_json  TEXT,
    source        TEXT NOT NULL DEFAULT 'udp'
);

CREATE INDEX IF NOT EXISTS idx_laps_combo
    ON laps (track, layout, car_model, driver_guid, cuts, laptime_ms);
CREATE INDEX IF NOT EXISTS idx_laps_ts ON laps (ts);
