"""YAML config loading + validation. Token comes from ACBOT_DISCORD_TOKEN."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

TOKEN_ENV = "ACBOT_DISCORD_TOKEN"

VANILLA = "vanilla"
ASSETTOSERVER = "assettoserver"
BACKENDS = (VANILLA, ASSETTOSERVER)


class ConfigError(Exception):
    pass


@dataclass
class DiscordConfig:
    guild_id: int = 0
    admin_role_ids: list[int] = field(default_factory=list)
    status_channel_id: int | None = None
    audit_channel_id: int | None = None
    upload_channel_id: int | None = None


@dataclass
class PathsConfig:
    ac_root: Path | None = None
    server_dir: Path | None = None
    assettoserver_dir: Path | None = None
    cm_presets_dir: str = "auto"  # "auto" or an explicit path
    data_dir: Path = Path("data")


@dataclass
class ServerConfig:
    backend: str = VANILLA
    public_ip: str = "auto"
    restart_cooldown_s: int = 60
    status_poll_s: int = 30
    udp_plugin_server_port: int = 11000
    udp_plugin_listen: str = "127.0.0.1:12000"
    autostart: bool = False

    @property
    def udp_listen_host(self) -> str:
        return self.udp_plugin_listen.rsplit(":", 1)[0]

    @property
    def udp_listen_port(self) -> int:
        return int(self.udp_plugin_listen.rsplit(":", 1)[1])


@dataclass
class AssettoServerConfig:
    collisions_yaml_key: str | None = None
    settime_console_template: str | None = None


@dataclass
class Config:
    discord: DiscordConfig = field(default_factory=DiscordConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    assettoserver: AssettoServerConfig = field(default_factory=AssettoServerConfig)
    base_dir: Path = Path(".")

    @property
    def data_dir(self) -> Path:
        d = self.paths.data_dir
        return d if d.is_absolute() else self.base_dir / d

    @property
    def staging_dir(self) -> Path:
        return self.data_dir / "active"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "leaderboard.sqlite3"

    @property
    def state_path(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def downloads_cache_dir(self) -> Path:
        return self.data_dir / "downloads_cache"

    @property
    def pending_upload_dir(self) -> Path:
        return self.data_dir / "pending_upload"

    def token(self) -> str:
        tok = os.environ.get(TOKEN_ENV, "").strip()
        if not tok:
            raise ConfigError(f"Discord token missing: set the {TOKEN_ENV} environment variable")
        return tok

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.staging_dir, self.logs_dir,
                  self.downloads_cache_dir, self.pending_upload_dir):
            d.mkdir(parents=True, exist_ok=True)


def _opt_int(value) -> int | None:
    if value in (None, "", "null"):
        return None
    return int(value)


def _opt_path(value) -> Path | None:
    if value in (None, "", "null"):
        return None
    return Path(str(value))


def load_config(path: Path | str) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(
            f"Config file not found: {path}\n"
            f"Copy config.example.yaml to {path.name} and edit it."
        )
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"Could not parse {path}: {e}") from e

    d = raw.get("discord") or {}
    p = raw.get("paths") or {}
    s = raw.get("server") or {}
    a = raw.get("assettoserver") or {}

    cfg = Config(
        discord=DiscordConfig(
            guild_id=int(d.get("guild_id") or 0),
            admin_role_ids=[int(r) for r in (d.get("admin_role_ids") or [])],
            status_channel_id=_opt_int(d.get("status_channel_id")),
            audit_channel_id=_opt_int(d.get("audit_channel_id")),
            upload_channel_id=_opt_int(d.get("upload_channel_id")),
        ),
        paths=PathsConfig(
            ac_root=_opt_path(p.get("ac_root")),
            server_dir=_opt_path(p.get("server_dir")),
            assettoserver_dir=_opt_path(p.get("assettoserver_dir")),
            cm_presets_dir=str(p.get("cm_presets_dir") or "auto"),
            data_dir=Path(str(p.get("data_dir") or "data")),
        ),
        server=ServerConfig(
            backend=str(s.get("backend") or VANILLA).lower(),
            public_ip=str(s.get("public_ip") or "auto"),
            restart_cooldown_s=int(s.get("restart_cooldown_s") or 60),
            status_poll_s=int(s.get("status_poll_s") or 30),
            udp_plugin_server_port=int(s.get("udp_plugin_server_port") or 11000),
            udp_plugin_listen=str(s.get("udp_plugin_listen") or "127.0.0.1:12000"),
            autostart=bool(s.get("autostart") or False),
        ),
        assettoserver=AssettoServerConfig(
            collisions_yaml_key=a.get("collisions_yaml_key") or None,
            settime_console_template=a.get("settime_console_template") or None,
        ),
        base_dir=path.resolve().parent,
    )

    if cfg.server.backend not in BACKENDS:
        raise ConfigError(f"server.backend must be one of {BACKENDS}, got {cfg.server.backend!r}")
    try:
        cfg.server.udp_listen_port  # noqa: B018 - validates the host:port format
    except (ValueError, IndexError) as e:
        raise ConfigError("server.udp_plugin_listen must look like host:port") from e
    return cfg


def validate_for_run(cfg: Config) -> list[str]:
    """Blocking problems for `acbot run` (doctor prints these as failures)."""
    problems: list[str] = []
    if not cfg.discord.guild_id:
        problems.append("discord.guild_id is not set")
    if not cfg.discord.admin_role_ids:
        problems.append("discord.admin_role_ids is empty — nobody could use admin commands")
    if cfg.server.backend == VANILLA:
        sd = cfg.paths.server_dir
        if not sd:
            problems.append("paths.server_dir is not set")
        elif not (sd / "acServer.exe").exists() and not (sd / "acServer").exists():
            problems.append(f"acServer executable not found in {sd}")
    else:
        ad = cfg.paths.assettoserver_dir
        if not ad:
            problems.append("paths.assettoserver_dir is not set (backend=assettoserver)")
        elif not (ad / "AssettoServer.exe").exists() and not (ad / "AssettoServer").exists():
            problems.append(f"AssettoServer executable not found in {ad}")
    if not os.environ.get(TOKEN_ENV):
        problems.append(f"{TOKEN_ENV} environment variable is not set")
    return problems
