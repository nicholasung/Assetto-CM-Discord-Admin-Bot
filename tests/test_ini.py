from pathlib import Path

from acbot.ac.ini import IniFile

SAMPLE = """\
; CM generated
[SERVER]
NAME=My Server ;inline comment
TRACK=ks_brands_hatch
MAX_CLIENTS=12

[LIGHTING]
SUN_ANGLE=-16
"""


def test_get_and_inline_comment_stripping():
    ini = IniFile.loads(SAMPLE)
    assert ini.get("SERVER", "NAME") == "My Server"
    assert ini.get("server", "track") == "ks_brands_hatch"  # case-insensitive
    assert ini.get_int("SERVER", "MAX_CLIENTS") == 12
    assert ini.get_float("LIGHTING", "SUN_ANGLE") == -16.0
    assert ini.get("SERVER", "MISSING", "fallback") == "fallback"


def test_set_existing_preserves_everything_else():
    ini = IniFile.loads(SAMPLE)
    ini.set("SERVER", "TRACK", "spa")
    out = ini.dumps()
    assert "TRACK=spa" in out
    assert "; CM generated" in out          # comment survives
    assert "NAME=My Server ;inline comment" in out  # untouched line survives
    assert out.index("[SERVER]") < out.index("TRACK=spa") < out.index("[LIGHTING]")


def test_set_new_key_lands_in_right_section():
    ini = IniFile.loads(SAMPLE)
    ini.set("SERVER", "UDP_PLUGIN_LOCAL_PORT", 11000)
    out = ini.dumps()
    assert out.index("[SERVER]") < out.index("UDP_PLUGIN_LOCAL_PORT=11000") < out.index("[LIGHTING]")
    assert ini.get_int("SERVER", "UDP_PLUGIN_LOCAL_PORT") == 11000


def test_set_new_section_appended():
    ini = IniFile.loads(SAMPLE)
    ini.set("BOOK", "NAME", "x")
    assert ini.get("BOOK", "NAME") == "x"
    assert ini.dumps().rstrip().endswith("NAME=x")


def test_bom_and_crlf_round_trip(tmp_path: Path):
    raw = b"\xef\xbb\xbf[SERVER]\r\nNAME=A\r\n"
    f = tmp_path / "cfg.ini"
    f.write_bytes(raw)
    ini = IniFile.load(f)
    assert ini.get("SERVER", "NAME") == "A"
    ini.set("SERVER", "NAME", "B")
    ini.save(f)
    out = f.read_bytes()
    assert out.startswith(b"\xef\xbb\xbf")  # BOM preserved
    assert b"\r\nNAME=B" in out             # CRLF preserved


def test_items_and_sections():
    ini = IniFile.loads(SAMPLE)
    assert ini.sections() == ["SERVER", "LIGHTING"]
    items = ini.items("SERVER")
    assert items["NAME"] == "My Server"
    assert set(items) == {"NAME", "TRACK", "MAX_CLIENTS"}
