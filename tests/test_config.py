from pathlib import Path

import pytest

from analyzer.config import PROJECT_ROOT, Config, ConfigError


def test_defaults_are_local_only_and_relative_to_project():
    config = Config()
    assert config.host == "127.0.0.1"
    assert config.data_dir == PROJECT_ROOT / "data"
    assert config.sessions_dir == PROJECT_ROOT / "data" / "sessions"
    assert config.uploads_dir == PROJECT_ROOT / "data" / "uploads"
    assert config.topology_path.name == "services.yaml"


def test_defaults_derive_from_project_root_not_hardcoded_paths():
    config = Config()
    for value in (config.data_dir, config.bundles_dir, config.topology_path, config.rules_dir):
        assert PROJECT_ROOT in value.parents
    # The legacy parser hardcoded an absolute user path; the source must not.
    source = (PROJECT_ROOT / "analyzer" / "config.py").read_text(encoding="utf-8")
    assert "/Users/" not in source
    assert "/home/" not in source


def test_from_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("ANALYZER_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("ANALYZER_BUNDLES_DIR", str(tmp_path / "b"))
    monkeypatch.setenv("ANALYZER_PORT", "9090")
    monkeypatch.setenv("ANALYZER_HOST", "0.0.0.0")
    monkeypatch.setenv("ANALYZER_MAX_UPLOAD_BYTES", "1024")
    config = Config.from_env()
    assert config.data_dir == tmp_path / "d"
    assert config.bundles_dir == tmp_path / "b"
    assert config.port == 9090
    assert config.host == "0.0.0.0"
    assert config.max_upload_bytes == 1024


def test_from_env_without_overrides_matches_defaults(monkeypatch):
    for name in list(__import__("os").environ):
        if name.startswith("ANALYZER_"):
            monkeypatch.delenv(name)
    assert Config.from_env() == Config()


@pytest.mark.parametrize("value", ["abc", "0", "70000", "-1"])
def test_invalid_port_rejected(monkeypatch, value):
    monkeypatch.setenv("ANALYZER_PORT", value)
    with pytest.raises(ConfigError):
        Config.from_env()


def test_invalid_max_upload_rejected(monkeypatch):
    monkeypatch.setenv("ANALYZER_MAX_UPLOAD_BYTES", "0")
    with pytest.raises(ConfigError):
        Config.from_env()
