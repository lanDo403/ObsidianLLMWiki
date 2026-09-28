"""vault_writer.py — Copy staged .md files to the Obsidian vault with dedup and folder routing.

Usage:
    python3 scripts/vault_writer.py --staging <staging-dir> [--atom-plan <atom-plan.json>]
    python3 scripts/vault_writer.py --staging <staging-dir> --non-interactive --on-conflict skip

vault_writer.py is the ONLY script permitted to write to vault_path.

Routing by note_type (from frontmatter):
    atomic  -> vault_path/notes_folder
    moc     -> vault_path/moc_folder
    source  -> vault_path/source_folder
    contact -> vault_path/contacts_folder
    wiki    -> vault_path/wiki_folder/<wiki_project>/<bucket>  (see _wiki_dest)
    <other> -> vault_path/notes_folder  (fallback)

Deduplication: (source_doc, title) pair tracked in processed.json registry.
MOC files are always overwritten (auto-generated, no manual edits expected).
MOC written last (sorted after all atomic notes).

Wiki notes always overwrite themselves: wiki_compile.py emits a fully merged
body, so the existing dedup branch is skipped. Wiki meta-pages (SCHEMA.md,
index.md, log.md) sort to the end of the run alongside MOC files.

All diagnostics go to stderr. Summary printed to both stdout and stderr.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

try:
    from scripts.config import REGISTRY_PATH, load_config as _load_config
    from scripts.wiki_models import WIKI_NOTE_TYPE, WIKI_RAW_KINDS, is_valid_slug
except ModuleNotFoundError:
    from config import REGISTRY_PATH, load_config as _load_config
    from wiki_models import WIKI_NOTE_TYPE, WIKI_RAW_KINDS, is_valid_slug


def load_config() -> dict:
    """Strict config loader — vault_path must be known before writing."""
    return _load_config(strict=True)


# ── Registry (processed.json) ───────────────────────────────────────────────────


def load_registry() -> dict:
    """Read processed.json from PROJECT_ROOT.

    Registry schema:
    {
        "SourceDoc.docx": {
            "source_doc": "SourceDoc.docx",
            "date": "2026-02-26",
            "note_count": 5,
            "note_titles": ["Title1", "Title2", ...]
        }
    }

    Returns {} on first run (file does not exist). Never crashes on missing file.
    """
    if not REGISTRY_PATH.exists():
        return {}
    try:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(
            f"WARNING: Could not read processed.json: {exc}. Starting with empty registry.",
            file=sys.stderr,
        )
        return {}


def save_registry(registry: dict) -> None:
    """Write processed.json atomically via tempfile + os.replace."""
    data = json.dumps(registry, ensure_ascii=False, indent=2)
    fd, tmp_path = tempfile.mkstemp(
        dir=REGISTRY_PATH.parent, suffix=".tmp", prefix="processed_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp_path, REGISTRY_PATH)
    except BaseException:
        # Clean up temp file on any failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ── Frontmatter parsing ─────────────────────────────────────────────────────────


def parse_frontmatter(content: str) -> dict:
    """Extract key/value pairs from YAML frontmatter block (between --- delimiters).

    Uses PyYAML for robust parsing. Returns an empty dict if no frontmatter found.
    """
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}

    fm_block = parts[1]
    try:
        parsed = yaml.safe_load(fm_block)
    except yaml.YAMLError:
        return {}

    if not isinstance(parsed, dict):
        return {}

    return parsed


# ── Conflict resolution ─────────────────────────────────────────────────────────


def resolve_conflict(
    title: str,
    source_doc: str,
    *,
    non_interactive: bool = False,
    on_conflict: str = "skip",
) -> str:
    """Resolve duplicate note handling.

    Returns 'skip' or 'overwrite'.

    In non-interactive mode, returns the configured policy immediately.
    """
    if non_interactive or not sys.stdin.isatty():
        print(
            f"  [non-interactive] Duplicate '{title}' from '{source_doc}' -> {on_conflict}",
            file=sys.stderr,
        )
        return on_conflict

    while True:
        try:
            choice = input(
                f"  Duplicate found: '{title}' (from {source_doc})\n"
                "  [s]kip / [o]verwrite? [s]: "
            ).strip().lower()
        except EOFError:
            return "skip"

        if choice in ("", "s", "skip"):
            return "skip"
        if choice in ("o", "overwrite"):
            return "overwrite"
        print("  Please enter 's' to skip or 'o' to overwrite.", file=sys.stderr)


# ── Vault destination routing ───────────────────────────────────────────────────


def get_vault_dest(
    note_type: str, config: dict, frontmatter: dict | None = None
) -> Path:
    """Return the vault subfolder Path for a given note_type.

    Routing:
        atomic  -> vault_path/notes_folder
        moc     -> vault_path/moc_folder
        source  -> vault_path/source_folder
        contact -> vault_path/contacts_folder
        wiki    -> vault_path/wiki_folder/<wiki_project>/<bucket>  (see _wiki_dest)
        <other> -> vault_path/notes_folder  (fallback)

    `frontmatter` is required for note_type=='wiki' so the wiki_project +
    wiki_page_type fields can drive sub-folder routing. Other note_types
    ignore it (back-compat with all existing call sites).
    """
    vault_path = Path(config["vault"]["vault_path"])
    vault_cfg = config.get("vault", {})

    if note_type == WIKI_NOTE_TYPE:
        return _wiki_dest(config, frontmatter or {})

    if note_type == "moc":
        folder = vault_cfg.get("moc_folder", "MOCs")
    elif note_type == "source":
        folder = vault_cfg.get("source_folder", "Sources")
    elif note_type == "contact":
        folder = vault_cfg.get("contacts_folder", "Networking")
    else:
        # "atomic" and any unknown type
        folder = vault_cfg.get("notes_folder", "Notes")

    return vault_path / folder


def _wiki_dest(config: dict, frontmatter: dict) -> Path:
    """Resolve the on-disk folder for a wiki page.

    Layout: <vault>/<wiki_folder>/<wiki_project>/<bucket>/

    Buckets per wiki_page_type:
        core       -> pages/
        entity     -> entities/
        concept    -> concepts/
        comparison -> comparisons/
        query      -> queries/
        readout    -> readouts/
        meta       -> <root>           (SCHEMA.md, index.md, log.md)
        raw        -> raw/<raw_kind>/  (raw_kind from frontmatter, default 'docs')

    Raises ValueError on missing wiki_project, missing/unknown wiki_page_type,
    or unknown raw_kind. Raising (rather than silently routing to a default)
    keeps wiki contour leaks loud and immediate.
    """
    vault_path = Path(config["vault"]["vault_path"])
    wiki_cfg = config.get("wiki", {})
    wiki_folder = str(wiki_cfg.get("wiki_folder", "LLM Wiki")).strip()
    # Guard: wiki_folder is joined into the vault path, so it must be a single
    # safe segment inside the vault — reject empty, traversal ('..'), absolute
    # paths and embedded separators before they can escape vault_path.
    _wf_parts = Path(wiki_folder).parts
    if not wiki_folder or ".." in _wf_parts or Path(wiki_folder).is_absolute() or len(_wf_parts) != 1:
        raise ValueError(
            f"[wiki].wiki_folder must be a single folder name inside the vault "
            f"(no separators, no '..', not absolute); got {wiki_folder!r}"
        )

    project = frontmatter.get("wiki_project")
    if not isinstance(project, str) or not project:
        raise ValueError(
            "wiki frontmatter missing 'wiki_project' — cannot resolve destination"
        )
    if not is_valid_slug(project):
        raise ValueError(
            f"wiki_project={project!r} is not a valid kebab-case slug; cannot route"
        )

    page_type = frontmatter.get("wiki_page_type")
    if page_type not in {"core", "entity", "concept", "comparison", "query", "readout", "meta", "raw"}:
        raise ValueError(
            f"wiki frontmatter has invalid wiki_page_type={page_type!r}"
        )

    project_root = vault_path / wiki_folder / project

    if page_type == "core":
        return project_root / "pages"
    if page_type == "entity":
        return project_root / "entities"
    if page_type == "concept":
        return project_root / "concepts"
    if page_type == "comparison":
        return project_root / "comparisons"
    if page_type == "query":
        return project_root / "queries"
    if page_type == "readout":
        return project_root / "readouts"
    if page_type == "meta":
        return project_root
    # raw
    raw_kind = frontmatter.get("raw_kind", "docs")
    if raw_kind not in WIKI_RAW_KINDS:
        raise ValueError(
            f"wiki raw frontmatter has invalid raw_kind={raw_kind!r}; "
            f"allowed: {WIKI_RAW_KINDS}"
        )
    return project_root / "raw" / raw_kind


# ── Sort key: MOC last ──────────────────────────────────────────────────────────


def write_raw_document(wiki_root: Path, slug: str, kind: str, label: str,
                       source: str, body: str, today: str, metadata: dict | None = None) -> Path:
    """Append a raw input exclusively; collisions get new names, never overwrite."""
    from datetime import date
    if kind not in WIKI_RAW_KINDS or not is_valid_slug(slug) or not is_valid_slug(label):
        raise ValueError("invalid raw kind, project slug or label")
    date.fromisoformat(today)
    raw_dir = wiki_root / "raw" / kind
    if not raw_dir.resolve().is_relative_to(wiki_root.resolve()):
        raise ValueError("raw destination escapes wiki-space")
    raw_dir.mkdir(parents=True, exist_ok=True)
    counter = 1
    while True:
        stem = f"{today}-{label}" + (f"-{counter}" if counter > 1 else "")
        dest = raw_dir / f"{stem}.md"
        fm = dict(metadata or {})
        fm.update(tags=list(dict.fromkeys([*fm.get("tags", []), "wiki/raw", "wiki/ingested"])),
                  date=today, source_doc=f"wiki:{slug}:raw:{stem}", note_type="wiki",
                  wiki_project=slug, wiki_page_type="raw", wiki_status="ingested",
                  raw_kind=kind, raw_source=source)
        content = "---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip() + "\n---\n\n" + body
        try:
            with dest.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
            return dest
        except FileExistsError:
            counter += 1


def _moc_sort_key(md_file: Path) -> tuple[int, str]:
    """Sort key that places MOC and wiki meta-pages after all other notes.

    Detection: frontmatter note_type=='moc' OR stem ending with ' — MOC'
    OR wiki meta-page (note_type=='wiki' AND wiki_page_type=='meta').
    Fallback (cannot read file): treat as non-MOC (sort first).

    Wiki meta-pages (SCHEMA.md, index.md, log.md) sort last so index.md is
    regenerated AFTER all entity/concept pages have been written.
    """
    is_late = False
    try:
        content = md_file.read_text(encoding="utf-8")
        fm = parse_frontmatter(content)
        if fm.get("note_type", "") == "moc" or md_file.stem.endswith(" \u2014 MOC"):
            is_late = True
        elif (
            fm.get("note_type", "") == WIKI_NOTE_TYPE
            and fm.get("wiki_page_type", "") == "meta"
        ):
            is_late = True
    except OSError:
        pass
    return (1 if is_late else 0, md_file.name)


# ── Main ────────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Copy staged .md files to the Obsidian vault with deduplication and "
            "folder routing. vault_writer.py is the only script that writes to vault_path."
        )
    )
    parser.add_argument(
        "--staging",
        required=True,
        help="Path to staging directory containing .md files",
    )
    parser.add_argument(
        "--atom-plan",
        help=(
            "Optional path to atom plan JSON for additional note_type/source_doc "
            "context. Frontmatter remains the primary source."
        ),
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Disable prompts and use the policy from --on-conflict",
    )
    parser.add_argument(
        "--on-conflict",
        choices=("skip", "overwrite"),
        default="skip",
        help="Duplicate note policy in non-interactive mode (default: skip)",
    )
    args = parser.parse_args()

    staging_dir = Path(args.staging)
    if not staging_dir.exists():
        print(f"ERROR: Staging directory not found: {args.staging}", file=sys.stderr)
        sys.exit(1)

    # Load config (hard error if missing — vault_path must be known)
    config = load_config()
    if "vault" not in config or "vault_path" not in config.get("vault", {}):
        print(
            "ERROR: config.toml is missing [vault] section or vault_path key.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Load dedup registry
    registry = load_registry()

    # Optionally load atom plan for supplementary note_type/source_doc context
    atom_plan_titles: dict[str, str] = {}  # stem -> note_type
    atom_plan_source: dict[str, str] = {}  # title -> source_doc
    if args.atom_plan:
        try:
            plan_data = json.loads(Path(args.atom_plan).read_text(encoding="utf-8"))
            for note in plan_data.get("notes", []):
                t = note.get("title", "")
                atom_plan_titles[t] = note.get("note_type", "")
                atom_plan_source[t] = note.get("source_doc", "")
        except (OSError, json.JSONDecodeError) as exc:
            print(
                f"WARNING: Could not read atom plan JSON '{args.atom_plan}': {exc}. "
                "Falling back to frontmatter-only mode.",
                file=sys.stderr,
            )

    # Collect all .md files (skip non-note files like proposed-tags.md).
    # rglob lets wiki_compile.py group pages into per-type subfolders inside
    # staging without confusing this writer; routing is still driven entirely
    # by frontmatter, not by the staging directory layout.
    md_files = [
        p for p in staging_dir.rglob("*.md")
        if p.name != "proposed-tags.md"
    ]
    if not md_files:
        print("WARNING: No .md files found in staging directory.", file=sys.stderr)
        summary = "Created 0 notes + 0 MOC, skipped 0 duplicates"
        print(summary, file=sys.stderr)
        print(summary)
        return

    # Sort: atomic notes first, MOC files last
    md_files.sort(key=_moc_sort_key)

    # Track per-session results for registry update
    # Maps source_doc -> {date, note_titles set}
    session_writes: dict[str, dict] = {}

    created_atomic = 0
    created_moc = 0
    skipped = 0

    for md_file in md_files:
        try:
            content = md_file.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"WARNING: Cannot read {md_file.name}: {exc}", file=sys.stderr)
            continue

        fm = parse_frontmatter(content)
        title = md_file.stem  # Use filename stem as title fallback
        # Extract title from the H1 heading if possible
        for line in content.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break

        note_type = fm.get("note_type", "")
        source_doc = fm.get("source_doc", "")
        date_val = fm.get("date", "")
        # YAML safe_load can parse dates as datetime.date objects — coerce to str
        if not isinstance(date_val, str):
            date_val = str(date_val)

        # Supplement with atom plan data if frontmatter is sparse
        if not note_type and title in atom_plan_titles:
            note_type = atom_plan_titles[title]
        if not source_doc and title in atom_plan_source:
            source_doc = atom_plan_source[title]

        # Dedup check — MOC always overwrites, wiki always overwrites
        # (wiki_compile.py emits a fully merged body and would lose data if
        # the writer skipped on conflict). Other note_types check the
        # registry by (source_doc, title) pair.
        if note_type not in ("moc", WIKI_NOTE_TYPE) and source_doc:
            existing = registry.get(source_doc, {})
            existing_titles: list[str] = existing.get("note_titles", [])
            if title in existing_titles:
                action = resolve_conflict(
                    title,
                    source_doc,
                    non_interactive=args.non_interactive,
                    on_conflict=args.on_conflict,
                )
                if action == "skip":
                    skipped += 1
                    continue
                # action == "overwrite": fall through to copy

        # Determine vault destination folder. Wiki pages need the frontmatter
        # to resolve <wiki_project>/<bucket>; other types ignore it.
        try:
            dest_dir = get_vault_dest(note_type, config, frontmatter=fm)
        except ValueError as exc:
            print(
                f"ERROR: cannot route {md_file.name}: {exc}",
                file=sys.stderr,
            )
            sys.exit(1)
        dest_dir.mkdir(parents=True, exist_ok=True)

        dest_path = dest_dir / md_file.name
        if note_type == WIKI_NOTE_TYPE and fm.get("wiki_page_type") == "raw":
            try:
                with dest_path.open("xb") as stream:
                    stream.write(content.encode("utf-8"))
            except FileExistsError:
                print(f"WIKI_RAW_IMMUTABLE: refusing to replace {dest_path}", file=sys.stderr)
                sys.exit(1)
        else:
            shutil.copy2(md_file, dest_path)

        # Track for registry update
        if source_doc:
            if source_doc not in session_writes:
                session_writes[source_doc] = {
                    "date": date_val,
                    "note_titles": [],
                }
            session_writes[source_doc]["note_titles"].append(title)

        if note_type == "moc":
            created_moc += 1
        else:
            created_atomic += 1

    # Update registry atomically after all vault writes complete
    for source_doc, info in session_writes.items():
        existing = registry.get(source_doc, {
            "source_doc": source_doc,
            "date": info["date"],
            "note_count": 0,
            "note_titles": [],
        })
        # Merge new titles (avoid duplication in overwrite scenarios)
        existing_titles_set = set(existing.get("note_titles", []))
        for t in info["note_titles"]:
            existing_titles_set.add(t)
        existing["note_titles"] = sorted(existing_titles_set)
        existing["note_count"] = len(existing["note_titles"])
        if info["date"] and not existing.get("date"):
            existing["date"] = info["date"]
        registry[source_doc] = existing

    if session_writes:
        save_registry(registry)
        print("Registry updated: processed.json", file=sys.stderr)

    summary = (
        f"Created {created_atomic} notes + {created_moc} MOC, "
        f"skipped {skipped} duplicates"
    )
    print(summary, file=sys.stderr)
    print(summary)

    # Best-effort FTS5 memory refresh ([memory] in config.toml). A broken or
    # missing index must never fail the vault write itself.
    try:
        try:
            from scripts.memory_index import auto_update_after_write
        except ModuleNotFoundError:
            from memory_index import auto_update_after_write
        auto_update_after_write(config)
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: memory index refresh skipped: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
