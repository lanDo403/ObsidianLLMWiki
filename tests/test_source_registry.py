"""Read-only registry parsing and provenance/freshness contracts."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from scripts.source_registry import (
    STATE_SCHEMA_VERSION, is_due, load_registry, page_freshness, parse_interval,
    registry_path, sources_config, state_dir,
)


NOW = datetime(2026, 9, 22, 12, tzinfo=timezone.utc)


@pytest.fixture
def cfg(tmp_path):
    vault = tmp_path / "vault"
    (vault / "System").mkdir(parents=True)
    return {"vault": {"vault_path": str(vault)}, "memory": {"db_dir": str(tmp_path / "cache")}}


def write_registry(cfg, sources):
    path = registry_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"sources": sources}), encoding="utf-8")


def write_state(cfg, records):
    directory = state_dir(cfg)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "state.json").write_text(json.dumps({
        "schema_version": STATE_SCHEMA_VERSION,
        "vault_path": str(Path(cfg["vault"]["vault_path"]).resolve()),
        "sources": records,
    }), encoding="utf-8")


def record(**overrides):
    return {
        "source_id": "bybit-v5", "url": "https://example.org/v5", "provider": "Bybit",
        "version": "V5", "last_checked": "2026-09-22T11:00:00Z", "last_changed": "2026-09-20",
        "content_hash": "abc123", "check_interval": "1d", "status": "fresh", **overrides,
    }


def test_missing_registry_never_creates_state(cfg):
    assert load_registry(cfg) == {}
    assert not registry_path(cfg).exists()
    assert not state_dir(cfg).exists()


def test_registry_defaults_and_yaml_dates_are_backward_compatible(cfg):
    path = registry_path(cfg)
    path.write_text("sources:\n  - source_id: basic\n    url: https://example.org/docs\n"
                    "    last_checked: 2026-09-22\n", encoding="utf-8")
    source = load_registry(cfg)["basic"]
    assert source["version"] == "unknown"
    assert source["status"] == "unknown"
    assert source["last_checked"] == "2026-09-22T00:00:00Z"
    assert source["last_changed"] == ""
    assert source["check_interval"] == "1d"
    assert not state_dir(cfg).exists()


@pytest.mark.parametrize("document", ["sources: {}", "- source_id: a", "sources: [null]", "hello"])
def test_registry_rejects_invalid_shape(cfg, document):
    registry_path(cfg).write_text(document, encoding="utf-8")
    with pytest.raises(ValueError):
        load_registry(cfg)


@pytest.mark.parametrize("url", [None, "file:///tmp/docs", "ftp://example.org/docs", "https://", "https://example.org/a b", "https://u:p@example.org", "http://example.org:bad"])
def test_registry_requires_valid_http_url(cfg, url):
    write_registry(cfg, [record(url=url)])
    with pytest.raises(ValueError, match="url|URL"):
        load_registry(cfg)


def test_duplicate_source_ids_are_rejected(cfg):
    write_registry(cfg, [record(), record(url="https://other.example/docs")])
    with pytest.raises(ValueError, match="Duplicate source_id"):
        load_registry(cfg)


@pytest.mark.parametrize("overrides", [
    {"source_id": "../../oops"}, {"last_checked": "tomorrow"},
    {"check_interval": "-1d"}, {"status": "perfect"}, {"version": [5]},
])
def test_registry_rejects_invalid_fields(cfg, overrides):
    write_registry(cfg, [record(**overrides)])
    with pytest.raises(ValueError):
        load_registry(cfg)


def test_explicit_registry_relative_to_vault_and_state_override(cfg, tmp_path):
    cfg["sources"] = {"registry_path": "Metadata/external.yaml", "state_dir": str(tmp_path / "checks"), "check_interval": "12h"}
    write_registry(cfg, [{"source_id": "source", "url": "https://example.org"}])
    assert registry_path(cfg) == Path(cfg["vault"]["vault_path"]) / "Metadata/external.yaml"
    assert state_dir(cfg) == tmp_path / "checks"
    assert load_registry(cfg)["source"]["check_interval"] == "12h"


def test_state_is_distinct_per_vault_and_outside_markdown(cfg, tmp_path):
    first = state_dir(cfg)
    other = {**cfg, "vault": {"vault_path": str(tmp_path / "other-vault")}}
    assert first != state_dir(other)
    assert first.parent == tmp_path / "cache"
    cfg["sources"] = {"state_dir": str(Path(cfg["vault"]["vault_path"]) / "System/cache")}
    with pytest.raises(ValueError, match="outside vault_path"):
        state_dir(cfg)


def test_state_cannot_contain_vault_as_descendant(cfg, tmp_path):
    cfg["sources"] = {"state_dir": str(tmp_path)}
    with pytest.raises(ValueError, match="not its ancestor"):
        state_dir(cfg)


def test_state_overlay_preserves_static_yaml(cfg):
    write_registry(cfg, [record(last_checked="2026-09-01")])
    original = registry_path(cfg).read_bytes()
    write_state(cfg, {"bybit-v5": record(last_checked="2026-09-22T11:00:00Z", status="changed", last_changed="2026-09-22T11:00:00Z")})
    effective = load_registry(cfg)["bybit-v5"]
    assert effective["status"] == "changed"
    assert effective["last_changed"] == "2026-09-22T11:00:00Z"
    assert load_registry(cfg, include_state=False)["bybit-v5"]["status"] == "fresh"
    assert registry_path(cfg).read_bytes() == original


@pytest.mark.parametrize("identity", [{"url": "https://example.org/v6"}, {"version": "V6"}, {"provider": "Another"}])
def test_changed_identity_does_not_reuse_old_check(cfg, identity):
    write_registry(cfg, [record(**identity)])
    write_state(cfg, {"bybit-v5": record(status="error")})
    assert load_registry(cfg)["bybit-v5"]["status"] == "fresh"


def test_corrupt_state_is_not_silently_replaced(cfg):
    write_registry(cfg, [record()])
    state_dir(cfg).mkdir(parents=True)
    (state_dir(cfg) / "state.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="Cannot read source watcher state"):
        load_registry(cfg)


def test_shared_explicit_state_cannot_cross_vaults(cfg, tmp_path):
    cfg["sources"] = {"state_dir": str(tmp_path / "shared-checks")}
    write_state(cfg, {"bybit-v5": record()})
    other = {**cfg, "vault": {"vault_path": str(tmp_path / "other")}}
    write_registry(other, [record()])
    with pytest.raises(ValueError, match="different vault"):
        load_registry(other)


@pytest.mark.parametrize("value,seconds", [("1d", 86400), ("1.5h", 5400), (60, 60), ("30m", 1800), ("1w", 604800)])
def test_interval_units(value, seconds):
    assert parse_interval(value) == timedelta(seconds=seconds)


@pytest.mark.parametrize("value", [0, -1, True, "forever", "1ms", "nan", "1e99d"])
def test_invalid_interval(value):
    with pytest.raises(ValueError):
        parse_interval(value)


def test_due_boundary_and_clock_reversal():
    assert not is_due(record(), now=NOW)
    assert is_due(record(last_checked="2026-09-21T12:00:00Z"), now=NOW)
    assert is_due(record(last_checked="2026-09-23"), now=NOW)


@pytest.mark.parametrize("verified,sources,declared", [
    (None, ["bybit-v5"], "fresh"), ("", ["bybit-v5"], "unknown"),
    ("2026-09-21", [], "fresh"), ("2026-09-21", ["missing"], "unknown"),
    ("bad-date", ["bybit-v5"], "fresh"), ("2026-09-23", ["bybit-v5"], "fresh"),
])
def test_missing_or_unverified_provenance_is_unknown(verified, sources, declared):
    assert page_freshness(sources, verified, {"bybit-v5": record()}, declared=declared, now=NOW) == "unknown"


def test_freshness_requires_both_verification_and_checked_sources():
    registry = {"bybit-v5": record()}
    assert page_freshness(["bybit-v5"], "2026-09-21", registry, now=NOW) == "fresh"
    assert page_freshness(["bybit-v5"], None, registry, declared="fresh", now=NOW) == "unknown"
    assert page_freshness(["bybit-v5"], "2026-09-21", {"bybit-v5": record(content_hash="")}, now=NOW) == "unknown"


@pytest.mark.parametrize("changes", [
    {"last_changed": "2026-09-22T09:00:00Z"}, {"last_checked": "2026-09-20"},
    {"status": "error"}, {"status": "stale"},
])
def test_changed_overdue_and_error_sources_make_verified_page_stale(changes):
    assert page_freshness(["missing", "bybit-v5"], "2026-09-21", {"bybit-v5": record(**changes)}, now=NOW) == "stale"


def test_verification_after_known_change_restores_freshness():
    source = record(status="changed", last_changed="2026-09-22T09:00:00Z")
    assert page_freshness("bybit-v5", "2026-09-22T10:00:00Z", {"bybit-v5": source}, now=NOW) == "fresh"
    assert page_freshness("bybit-v5", "2026-09-22", {"bybit-v5": source}, now=NOW) == "stale"


def test_declared_fresh_does_not_override_source_error():
    assert page_freshness(["bybit-v5"], "2026-09-21", {"bybit-v5": record(status="error")}, declared="fresh", now=NOW) == "stale"


@pytest.mark.parametrize("verified", [None, "", "invalid-date", "2026-09-23"])
def test_explicit_stale_survives_missing_or_invalid_verification(verified):
    assert page_freshness([], verified, {}, declared="stale", now=NOW) == "stale"


def test_config_bounds_are_validated():
    with pytest.raises(ValueError, match="positive"):
        sources_config({"sources": {"max_response_bytes": 0}})
    with pytest.raises(ValueError, match="positive"):
        sources_config({"sources": {"timeout_seconds": float("inf")}})
