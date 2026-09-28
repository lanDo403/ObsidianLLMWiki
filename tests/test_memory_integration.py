import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from scripts import vault_writer, wiki_ingest
from scripts.memory_index import read_block, search, update_index
from scripts.migrate import ensure_memory_section
from scripts.config import tomllib
from scripts.wiki_compile import (
    ValidationError, _append_log_to_staging, _split_frontmatter, assemble_prompt,
    carry_provenance, materialize_to_staging, select_raw_inputs, snapshot_wiki_space,
    validate_changeset,
)
from scripts.wiki_models import ChangeSet, LogEntry, WikiPage, WikiPageUpdate
from tests.test_wiki_compile import _good_fm, _seed_space


def test_migration_preserves_values_comments_crlf_and_is_idempotent(tmp_path):
    path = tmp_path / "config.toml"
    original = b'# user comment\r\n[vault]\r\nvault_path = "X:/vault"\r\n[memory]\r\nenabled = false # choice\r\nauto_update = false\r\n[memory.semantic]\r\nenabled = false\r\n'
    path.write_bytes(original)
    assert ensure_memory_section(path) == "added"
    first = path.read_bytes()
    config = tomllib.loads(first.decode())
    assert config["memory"]["enabled"] is False and config["memory"]["auto_update"] is False
    assert config["memory"]["search_limit"] == 8
    assert config["memory"]["semantic"]["provider"] == "none"
    assert b'enabled = false # choice\r\n' in first
    assert first.count(b'\n') == first.count(b'\r\n')
    assert ensure_memory_section(path) == "present" and path.read_bytes() == first


@pytest.mark.parametrize("text", ['memory = {enabled = false}\n', '[memory]\nranking = {prefer_wiki = false}\n', '["memory"]\n'])
def test_migration_inline_and_quoted_tables_preserved(tmp_path, text):
    path = tmp_path / "config.toml"
    path.write_text(text)
    ensure_memory_section(path)
    assert all(line in path.read_text().splitlines() for line in text.splitlines())
    tomllib.loads(path.read_text())
    first = path.read_bytes()
    ensure_memory_section(path)
    assert path.read_bytes() == first


def test_migration_publish_failure_preserves_original(tmp_path, monkeypatch):
    from scripts import migrate
    path = tmp_path / "config.toml"
    original = b'[memory]\nenabled = false\n'
    path.write_bytes(original)
    def fail(*args):
        raise OSError("replace failed")
    monkeypatch.setattr(migrate.os, "replace", fail)
    with pytest.raises(OSError, match="replace failed"):
        ensure_memory_section(path)
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_documentation_ingestion_no_llm_raw_immutable_and_searchable(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    root = vault / "LLM Wiki" / "demo"
    _seed_space(root)
    cfg = {"vault": {"vault_path": str(vault)}, "memory": {"db_dir": str(tmp_path / "cache")}}
    update_index(cfg, quiet=True)
    source = tmp_path / "docs.md"
    source.write_text("---\naliases: [book depth]\n---\n# API\n## Reconnect\nGet snapshot.\n## Authentication\nSign.\n", encoding="utf-8")
    monkeypatch.setattr(wiki_ingest, "_load_config", lambda **kw: cfg)
    monkeypatch.setattr(wiki_ingest.subprocess, "run", lambda *a, **kw: pytest.fail("documentation started LLM compile"))
    argv = ["wiki_ingest.py", "demo", str(source), "--mode", "documentation", "--source-id", "demo-api", "--version", "V5"]
    monkeypatch.setattr(sys, "argv", argv)
    assert wiki_ingest.main() == 0
    files = {p: p.read_bytes() for p in root.rglob("*.md")}
    assert wiki_ingest.main() == 0
    assert all(p.read_bytes() == data for p, data in files.items())
    assert not search(cfg, "book depth")
    hits = search(cfg, "book depth reconnect", scope="raw")
    assert hits and hits[0]["version"] == "V5"
    block = read_block(cfg, hits[0]["block_key"])
    assert "snapshot" in block["content"] and "Sign." not in block["content"]
    snap = snapshot_wiki_space(root)
    assert len(select_raw_inputs(snap, since_last_compile=True, raw_only_glob=None)) == 1  # old knowledge input only
    assert len(select_raw_inputs(snap, since_last_compile=False, raw_only_glob="raw/docs/*docs*.md")) == 2


def test_writer_refuses_raw_overwrite_even_with_overwrite_policy(tmp_path, monkeypatch):
    vault, staging = tmp_path / "vault", tmp_path / "staging"
    staging.mkdir()
    fm = _good_fm("demo", "raw", "input")
    content = "---\n" + yaml.safe_dump(fm) + "---\n# Input\noriginal"
    source = staging / "input.md"
    source.write_text(content, encoding="utf-8")
    cfg = {"vault": {"vault_path": str(vault)}, "memory": {"enabled": False}}
    monkeypatch.setattr(vault_writer, "load_config", lambda: cfg)
    monkeypatch.setattr(vault_writer, "REGISTRY_PATH", tmp_path / "processed.json")
    monkeypatch.setattr(sys, "argv", ["vault_writer.py", "--staging", str(staging), "--on-conflict", "overwrite", "--non-interactive"])
    vault_writer.main()
    dest = vault / "LLM Wiki/demo/raw/docs/input.md"
    before = dest.read_bytes()
    source.write_text(content + "changed", encoding="utf-8")
    with pytest.raises(SystemExit):
        vault_writer.main()
    assert dest.read_bytes() == before


def test_compile_provenance_and_no_raw_duplication(tmp_path):
    root = tmp_path / "demo"
    _seed_space(root)
    snap = snapshot_wiki_space(root)
    snap["pages"]["raw/docs/notes.md"]["frontmatter"]["sources"] = ["demo-api"]
    snap["pages"]["raw/docs/notes.md"]["body"] = "CHANGED-EVIDENCE-ONLY"
    snap["pages"]["raw/docs/other.md"] = {"frontmatter": {}, "body": "UNRELATED-RAW-SECRET"}
    batch = select_raw_inputs(snap, since_last_compile=False, raw_only_glob="raw/docs/notes.md")
    prompt = assemble_prompt(snapshot=snap, raw_batch=batch, mode="project", update_only=True)
    assert prompt.count("CHANGED-EVIDENCE-ONLY") == 1
    assert "UNRELATED-RAW-SECRET" not in prompt
    fm = _good_fm("demo", "core", "architecture")
    snap["pages"]["pages/architecture.md"]["frontmatter"].update(version="4", last_verified="2026-01-01", aliases=["arch"])
    upd = WikiPageUpdate("pages/architecture.md", [], fm, "# Architecture\n[[postgres]] [[redis]] updated", ["raw/docs/notes.md"])
    cs = ChangeSet("demo", "test", updates=[upd])
    carry_provenance(cs, snap)
    assert upd.frontmatter["version"] == "4" and upd.frontmatter["aliases"] == ["arch"]
    assert upd.frontmatter["sources"] == ["raw/docs/notes.md", "demo-api"]
    assert "last_verified" not in upd.frontmatter
    validate_changeset(cs, snap)
    materialize_to_staging(cs, tmp_path / "staging")
    fm, _ = _split_frontmatter((tmp_path / "staging/pages/architecture.md").read_text(encoding="utf-8"))
    assert fm["sources"] == ["raw/docs/notes.md", "demo-api"]


def test_consumed_raw_skipped_without_mutating_it(tmp_path):
    root = tmp_path / "demo"
    _seed_space(root)
    snap = snapshot_wiki_space(root)
    raw = root / "raw/docs/notes.md"
    before = raw.read_bytes()
    cs = ChangeSet("demo", "test", log_entry=LogEntry("done", ["raw/docs/notes.md"]))
    staging = tmp_path / "staging"
    staging.mkdir()
    _append_log_to_staging(staging, "demo", snap, cs, "test", 0)
    fm, body = _split_frontmatter((staging / "log.md").read_text(encoding="utf-8"))
    snap["pages"]["log.md"] = {"frontmatter": fm, "body": body}
    assert select_raw_inputs(snap, since_last_compile=True, raw_only_glob=None) == []
    assert select_raw_inputs(snap, since_last_compile=False, raw_only_glob="raw/docs/notes.md")
    assert raw.read_bytes() == before


@pytest.mark.parametrize("path,kind", [("raw/docs/new.md", "raw"), ("../escaped.md", "concept"), ("SCHEMA.md", "meta")])
def test_compile_rejects_immutable_and_unsafe_outputs(tmp_path, path, kind):
    root = tmp_path / "demo"
    _seed_space(root)
    cs = ChangeSet("demo", "test", creates=[WikiPage(path, _good_fm("demo", kind, "new"), "# New")])
    with pytest.raises(ValidationError, match="WIKI_WRITE_FORBIDDEN"):
        validate_changeset(cs, snapshot_wiki_space(root))


def test_freshness_overlay_changes_without_markdown_reindex(tmp_path):
    vault = tmp_path / "vault"
    root = vault / "LLM Wiki/demo/concepts"
    root.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    verified = now - timedelta(hours=2)
    note = root / "api.md"
    note.write_text("---\nsources: [raw/docs/input.md, demo-api]\nlast_verified: " + verified.isoformat() + "\n---\n# API\nSnapshot.", encoding="utf-8")
    system = vault / "System"
    system.mkdir()
    registry = system / "sources.yaml"
    record = {"source_id": "demo-api", "url": "https://example.invalid/api", "last_checked": now.isoformat(),
              "last_changed": (verified - timedelta(days=1)).isoformat(), "content_hash": "known", "status": "fresh"}
    registry.write_text(yaml.safe_dump({"sources": [record]}), encoding="utf-8")
    cfg = {"vault": {"vault_path": str(vault)}, "memory": {"db_dir": str(tmp_path / "cache")}}
    update_index(cfg, quiet=True)
    hit = search(cfg, "snapshot")[0]
    assert hit["freshness"] == "fresh" and hit["source_ids"] == ["demo-api"]
    record["last_changed"] = now.isoformat()
    registry.write_text(yaml.safe_dump({"sources": [record]}), encoding="utf-8")
    assert search(cfg, "snapshot")[0]["freshness"] == "stale"


def test_update_cannot_route_around_link_guard(tmp_path):
    root = tmp_path / "demo"
    _seed_space(root)
    page = WikiPageUpdate("pages/architecture.md", [], _good_fm("demo", "entity", "architecture"),
                          "# Architecture\n[[postgres]] [[redis]]")
    with pytest.raises(ValidationError, match="routing points"):
        validate_changeset(ChangeSet("demo", "test", updates=[page]), snapshot_wiki_space(root))
