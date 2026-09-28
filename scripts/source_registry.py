"""Read-only source registry and deterministic provenance/freshness helpers.

The user owns ``<vault>/System/sources.yaml`` (or [sources].registry_path).
Watcher state is derived data outside the vault. Reading this module never
performs network requests and never writes files.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import yaml


STATE_SCHEMA_VERSION = 1
_SOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_INTERVAL = re.compile(r"(\d+(?:\.\d+)?)\s*([smhdw])?\Z", re.IGNORECASE)
_STATUSES = {"unknown", "fresh", "stale", "changed", "error"}
_STATE_FIELDS = {
    "last_checked", "last_changed", "content_hash", "etag", "last_modified",
    "status", "error", "snapshot_path", "last_diff_path", "last_change_path",
}


def parse_timestamp(value: object) -> datetime | None:
    """Read YAML dates / ISO dates or timestamps as UTC; absent values are None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"Invalid ISO source timestamp: {value!r}") from exc
    else:
        raise ValueError(f"Invalid source timestamp type: {type(value).__name__}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def utc_now(now: object = None) -> datetime:
    current = parse_timestamp(now) if now is not None else datetime.now(timezone.utc)
    if current is None:
        raise ValueError("now must be an ISO date or timestamp")
    return current


def timestamp_text(value: object) -> str:
    parsed = parse_timestamp(value)
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z") if parsed else ""


def parse_interval(value: object) -> timedelta:
    """Accept positive seconds or values such as 30m, 12h, 1d and 1w."""
    if isinstance(value, bool):
        raise ValueError("check_interval must be positive seconds or a duration such as 1d")
    match = _INTERVAL.fullmatch(str(value).strip())
    if not match:
        raise ValueError(f"Invalid check_interval: {value!r}; expected e.g. 1d")
    seconds = float(match.group(1)) * {
        None: 1, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800,
    }[match.group(2).lower() if match.group(2) else None]
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("check_interval must be positive and finite")
    try:
        duration = timedelta(seconds=seconds)
    except OverflowError as exc:
        raise ValueError("check_interval is too large") from exc
    if not duration:
        raise ValueError("check_interval is too small")
    return duration


def sources_config(config: dict) -> dict:
    raw = config.get("sources", {}) or {}
    if not isinstance(raw, dict):
        raise ValueError("[sources] must be a configuration section")
    try:
        timeout = float(raw.get("timeout_seconds", 20))
        max_bytes = int(raw.get("max_response_bytes", 5 * 1024 * 1024))
    except (TypeError, ValueError) as exc:
        raise ValueError("Source timeout_seconds and max_response_bytes must be numbers") from exc
    if not math.isfinite(timeout) or timeout <= 0 or max_bytes <= 0:
        raise ValueError("Source timeout_seconds and max_response_bytes must be positive")
    interval = raw.get("check_interval", "1d")
    parse_interval(interval)
    return {
        "enabled": bool(raw.get("enabled", True)),
        "registry_path": str(raw.get("registry_path", "") or ""),
        "state_dir": str(raw.get("state_dir", "") or ""),
        "check_interval": interval,
        "timeout_seconds": timeout,
        "max_response_bytes": max_bytes,
    }


def _vault_path(config: dict) -> Path:
    value = config.get("vault", {}).get("vault_path", "")
    if not value or value == "/path/to/your/obsidian/vault":
        raise ValueError("vault_path is not configured in config.toml")
    return Path(value).expanduser().resolve()


def registry_path(config: dict) -> Path:
    """Relative registry paths are relative to the vault, never the process cwd."""
    vault = _vault_path(config)
    configured = sources_config(config)["registry_path"]
    path = Path(configured).expanduser() if configured else Path("System/sources.yaml")
    return (path if path.is_absolute() else vault / path).resolve()


def state_dir(config: dict) -> Path:
    """Resolve the watcher cache, rejecting locations within the vault."""
    vault = _vault_path(config)
    configured = sources_config(config)["state_dir"]
    if configured:
        directory = Path(configured).expanduser().resolve()
    else:
        # Lazy import: memory_index can import this module for freshness without
        # introducing an import cycle. Reuse its exact per-vault cache identity.
        try:
            from scripts.memory_index import db_path_for_vault
        except ModuleNotFoundError:  # direct script invocation
            from memory_index import db_path_for_vault
        db_dir = str((config.get("memory", {}) or {}).get("db_dir", "") or "")
        db_path = db_path_for_vault(vault, db_dir)
        directory = (db_path.parent / f"{db_path.stem}-sources").resolve()
    if directory == vault or vault in directory.parents:
        raise ValueError("Source watcher state_dir must be outside vault_path")
    if directory in vault.parents:
        raise ValueError("Source watcher state_dir must be separate from vault_path, not its ancestor")
    return directory


def validate_url(value: object) -> str:
    if not isinstance(value, str) or any(ord(char) < 33 for char in value):
        raise ValueError("Source url must be a nonempty HTTP(S) URL without whitespace")
    try:
        parsed = urlsplit(value)
        parsed.port  # validate a malformed or out-of-range port
    except ValueError as exc:
        raise ValueError("Source url is not a valid HTTP(S) URL") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Source url must use http:// or https:// with a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Source url must not contain credentials")
    return value


def source_identity(record: dict) -> dict:
    """A changed URL/provider/version starts a new source baseline."""
    return {key: record.get(key, "") for key in ("url", "provider", "version")}


def _record(raw: object, default_interval: object) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Every registry source must be a mapping")
    source_id = raw.get("source_id")
    if not isinstance(source_id, str) or not _SOURCE_ID.fullmatch(source_id):
        raise ValueError("source_id must contain 1-128 ASCII letters, digits, '.', '_' or '-'")
    record = {
        "source_id": source_id,
        "url": validate_url(raw.get("url")),
        "check_interval": raw.get("check_interval", default_interval),
    }
    parse_interval(record["check_interval"])
    for field, default in (
        ("name", source_id), ("type", "official_docs"), ("provider", ""),
        ("version", "unknown"), ("content_hash", ""), ("etag", ""),
        ("last_modified", ""), ("status", "unknown"),
    ):
        value = raw.get(field, default)
        if value is None or value == "":
            value = default
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            raise ValueError(f"Source {source_id!r}: {field} must be a scalar string")
        record[field] = str(value)
    if record["status"] not in _STATUSES:
        raise ValueError(f"Source {source_id!r}: invalid status {record['status']!r}")
    for field in ("last_checked", "last_changed"):
        record[field] = timestamp_text(raw.get(field))
    return record


def load_state(config: dict) -> dict[str, dict]:
    """Read atomically published cache state; never silently erase corrupt state."""
    path = state_dir(config) / "state.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read source watcher state: {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != STATE_SCHEMA_VERSION:
        raise ValueError(f"Unsupported source watcher state schema: {path}")
    if data.get("vault_path") != str(_vault_path(config)):
        raise ValueError("Source watcher state_dir belongs to a different vault")
    records = data.get("sources")
    if not isinstance(records, dict) or not all(
        isinstance(key, str) and isinstance(value, dict) for key, value in records.items()
    ):
        raise ValueError(f"Invalid source watcher state records: {path}")
    for key, record in records.items():
        for field in _STATE_FIELDS | {"url", "provider", "version"}:
            if field in record and not isinstance(record[field], str):
                raise ValueError(f"Invalid source watcher state field: {key}.{field}")
        for field in ("last_checked", "last_changed"):
            parse_timestamp(record.get(field))
        if record.get("status", "unknown") not in _STATUSES:
            raise ValueError(f"Invalid source watcher state status: {key}")
    return records


def load_registry(config: dict, *, include_state: bool = True) -> dict[str, dict]:
    path = registry_path(config)
    if not path.exists():
        return {}
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"Cannot read source registry: {path}: {exc}") from exc
    if document is None:
        return {}
    if not isinstance(document, dict) or not isinstance(document.get("sources", []), list):
        raise ValueError("Source registry must be a mapping containing a 'sources' list")
    interval = sources_config(config)["check_interval"]
    records = {}
    for raw in document.get("sources", []):
        record = _record(raw, interval)
        source_id = record["source_id"]
        if source_id in records:
            raise ValueError(f"Duplicate source_id in registry: {source_id}")
        records[source_id] = record
    if include_state and records:
        for source_id, cached in load_state(config).items():
            current = records.get(source_id)
            if current and source_identity(current) == source_identity(cached):
                current.update({key: cached[key] for key in _STATE_FIELDS if key in cached})
    return records


def is_due(record: dict, *, now: object = None) -> bool:
    checked = parse_timestamp(record.get("last_checked"))
    current = utc_now(now)
    return checked is None or checked > current or current - checked >= parse_interval(
        record.get("check_interval", "1d")
    )


def page_freshness(
    source_ids: object, last_verified: object, registry: dict[str, dict], *,
    declared: str = "unknown", now: object = None,
) -> str:
    """Conservative freshness; checking sources never verifies a Wiki page.

    Explicitly stale pages remain ``stale`` even without verification metadata.
    Other legacy pages without verification or known registry IDs stay ``unknown``.
    Date-only verification means midnight UTC, conservatively treating a later
    source change on that same date as stale. Errors / overdue checks are stale.
    """
    if declared == "stale":
        return "stale"
    try:
        verified = parse_timestamp(last_verified)
    except ValueError:
        return "unknown"
    current = utc_now(now)
    if verified is None or verified > current:
        return "unknown"
    if isinstance(source_ids, str):
        source_ids = [source_ids]
    if not isinstance(source_ids, (list, tuple, set)) or not source_ids:
        return "unknown"
    unknown = False
    for source_id in source_ids:
        record = registry.get(str(source_id))
        if not record:
            unknown = True
            continue
        try:
            changed = parse_timestamp(record.get("last_changed"))
            checked = parse_timestamp(record.get("last_checked"))
            overdue = is_due(record, now=current) if checked else False
        except ValueError:
            unknown = True
            continue
        if (changed and changed > verified) or record.get("status") in {"error", "stale"}:
            return "stale"
        if checked and checked <= current and overdue:
            return "stale"
        if (
            not checked or checked > current or not record.get("content_hash")
            or record.get("status") not in {"fresh", "changed"}
        ):
            unknown = True
    return "unknown" if unknown else "fresh"
