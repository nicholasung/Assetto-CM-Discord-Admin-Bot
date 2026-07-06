"""Line-preserving INI editor for AC server config files.

Content Manager generates server_cfg.ini / entry_list.ini with its own
ordering, comments and occasionally a UTF-8 BOM. configparser would rewrite
all of that, so this module patches files in place: unrelated lines survive
byte-for-byte, only the targeted KEY=VALUE lines are replaced.

Lookups are case-insensitive; original casing is preserved on write.
"""

from __future__ import annotations

import re
from pathlib import Path

_SECTION_RE = re.compile(r"^\s*\[(?P<name>[^\]]+)\]\s*(?:[;#].*)?$")
_KEY_RE = re.compile(r"^\s*(?P<key>[^=;#\[\]][^=]*?)\s*=(?P<value>.*)$")


def _strip_inline_comment(value: str) -> str:
    # AC values never legitimately contain " ;" / "\t;" — CM writes comments
    # that way. A bare ";" inside a value (e.g. a server name) is kept.
    for marker in ("\t;", " ;", "\t#", " #"):
        idx = value.find(marker)
        if idx != -1:
            value = value[:idx]
    return value.strip()


class IniFile:
    """An INI document held as raw lines plus a (section, key) index."""

    def __init__(self, lines: list[str], newline: str = "\r\n", bom: bool = False):
        self._lines = lines
        self._newline = newline
        self._bom = bom
        self._reindex()

    # -- construction ------------------------------------------------------

    @classmethod
    def loads(cls, text: str) -> IniFile:
        bom = text.startswith("\ufeff")
        if bom:
            text = text[1:]
        newline = "\r\n" if "\r\n" in text else "\n"
        lines = [ln.rstrip("\r") for ln in text.split("\n")]
        return cls(lines, newline=newline, bom=bom)

    @classmethod
    def load(cls, path: Path | str) -> IniFile:
        raw = Path(path).read_bytes()
        bom = raw.startswith(b"\xef\xbb\xbf")
        ini = cls.loads(raw.decode("utf-8-sig", errors="replace"))
        ini._bom = ini._bom or bom
        return ini

    def _reindex(self) -> None:
        # section name (upper) -> line index of its [header]
        self._section_line: dict[str, int] = {}
        # (section upper, key upper) -> line index of KEY=VALUE
        self._key_line: dict[tuple[str, str], int] = {}
        # section name upper -> original-cased name, in file order
        self._section_names: dict[str, str] = {}
        current = ""
        for i, line in enumerate(self._lines):
            m = _SECTION_RE.match(line)
            if m:
                name = m.group("name").strip()
                current = name.upper()
                if current not in self._section_line:
                    self._section_line[current] = i
                    self._section_names[current] = name
                continue
            m = _KEY_RE.match(line)
            if m and current is not None:
                key = m.group("key").strip().upper()
                self._key_line.setdefault((current, key), i)

    # -- reads -------------------------------------------------------------

    def sections(self) -> list[str]:
        return list(self._section_names.values())

    def has_section(self, section: str) -> bool:
        return section.upper() in self._section_line

    def get(self, section: str, key: str, default: str | None = None) -> str | None:
        idx = self._key_line.get((section.upper(), key.upper()))
        if idx is None:
            return default
        m = _KEY_RE.match(self._lines[idx])
        assert m is not None
        return _strip_inline_comment(m.group("value"))

    def get_int(self, section: str, key: str, default: int | None = None) -> int | None:
        raw = self.get(section, key)
        if raw is None or raw == "":
            return default
        try:
            return int(float(raw))
        except ValueError:
            return default

    def get_float(self, section: str, key: str, default: float | None = None) -> float | None:
        raw = self.get(section, key)
        if raw is None or raw == "":
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    def items(self, section: str) -> dict[str, str]:
        out: dict[str, str] = {}
        sec = section.upper()
        for (s, _k), idx in self._key_line.items():
            if s != sec:
                continue
            m = _KEY_RE.match(self._lines[idx])
            if m:
                out[m.group("key").strip()] = _strip_inline_comment(m.group("value"))
        return out

    # -- writes ------------------------------------------------------------

    def set(self, section: str, key: str, value: str | int | float) -> None:
        value = str(value)
        sec_u, key_u = section.upper(), key.upper()
        idx = self._key_line.get((sec_u, key_u))
        if idx is not None:
            m = _KEY_RE.match(self._lines[idx])
            assert m is not None
            self._lines[idx] = f"{m.group('key').strip()}={value}"
            return
        if sec_u not in self._section_line:
            # New section at end of file.
            if self._lines and self._lines[-1].strip():
                self._lines.append("")
            self._lines.append(f"[{section}]")
            self._lines.append(f"{key}={value}")
            self._reindex()
            return
        # Existing section: insert after its last non-blank content line.
        start = self._section_line[sec_u]
        end = len(self._lines)
        for i in range(start + 1, len(self._lines)):
            if _SECTION_RE.match(self._lines[i]):
                end = i
                break
        insert_at = end
        while insert_at - 1 > start and not self._lines[insert_at - 1].strip():
            insert_at -= 1
        self._lines.insert(insert_at, f"{key}={value}")
        self._reindex()

    # -- output ------------------------------------------------------------

    def dumps(self) -> str:
        text = self._newline.join(self._lines)
        return ("\ufeff" + text) if self._bom else text

    def save(self, path: Path | str) -> None:
        Path(path).write_bytes(self.dumps().encode("utf-8"))
