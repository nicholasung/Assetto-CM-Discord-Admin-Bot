from pathlib import Path

import pytest

from acbot.ac.ini import IniFile
from acbot.ac.presets import find_preset, list_presets, read_preset
from acbot.ac.staging import Staging, StagingError, sun_angle_to_time, time_to_sun_angle


def test_list_presets_skips_junk(presets_dir: Path):
    names = [p.name for p in list_presets(presets_dir)]
    assert names == ["Practice Day", "Race Night"]


def test_read_preset_summary(presets_dir: Path):
    p = read_preset(presets_dir / "Race Night")
    assert p.track == "ks_brands_hatch"
    assert p.layout == "gp"
    assert p.max_clients == 4
    assert p.cars == ["ks_mazda_mx5_cup", "abarth500"]


def test_find_preset_case_insensitive(presets_dir: Path):
    assert find_preset(presets_dir, "race night").name == "Race Night"
    assert find_preset(presets_dir, "nope") is None


@pytest.fixture
def staging(tmp_path: Path, presets_dir: Path) -> Staging:
    s = Staging(tmp_path / "active")
    s.apply_preset(read_preset(presets_dir / "Race Night"))
    return s


def test_apply_preset_copies_and_tracks_name(staging: Staging, presets_dir: Path):
    assert staging.is_ready()
    assert staging.preset_name() == "Race Night"
    assert staging.server_name() == "Test Server"
    assert staging.track() == ("ks_brands_hatch", "gp")
    assert staging.http_port() == 8081


def test_entries_parsed_in_slot_order(staging: Staging):
    entries = staging.entries()
    assert [e.slot for e in entries] == [0, 1, 2]
    assert entries[0].model == "ks_mazda_mx5_cup"
    assert entries[2].model == "abarth500"


def test_set_entry_car_only_touches_target(staging: Staging, presets_dir: Path):
    desc = staging.set_entry_car(1, "abarth500", "stripes")
    assert "slot 1" in desc and "abarth500" in desc
    entries = staging.entries()
    assert entries[1].model == "abarth500"
    assert entries[1].skin == "stripes"
    assert entries[0].model == "ks_mazda_mx5_cup"  # neighbors untouched
    # The CM preset itself must stay pristine.
    original = IniFile.load(presets_dir / "Race Night" / "entry_list.ini")
    assert original.get("CAR_1", "MODEL") == "ks_mazda_mx5_cup"


def test_set_entry_car_adds_to_allowed_cars(staging: Staging):
    # A fresh model must land in server_cfg CARS or the server calls it "illegal".
    assert "lotus_elise_sc" not in staging.allowed_cars()
    staging.set_entry_car(1, "lotus_elise_sc", "")
    # CARS mirrors the entry list, in slot order, with no duplicates.
    assert staging.allowed_cars() == ["ks_mazda_mx5_cup", "lotus_elise_sc", "abarth500"]


def test_set_entry_car_prunes_orphaned_allowed_cars(staging: Staging):
    # abarth500 is only used by CAR_2; swapping it away must drop it from CARS,
    # else CM greys it out with no entry count.
    assert "abarth500" in staging.allowed_cars()
    staging.set_entry_car(2, "ks_mazda_mx5_cup", "red")
    assert staging.allowed_cars() == ["ks_mazda_mx5_cup"]


def test_set_entry_car_existing_model_not_duplicated(staging: Staging):
    staging.set_entry_car(1, "abarth500", "stripes")  # CAR_2 already uses abarth500
    assert staging.allowed_cars() == ["ks_mazda_mx5_cup", "abarth500"]


def test_set_entry_skin_and_missing_slot(staging: Staging):
    staging.set_entry_skin(0, "blue")
    assert staging.entry(0).skin == "blue"
    with pytest.raises(StagingError):
        staging.set_entry_skin(9, "red")


def test_damage(staging: Staging):
    assert staging.get_damage() == 50
    desc = staging.set_damage(0)
    assert "50% → 0%" in desc
    assert staging.get_damage() == 0
    with pytest.raises(StagingError):
        staging.set_damage(150)


def test_time_conversion_round_trip():
    for h, m in [(8, 0), (9, 30), (13, 0), (16, 45), (18, 0)]:
        assert sun_angle_to_time(time_to_sun_angle(h, m)) == (h, m)


def test_set_time_and_range(staging: Staging):
    staging.set_time(9, 30)
    assert staging.get_time() == (9, 30)
    cfg = staging.server_cfg()
    assert cfg.get_float("LIGHTING", "SUN_ANGLE") == pytest.approx(-56.0)
    with pytest.raises(StagingError):
        staging.set_time(22, 0)  # night needs CSP/AssettoServer


def test_policy_snapshot(staging: Staging):
    snap = staging.policy_snapshot()
    assert snap["abs_allowed"] == "1"
    assert snap["damage_multiplier"] == "50"
    assert snap["tyre_wear_rate"] == "100"
    assert snap["dynamic_track"]["SESSION_START"] == "96"


def test_unstaged_raises(tmp_path: Path):
    s = Staging(tmp_path / "empty")
    assert not s.is_ready()
    with pytest.raises(StagingError):
        s.entries()
