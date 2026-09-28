"""Bounded HTTP source checks without LLM calls or writes to the Obsidian vault.

    python3 scripts/source_watcher.py check [source_id] [--force] [--json]
    python3 scripts/source_watcher.py status [--json]

ETag/Last-Modified avoid downloads; normalized text hashes avoid unchanged
diffs. Content-addressed snapshots and changed-section artifacts live beside
the memory database. Nothing is automatically ingested, compiled or promoted.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from contextlib import contextmanager
from html.parser import HTMLParser
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

try:
    from scripts.config import load_config
    from scripts.source_registry import (
        STATE_SCHEMA_VERSION, _vault_path, is_due, load_registry, load_state,
        registry_path, source_identity, sources_config, state_dir,
        timestamp_text, utc_now, validate_url,
    )
except ModuleNotFoundError:
    from config import load_config
    from source_registry import (
        STATE_SCHEMA_VERSION, _vault_path, is_due, load_registry, load_state,
        registry_path, source_identity, sources_config, state_dir,
        timestamp_text, utc_now, validate_url,
    )


class _HTTPOnlyRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _HTMLText(HTMLParser):
    """Small deterministic text extractor; preserves headings, lists and code.

    This does not run JavaScript or infer a site's article/main-content area.
    Navigation, scripts, styles and footer elements are excluded explicitly.
    """

    _SKIP = {"script", "style", "noscript", "svg", "head", "nav", "footer"}
    _BLOCK = {"p", "div", "article", "section", "blockquote", "ul", "ol", "table", "tr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored: list[str] = []
        self.in_pre = False

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self.ignored.append(tag)
            return
        if self.ignored:
            return
        if re.fullmatch(r"h[1-6]", tag):
            self.parts.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "pre":
            self.parts.append("\n\n````\n")
            self.in_pre = True
        elif tag == "code" and not self.in_pre:
            self.parts.append("`")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag == "br":
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            self.parts.append(" | ")
        elif tag in self._BLOCK:
            self.parts.append("\n\n")

    def handle_endtag(self, tag):
        if self.ignored:
            if tag == self.ignored[-1]:
                self.ignored.pop()
            return
        if tag == "pre":
            self.in_pre = False
            self.parts.append("\n````\n\n")
        elif tag == "code" and not self.in_pre:
            self.parts.append("`")
        elif tag in self._BLOCK or re.fullmatch(r"h[1-6]", tag):
            self.parts.append("\n\n")

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data if self.in_pre else re.sub(r"\s+", " ", data))

    def text(self) -> str:
        text = "".join(self.parts).replace("\r\n", "\n").replace("\r", "\n")
        # Trim layout noise outside code without changing blank lines in pre.
        pieces = re.split(r"(````\n.*?\n````)", text, flags=re.DOTALL)
        return "".join(
            part if index % 2 else re.sub(r"\n{3,}", "\n\n", part)
            for index, part in enumerate(pieces)
        ).strip() + "\n"


def normalize_document(body: bytes, content_type: str = "text/plain") -> str:
    """Normalize textual HTTP responses; binary/PDF inputs are unsupported."""
    mime = content_type.split(";", 1)[0].strip().lower()
    if mime and not (mime.startswith("text/") or mime in {
        "application/json", "application/xml", "application/xhtml+xml",
    }):
        raise ValueError(f"Unsupported source content type: {mime}; use an HTML, Markdown or text URL")
    charset = re.search(r"charset\s*=\s*[\"']?([^;\s\"']+)", content_type, re.I)
    encoding = charset.group(1) if charset else "utf-8"
    try:
        text = body.decode(encoding, errors="replace")
    except LookupError as exc:
        raise ValueError(f"Unsupported source charset: {encoding}") from exc
    if "\x00" in text:
        raise ValueError("Source response contains binary data; use a textual documentation URL")
    if mime in {"text/html", "application/xhtml+xml"} or re.match(
        r"\s*<(?:!doctype\s+html|html|head|body)\b", text, re.I
    ):
        parser = _HTMLText()
        parser.feed(text)
        parser.close()
        text = parser.text()
    return text.replace("\r\n", "\n").replace("\r", "\n").strip("\n") + "\n"


def document_sections(text: str) -> list[dict]:
    """Split on ATX/setext headings, ignoring headings inside fenced code."""
    lines = text.splitlines(keepends=True)
    sections = []
    hierarchy: list[tuple[int, str]] = []
    current: list[str] = []
    heading_path: list[str] = []
    fence_char = ""
    fence_length = 0
    occurrences: dict[tuple[str, ...], int] = {}

    def finish():
        if not current or not "".join(current).strip():
            return
        key = tuple(heading_path)
        occurrence = occurrences.get(key, 0) + 1
        occurrences[key] = occurrence
        sections.append({
            "heading_path": list(heading_path), "occurrence": occurrence,
            "content": "".join(current).rstrip("\n") + "\n",
        })

    index = 0
    while index < len(lines):
        line = lines[index]
        fence = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence_char:
            if fence and fence.group(1)[0] == fence_char and len(fence.group(1)) >= fence_length and not fence.group(2).strip():
                fence_char = ""
            current.append(line)
            index += 1
            continue
        if fence:
            fence_char, fence_length = fence.group(1)[0], len(fence.group(1))
            current.append(line)
            index += 1
            continue
        heading = re.match(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*$", line.rstrip("\r\n"))
        setext = (
            re.match(r"^ {0,3}(=+|-+)\s*$", lines[index + 1])
            if index + 1 < len(lines) and line.strip()
            and not re.match(r"^(?: {4}| {0,3}\t)", line)
            and not re.match(r"^\s*(?:[-+*]|\d+[.)])[ \t]+", line)
            else None
        )
        if heading or setext:
            finish()
            current = []
            level = len(heading.group(1)) if heading else (1 if setext.group(1)[0] == "=" else 2)
            title = re.sub(r"[ \t]+#+$", "", heading.group(2)) if heading else line.strip()
            hierarchy = [part for part in hierarchy if part[0] < level] + [(level, title)]
            heading_path = [part[1] for part in hierarchy]
            current.append(line)
            if setext and not heading:
                index += 1
                current.append(lines[index])
        else:
            current.append(line)
        index += 1
    finish()
    return sections


def changed_sections(old: str, new: str) -> list[dict]:
    """Return only changed/added/removed sections, in stable document order."""
    before = {(tuple(s["heading_path"]), s["occurrence"]): s for s in document_sections(old)}
    after = {(tuple(s["heading_path"]), s["occurrence"]): s for s in document_sections(new)}
    keys = list(after) + [key for key in before if key not in after]
    changes = []
    for key in keys:
        old_text = before.get(key, {}).get("content", "")
        new_text = after.get(key, {}).get("content", "")
        if old_text != new_text:
            changes.append({
                "heading_path": list(key[0]), "occurrence": key[1],
                "change": "added" if key not in before else "removed" if key not in after else "modified",
                "old": old_text, "new": new_text,
            })
    return changes


def _safe_child(directory: Path, relative: str) -> Path:
    path = (directory / relative).resolve()
    if path == directory or directory not in path.parents:
        raise ValueError("Source cache artifact path escapes state_dir")
    return path


@contextmanager
def _state_lock(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    lock = _safe_child(directory, ".watcher.lock")
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(
            f"Source watcher is already locked: {lock}. Remove a stale lock only after confirming no watcher is running."
        ) from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        yield
    finally:
        lock.unlink(missing_ok=True)


def _write_state(config: dict, directory: Path, records: dict):
    data = json.dumps({
        "schema_version": STATE_SCHEMA_VERSION, "vault_path": str(_vault_path(config)),
        "sources": records,
    }, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path = _safe_child(directory, "state.json")
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix="state-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(data)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _immutable_text(directory: Path, relative: str, text: str) -> str:
    path = _safe_child(directory, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            raise ValueError(f"Source cache artifact has unexpected content: {path}")
        return relative
    # The state lock owns all writes here. Publish a complete file so an
    # interrupted download/check never leaves a partial immutable snapshot.
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix="artifact-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.rename(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return relative


def _fetch(record: dict, options: dict, opener=None) -> tuple[int, dict, bytes]:
    headers = {
        "User-Agent": "obsidian-llmwiki-source-watcher/1",
        "Accept": "text/markdown, text/plain, text/html, application/xhtml+xml",
        "Accept-Encoding": "identity",
    }
    if record.get("content_hash"):
        if record.get("etag"):
            headers["If-None-Match"] = record["etag"]
        if record.get("last_modified"):
            headers["If-Modified-Since"] = record["last_modified"]
    request = Request(validate_url(record["url"]), headers=headers)
    open_url = opener or build_opener(_HTTPOnlyRedirect()).open
    started = time.monotonic()
    try:
        response = open_url(request, timeout=options["timeout_seconds"])
    except HTTPError as exc:
        if exc.code != 304:
            exc.close()
            raise
        response = exc
    with response:
        status_code = response.status if hasattr(response, "status") else response.getcode()
        response_headers = {key.lower(): value for key, value in response.headers.items()}
        if status_code == 304:
            if not record.get("content_hash"):
                raise ValueError("HTTP 304 without a known source hash; a full baseline response is required")
            return status_code, response_headers, b""
        if status_code != 200:
            raise ValueError(f"Unexpected HTTP source status: {status_code}")
        if response_headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
            raise ValueError("Source server ignored Accept-Encoding: identity")
        limit = options["max_response_bytes"]
        try:
            length = int(response_headers.get("content-length", "0"))
        except ValueError:
            length = 0
        if length > limit:
            raise ValueError(f"Source response exceeds max_response_bytes={limit}")
        chunks, size = [], 0
        read = getattr(response, "read1", response.read)
        while True:
            if time.monotonic() - started > options["timeout_seconds"]:
                raise TimeoutError("Source download exceeded timeout_seconds")
            chunk = read(min(65536, limit - size + 1))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > limit:
                raise ValueError(f"Source response exceeds max_response_bytes={limit}")
        return status_code, response_headers, b"".join(chunks)


def _check_one(record: dict, options: dict, directory: Path, now, opener=None):
    code, headers, body = _fetch(record, options, opener)
    checked = timestamp_text(now)
    updated = {key: value for key, value in record.items() if key in {
        "last_changed", "snapshot_path", "last_diff_path", "last_change_path",
    }}
    updated.update(source_identity(record))
    updated.update({
        "last_checked": checked, "last_changed": record.get("last_changed", ""),
        "status": "fresh", "error": "",
        "etag": headers.get("etag", record.get("etag", "") if code == 304 else ""),
        "last_modified": headers.get("last-modified", record.get("last_modified", "") if code == 304 else ""),
    })
    result = {"source_id": record["source_id"], "status": "unchanged", "changed": False}
    if code == 304:
        updated["content_hash"] = record["content_hash"]
    else:
        text = normalize_document(body, headers.get("content-type", "text/plain"))
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        old_digest = record.get("content_hash", "")
        source_key = hashlib.sha256(record["source_id"].encode("utf-8")).hexdigest()
        updated["content_hash"] = digest
        updated["snapshot_path"] = _immutable_text(directory, f"snapshots/{source_key}/{digest}.md", text)
        if not old_digest:
            result["status"] = "baseline"
        elif old_digest != digest:
            previous_path = record.get("snapshot_path")
            previous = _safe_child(directory, previous_path) if previous_path else None
            old_text = previous.read_text(encoding="utf-8") if previous and previous.is_file() else ""
            if previous and previous.is_file() and hashlib.sha256(old_text.encode("utf-8")).hexdigest() != old_digest:
                raise ValueError("Previous source snapshot hash does not match saved state")
            sections = changed_sections(old_text, text)
            # Hash the pair so user-supplied baseline hashes cannot become paths.
            change_key = hashlib.sha256(f"{old_digest}\n{digest}".encode("utf-8")).hexdigest()
            stem = f"changes/{source_key}/{change_key}"
            payload = {
                "source_id": record["source_id"], "old_hash": old_digest, "new_hash": digest,
                "previous_available": bool(previous and previous.is_file()), "sections": sections,
            }
            updated["last_change_path"] = _immutable_text(
                directory, stem + ".json",
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )
            diff = "".join(difflib.unified_diff(
                old_text.splitlines(keepends=True), text.splitlines(keepends=True),
                fromfile=f"{record['source_id']}@{old_digest}", tofile=f"{record['source_id']}@{digest}",
            ))
            updated["last_diff_path"] = _immutable_text(directory, stem + ".diff", diff)
            updated.update({"status": "changed", "last_changed": checked})
            result.update({
                "status": "changed", "changed": True,
                "changed_section_count": len(sections), "previous_available": payload["previous_available"],
                "diff_path": str(_safe_child(directory, updated["last_diff_path"])),
                "changed_sections_path": str(_safe_child(directory, updated["last_change_path"])),
            })
    result.update({"last_checked": checked, "content_hash": updated["content_hash"]})
    return updated, result


def check_sources(
    config: dict, source_id: str | None = None, *, force: bool = False,
    now=None, opener=None,
) -> list[dict]:
    """Check due sources independently; errors preserve previous snapshots/hash.

    ``opener`` is an optional urllib-compatible callable for deterministic tests.
    Results are compact metadata; changed content stays in referenced artifacts.
    A lock serializes writers and each result is published atomically.
    """
    options = sources_config(config)
    if not options["enabled"]:
        return []
    registry = load_registry(config)
    if source_id is not None and source_id not in registry:
        raise ValueError(f"Unknown source_id: {source_id}")
    selected = [source_id] if source_id else sorted(registry)
    if not selected:
        return []
    current = utc_now(now)
    directory = state_dir(config)
    results = []
    with _state_lock(directory):
        # Reload under the lock so another completed watcher cannot be lost.
        registry = load_registry(config)
        state = load_state(config)
        for key in selected:
            record = registry[key]
            if not force and not is_due(record, now=current):
                results.append({"source_id": key, "status": "skipped", "changed": False})
                continue
            try:
                updated, result = _check_one(record, options, directory, current, opener)
            except (OSError, ValueError, URLError, HTTPError, HTTPException) as exc:
                updated = dict(record)
                updated.update({"last_checked": timestamp_text(current), "status": "error", "error": str(exc)})
                result = {"source_id": key, "status": "error", "changed": False, "error": str(exc)}
            state[key] = updated
            _write_state(config, directory, state)
            results.append(result)
    return results


def status(config: dict, *, now=None) -> dict:
    """Read registry and cached checks only; no network and no writes."""
    current = utc_now(now)
    registry = load_registry(config)
    return {
        "enabled": sources_config(config)["enabled"],
        "registry_path": str(registry_path(config)), "state_dir": str(state_dir(config)),
        "sources": [dict(record, due=is_due(record, now=current)) for _, record in sorted(registry.items())],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="Check registered sources without compiling Wiki pages")
    check.add_argument("source_id", nargs="?")
    check.add_argument("--force", action="store_true", help="Ignore check_interval")
    check.add_argument("--json", action="store_true")
    show = commands.add_parser("status", help="Read registry and cached check status")
    show.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = load_config(strict=True)
        if args.command == "status":
            output = status(config)
            rows = output["sources"]
        else:
            rows = check_sources(config, args.source_id, force=args.force)
            output = {"enabled": sources_config(config)["enabled"], "sources": rows}
        if args.json:
            print(json.dumps(output, ensure_ascii=False, indent=2))
        elif not output["enabled"]:
            print("Source watcher is disabled in [sources].enabled")
        elif not rows:
            print("No registered sources. Add records to " + str(registry_path(config)))
        else:
            for row in rows:
                detail = f" — {row['error']}" if row.get("error") else ""
                if row.get("diff_path"):
                    detail += f" — diff: {row['diff_path']}"
                if args.command == "status" and row.get("due"):
                    detail += " — check due"
                print(f"{row['source_id']}: {row['status']}{detail}")
        return 1 if args.command == "check" and any(row["status"] == "error" for row in rows) else 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
