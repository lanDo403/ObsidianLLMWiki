"""Block-level local FTS5 memory; Markdown remains authoritative.

Search returns metadata, read returns one block, context returns budgeted evidence.
No default retrieval operation uses network or an LLM.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

try:
    from scripts.config import load_config
    from scripts.scan_vault import SKIP_DIRS
    from scripts.memory_blocks import parse_blocks, split_frontmatter, string_list
except ModuleNotFoundError:
    from config import load_config
    from scan_vault import SKIP_DIRS
    from memory_blocks import parse_blocks, split_frontmatter, string_list

DEFAULT_DB_DIR = Path.home() / ".cache" / "obsidian-llmwiki"
VALID_TOKENIZERS = ("unicode61", "trigram")
TRIGRAM_MIN_SQLITE = (3, 34, 0)
SCHEMA_VERSION = "2"
SCOPES = ("auto", "wiki", "notes", "raw", "all")


def memory_config(config: dict) -> dict:
    raw = config.get("memory", {}) or {}
    tokenizer = str(raw.get("tokenizer", "unicode61"))
    if tokenizer not in VALID_TOKENIZERS:
        print(f"WARNING: unknown tokenizer {tokenizer!r}; using unicode61", file=sys.stderr)
        tokenizer = "unicode61"
    ranking = raw.get("ranking", {}) or {}
    return {
        "enabled": bool(raw.get("enabled", True)),
        "db_dir": str(raw.get("db_dir", "") or ""),
        "tokenizer": tokenizer,
        "auto_update": bool(raw.get("auto_update", True)),
        "default_scope": raw.get("default_scope", "auto"),
        "search_limit": max(1, int(raw.get("search_limit", 8))),
        "context_budget_tokens": max(0, int(raw.get("context_budget_tokens", 1200))),
        "prefer_wiki": bool(ranking.get("prefer_wiki", True)),
        "fallback_to_notes": bool(ranking.get("fallback_to_notes", True)),
        "semantic": raw.get("semantic", {}) or {},
    }


def vault_path_from(config: dict) -> Path:
    vault = config.get("vault", {}).get("vault_path", "")
    if not vault or vault == "/path/to/your/obsidian/vault":
        raise ValueError("vault_path is not configured in config.toml")
    return Path(vault).expanduser()


def db_path_for_vault(vault_path: Path, db_dir: str = "") -> Path:
    base = Path(db_dir).expanduser() if db_dir else DEFAULT_DB_DIR
    if base.resolve().is_relative_to(vault_path.resolve()):
        raise ValueError("memory db_dir must be outside the vault")
    digest = hashlib.sha1(str(vault_path.resolve()).encode("utf-8")).hexdigest()[:10]
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", vault_path.name).strip("-") or "vault"
    return base / f"{slug}-{digest}.db"


def fts5_available() -> bool:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


def resolve_tokenizer(name: str) -> str:
    if name == "trigram":
        if sqlite3.sqlite_version_info >= TRIGRAM_MIN_SQLITE:
            return "trigram"
        print("WARNING: trigram unavailable; using unicode61", file=sys.stderr)
    return "unicode61 remove_diacritics 2"


def open_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _drop_index(conn: sqlite3.Connection) -> None:
    for trigger in ("blocks_ai", "blocks_ad"):
        conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    for table in ("notes_fts", "blocks", "files"):
        conn.execute(f"DROP TABLE IF EXISTS {table}")


def ensure_schema(conn: sqlite3.Connection, tokenizer: str) -> None:
    """Recreate incompatible derived tables in the caller's transaction."""
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    meta = dict(conn.execute("SELECT key,value FROM meta"))
    if meta.get("schema_version") != SCHEMA_VERSION or meta.get("tokenizer") != tokenizer:
        _drop_index(conn)
    conn.execute("CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, mtime INTEGER NOT NULL, size INTEGER NOT NULL)")
    conn.execute("""CREATE TABLE IF NOT EXISTS blocks (
        id INTEGER PRIMARY KEY, block_key TEXT UNIQUE NOT NULL, path TEXT NOT NULL,
        folder TEXT NOT NULL, title TEXT NOT NULL, headings TEXT NOT NULL,
        tags TEXT NOT NULL, aliases TEXT NOT NULL, body TEXT NOT NULL,
        knowledge_tier TEXT NOT NULL, mtime INTEGER NOT NULL, content_hash TEXT NOT NULL,
        estimated_tokens INTEGER NOT NULL, source_ids TEXT NOT NULL, version TEXT NOT NULL,
        provider TEXT NOT NULL, applies_to TEXT NOT NULL, last_verified TEXT NOT NULL,
        freshness TEXT NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS blocks_path ON blocks(path)")
    conn.execute("CREATE INDEX IF NOT EXISTS blocks_tier ON blocks(knowledge_tier)")
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5("
        "title, headings, tags, body, aliases, content='blocks', content_rowid='id',"
        f"tokenize=\"{resolve_tokenizer(tokenizer)}\", prefix='2 3')"
    )
    conn.execute("""CREATE TRIGGER IF NOT EXISTS blocks_ai AFTER INSERT ON blocks BEGIN
        INSERT INTO notes_fts(rowid,title,headings,tags,body,aliases)
        VALUES(new.id,new.title,new.headings,new.tags,new.body,new.aliases); END""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS blocks_ad AFTER DELETE ON blocks BEGIN
        INSERT INTO notes_fts(notes_fts,rowid,title,headings,tags,body,aliases)
        VALUES('delete',old.id,old.title,old.headings,old.tags,old.body,old.aliases); END""")
    for key, value in (("schema_version", SCHEMA_VERSION), ("tokenizer", tokenizer)):
        conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))


def knowledge_tier(path: str, fm: dict, config: dict) -> str:
    parts = Path(path).parts
    lower = {p.casefold() for p in parts[:-1]}
    in_wiki = parts[0] == config.get("wiki", {}).get("wiki_folder", "LLM Wiki")
    page_type = fm.get("wiki_page_type")
    if ("system" in lower or any(p.startswith(".") for p in parts)
            or fm.get("note_type") == "system" or page_type == "meta"
            or (in_wiki and (len(parts) == 3 or Path(path).name.startswith("_")))):
        return "system"
    source_folder = config.get("vault", {}).get("source_folder", "Sources")
    if ("raw" in lower or page_type == "raw"
            or fm.get("note_type") == "source" or parts[0] == source_folder):
        return "raw_sources"
    if ((fm.get("note_type") == "wiki" and page_type in
         {"core", "entity", "concept", "comparison", "query", "readout"})
            or (in_wiki and len(parts) > 3 and parts[2] in
                {"pages", "entities", "concepts", "comparisons", "queries", "readouts"})):
        return "canonical_wiki"
    return "atomic_notes"


def parse_note(md_file: Path, vault_path: Path, config: dict | None = None) -> dict:
    """Read once during indexing, retaining the legacy parser's public fields."""
    fm, body = split_frontmatter(md_file.read_text(encoding="utf-8", errors="replace"))
    rel = md_file.relative_to(vault_path).as_posix()
    blocks = parse_blocks(body, rel)
    first = next((b.heading_path.split(" > ")[0] for b in blocks if b.heading_path), "")
    return {
        "path": rel, "folder": Path(rel).parent.as_posix(),
        "title": str(fm.get("title") or first or md_file.stem),
        "headings": " ".join(b.heading_path for b in blocks), "body": body,
        "tags": " ".join(string_list(fm.get("tags"))),
        "aliases": " ".join(string_list(fm.get("aliases")) + [md_file.stem]),
        "knowledge_tier": knowledge_tier(rel, fm, config or {}), "blocks": blocks,
        "source_ids": json.dumps(string_list(fm.get("sources")) + string_list(fm.get("source_ids")), ensure_ascii=False),
        "version": str(fm.get("version") or "unknown"),
        "provider": str(fm.get("provider") or fm.get("library") or "unknown"),
        "applies_to": json.dumps(string_list(fm.get("applies_to")), ensure_ascii=False),
        "last_verified": str(fm.get("last_verified") or ""),
        "freshness": str(fm.get("freshness") or ("stale" if fm.get("wiki_status") in {"stale", "contradicted"} else "unknown")),
    }


def scan_files(vault_path: Path) -> dict[str, tuple[int, int]]:
    out = {}
    root = vault_path.resolve()
    for md_file in vault_path.rglob("*.md"):
        rel = md_file.relative_to(vault_path)
        if any(p in SKIP_DIRS or p.startswith(".") for p in rel.parts):
            continue
        if not md_file.resolve().is_relative_to(root):
            continue
        stat = md_file.stat()
        out[rel.as_posix()] = (stat.st_mtime_ns, stat.st_size)
    return out


def update_index(config: dict, *, full: bool = False, quiet: bool = False) -> dict:
    mem, vault_path = memory_config(config), vault_path_from(config)
    if not vault_path.is_dir():
        raise ValueError(f"vault_path does not exist: {vault_path}")
    if not fts5_available():
        raise RuntimeError("this Python's sqlite3 lacks FTS5 support")
    db_path = db_path_for_vault(vault_path, mem["db_dir"])
    conn = open_db(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if full:
            _drop_index(conn)
        ensure_schema(conn, mem["tokenizer"])
        routing = json.dumps([config.get("wiki", {}).get("wiki_folder", "LLM Wiki"),
                              config.get("vault", {}).get("source_folder", "Sources")])
        old_routing = conn.execute("SELECT value FROM meta WHERE key='routing'").fetchone()
        if old_routing and old_routing[0] != routing:
            conn.execute("DELETE FROM blocks")
            conn.execute("DELETE FROM files")
        on_disk = scan_files(vault_path)
        in_db = {p: (mtime, size) for p, mtime, size in conn.execute("SELECT path,mtime,size FROM files")}
        changed = [p for p, sig in on_disk.items() if p not in in_db or sig != in_db[p]]
        removed = [p for p in in_db if p not in on_disk]
        for rel in removed:
            conn.execute("DELETE FROM blocks WHERE path=?", (rel,))
            conn.execute("DELETE FROM files WHERE path=?", (rel,))
        for rel in changed:
            note = parse_note(vault_path / rel, vault_path, config)
            conn.execute("DELETE FROM blocks WHERE path=?", (rel,))
            for b in note["blocks"]:
                values = (b.block_key, rel, note["folder"], note["title"], b.heading_path,
                          note["tags"], note["aliases"], b.content, note["knowledge_tier"],
                          on_disk[rel][0], b.content_hash, b.estimated_tokens,
                          note["source_ids"], note["version"], note["provider"], note["applies_to"],
                          note["last_verified"], note["freshness"])
                conn.execute("""INSERT INTO blocks(block_key,path,folder,title,headings,tags,aliases,body,
                    knowledge_tier,mtime,content_hash,estimated_tokens,source_ids,version,provider,
                    applies_to,last_verified,freshness) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", values)
            conn.execute("INSERT OR REPLACE INTO files VALUES (?,?,?)", (rel, *on_disk[rel]))
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('last_update',?)", (str(int(time.time())),))
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('routing',?)", (routing,))
        block_count = conn.execute("SELECT count(*) FROM blocks").fetchone()[0]
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
    stats = {"db": str(db_path), "indexed": len(changed), "removed": len(removed),
             "total": len(on_disk), "blocks": block_count}
    if not quiet:
        print(f"Index {'rebuilt' if full else 'updated'}: {len(changed)} indexed, {len(removed)} removed, "
              f"{len(on_disk)} notes / {block_count} blocks\nDB: {db_path}")
    return stats


def auto_update_after_write(config: dict) -> None:
    mem = memory_config(config)
    if not (mem["enabled"] and mem["auto_update"]):
        return
    try:
        vault = vault_path_from(config)
    except ValueError:
        return
    if not db_path_for_vault(vault, mem["db_dir"]).exists():
        print("NOTE: vault memory index not built yet — run: python3 scripts/memory_index.py build", file=sys.stderr)
        return
    update_index(config, quiet=True)


def _read_db(config: dict) -> sqlite3.Connection:
    mem = memory_config(config)
    if not mem["enabled"]:
        raise ValueError("memory is disabled ([memory].enabled = false)")
    path = db_path_for_vault(vault_path_from(config), mem["db_dir"])
    if not path.exists():
        raise RuntimeError(f"index not built yet ({path}) — run: python3 scripts/memory_index.py build")
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        meta = dict(conn.execute("SELECT key,value FROM meta"))
        if meta.get("schema_version") != SCHEMA_VERSION or meta.get("tokenizer") != mem["tokenizer"]:
            raise RuntimeError("index schema/config changed — run: python3 scripts/memory_index.py update")
    except BaseException:
        conn.close()
        raise
    return conn


def build_match_expr(query: str, *, prefix: bool = False, raw: bool = False, tag: str = "") -> str:
    if raw:
        expr = query
    else:
        terms = query.split()
        if not terms:
            raise ValueError("empty query")
        quoted = ['"' + t.replace('"', '""') + '"' for t in terms]
        if prefix:
            quoted[-1] += "*"
        expr = " ".join(quoted)
    if tag:
        expr = f'({expr}) AND tags:"{tag.replace(chr(34), chr(34) * 2)}"'
    return expr


def version_matches(actual: str, requested: str) -> bool:
    """Exact or dotted component prefix: 4 matches v4.2, never 40 or unknown."""
    actual, requested = actual.casefold().removeprefix("v"), requested.casefold().removeprefix("v")
    return actual == requested or actual.startswith(requested.rstrip(".") + ".")


_METADATA = """b.block_key,b.path,b.folder,b.title,b.headings AS heading,b.knowledge_tier AS tier,
    b.estimated_tokens,b.source_ids,b.version,b.provider,b.applies_to,b.last_verified,b.freshness"""


def _metadata(row: sqlite3.Row) -> dict:
    result = dict(row)
    result["page"] = result["title"]
    result["source_ids"] = json.loads(result["source_ids"])
    local_refs = [s for s in result["source_ids"] if s.replace("\\", "/").startswith("raw/")]
    if local_refs:
        result["source_paths"] = local_refs
        result["source_ids"] = [s for s in result["source_ids"] if s not in local_refs]
    result["applies_to"] = json.loads(result["applies_to"])
    return result


def _refresh_freshness(config: dict, rows: list[dict]) -> None:
    if not rows:
        return
    try:
        from scripts.source_registry import load_registry, page_freshness
    except ModuleNotFoundError:
        from source_registry import load_registry, page_freshness
    registry = load_registry(config)
    for row in rows:
        row["freshness"] = page_freshness(row["source_ids"], row["last_verified"], registry, declared=row["freshness"])


def _lexical(config: dict, query: str, tiers: tuple[str, ...], *, limit: int, folder: str,
             tag: str, prefix: bool, raw: bool, version: str, debug: bool) -> list[dict]:
    expr = build_match_expr(query, prefix=prefix, raw=raw, tag=tag)
    mem, conn = memory_config(config), _read_db(config)
    try:
        conn.create_function("version_matches", 2, version_matches, deterministic=True)
        sql = f"""SELECT {_METADATA}, snippet(notes_fts,3,'«','»','…',18) AS snippet,
            bm25(notes_fts,10.0,4.0,6.0,1.0,8.0) AS bm25
            FROM notes_fts JOIN blocks b ON b.id=notes_fts.rowid
            WHERE notes_fts MATCH ? AND b.knowledge_tier IN ({','.join('?' for _ in tiers)})"""
        params: list = [expr, *tiers]
        if folder:
            folder = folder.replace("\\", "/").rstrip("/")
            sql += " AND (b.folder=? OR substr(b.folder,1,?)=?)"
            params += [folder, len(folder) + 1, folder + "/"]
        if version:
            sql += " AND version_matches(b.version,?)"
            params.append(version)
        if mem["prefer_wiki"]:
            sql += " ORDER BY CASE b.knowledge_tier WHEN 'canonical_wiki' THEN 0 WHEN 'atomic_notes' THEN 1 ELSE 2 END, bm25, b.block_key"
        else:
            sql += " ORDER BY bm25, b.block_key"
        sql += " LIMIT ?"
        rows = [_metadata(r) for r in conn.execute(sql, [*params, limit])]
    finally:
        conn.close()
    _refresh_freshness(config, rows)
    for r in rows:
        bm25 = r.pop("bm25")
        tier_boost = 1.5 if r["tier"] == "canonical_wiki" and mem["prefer_wiki"] else 1.0
        r["score"] = round(-bm25 * tier_boost, 6)
        r["snippet"] = " ".join(r["snippet"].split())[:240]
        r["retrieval"] = "fts"
        if debug:
            r["ranking"] = {"bm25": round(-bm25, 6), "tier_boost": tier_boost,
                            "tier_preference": mem["prefer_wiki"], "matched_all_terms": not raw}
    return rows


def search(config: dict, query: str, *, limit: int | None = None, folder: str = "", tag: str = "",
           prefix: bool = False, raw: bool = False, scope: str | None = None,
           version: str = "", debug: bool = False) -> list[dict]:
    mem = memory_config(config)
    scope = scope or mem["default_scope"]
    if scope not in SCOPES:
        raise ValueError(f"unknown scope: {scope}")
    limit = mem["search_limit"] if limit is None else max(1, limit)
    kwargs = dict(limit=limit, folder=folder, tag=tag, prefix=prefix, raw=raw, version=version, debug=debug)
    tiers = {"wiki": ("canonical_wiki",), "notes": ("atomic_notes",), "raw": ("raw_sources",),
             "all": ("canonical_wiki", "atomic_notes", "raw_sources")}
    if scope == "auto":
        rows = _lexical(config, query, ("canonical_wiki",), **kwargs)
        if rows:
            return rows
        rows = _lexical(config, query, ("atomic_notes",), **kwargs) if mem["fallback_to_notes"] else []
    else:
        rows = _lexical(config, query, tiers[scope], **kwargs)
    if not rows and mem["semantic"].get("enabled"):
        try:
            from scripts.memory_semantic import semantic_fallback
        except ModuleNotFoundError:
            from memory_semantic import semantic_fallback
        rows = semantic_fallback(config, query, scope=scope, **kwargs)
    return rows


def read_blocks(config: dict, block_keys: list[str]) -> list[dict]:
    """One SQLite query and no vault Markdown reads; missing keys are omitted."""
    if not block_keys:
        return []
    conn = _read_db(config)
    try:
        sql = f"SELECT {_METADATA},b.body AS content,b.content_hash FROM blocks b WHERE b.block_key IN ({','.join('?' for _ in block_keys)})"
        rows = [_metadata(r) for r in conn.execute(sql, block_keys)]
    finally:
        conn.close()
    _refresh_freshness(config, rows)
    indexed = {r["block_key"]: r for r in rows}
    return [indexed[k] for k in block_keys if k in indexed]


def read_block(config: dict, block_key: str) -> dict:
    rows = read_blocks(config, [block_key])
    if not rows:
        raise ValueError("block not found; note may have moved — search again after update")
    return rows[0]


def status(config: dict) -> dict:
    path = db_path_for_vault(vault_path_from(config), memory_config(config)["db_dir"])
    if not path.exists():
        return {"db": str(path), "exists": False}
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        meta = dict(conn.execute("SELECT key,value FROM meta"))
        notes = conn.execute("SELECT count(*) FROM files").fetchone()[0]
        compatible = meta.get("schema_version") == SCHEMA_VERSION
        blocks = conn.execute("SELECT count(*) FROM blocks").fetchone()[0] if compatible else 0
    finally:
        conn.close()
    return {"db": str(path), "exists": True, "notes": notes, "blocks": blocks,
            "needs_rebuild": not compatible, "schema_version": meta.get("schema_version", "1"),
            "size_kb": path.stat().st_size // 1024, "tokenizer": meta.get("tokenizer", "?"),
            "last_update": meta.get("last_update", "never")}


def main() -> int:
    parser = argparse.ArgumentParser(description="Local block-level FTS5 vault memory")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for cmd in ("build", "update", "status"):
        sub.add_parser(cmd)
    for cmd in ("search", "context"):
        p = sub.add_parser(cmd)
        p.add_argument("query")
        p.add_argument("--limit", type=int)
        p.add_argument("--scope", choices=SCOPES)
        p.add_argument("--folder", default="")
        p.add_argument("--tag", default="")
        p.add_argument("--version", default="")
        p.add_argument("--prefix", action="store_true")
        p.add_argument("--raw", action="store_true", help="raw FTS syntax, NOT raw-source scope")
        p.add_argument("--debug", action="store_true")
        if cmd == "search":
            p.add_argument("--json", action="store_true", dest="as_json")
        else:
            p.add_argument("--budget-tokens", type=int)
    p = sub.add_parser("read")
    p.add_argument("block_key")
    p.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    config = load_config(strict=True)
    try:
        if not memory_config(config)["enabled"] and args.cmd != "status":
            print("memory is disabled ([memory].enabled = false)", file=sys.stderr)
            return 2
        if args.cmd in {"build", "update"}:
            update_index(config, full=args.cmd == "build")
        elif args.cmd == "status":
            print(json.dumps(status(config), ensure_ascii=False, indent=2))
        elif args.cmd == "read":
            result = read_block(config, args.block_key)
            if args.as_json:
                print(json.dumps(result, ensure_ascii=False))
            else:
                print(f"{result['path']} | {result['heading']} | {result['tier']} | "
                      f"version={result['version']} freshness={result['freshness']}\n\n{result['content']}")
        else:
            opts = {k: getattr(args, k) for k in ("limit", "scope", "folder", "tag", "version", "prefix", "raw", "debug")}
            if args.cmd == "context":
                try:
                    from scripts.memory_context import build_context
                except ModuleNotFoundError:
                    from memory_context import build_context
                if hasattr(sys.stdout, "reconfigure"):
                    sys.stdout.reconfigure(newline="\n")
                sys.stdout.write(build_context(config, args.query, budget_tokens=args.budget_tokens, **opts)["text"])
            else:
                rows = search(config, args.query, **opts)
                if args.as_json:
                    print(json.dumps(rows, ensure_ascii=False, separators=(",", ":")))
                else:
                    for row in rows:
                        print(f"{row['score']:>9} {row['block_key']} {row['path']} > {row['heading']}\n  {row['snippet']}")
                    if not rows:
                        print("no matches")
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
