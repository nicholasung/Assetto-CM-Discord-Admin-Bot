"""`acbot doctor` — validates the config against the machine, prints findings."""

from __future__ import annotations

import os
import socket

from .ac.content import ContentIndex
from .ac.presets import candidate_preset_dirs, list_presets, resolve_presets_dir
from .config import TOKEN_ENV, Config, validate_for_run

OK, WARN, FAIL = "✓", "⚠", "✗"


def _line(mark: str, text: str) -> None:
    print(f" {mark}  {text}")


def _udp_port_free(host: str, port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def run_doctor(cfg: Config) -> int:
    print(f"\nacbot doctor — backend: {cfg.server.backend}\n")
    failures = 0

    # Blocking problems for `acbot run`
    problems = validate_for_run(cfg)
    for p in problems:
        _line(FAIL, p)
        failures += 1
    if os.environ.get(TOKEN_ENV):
        _line(OK, f"{TOKEN_ENV} is set")

    # Content index
    content = ContentIndex(cfg.paths.ac_root)
    cars = content.all_cars()
    if cars:
        with_skins = sum(1 for c in cars if c.skins)
        _line(OK, f"content index: {len(cars)} cars ({with_skins} with skins) "
                  f"in {content.cars_dir}")
    else:
        _line(WARN, f"no cars found under paths.ac_root ({cfg.paths.ac_root}) — "
                    "car/skin autocomplete and validation will be empty")

    # CM presets
    presets_dir = resolve_presets_dir(cfg.paths.cm_presets_dir)
    if presets_dir:
        presets = list_presets(presets_dir)
        _line(OK, f"presets: {len(presets)} found in {presets_dir}")
        for p in presets[:10]:
            print(f"      · {p.name}  ({p.track_label}, {p.max_clients} slots)")
    else:
        _line(FAIL, "no CM presets folder found; candidates checked:")
        if cfg.paths.cm_presets_dir.lower() != "auto":
            print(f"      · {cfg.paths.cm_presets_dir} (configured)")
        for c in candidate_preset_dirs():
            print(f"      · {c}")
        print("      Set paths.cm_presets_dir to the folder that holds your "
              "CM server presets (each preset = folder with server_cfg.ini).")
        failures += 1

    # Data dir writable
    try:
        cfg.ensure_dirs()
        probe = cfg.data_dir / ".write_probe"
        probe.write_text("ok")
        probe.unlink()
        _line(OK, f"data dir writable: {cfg.data_dir}")
    except OSError as e:
        _line(FAIL, f"data dir not writable ({cfg.data_dir}): {e}")
        failures += 1

    # UDP plugin listen port
    host, port = cfg.server.udp_listen_host, cfg.server.udp_listen_port
    if _udp_port_free(host, port):
        _line(OK, f"UDP plugin listen port free: {host}:{port}")
    else:
        _line(WARN, f"UDP {host}:{port} is in use — fine if the bot is already "
                    "running, otherwise pick another port")

    # Stray servers
    try:
        import psutil

        from .ac.process import STRAY_NAMES
        strays = [p for p in psutil.process_iter(["name"])
                  if (p.info.get("name") or "").lower() in STRAY_NAMES]
        if strays:
            _line(WARN, "AC server process(es) already running: "
                        + ", ".join(f"pid {p.pid}" for p in strays))
        else:
            _line(OK, "no stray AC server processes")
    except Exception as e:
        _line(WARN, f"could not scan processes: {e}")

    # AssettoServer extras
    if cfg.server.backend == "assettoserver":
        if cfg.assettoserver.collisions_yaml_key:
            _line(OK, f"collision toggle wired to extra_cfg.yml key "
                      f"'{cfg.assettoserver.collisions_yaml_key}'")
        else:
            _line(WARN, "assettoserver.collisions_yaml_key not set — "
                        "/settings collisions will explain instead of toggling")

    print()
    if failures:
        print(f"{failures} blocking problem(s). Fix them, then run `acbot doctor` again.")
        return 1
    print("All good — start the bot with `python -m acbot run`.")
    return 0
