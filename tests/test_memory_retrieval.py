import json
import sqlite3
import sys
from pathlib import Path

import pytest

from scripts import memory_index as mi
from scripts.memory_blocks import estimate_tokens
from scripts.memory_context import build_context


def put(vault, path, content):
    dest = vault / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")
    return dest


@pytest.fixture
def cfg(tmp_path):
    vault = tmp_path / "vault"
    meta = "---\naliases: [market depth, depth stream]\ntags: [exchange, trading]\nversion: V5\n---\n"
    body = "## Reconnect\nWait for a new snapshot after reconnect before applying deltas.\n"
    put(vault, "LLM Wiki/bybit/concepts/orderbook.md", meta + "# Bybit WebSocket\n" + body + "## Heartbeat\nSend ping regularly.\n")
    put(vault, "Notes/bybit.md", "# Bybit WebSocket\n" + body)
    put(vault, "LLM Wiki/bybit/raw/docs/api.md", "# Bybit WebSocket\n" + body)
    put(vault, "System/state.md", "# Bybit WebSocket\n" + body)
    return {"vault": {"vault_path": str(vault)}, "memory": {"db_dir": str(tmp_path / "cache")}}


def test_scope_cascade_and_legacy_keys(cfg):
    mi.update_index(cfg, quiet=True)
    hits = mi.search(cfg, "bybit reconnect")
    assert len(hits) == 1 and hits[0]["tier"] == "canonical_wiki"
    assert {"path", "folder", "title", "snippet", "score"} <= hits[0].keys()
    assert {"block_key", "page", "heading", "estimated_tokens", "version", "freshness"} <= hits[0].keys()
    assert "content" not in hits[0] and "body" not in hits[0]
    assert len(hits[0]["snippet"]) <= 240
    assert hits[0]["version"] == "V5" and hits[0]["freshness"] == "unknown"
    assert mi.search(cfg, "bybit", scope="notes")[0]["tier"] == "atomic_notes"
    assert mi.search(cfg, "bybit", scope="raw")[0]["tier"] == "raw_sources"
    all_hits = mi.search(cfg, "bybit reconnect", scope="all")
    assert [h["tier"] for h in all_hits] == ["canonical_wiki", "atomic_notes", "raw_sources"]
    assert not any(h["tier"] in {"system", "raw_sources"} for h in mi.search(cfg, "bybit", raw=True))


def test_atomic_fallback_aliases_and_tags(cfg):
    vault = Path(cfg["vault"]["vault_path"])
    put(vault, "Notes/missing.md", "# Rare term\nUniqueinformation.")
    mi.update_index(cfg, quiet=True)
    assert mi.search(cfg, "Uniqueinformation")[0]["tier"] == "atomic_notes"
    assert mi.search(cfg, "market depth")
    assert mi.search(cfg, "trading")
    assert mi.search(cfg, "bybit", tag="exchange")
    assert not mi.search(cfg, "bybit", tag="nonexistent")


def test_targeted_read_and_search_never_read_markdown(cfg, monkeypatch):
    mi.update_index(cfg, quiet=True)
    original = Path.read_text
    def checked_read(path, *a, **kw):
        assert path.suffix != ".md", "retrieval opened Markdown"
        return original(path, *a, **kw)
    monkeypatch.setattr(Path, "read_text", checked_read)
    hit = mi.search(cfg, "heartbeat")[0]
    result = mi.read_block(cfg, hit["block_key"])
    assert "Send ping" in result["content"] and "snapshot" not in result["content"]
    assert "Heartbeat" in result["heading"]
    build_context(cfg, "heartbeat")


@pytest.mark.parametrize("budget", [0, 1, 10, 50, 100, 300, 1200])
def test_hard_budget_includes_entire_output(cfg, budget):
    mi.update_index(cfg, quiet=True)
    pack = build_context(cfg, "bybit reconnect", budget_tokens=budget)
    assert pack["estimated_tokens"] == estimate_tokens(pack["text"]) <= budget
    if budget == 1200:
        assert len(pack["sources"]) == 1
        assert pack["sources"][0]["tier"] == "canonical_wiki"


def test_context_dedup_and_huge_query(cfg):
    vault = Path(cfg["vault"]["vault_path"])
    put(vault, "LLM Wiki/bybit/concepts/duplicate.md", "# Another title\n## Bybit Reconnect\nWait for a new snapshot after reconnect before applying deltas.")
    mi.update_index(cfg, quiet=True)
    pack = build_context(cfg, "bybit reconnect", scope="all", budget_tokens=1200)
    assert len(pack["sources"]) == 1
    assert pack["sources"][0]["tier"] == "canonical_wiki"
    assert estimate_tokens(build_context(cfg, "bybit " * 100, budget_tokens=40)["text"]) <= 40


def test_oversized_code_not_read_or_cut(cfg, monkeypatch):
    vault = Path(cfg["vault"]["vault_path"])
    put(vault, "Notes/code.md", "# Giantcode\n\n```\n" + "token = 1\n" * 1000 + "```\n")
    mi.update_index(cfg, quiet=True)
    pack = build_context(cfg, "giantcode", budget_tokens=300)
    assert not pack["sources"] and "```" not in pack["text"]


def test_incremental_rename_delete_and_transactional_rebuild(cfg, monkeypatch):
    mi.update_index(cfg, quiet=True)
    assert mi.update_index(cfg, quiet=True)["indexed"] == 0
    vault = Path(cfg["vault"]["vault_path"])
    old = mi.search(cfg, "heartbeat")[0]
    note = vault / old["path"]
    note.rename(note.with_name("renamed.md"))
    stats = mi.update_index(cfg, quiet=True)
    assert stats["indexed"] == 1 and stats["removed"] == 1
    with pytest.raises(ValueError, match="block not found"):
        mi.read_block(cfg, old["block_key"])
    assert mi.search(cfg, "heartbeat")[0]["path"].endswith("renamed.md")
    def fail(*a, **kw):
        raise OSError("read failed")
    monkeypatch.setattr(mi, "parse_note", fail)
    with pytest.raises(OSError):
        mi.update_index(cfg, full=True, quiet=True)
    assert mi.search(cfg, "heartbeat")  # prior committed snapshot survives


def test_old_schema_rebuild_and_no_user_content_changes(cfg):
    vault = Path(cfg["vault"]["vault_path"])
    before = {p: p.read_bytes() for p in vault.rglob("*.md")}
    db = mi.db_path_for_vault(vault, cfg["memory"]["db_dir"])
    db.parent.mkdir()
    with sqlite3.connect(db) as conn:
        conn.executescript("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT); INSERT INTO meta VALUES('tokenizer','unicode61'); CREATE TABLE files(path TEXT,mtime REAL,size INTEGER); CREATE VIRTUAL TABLE notes_fts USING fts5(title);")
    assert mi.status(cfg)["needs_rebuild"]
    with pytest.raises(RuntimeError, match="schema"):
        mi.search(cfg, "bybit")
    mi.update_index(cfg, quiet=True)
    assert not mi.status(cfg)["needs_rebuild"] and mi.search(cfg, "bybit")
    assert before == {p: p.read_bytes() for p in vault.rglob("*.md")}


@pytest.mark.parametrize("actual,requested,expected", [("V5", "5", True), ("4.2.1", "4", True), ("40", "4", False), ("unknown", "4", False), ("4.20", "4.2", False)])
def test_version_contract(actual, requested, expected):
    assert mi.version_matches(actual, requested) == expected


def test_version_filter_and_cli_compatibility(cfg, monkeypatch, capsys):
    mi.update_index(cfg, quiet=True)
    assert mi.search(cfg, "bybit", version="5")
    assert not mi.search(cfg, "bybit", version="4")
    monkeypatch.setattr(mi, "load_config", lambda **kw: cfg)
    monkeypatch.setattr(sys, "argv", ["memory_index.py", "search", "bybit reconnect", "--json", "--debug"])
    assert mi.main() == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["ranking"]["bm25"] > 0
    monkeypatch.setattr(sys, "argv", ["memory_index.py", "read", rows[0]["block_key"], "--json"])
    assert mi.main() == 0
    assert "snapshot" in json.loads(capsys.readouterr().out)["content"]


def test_semantic_off_and_gated_local_fallback(cfg, monkeypatch):
    from scripts import memory_semantic as semantic
    mi.update_index(cfg, quiet=True)
    raw = mi.search(cfg, "bybit", scope="raw")[0]["block_key"]
    wiki = mi.search(cfg, "heartbeat")[0]["block_key"]
    class Provider:
        calls = 0
        def search(self, query, conn, *, limit):
            self.calls += 1
            return [raw, wiki]
    provider = Provider()
    monkeypatch.setitem(semantic.LOCAL_PROVIDERS, "test-local", provider)
    cfg["memory"]["semantic"] = {"enabled": False, "provider": "test-local"}
    assert not mi.search(cfg, "unfindable") and provider.calls == 0
    cfg["memory"]["semantic"]["enabled"] = True
    assert mi.search(cfg, "heartbeat") and provider.calls == 0
    hits = mi.search(cfg, "unfindable")
    assert provider.calls == 1 and [h["block_key"] for h in hits] == [wiki]
    assert not mi.search(cfg, "unfindable", version="4")
