"""Deterministic source checks, immutable evidence and zero vault writes."""

import hashlib
import io
import json
from datetime import datetime, timezone
from http.client import IncompleteRead
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest
import yaml

from scripts import source_watcher
from scripts.source_registry import load_registry, page_freshness, registry_path, state_dir
from scripts.source_watcher import (
    changed_sections, check_sources, document_sections, normalize_document, status,
)


NOW = datetime(2026, 9, 22, 12, tzinfo=timezone.utc)
OLD = "# WebSocket\n\nConnection docs.\n\n## Reconnect\n\nUse a new snapshot.\n\n## Heartbeat\n\nSend ping every 30s.\n"
NEW = OLD.replace("every 30s", "every 20s")


class Response(io.BytesIO):
    def __init__(self, body=OLD, *, code=200, headers=None):
        super().__init__(body.encode("utf-8") if isinstance(body, str) else body)
        self.status = code
        self.headers = {"Content-Type": "text/markdown", **(headers or {})}


class Opener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, *, timeout):
        self.requests.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def cfg(tmp_path):
    vault = tmp_path / "vault"
    (vault / "System").mkdir(parents=True)
    (vault / "raw").mkdir()
    (vault / "raw/reference.md").write_text(OLD, encoding="utf-8")
    (vault / "knowledge.md").write_text("# Knowledge\n\nReviewed content.\n", encoding="utf-8")
    config = {"vault": {"vault_path": str(vault)}, "memory": {"db_dir": str(tmp_path / "cache")}}
    registry_path(config).write_text(yaml.safe_dump({"sources": [{
        "source_id": "bybit-v5", "name": "Bybit API", "url": "https://example.org/v5",
        "provider": "Bybit", "version": "V5", "check_interval": "1d",
    }]}), encoding="utf-8")
    return config


def snapshot_files(cfg):
    return sorted((state_dir(cfg) / "snapshots").rglob("*.md"))


def vault_bytes(cfg):
    vault = Path(cfg["vault"]["vault_path"])
    return {path.relative_to(vault).as_posix(): path.read_bytes() for path in vault.rglob("*") if path.is_file()}


def test_first_fetch_is_baseline_and_never_writes_vault(cfg):
    before = vault_bytes(cfg)
    opener = Opener(Response())
    result = check_sources(cfg, now=NOW, opener=opener)
    assert result[0]["status"] == "baseline"
    assert not result[0]["changed"]
    assert "diff_path" not in result[0]
    assert len(snapshot_files(cfg)) == 1
    assert snapshot_files(cfg)[0].read_text(encoding="utf-8") == OLD
    cached = load_registry(cfg)["bybit-v5"]
    assert cached["content_hash"] == hashlib.sha256(OLD.encode()).hexdigest()
    assert cached["last_changed"] == ""
    assert cached["last_checked"] == "2026-09-22T12:00:00Z"
    assert vault_bytes(cfg) == before
    assert not (state_dir(cfg) / "changes").exists()
    assert opener.requests[0][1] == 20


def test_same_normalized_hash_produces_no_change_or_new_artifact(cfg):
    check_sources(cfg, now=NOW, opener=Opener(Response()))
    files_before = snapshot_files(cfg)
    result = check_sources(cfg, now="2026-09-23T12:00:00Z", opener=Opener(Response(OLD.replace("\n", "\r\n"))))
    assert result[0]["status"] == "unchanged"
    assert not result[0]["changed"]
    assert snapshot_files(cfg) == files_before
    assert not (state_dir(cfg) / "changes").exists()


@pytest.mark.parametrize("validators", [
    {"ETag": '"abc"'}, {"Last-Modified": "Mon, 21 Sep 2026 10:00:00 GMT"},
    {"ETag": '"abc"', "Last-Modified": "Mon, 21 Sep 2026 10:00:00 GMT"},
])
def test_conditional_headers_and_http_304_avoid_body_work(cfg, validators):
    opener = Opener(
        Response(headers=validators),
        HTTPError("https://example.org/v5", 304, "Not modified", {}, io.BytesIO(b"")),
    )
    check_sources(cfg, now=NOW, opener=opener)
    result = check_sources(cfg, now="2026-09-23T12:00:00Z", opener=opener)
    assert result[0]["status"] == "unchanged"
    request = opener.requests[1][0]
    if "ETag" in validators:
        assert request.get_header("If-none-match") == validators["ETag"]
    if "Last-Modified" in validators:
        assert request.get_header("If-modified-since") == validators["Last-Modified"]
    assert len(snapshot_files(cfg)) == 1
    assert not (state_dir(cfg) / "changes").exists()


def test_304_without_hash_is_error_and_does_not_invent_baseline(cfg):
    result = check_sources(cfg, now=NOW, opener=Opener(Response(code=304)))
    assert result[0]["status"] == "error"
    assert "without a known source hash" in result[0]["error"]
    assert snapshot_files(cfg) == []


def test_changed_hash_preserves_old_snapshot_and_only_changed_sections(cfg):
    before_vault = vault_bytes(cfg)
    check_sources(cfg, now=NOW, opener=Opener(Response()))
    old_snapshot = snapshot_files(cfg)[0]
    result = check_sources(cfg, now="2026-09-23T12:00:00Z", opener=Opener(Response(NEW)))[0]
    assert result["status"] == "changed" and result["changed"]
    assert result["changed_section_count"] == 1
    assert result["previous_available"]
    change = json.loads(Path(result["changed_sections_path"]).read_text(encoding="utf-8"))
    assert change["sections"][0]["heading_path"] == ["WebSocket", "Heartbeat"]
    assert "Reconnect" not in json.dumps(change["sections"])
    assert "every 30s" in change["sections"][0]["old"]
    assert "every 20s" in change["sections"][0]["new"]
    assert "-Send ping every 30s." in Path(result["diff_path"]).read_text(encoding="utf-8")
    assert old_snapshot.read_text(encoding="utf-8") == OLD
    assert len(snapshot_files(cfg)) == 2
    assert vault_bytes(cfg) == before_vault
    registry = load_registry(cfg)
    assert registry["bybit-v5"]["last_changed"] == "2026-09-23T12:00:00Z"
    assert page_freshness(["bybit-v5"], "2026-09-22", registry, now="2026-09-23T13:00:00Z") == "stale"


def test_repeated_transition_reuses_deterministic_immutable_diff(cfg):
    check_sources(cfg, now=NOW, opener=Opener(Response()))
    first = check_sources(cfg, force=True, now="2026-09-22T13:00:00Z", opener=Opener(Response(NEW)))[0]
    first_bytes = Path(first["changed_sections_path"]).read_bytes()
    check_sources(cfg, force=True, now="2026-09-22T14:00:00Z", opener=Opener(Response(OLD)))
    repeated = check_sources(cfg, force=True, now="2026-09-22T15:00:00Z", opener=Opener(Response(NEW)))[0]
    assert first["changed_sections_path"] == repeated["changed_sections_path"]
    assert Path(repeated["changed_sections_path"]).read_bytes() == first_bytes
    assert len(snapshot_files(cfg)) == 2


def test_check_interval_and_force(cfg):
    opener = Opener(Response(), Response())
    check_sources(cfg, now=NOW, opener=opener)
    assert check_sources(cfg, now="2026-09-22T13:00:00Z", opener=opener)[0]["status"] == "skipped"
    assert len(opener.requests) == 1
    assert check_sources(cfg, force=True, now="2026-09-22T13:00:00Z", opener=opener)[0]["status"] == "unchanged"
    assert len(opener.requests) == 2


def test_disabled_sources_never_create_state_or_network(cfg):
    cfg["sources"] = {"enabled": False}
    opener = Opener()
    assert check_sources(cfg, force=True, opener=opener) == []
    assert not state_dir(cfg).exists()
    assert opener.requests == []


def test_unknown_source_does_not_create_state(cfg):
    with pytest.raises(ValueError, match="Unknown source_id"):
        check_sources(cfg, "missing", opener=Opener())
    assert not state_dir(cfg).exists()


def test_error_retains_known_hash_and_snapshot_and_is_stale(cfg):
    opener = Opener(Response(headers={"ETag": '"prior"'}), URLError("temporary failure"))
    check_sources(cfg, now=NOW, opener=opener)
    prior = load_registry(cfg)["bybit-v5"]
    result = check_sources(cfg, now="2026-09-23T12:00:00Z", opener=opener)[0]
    after = load_registry(cfg)["bybit-v5"]
    assert result["status"] == "error"
    assert after["content_hash"] == prior["content_hash"]
    assert after["snapshot_path"] == prior["snapshot_path"]
    assert after["etag"] == prior["etag"]
    assert page_freshness(["bybit-v5"], "2026-09-22", {"bybit-v5": after}, now="2026-09-23T13:00:00Z") == "stale"


def test_unchanged_check_does_not_reverify_page_after_changed_source(cfg):
    check_sources(cfg, now=NOW, opener=Opener(Response()))
    check_sources(cfg, now="2026-09-23T12:00:00Z", opener=Opener(Response(NEW)))
    check_sources(cfg, now="2026-09-24T12:00:00Z", opener=Opener(Response(NEW)))
    registry = load_registry(cfg)
    assert registry["bybit-v5"]["status"] == "fresh"
    assert registry["bybit-v5"]["last_changed"] == "2026-09-23T12:00:00Z"
    assert page_freshness(["bybit-v5"], "2026-09-22", registry, now="2026-09-24T13:00:00Z") == "stale"


def test_failed_source_does_not_stop_other_checks(cfg):
    registry = yaml.safe_load(registry_path(cfg).read_text(encoding="utf-8"))
    registry["sources"].append({"source_id": "second", "url": "https://example.org/second"})
    registry_path(cfg).write_text(yaml.safe_dump(registry), encoding="utf-8")
    results = check_sources(cfg, now=NOW, opener=Opener(URLError("offline"), Response()))
    assert [result["status"] for result in results] == ["error", "baseline"]
    assert len(load_registry(cfg)) == 2


def test_incomplete_http_response_is_reported_per_source(cfg):
    result = check_sources(cfg, now=NOW, opener=Opener(IncompleteRead(b"partial", 40)))[0]
    assert result["status"] == "error"
    assert "IncompleteRead" in result["error"]


@pytest.mark.parametrize("headers", [{"Content-Length": "101"}, {}])
def test_http_download_size_is_bounded(cfg, headers):
    cfg["sources"] = {"max_response_bytes": 100}
    result = check_sources(cfg, now=NOW, opener=Opener(Response("x" * 101, headers=headers)))[0]
    assert result["status"] == "error"
    assert "max_response_bytes=100" in result["error"]
    assert snapshot_files(cfg) == []


def test_http_timeout_is_forwarded_and_reported_per_source(cfg):
    cfg["sources"] = {"timeout_seconds": 0.25}
    opener = Opener(TimeoutError("timed out"))
    assert check_sources(cfg, now=NOW, opener=opener)[0]["status"] == "error"
    assert opener.requests[0][1] == 0.25


@pytest.mark.parametrize("headers,body", [
    ({"Content-Type": "application/pdf"}, b"%PDF-1.0"),
    ({"Content-Type": "text/plain; charset=unknown-charset"}, b"data"),
    ({"Content-Encoding": "gzip"}, b"data"),
    ({"Content-Type": "text/plain"}, b"a\x00b"),
])
def test_unsupported_response_is_explicit_error(cfg, headers, body):
    result = check_sources(cfg, now=NOW, opener=Opener(Response(body, headers=headers)))[0]
    assert result["status"] == "error"
    assert snapshot_files(cfg) == []


def test_status_is_read_only_and_never_networks(cfg):
    before = vault_bytes(cfg)
    info = status(cfg, now=NOW)
    assert info["sources"][0]["status"] == "unknown"
    assert info["sources"][0]["due"]
    assert not state_dir(cfg).exists()
    assert vault_bytes(cfg) == before


def test_single_writer_lock_preserves_other_writer_lock(cfg):
    directory = state_dir(cfg)
    directory.mkdir(parents=True)
    lock = directory / ".watcher.lock"
    lock.write_text("99999", encoding="utf-8")
    opener = Opener()
    with pytest.raises(RuntimeError, match="already locked"):
        check_sources(cfg, now=NOW, opener=opener)
    assert lock.read_text(encoding="utf-8") == "99999"
    assert opener.requests == []


def test_inside_vault_cache_is_rejected_before_network(cfg):
    cfg["sources"] = {"state_dir": str(Path(cfg["vault"]["vault_path"]) / "System/checks")}
    opener = Opener()
    with pytest.raises(ValueError, match="outside vault_path"):
        check_sources(cfg, now=NOW, opener=opener)
    assert opener.requests == []


def test_changed_source_identity_starts_new_baseline_without_validators(cfg):
    check_sources(cfg, now=NOW, opener=Opener(Response(headers={"ETag": '"old"'})))
    registry = yaml.safe_load(registry_path(cfg).read_text(encoding="utf-8"))
    registry["sources"][0]["version"] = "V6"
    registry["sources"][0]["url"] = "https://example.org/v6"
    registry_path(cfg).write_text(yaml.safe_dump(registry), encoding="utf-8")
    opener = Opener(Response(NEW))
    result = check_sources(cfg, now=NOW, opener=opener)[0]
    assert result["status"] == "baseline"
    assert opener.requests[0][0].get_header("If-none-match") is None
    assert len(snapshot_files(cfg)) == 2


def test_missing_old_snapshot_is_explicit_in_change_artifact(cfg):
    registry = yaml.safe_load(registry_path(cfg).read_text(encoding="utf-8"))
    registry["sources"][0]["content_hash"] = hashlib.sha256(OLD.encode()).hexdigest()
    registry_path(cfg).write_text(yaml.safe_dump(registry), encoding="utf-8")
    result = check_sources(cfg, now=NOW, opener=Opener(Response(NEW)))[0]
    assert result["changed"]
    assert result["previous_available"] is False
    payload = json.loads(Path(result["changed_sections_path"]).read_text(encoding="utf-8"))
    assert payload["previous_available"] is False


def test_corrupted_snapshot_is_reported_and_state_preserved(cfg):
    check_sources(cfg, now=NOW, opener=Opener(Response()))
    prior = load_registry(cfg)["bybit-v5"]
    snapshot_files(cfg)[0].write_text("corrupt snapshot", encoding="utf-8")
    result = check_sources(cfg, force=True, now=NOW, opener=Opener(Response(NEW)))[0]
    assert result["status"] == "error"
    assert "hash does not match" in result["error"]
    assert load_registry(cfg)["bybit-v5"]["content_hash"] == prior["content_hash"]


def test_markdown_section_hierarchy_preserves_fenced_code():
    text = "# Root\n\n## API\n\n### Connect\n\n```python\n# not a heading\nx = 1\n```\n\n### Retry\n\nAgain.\n"
    sections = document_sections(text)
    assert [section["heading_path"] for section in sections] == [
        ["Root"], ["Root", "API"], ["Root", "API", "Connect"], ["Root", "API", "Retry"],
    ]
    assert "# not a heading" in sections[2]["content"]
    assert "```python" in sections[2]["content"]


def test_setext_and_duplicate_heading_sections():
    sections = document_sections("Root\n====\n\n## Retry\n\nFirst.\n\n## Retry\n\nSecond.\n")
    assert sections[0]["heading_path"] == ["Root"]
    assert [s["occurrence"] for s in sections[1:]] == [1, 2]
    changes = changed_sections("# A\n\nold\n\n# B\n\nkeep\n", "# B\n\nkeep\n\n# C\n\nnew\n")
    assert [(c["heading_path"], c["change"]) for c in changes] == [(["C"], "added"), (["A"], "removed")]


@pytest.mark.parametrize("heading,title", [("## C#", "C#"), ("## C# ###", "C#"), ("## Retry ##", "Retry")])
def test_atx_only_strips_whitespace_preceded_closing_hashes(heading, title):
    sections = document_sections(f"# API\n\n{heading}\n\nContent.\n")
    assert sections[-1]["heading_path"] == ["API", title]


@pytest.mark.parametrize("line", ["    code", "\tcode", "  \tcode", "- item", "+ item", "* item", "1. item", "2) item"])
def test_setext_does_not_turn_indented_code_or_lists_into_headings(line):
    sections = document_sections(f"# Root\n\n{line}\n---\n\nBody.\n")
    assert [section["heading_path"] for section in sections] == [["Root"]]
    assert line in sections[0]["content"]


def test_html_normalization_keeps_headings_code_and_omits_scripts_navigation():
    html = b"<html><head><style>garbage</style></head><body><nav>menu</nav><h1>API</h1><h2>Retry</h2><p>Use a <code>snapshot</code>.</p><pre><code># not heading\n  x = 1</code></pre><script>bad()</script><footer>footer</footer></body></html>"
    text = normalize_document(html, "text/html; charset=utf-8")
    assert "# API" in text and "## Retry" in text
    assert "`snapshot`" in text and "  x = 1" in text
    assert "garbage" not in text and "menu" not in text and "bad()" not in text
    assert len(document_sections(text)) == 2


def test_cli_json_status_and_failure_exit(cfg, monkeypatch, capsys):
    monkeypatch.setattr(source_watcher, "load_config", lambda **kwargs: cfg)
    assert source_watcher.main(["status", "--json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["sources"][0]["source_id"] == "bybit-v5"
    monkeypatch.setattr(source_watcher, "check_sources", lambda *args, **kwargs: [{"source_id": "bybit-v5", "status": "error", "changed": False, "error": "offline"}])
    assert source_watcher.main(["check", "bybit-v5", "--force", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["sources"][0]["error"] == "offline"
