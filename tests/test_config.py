from pathlib import Path

import pytest

from acbot.config import ConfigError, load_config

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_example_config_loads():
    cfg = load_config(REPO_ROOT / "config.example.yaml")
    assert cfg.server.backend == "vanilla"
    assert cfg.server.udp_listen_host == "127.0.0.1"
    assert cfg.server.udp_listen_port == 12000
    assert cfg.server.autostart is False
    assert cfg.paths.cm_presets_dir == "auto"
    assert cfg.discord.audit_channel_id is None
    assert cfg.assettoserver.collisions_yaml_key is None
    # data dir resolves relative to the config file location
    assert cfg.data_dir == REPO_ROOT / "data"


def test_missing_file_message(tmp_path: Path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_bad_backend_rejected(tmp_path: Path):
    f = tmp_path / "config.yaml"
    f.write_text("server:\n  backend: quake\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="backend"):
        load_config(f)


def test_bad_udp_listen_rejected(tmp_path: Path):
    f = tmp_path / "config.yaml"
    f.write_text("server:\n  udp_plugin_listen: nonsense\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="host:port"):
        load_config(f)
