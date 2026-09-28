"""Optional LOCAL provider extension. No backend or model is installed by default.

A provider receives a read-only SQLite connection and returns ordered block keys.
It must perform local inference only, never network requests or model downloads.
Register an explicitly installed implementation in LOCAL_PROVIDERS; scope and
version are rechecked against the authoritative index before exposing its results.
"""
from __future__ import annotations

import sqlite3
from typing import Protocol


class LocalSemanticProvider(Protocol):
    def search(self, query: str, conn: sqlite3.Connection, *, limit: int) -> list[str]: ...


LOCAL_PROVIDERS: dict[str, LocalSemanticProvider] = {}


def semantic_fallback(config: dict, query: str, *, scope: str, limit: int, folder: str,
                      tag: str, version: str, prefix: bool = False, raw: bool = False,
                      debug: bool = False) -> list[dict]:
    try:
        from scripts.memory_index import _read_db, _METADATA, _metadata, _refresh_freshness, memory_config, version_matches
    except ModuleNotFoundError:
        from memory_index import _read_db, _METADATA, _metadata, _refresh_freshness, memory_config, version_matches
    semantic = memory_config(config)["semantic"]
    name = semantic.get("provider", "none")
    if not semantic.get("enabled") or name == "none" or raw or prefix:
        return []
    if name not in LOCAL_PROVIDERS:
        raise ValueError(f"local semantic provider {name!r} is not installed; use provider='none'")
    conn = _read_db(config)
    try:
        keys = list(dict.fromkeys(LOCAL_PROVIDERS[name].search(query, conn, limit=limit)))[:limit]
        if not keys:
            return []
        rows = conn.execute(f"SELECT {_METADATA},substr(b.body,1,240) AS snippet,b.tags FROM blocks b "
                            f"WHERE b.block_key IN ({','.join('?' for _ in keys)})", keys)
        indexed = {r["block_key"]: _metadata(r) for r in rows}
    finally:
        conn.close()
    allowed = {"wiki": {"canonical_wiki"}, "notes": {"atomic_notes"}, "raw": {"raw_sources"},
               "auto": {"canonical_wiki", "atomic_notes"},
               "all": {"canonical_wiki", "atomic_notes", "raw_sources"}}[scope]
    folder = folder.replace("\\", "/").rstrip("/")
    result = []
    for key in keys:
        row = indexed.get(key)
        if not row or row["tier"] not in allowed:
            continue
        if folder and row["folder"] != folder and not row["folder"].startswith(folder + "/"):
            continue
        if version and not version_matches(row["version"], version):
            continue
        if tag and tag not in row["tags"].split():
            continue
        row.pop("tags")
        row.update(score=0.0, retrieval="semantic", snippet=" ".join(row["snippet"].split()))
        result.append(row)
    if scope == "auto":
        wiki = [r for r in result if r["tier"] == "canonical_wiki"]
        result = wiki or (result if memory_config(config)["fallback_to_notes"] else [])
    _refresh_freshness(config, result)
    return result
