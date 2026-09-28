"""wiki_ingest.py — Normalize raw inputs into a wiki-space's raw/ folder.

Accepts a single file, a directory, or a URL and writes one .md file per
input under ``<wiki-space>/raw/<kind>/<YYYY-MM-DD>-<slug>.md`` with a
minimal wiki frontmatter. **No LLM call.** This is pure normalization;
the compile pass that follows is what does the merge into wiki pages.

Usage:
    python3 scripts/wiki_ingest.py <slug> <path>          # file or dir
    python3 scripts/wiki_ingest.py <slug> <url> --kind articles
    python3 scripts/wiki_ingest.py <slug> <path> --label customer-survey

Flags:
    --kind {articles,docs,transcripts,assets}  (default: docs)
    --label <slug>                              (override slug part of filename)
    --no-compile                                (skip the chained compile call)
    --backend {auto,claude,codex}               (passed through to wiki_compile)

Exit codes:
    0 — at least one raw file written
    1 — bad arguments / config
    2 — wiki-space not found            (WIKI_PROJECT_NOT_FOUND on stderr)
    3 — input expanded to zero files    (WIKI_INGEST_EMPTY on stderr)
    4 — input read failed               (per-file errors logged to stderr)
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

try:
    from scripts.config import PROJECT_ROOT, load_config as _load_config
    from scripts.wiki_models import WIKI_RAW_KINDS, is_valid_slug
    from scripts.vault_writer import write_raw_document, get_vault_dest
    from scripts.memory_blocks import split_frontmatter, string_list
    from scripts.memory_index import auto_update_after_write
except ModuleNotFoundError:
    from config import PROJECT_ROOT, load_config as _load_config
    from wiki_models import WIKI_RAW_KINDS, is_valid_slug
    from vault_writer import write_raw_document, get_vault_dest
    from memory_blocks import split_frontmatter, string_list
    from memory_index import auto_update_after_write


_SLUG_STRIP = re.compile(r"[^a-z0-9-]+")
_SLUG_DASHES = re.compile(r"-{2,}")


def _slugify(text: str) -> str:
    """Coerce arbitrary text into kebab-case ASCII slug."""
    lower = text.strip().lower()
    lower = lower.replace(" ", "-").replace("_", "-")
    lower = _SLUG_STRIP.sub("", lower)
    lower = _SLUG_DASHES.sub("-", lower).strip("-")
    return lower or "input"


def _is_url(token: str) -> bool:
    parsed = urlparse(token)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _slug_from_url(url: str) -> str:
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    base = parts[-1] if parts else parsed.netloc
    base = base.rsplit(".", 1)[0]
    return _slugify(base) or _slugify(parsed.netloc)


def expand_inputs(token: str) -> list[Path]:
    """Resolve a CLI input token to a list of input files.

    URLs are not expanded here (caller fetches them); we return an empty
    list for URLs and let the caller branch.
    """
    if _is_url(token):
        return []
    p = Path(token)
    if not p.exists():
        raise FileNotFoundError(token)
    if p.is_file():
        return [p]
    return sorted(f for f in p.rglob("*") if f.is_file())


def _build_raw_frontmatter(slug: str, label: str, source: str, today: str) -> str:
    return (
        "---\n"
        f"tags:\n  - wiki/raw\n  - wiki/ingested\n"
        f"date: {today}\n"
        f"source_doc: \"wiki:{slug}:raw:{label}\"\n"
        "note_type: wiki\n"
        f"wiki_project: {slug}\n"
        "wiki_page_type: raw\n"
        "wiki_status: ingested\n"
        f"raw_source: \"{source}\"\n"
        "---\n"
        "\n"
    )


def _read_text_file(path: Path) -> str:
    """Read a text file, falling back to a one-line marker for binaries."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"_(binary file copied as-is from {path.name})_\n"


def write_raw_note(
    wiki_root: Path,
    slug: str,
    kind: str,
    label: str,
    source_id: str,
    body: str,
    today: str,
    metadata: dict | None = None,
) -> Path:
    """Write a single raw note. Returns the destination path."""
    return write_raw_document(wiki_root, slug, kind, label, source_id, body, today, metadata)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Normalize one or more raw inputs into a wiki-space's raw/ folder."
    )
    parser.add_argument("slug", help="target wiki-space slug")
    parser.add_argument("path", help="file, directory, or http(s) URL to ingest")
    parser.add_argument(
        "--kind",
        default="docs",
        choices=list(WIKI_RAW_KINDS),
        help="raw kind subfolder (default: docs)",
    )
    parser.add_argument("--label", default=None, help="override slug part of filename")
    parser.add_argument("--mode", choices=("knowledge", "documentation"), default="knowledge",
                        help="documentation preserves sections and skips automatic LLM compilation")
    parser.add_argument("--source-id", action="append", default=[], help="source registry ID (repeatable)")
    parser.add_argument("--version", default=None)
    parser.add_argument("--provider", default=None)
    parser.add_argument("--applies-to", action="append", default=[])
    parser.add_argument("--last-verified", default=None, help="actual ISO verification date, never inferred from ingestion")
    parser.add_argument(
        "--no-compile", action="store_true", help="skip chained wiki_compile.py call"
    )
    parser.add_argument(
        "--backend",
        choices=["auto", "claude", "codex"],
        default="auto",
        help="passed through to wiki_compile.py if --no-compile is not set",
    )
    args = parser.parse_args()

    if not is_valid_slug(args.slug):
        print(f"ERROR: slug '{args.slug}' is not valid kebab-case", file=sys.stderr)
        return 1

    config = _load_config(strict=True)
    vault_path = Path(config["vault"]["vault_path"]).expanduser()
    try:
        wiki_root = get_vault_dest("wiki", config, {"wiki_project": args.slug, "wiki_page_type": "meta"})
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    metadata = {"ingestion_mode": args.mode}
    if args.source_id:
        metadata["sources"] = args.source_id
    for key in ("version", "provider", "last_verified"):
        if getattr(args, key):
            metadata[key] = getattr(args, key)
    if args.applies_to:
        metadata["applies_to"] = args.applies_to

    if not (wiki_root / "SCHEMA.md").exists():
        print(
            f"WIKI_PROJECT_NOT_FOUND: no SCHEMA.md at {wiki_root}; run "
            f"`wiki_init.py {args.slug}` first",
            file=sys.stderr,
        )
        return 2

    today = date.today().isoformat()
    written: list[Path] = []

    if _is_url(args.path):
        # We do not auto-fetch URLs (would invite scope creep + secrets).
        # Instead, write a stub that records the URL — user can paste content
        # in later, or run a separate fetch step.
        label = args.label or _slug_from_url(args.path)
        body = (
            f"# {label}\n\n"
            f"_(stub — paste fetched content below or rerun with a downloaded file)_\n\n"
            f"Source URL: {args.path}\n"
        )
        try:
            dest = write_raw_note(
                wiki_root, args.slug, args.kind, label, args.path, body, today, metadata
            )
        except (OSError, ValueError) as exc:
            print(f"ERROR: failed to write raw note: {exc}", file=sys.stderr)
            return 4
        written.append(dest)
    else:
        try:
            inputs = expand_inputs(args.path)
        except FileNotFoundError:
            print(f"ERROR: input not found: {args.path}", file=sys.stderr)
            return 1

        if not inputs:
            print(
                f"WIKI_INGEST_EMPTY: '{args.path}' expanded to zero files",
                file=sys.stderr,
            )
            return 3

        for src in inputs:
            base_label = args.label or _slugify(src.stem)
            # If we got many inputs from a directory, append the original stem
            # to the label so each is unique even with --label.
            if len(inputs) > 1 and args.label:
                base_label = f"{args.label}-{_slugify(src.stem)}"
            try:
                if src.suffix.lower() == ".md":
                    original_fm, body = split_frontmatter(_read_text_file(src))
                else:
                    original_fm = {}
                    body = (
                        f"# {src.name}\n\n"
                        f"_(non-markdown source copied as-is from `{src}`)_\n\n"
                        f"```\n{_read_text_file(src)}\n```\n"
                    )
                retained = {key: original_fm[key] for key in
                            ("aliases", "tags", "sources", "source_ids", "version", "provider", "library", "applies_to", "last_verified")
                            if key in original_fm}
                retained.update(metadata)
                for key in ("aliases", "tags", "sources", "source_ids", "applies_to"):
                    if key in retained:
                        retained[key] = string_list(retained[key])
                dest = write_raw_note(
                    wiki_root,
                    args.slug,
                    args.kind,
                    base_label,
                    str(src),
                    body,
                    today,
                    retained,
                )
            except (OSError, ValueError) as exc:
                print(f"ERROR: failed to ingest {src}: {exc}", file=sys.stderr)
                continue
            written.append(dest)

    if not written:
        print(f"WIKI_INGEST_EMPTY: nothing was written from '{args.path}'", file=sys.stderr)
        return 3

    rel_root = wiki_root.relative_to(vault_path)
    for dest in written:
        rel = dest.relative_to(wiki_root)
        print(f"OK: {rel_root}/{rel}")

    try:
        auto_update_after_write(config)
    except Exception as exc:
        print(f"WARNING: raw saved; memory index refresh skipped: {exc}", file=sys.stderr)
    if args.no_compile or args.mode == "documentation":
        return 0

    compile_script = PROJECT_ROOT / "scripts" / "wiki_compile.py"
    if not compile_script.exists():
        print(
            "INFO: wiki_compile.py not yet available; skipping chained compile",
            file=sys.stderr,
        )
        return 0

    cmd = [
        sys.executable,
        str(compile_script),
        args.slug,
        "--since-last-compile",
        "--backend",
        args.backend,
    ]
    print(f"INFO: chaining: {' '.join(cmd)}", file=sys.stderr)
    proc = subprocess.run(cmd, check=False)
    return proc.returncode


__all__ = ["expand_inputs", "write_raw_note", "main"]


if __name__ == "__main__":
    sys.exit(main())
