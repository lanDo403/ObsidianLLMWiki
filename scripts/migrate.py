"""migrate.py — idempotent upgrade step for existing ObsidianLLMWIKI installs.

Run after every `git pull` (install.sh calls it automatically):

    python3 scripts/migrate.py

Ensure-style, no version bookkeeping: each step checks reality and only does
what is missing. Safe to run any number of times.

Current steps:
1. config.toml gets the [memory] section (FTS5 memory defaults) if absent.
2. The FTS5 vault index is built (or incrementally refreshed) when
   vault_path is configured and [memory].enabled is true.

Nothing in the vault is ever touched; the legacy Smart Connections cache
(.smart-env/) is left as-is for users who still want the plugin.
"""

from __future__ import annotations

import sys
import re
import os
import tempfile
from pathlib import Path

try:
    from scripts.config import PROJECT_ROOT, load_config, tomllib
    from scripts.memory_index import (
        db_path_for_vault,
        fts5_available,
        memory_config,
        update_index,
        vault_path_from,
    )
except ModuleNotFoundError:
    from config import PROJECT_ROOT, load_config, tomllib
    from memory_index import (
        db_path_for_vault,
        fts5_available,
        memory_config,
        update_index,
        vault_path_from,
    )

MEMORY_SECTION = """
[memory]
# FTS5 full-text memory over the whole vault (replaces the legacy
# Smart Connections embedding layer). Zero dependencies: stdlib sqlite3.
enabled = true

# Where the SQLite index lives. Empty → ~/.cache/obsidian-llmwiki/
# (one db per vault, named <vault-slug>-<hash>.db). The index is always
# OUTSIDE the vault so vault sync (gdrive/iCloud/...) never touches it.
db_dir = ""

# "unicode61" (default — compact, word-level, Cyrillic-safe) or
# "trigram" (substring matching like pg_trgm; needs SQLite >= 3.34,
# index is noticeably larger).
tokenizer = "unicode61"

# Refresh the index automatically after each vault_writer write.
# First build is always explicit: scripts/memory_index.py build (or migrate.py).
auto_update = true
"""


def ensure_memory_section(config_path: Path) -> str:
    """Insert only absent defaults; preserve user values, comments and line endings.

    Inline tables are left untouched (runtime defaults still apply), because TOML
    prohibits extending them. No config is created for an unconfigured install.
    """
    if not config_path.exists():
        return "no-config"
    original = config_path.read_bytes().decode("utf-8")
    parsed = tomllib.loads(original)
    text = original
    newline = "\r\n" if "\r\n" in original else "\n"
    defaults = {
        "memory": {"enabled": "true", "db_dir": '""', "tokenizer": '"unicode61"',
                   "auto_update": "true", "default_scope": '"auto"', "search_limit": "8",
                   "context_budget_tokens": "1200"},
        "memory.ranking": {"prefer_wiki": "true", "fallback_to_notes": "true"},
        "memory.semantic": {"enabled": "false", "provider": '"none"'},
        "sources": {"enabled": "true", "registry_path": '""', "state_dir": '""',
                    "check_interval": '"1d"', "timeout_seconds": "20", "max_response_bytes": "5242880"},
    }
    for table, entries in defaults.items():
        parts = table.split(".")
        if any(re.search(r"(?m)^\s*" + re.escape(part) + r"\s*=\s*\{", original) for part in parts):
            continue
        existing = parsed
        found = True
        for part in parts:
            found = found and part in existing
            existing = existing.get(part, {})
        missing = {key: value for key, value in entries.items() if key not in existing}
        if not missing:
            continue
        addition = newline.join(f"{key} = {value}" for key, value in missing.items()) + newline
        header = re.search(r"(?m)^\[" + re.escape(table) + r"\][ \t]*(?:#[^\r\n]*)?(?:\r?\n|$)", text)
        if header:
            text = text[:header.end()] + ("" if header[0].endswith("\n") else newline) + addition + text[header.end():]
        else:
            # A parsed existing table with a different spelling may be inline or
            # use dotted assignments. Leave it to runtime defaults instead.
            if found:
                continue
            text += ("" if text.endswith("\n") else newline) + newline + f"[{table}]" + newline + addition
    tomllib.loads(text)  # validate the proposed complete config before replacing it
    if text == original:
        return "present"
    descriptor, temporary = tempfile.mkstemp(dir=config_path.parent, prefix="config-defaults-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(text.encode("utf-8"))
        os.chmod(temporary, config_path.stat().st_mode)
        os.replace(temporary, config_path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return "added"


def ensure_index(config: dict) -> str:
    """Build or refresh the FTS5 index. Returns a human status string."""
    mem = memory_config(config)
    if not mem["enabled"]:
        return "skipped (memory disabled)"
    if not fts5_available():
        return "skipped (sqlite3 lacks FTS5)"
    try:
        vault_path = vault_path_from(config)
    except ValueError:
        return "skipped (vault_path not configured yet)"
    if not vault_path.is_dir():
        return f"skipped (vault_path does not exist: {vault_path})"

    db_path = db_path_for_vault(vault_path, mem["db_dir"])
    full = not db_path.exists()
    stats = update_index(config, full=full, quiet=True)
    verb = "built" if full else "refreshed"
    return f"{verb}: {stats['total']} notes ({stats['db']})"


def main() -> int:
    config_path = PROJECT_ROOT / "config.toml"

    print("== Migrate: config ==")
    try:
        state = ensure_memory_section(config_path)
    except (OSError, ValueError) as exc:
        print(f"config: FAILED ({exc}); original config retained", file=sys.stderr)
        return 1
    if state == "added":
        print("config.toml: missing memory/source defaults added")
    elif state == "present":
        print("config.toml: [memory] section already present")
    else:
        print("config.toml: not found — skipping (install.sh creates it)")

    print("== Migrate: FTS5 memory index ==")
    if state == "no-config":
        print("index: skipped (no config)")
        return 0
    config = load_config(strict=False)
    try:
        print(f"index: {ensure_index(config)}")
    except Exception as exc:  # noqa: BLE001 — migration must report, not crash
        print(f"index: FAILED ({exc})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
