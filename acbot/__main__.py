"""Entry point: `python -m acbot [run|doctor] [--config config.yaml]`."""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
from pathlib import Path

from .config import Config, ConfigError, load_config, validate_for_run


def setup_logging(cfg: Config, verbose: bool = False) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root.addHandler(console)

    cfg.ensure_dirs()
    file_handler = logging.handlers.RotatingFileHandler(
        cfg.logs_dir / "acbot.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    root.addHandler(file_handler)

    audit = logging.getLogger("acbot.audit")
    audit_handler = logging.handlers.RotatingFileHandler(
        cfg.logs_dir / "audit.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    audit_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    audit.addHandler(audit_handler)

    logging.getLogger("discord").setLevel(logging.WARNING)


def cmd_run(cfg: Config) -> int:
    problems = validate_for_run(cfg)
    if problems:
        print("Cannot start — fix these first (see `acbot doctor`):", file=sys.stderr)
        for p in problems:
            print(f"  ✗ {p}", file=sys.stderr)
        return 1

    from .app import App
    from .bot import ACBot

    app = App(cfg)
    bot = ACBot(app)
    bot.run(cfg.token(), log_handler=None)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="acbot",
                                     description="Assetto Corsa Discord admin bot")
    parser.add_argument("command", nargs="?", default="run", choices=["run", "doctor"])
    parser.add_argument("--config", default="config.yaml",
                        help="path to config.yaml (default: ./config.yaml)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    try:
        cfg = load_config(Path(args.config))
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1

    setup_logging(cfg, verbose=args.verbose)

    if args.command == "doctor":
        from .doctor import run_doctor
        return run_doctor(cfg)
    return cmd_run(cfg)


if __name__ == "__main__":
    sys.exit(main())
