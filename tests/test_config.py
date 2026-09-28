"""Tests for scripts/config.py — shared configuration module.

These must pass on a fresh clone / CI where config.toml (git-ignored, local
machine state) does not exist. Schema is validated against the always-present
committed config.example.toml, never against a live config.toml.
"""

from unittest.mock import patch

import pytest

from scripts.config import (
    DEFAULT_STAGING_DIR,
    PROJECT_ROOT,
    REGISTRY_PATH,
    load_config,
    tomllib,
)

EXAMPLE_CONFIG = PROJECT_ROOT / "config.example.toml"

requires_toml = pytest.mark.skipif(
    tomllib is None, reason="no TOML parser (tomllib/tomli) available"
)


def _load_example() -> dict:
    with open(EXAMPLE_CONFIG, "rb") as f:
        return tomllib.load(f)


def test_project_root_exists():
    assert PROJECT_ROOT.exists()
    # config.example.toml is committed and always present; config.toml is not.
    assert EXAMPLE_CONFIG.exists()


def test_registry_path_under_project():
    assert REGISTRY_PATH.parent == PROJECT_ROOT
    assert REGISTRY_PATH.name == "processed.json"


def test_default_staging_dir():
    assert DEFAULT_STAGING_DIR == "/tmp/obsidian-llmwiki/staging"


@requires_toml
def test_example_config_parses():
    assert isinstance(_load_example(), dict)


@requires_toml
def test_example_config_has_vault_section():
    cfg = _load_example()
    assert "vault" in cfg
    assert "vault_path" in cfg["vault"]


@requires_toml
def test_example_config_has_rclone_section():
    cfg = _load_example()
    assert "rclone" in cfg
    assert "staging_dir" in cfg["rclone"]


def test_load_config_soft_fallback(tmp_path):
    """When config.toml is missing and strict=False, returns defaults."""
    with patch("scripts.config.PROJECT_ROOT", tmp_path):
        cfg = load_config(strict=False)
        assert cfg["rclone"]["staging_dir"] == DEFAULT_STAGING_DIR


def test_load_config_strict_exits(tmp_path):
    """When config.toml is missing and strict=True, exits."""
    with patch("scripts.config.PROJECT_ROOT", tmp_path):
        with pytest.raises(SystemExit):
            load_config(strict=True)
