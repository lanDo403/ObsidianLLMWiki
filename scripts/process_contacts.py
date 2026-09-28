"""process_contacts.py — Process a networking contacts note into individual contact cards.

Usage:
    python3 scripts/process_contacts.py "My Contacts Note"
    python3 scripts/process_contacts.py "contacts.md"
    python3 scripts/process_contacts.py "/absolute/path/to/note.md"
    python3 scripts/process_contacts.py "contacts.md" --dry-run
    python3 scripts/process_contacts.py "contacts.md" --non-interactive --on-conflict skip

Flow:
    1. Find note in vault (by title, filename, or absolute path)
    2. Read body + existing frontmatter
    3. Scan vault for existing titles (for wikilink resolution)
    4. Assemble prompt (SKILL_CONTACTS.md + rules/contacts.md + taxonomy + tags + vault_titles + body)
    5. Call the active rewrite backend
    6. Validate response (contacts JSON)
    7. Write individual contact notes + MOC to vault
"""

import argparse
import json
import re
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

try:
    from scripts.config import PROJECT_ROOT, load_config as load_config_strict
    from scripts.atomize import extract_json, load_tags, validate_tags, write_proposed_tags
    from scripts.rewrite_backend import call_rewriter
    from scripts.generate_notes import render_note_md, sanitize_filename
    from scripts.scan_vault import scan_vault
    from scripts.vault_writer import (
        get_vault_dest, load_registry, parse_frontmatter, save_registry,
    )
except ModuleNotFoundError:
    from config import PROJECT_ROOT, load_config as load_config_strict
    from atomize import extract_json, load_tags, validate_tags, write_proposed_tags
    from rewrite_backend import call_rewriter
    from generate_notes import render_note_md, sanitize_filename
    from scan_vault import scan_vault
    from vault_writer import (
        get_vault_dest, load_registry, parse_frontmatter, save_registry,
    )

SCRIPTS_DIR = Path(__file__).parent


# ── Skill & rules loading ────────────────────────────────────────────────────


def load_skill_contacts_md() -> str:
    """Read SKILL_CONTACTS.md from project root."""
    path = PROJECT_ROOT / "SKILL_CONTACTS.md"
    return path.read_text(encoding="utf-8")


def load_rules() -> tuple[str, str]:
    """Read contacts and taxonomy rule files."""
    contacts = (PROJECT_ROOT / "rules" / "contacts.md").read_text(encoding="utf-8")
    taxonomy = (PROJECT_ROOT / "rules" / "taxonomy.md").read_text(encoding="utf-8")
    return contacts, taxonomy


# ── Note finding ─────────────────────────────────────────────────────────────


DEFAULT_MOC_TITLE = "Networking — MOC"
WIKILINK_RE = re.compile(r"\[\[([^\]|]+?)(?:\|[^\]]*)?\]\]")


def extract_wikilinks(body: str) -> set[str]:
    """Return the set of `[[Target]]` link targets referenced in `body`.

    `[[Target|Alias]]` is normalized to just `Target` so rename-via-alias
    does not look like link loss. Anchors are preserved (`Page#heading`
    remains distinct from `Page`) because losing a section anchor IS a
    silent regression.
    """
    return {m.strip() for m in WIKILINK_RE.findall(body or "") if m.strip()}


def find_existing_moc(
    vault_path: Path, config: dict, moc_title: str = DEFAULT_MOC_TITLE
) -> Path | None:
    """Return the path to the Networking MOC file, if it already exists.

    Looks first in the configured `moc_folder`, then the `contacts_folder`,
    then the vault root, then a recursive scan by stem — the same order as
    `find_note()` to keep behavior consistent with how users already
    organize their vault.
    """
    filename = f"{moc_title}.md"
    moc_folder = vault_path / config["vault"].get("moc_folder", "MOCs")
    contacts_folder = vault_path / config["vault"].get("contacts_folder", "Networking")

    for folder in [moc_folder, contacts_folder, vault_path]:
        candidate = folder / filename
        if candidate.exists() and _contained(candidate, vault_path):
            return candidate

    for md_file in vault_path.rglob("*.md"):
        rel_parts = md_file.relative_to(vault_path).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        if md_file.stem == moc_title and _contained(md_file, vault_path):
            return md_file

    return None


def _contained(candidate: Path, vault_root: Path) -> bool:
    """Return True iff `candidate` resolves to a path inside `vault_root`."""
    try:
        candidate.resolve().relative_to(vault_root.resolve())
    except (ValueError, OSError):
        return False
    return True


def find_note(query: str, config: dict) -> Path | None:
    """Find a note in the vault by title, filename, or absolute path.

    Enforces vault containment: absolute paths and relative queries alike are
    rejected (with a printed error) if they resolve outside of `vault_path`.
    This prevents `process_contacts.py /etc/passwd` and `../../escape` tricks
    from touching files archive_original() would later move.
    """
    vault_path = Path(config["vault"]["vault_path"])

    query_path = Path(query)
    if query_path.is_absolute():
        if not query_path.exists():
            return None
        if not _contained(query_path, vault_path):
            print(
                f"ERROR: Refusing to operate on '{query}' — path is outside the "
                f"configured vault ({vault_path}).",
                file=sys.stderr,
            )
            return None
        return query_path

    notes_folder = vault_path / config["vault"].get("notes_folder", "Notes")
    contacts_folder = vault_path / config["vault"].get("contacts_folder", "Networking")
    moc_folder = vault_path / config["vault"].get("moc_folder", "MOCs")

    filename = query if query.endswith(".md") else f"{query}.md"

    for folder in [vault_path, notes_folder, contacts_folder, moc_folder]:
        candidate = folder / filename
        if candidate.exists() and _contained(candidate, vault_path):
            return candidate

    stem = query.removesuffix(".md")
    for md_file in vault_path.rglob("*.md"):
        rel_parts = md_file.relative_to(vault_path).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        if md_file.stem == stem and _contained(md_file, vault_path):
            return md_file

    return None


def split_frontmatter_and_body(content: str) -> tuple[dict | None, str]:
    """Split .md content into frontmatter dict and body string."""
    if not content.startswith("---"):
        return None, content

    parts = content.split("---", 2)
    if len(parts) < 3:
        return None, content

    fm = parse_frontmatter(content)
    body = parts[2].strip()
    return (fm if fm else None), body


# ── Prompt assembly ──────────────────────────────────────────────────────────


def assemble_prompt(
    note_input: dict,
    tags: list[str],
    skill_md: str,
    contacts_rules: str,
    taxonomy_rules: str,
) -> str:
    """Build the complete prompt for the rewrite backend."""
    lines: list[str] = []

    lines.append(skill_md)

    lines.append("---")
    lines.append("## Contact Processing Rules")
    lines.append("")
    lines.append(contacts_rules)

    lines.append("---")
    lines.append("## Taxonomy Rules")
    lines.append("")
    lines.append(taxonomy_rules)

    lines.append("---")
    lines.append("## Available Tags (from tags.yaml)")
    lines.append("")
    for tag in tags:
        lines.append(f"- {tag}")

    lines.append("")
    lines.append("---")
    lines.append("## Note to Process")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(note_input, ensure_ascii=False, indent=2))
    lines.append("```")

    lines.append("")
    lines.append(
        "Process this contacts note now. Output ONLY the JSON with "
        '"contacts" array and "moc" object, no prose.'
    )

    return "\n".join(lines)


# ── Validation ───────────────────────────────────────────────────────────────


def validate_contacts_result(
    result: dict,
    vault_titles: set[str],
    *,
    strict_collisions: bool = True,
    existing_moc_links: set[str] | None = None,
) -> list[str]:
    """Validate the contacts JSON output.

    When `strict_collisions` is True (the default, and the only safe policy
    unless the caller explicitly passed `--on-conflict overwrite`), any
    contact whose title already exists in the vault is reported as an error.
    Without this check the MOC wipe / archive flow could silently overwrite
    an unrelated note that happened to share a title with a new contact.
    """
    errors: list[str] = []

    if "contacts" not in result:
        errors.append("Result missing 'contacts' key")
        return errors

    contacts = result["contacts"]
    if not isinstance(contacts, list) or len(contacts) == 0:
        errors.append("'contacts' must be a non-empty list")
        return errors

    seen_titles: set[str] = set()

    for i, contact in enumerate(contacts):
        cid = contact.get("id", f"contact-{i}")

        # Required fields
        required = {"title", "tags", "date", "source_doc", "note_type", "body"}
        missing = required - set(contact.keys())
        if missing:
            errors.append(f"Contact '{cid}' missing fields: {sorted(missing)}")

        # note_type must be "contact"
        if contact.get("note_type") != "contact":
            errors.append(
                f"Contact '{cid}': note_type must be 'contact', "
                f"got '{contact.get('note_type')}'"
            )

        # source_doc must be "Контакты"
        if contact.get("source_doc") != "Контакты":
            errors.append(
                f"Contact '{cid}': source_doc must be 'Контакты', "
                f"got '{contact.get('source_doc')}'"
            )

        # Tag count (2-5) and must include networking/contact
        tags = contact.get("tags", [])
        if not (2 <= len(tags) <= 5):
            errors.append(f"Contact '{cid}' has {len(tags)} tags; must be 2-5")
        if "networking/contact" not in tags:
            errors.append(f"Contact '{cid}' missing required tag 'networking/contact'")

        # Title uniqueness within batch
        title = contact.get("title", "")
        if title in seen_titles:
            errors.append(f"Contact '{cid}': duplicate title '{title}' in batch")
        seen_titles.add(title)

        # Collision with an existing vault note
        if strict_collisions and title and title in vault_titles:
            errors.append(
                f"Contact '{cid}': title '{title}' already exists in vault. "
                "Re-run with `--on-conflict overwrite` to replace the existing "
                "note, or rename the contact."
            )

        # Body length check
        body = contact.get("body", "")
        word_count = len(body.split())
        if word_count < 30:
            errors.append(
                f"Contact '{cid}': body too short ({word_count} words, minimum ~50)"
            )

    # Validate MOC if present
    moc = result.get("moc")
    if moc:
        if moc.get("note_type") != "moc":
            errors.append(f"MOC note_type must be 'moc', got '{moc.get('note_type')}'")
        moc_body = moc.get("body") or ""
        if not moc_body:
            errors.append("MOC body is empty")

        # If we loaded an existing MOC, the LLM is required to preserve every
        # wikilink that was already there — adding contacts must be additive.
        # rules/contacts.md line 133 is explicit: "Do not remove existing
        # entries." Trust nothing; verify with a regex pass.
        if existing_moc_links:
            new_links = extract_wikilinks(moc_body)
            lost = existing_moc_links - new_links
            if lost:
                sample = sorted(lost)[:10]
                extra = f" (+{len(lost) - len(sample)} more)" if len(lost) > len(sample) else ""
                errors.append(
                    "MOC regenerated without preserving existing wikilinks. "
                    f"Lost entries: {sample}{extra}. "
                    "The model must include every old [[wikilink]] in the "
                    "updated MOC body (see rules/contacts.md §MOC Generation)."
                )

    return errors


# ── Result writing ───────────────────────────────────────────────────────────


def archive_original(note_path: Path, vault_path: Path) -> None:
    """Move original note to .archive/ in vault root."""
    archive_dir = vault_path / ".archive"
    archive_dir.mkdir(exist_ok=True)

    today = date.today().isoformat()
    archive_name = f"{today}_{note_path.stem}.md"
    dest = archive_dir / archive_name

    counter = 1
    while dest.exists():
        archive_name = f"{today}_{note_path.stem}_{counter}.md"
        dest = archive_dir / archive_name
        counter += 1

    shutil.move(str(note_path), str(dest))
    print(f"  Archived original: {dest.name}", file=sys.stderr)


def write_contacts_result(
    result: dict,
    original_path: Path,
    config: dict,
    *,
    non_interactive: bool = False,
    on_conflict: str = "skip",
) -> None:
    """Write contact notes and MOC to vault via staging."""
    staging_root = Path(config.get("rclone", {}).get("staging_dir", "/tmp/obsidian-llmwiki/staging"))
    staging_root.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(tempfile.mkdtemp(prefix="contacts-", dir=staging_root))

    # Write contacts plan to staging for reference
    plan_path = staging_dir / "contacts-plan.json"
    plan_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Generate contact .md files
    contacts = result.get("contacts", [])
    for contact in contacts:
        title = contact.get("title", "untitled")
        filename = sanitize_filename(title) + ".md"
        content = render_note_md(contact)
        (staging_dir / filename).write_text(content, encoding="utf-8")

    # Generate MOC .md file
    moc = result.get("moc")
    if moc:
        moc_title = moc.get("title", "Networking — MOC")
        moc_filename = sanitize_filename(moc_title) + ".md"
        moc_content = render_note_md(moc)
        (staging_dir / moc_filename).write_text(moc_content, encoding="utf-8")

    contact_count = len(contacts)
    moc_count = 1 if moc else 0
    print(
        f"  Generated {contact_count + moc_count} .md files "
        f"({contact_count} contacts + {moc_count} MOC)",
        file=sys.stderr,
    )

    # Write proposed tags
    write_proposed_tags(
        {"notes": contacts}, staging_dir, "contacts"
    )

    # Use vault_writer.py for actual vault writing
    vault_writer_py = str(SCRIPTS_DIR / "vault_writer.py")
    vw_cmd = [
        sys.executable, vault_writer_py,
        "--staging", str(staging_dir),
        "--atom-plan", str(plan_path),
    ]
    if non_interactive:
        vw_cmd.extend(["--non-interactive", "--on-conflict", on_conflict])

    import subprocess
    vw_result = subprocess.run(
        vw_cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if vw_result.stderr:
        print(vw_result.stderr, end="", file=sys.stderr)
    if vw_result.returncode != 0:
        print("ERROR: vault_writer.py failed.", file=sys.stderr)
        sys.exit(1)
    if vw_result.stdout:
        print(f"  {vw_result.stdout.strip()}", file=sys.stderr)

    # Archive original note only when the full pipeline produced output.
    # If the MOC was not generated, the user would lose the source note
    # without getting the organizational entry point for those contacts.
    vault_path = Path(config["vault"]["vault_path"])
    if moc:
        archive_original(original_path, vault_path)
    else:
        print(
            "  WARNING: MOC was not generated — original note NOT archived. "
            "Re-run to produce a complete output before archiving.",
            file=sys.stderr,
        )


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Process a networking contacts note into individual contact cards."
    )
    parser.add_argument(
        "input",
        help="Note title, filename (with or without .md), or absolute path",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print assembled prompt to stdout and exit without calling the rewrite backend",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Disable prompts during vault writes and use the policy from --on-conflict",
    )
    parser.add_argument(
        "--on-conflict",
        choices=("skip", "overwrite"),
        default="skip",
        help="Duplicate contact policy in non-interactive mode (default: skip)",
    )
    parser.add_argument(
        "--backend",
        choices=("auto", "claude", "codex"),
        default="auto",
        help="Rewrite backend to use (default: auto-detect)",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=300,
        help="Timeout for the rewrite backend call (default: 300)",
    )
    args = parser.parse_args()

    # Load config
    config = load_config_strict()
    vault_path = Path(config["vault"]["vault_path"])

    # Step 1: Find the note
    print(f">> Finding note: {args.input}", file=sys.stderr)
    note_path = find_note(args.input, config)
    if note_path is None:
        print(f"ERROR: Note not found: '{args.input}'", file=sys.stderr)
        print(
            f"  Searched: vault root, "
            f"{config['vault'].get('notes_folder', 'Notes')}, "
            f"{config['vault'].get('contacts_folder', 'Networking')}, "
            f"{config['vault'].get('moc_folder', 'MOCs')}, "
            f"recursive by stem",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"  Found: {note_path}", file=sys.stderr)

    # Step 2: Read content
    content = note_path.read_text(encoding="utf-8")
    frontmatter, body = split_frontmatter_and_body(content)

    word_count = len(body.split())
    print(f"  Word count: {word_count}", file=sys.stderr)

    if word_count < 10:
        print(
            f"ERROR: Note too short ({word_count} words). "
            "Need at least some contact information to process.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Step 3: Scan vault for existing titles
    print(">> Scanning vault for existing titles...", file=sys.stderr)
    exclude = {note_path.name}
    vault_data = scan_vault(vault_path, exclude=exclude)
    vault_titles = vault_data["titles"]
    print(f"  Found {len(vault_titles)} existing notes", file=sys.stderr)

    # Step 4: Assemble prompt
    tags = load_tags()
    skill_md = load_skill_contacts_md()
    contacts_rules, taxonomy_rules = load_rules()

    # Load existing Networking MOC (if any) so the model can EXTEND it
    # instead of regenerating from scratch. We also remember every wikilink
    # the old MOC contained so `validate_contacts_result()` can hard-fail on
    # silent entry loss.
    existing_moc_path = find_existing_moc(vault_path, config)
    existing_moc_body: str | None = None
    existing_moc_links: set[str] = set()
    if existing_moc_path is not None:
        raw = existing_moc_path.read_text(encoding="utf-8")
        _, existing_moc_body = split_frontmatter_and_body(raw)
        existing_moc_links = extract_wikilinks(existing_moc_body or "")
        print(
            f"  Existing MOC: {existing_moc_path.name} "
            f"({len(existing_moc_links)} wikilinks)",
            file=sys.stderr,
        )

    note_input = {
        "source_file": note_path.name,
        "body": body,
        "existing_frontmatter": frontmatter,
        "vault_titles": vault_titles,
        "word_count": word_count,
        "existing_moc_body": existing_moc_body,
    }

    prompt = assemble_prompt(
        note_input, tags, skill_md,
        contacts_rules, taxonomy_rules,
    )

    # Dry-run: print prompt and exit
    if args.dry_run:
        print(prompt)
        sys.exit(0)

    # Step 5: Call rewrite backend
    print(">> Calling rewrite backend...", file=sys.stderr)
    try:
        resolved_backend, raw_response = call_rewriter(
            prompt,
            backend=args.backend,
            timeout_seconds=args.timeout_seconds,
            project_root=PROJECT_ROOT,
        )
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"  Rewrite backend: {resolved_backend}", file=sys.stderr)

    # Step 6: Extract and validate
    try:
        result = extract_json(raw_response)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    vault_titles_set = set(vault_titles)
    valid_tags_set = set(tags)

    # Strict collisions unless the caller explicitly asked to overwrite.
    # `--on-conflict overwrite` is a loaded pistol — vault_writer.py will
    # clobber the existing note on disk — so require it to be explicit.
    strict = args.on_conflict != "overwrite"
    errors = validate_contacts_result(
        result,
        vault_titles_set,
        strict_collisions=strict,
        existing_moc_links=existing_moc_links,
    )

    if errors:
        print("ERROR: Validation failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        debug_path = Path("/tmp/obsidian-llmwiki/debug-response.json")
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        debug_path.write_text(raw_response, encoding="utf-8")
        print(f"  Raw response saved to: {debug_path}", file=sys.stderr)
        sys.exit(1)

    # Tag warnings (non-fatal)
    contacts_for_tags = {"notes": result.get("contacts", [])}
    tag_warnings = validate_tags(contacts_for_tags, valid_tags_set)
    for warn in tag_warnings:
        print(f"WARNING: {warn}", file=sys.stderr)

    # Step 7: Write results
    contact_count = len(result.get("contacts", []))
    print(f">> Writing {contact_count} contact(s) to vault...", file=sys.stderr)

    write_contacts_result(
        result,
        note_path,
        config,
        non_interactive=args.non_interactive,
        on_conflict=args.on_conflict,
    )

    print("Done.", file=sys.stderr)


if __name__ == "__main__":
    main()
