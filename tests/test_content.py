from pathlib import Path

from acbot.ac.content import ContentIndex


def test_index_and_skins(content_root: Path):
    idx = ContentIndex(content_root)
    cars = idx.all_cars()
    assert [c.car_id for c in cars] == ["abarth500", "ks_mazda_mx5_cup"]
    assert idx.skins_for("ks_mazda_mx5_cup") == ["blue", "red"]
    assert idx.get("KS_MAZDA_MX5_CUP").car_id == "ks_mazda_mx5_cup"  # case-insensitive


def test_display_name_from_ui_json_even_malformed(content_root: Path):
    idx = ContentIndex(content_root)
    assert idx.get("ks_mazda_mx5_cup").display_name == "Mazda MX5 Cup"
    # abarth's ui_car.json has a BOM + trailing comma; strict=False salvages it
    # or falls back to the folder name — either way it must not blow up.
    assert idx.get("abarth500").display_name in ("Abarth 500", "abarth500")


def test_search(content_root: Path):
    idx = ContentIndex(content_root)
    assert [c.car_id for c in idx.search("mazda")] == ["ks_mazda_mx5_cup"]
    assert [c.car_id for c in idx.search("500")] == ["abarth500"]
    assert len(idx.search("")) == 2


def test_missing_root_is_empty():
    idx = ContentIndex(None)
    assert idx.all_cars() == []
    assert idx.skins_for("x") == []
    idx2 = ContentIndex(Path("/nonexistent/nowhere"))
    assert idx2.all_cars() == []
