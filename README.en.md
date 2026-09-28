<div align="center">

# ObsidianLLMWIKI

**Full NotebookLM control from Claude Code / Codex, .docx import with Zettelkasten atomization, and a compiled LLM Wiki layer that grows by explicit merge, plus a zero-dependency FTS5 full-text memory over the whole vault — all programmatic, all into your Obsidian vault.**

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)
![Claude Code](https://img.shields.io/badge/Claude%20Code-Skill-blueviolet)
![Codex](https://img.shields.io/badge/Codex-AGENTS.md-green)
![NotebookLM](https://img.shields.io/badge/NotebookLM-API%20Control-orange)
![LLM Wiki](https://img.shields.io/badge/LLM%20Wiki-Compiled%20Knowledge-teal)
![FTS5 Memory](https://img.shields.io/badge/FTS5-Vault%20Memory-9cf)

---

🇷🇺 **[Читать по-русски → README.md](README.md)**

</div>

## Block memory in this fork

Use repository code first for local tasks, then local memory for domain knowledge,
then official docs when missing/stale. Search is block-level SQLite FTS5 with compact
metadata and legacy JSON keys. Default scope auto prefers canonical Wiki, falling
back to notes; raw requires --scope raw/all and system metadata is excluded.
--raw still means FTS syntax. Aliases/tags are searchable; --version 4 matches 4.x.

```bash
python scripts/migrate.py
python scripts/memory_index.py search "bybit orderbook reconnect" --json
python scripts/memory_index.py read "<block_key>"
python scripts/memory_index.py context "bybit orderbook reconnect" --budget-tokens 1200
python scripts/source_watcher.py check
python scripts/wiki_ingest.py demo api.md --mode documentation --source-id example-api-v5 --version V5
```

Context deduplicates and budgets the entire output using ceil(UTF-8 bytes / 3),
not a model-specific tokenizer. Registry/provenance tracks sources, version,
applies_to, last_verified and freshness. Watcher uses conditional HTTP/hash/diff
without LLM or automatic Wiki edits. Documentation ingestion preserves headings
and skips compilation. A local semantic provider interface is optional and disabled;
no embedding backend ships. Existing import/compile/update commands remain available.

See [retrieval](rules/memory_retrieval.md), [sources](rules/source_registry.md),
[validation](docs/VALIDATION.md), [tasks](TASKS.md) and [decisions](DECISIONS.md).
Regression benchmark: `python benchmarks/retrieval_benchmark.py` (synthetic, no LLM).
Real tests on Linux and with Claude have not been conducted.

---

## What is this

ObsidianLLMWIKI turns Claude Code and Codex into a full remote control for NotebookLM and your Obsidian vault. Run deep research, manage sources, pull notes from notebooks — all through a single natural-language command. It also imports `.docx` from Google Drive and atomizes them into Zettelkasten notes with MOC, tags, and wikilinks. On top of all that — an isolated **LLM Wiki** layer: a Karpathy-style compiled knowledge base that grows by explicit merge, never recomputed on every query. Search across all of it is the **FTS5 memory**: a local full-text index of the whole vault (notes, wiki, NotebookLM imports) that refreshes itself after every write.

### What you can do with NotebookLM

| Capability | Command |
|---|---|
| Run deep/fast research into a notebook | `research_notebook.py run <id> "<query>"` |
| Safe one-shot source import (no duplicates) | same, bypasses [upstream CLI bug](https://github.com/teng-lin/notebooklm-py/issues/241) |
| Clean up duplicate and error-state sources | `research_notebook.py dedupe <id>` |
| Pull all notes → atomic notes in Obsidian | `process_notebook.py <id>` |
| Pull notes + source fulltext + mind maps | `process_notebook.py <id> --include-sources --include-mindmap` |
| Download notes without atomization | `fetch_notebook.py <id>` |

All NotebookLM interaction goes through the Python API (`notebooklm-py`), not the CLI — single call, no retry duplication. Auth is a file on disk, no interactive browser on every run.

### What else it does

- Import `.docx` from Google Drive → atomic notes + MOC in vault
- Enrich and atomize existing vault notes
- Process networking contacts → individual contact cards + Networking MOC
- Deduplicate vault by semantic similarity
- Automatic tag taxonomy and cross-note wikilinks
- **LLM Wiki** — an isolated compiled-knowledge layer (project / corpus modes, RU/EN templates, hard guard that preserves existing `[[wikilinks]]` on merge, see [section below](#llm-wiki))
- **FTS5 Memory** — full-text search over the whole vault on stdlib SQLite: `memory_index.py search "query" --json`, bm25 ranking with snippets, incremental index kept outside the vault

## Install

```bash
git clone https://github.com/lanDo403/ObsidianLLMWIKI.git
cd ObsidianLLMWIKI
bash install.sh --vault-path "/path/to/your/obsidian/vault"
```

Or copy this prompt into Claude Code or Codex — it handles everything:

```
Clone https://github.com/lanDo403/ObsidianLLMWIKI.git and run bash install.sh --vault-path "/path/to/vault" in the cloned directory.
```

The installer will:
- Check Python 3.10+ and install dependencies (`python-docx`, `pyyaml`)
- Create `config.toml` with your vault path
- Register the skill globally in `~/.claude/skills/obsidian-llmwiki/`
- Add a helper block to `~/.claude/CLAUDE.md`

After installation the skill works **from any directory**.

## Upgrade

```bash
cd ObsidianLLMWIKI && git pull && bash install.sh
```

The installer is idempotent: skill symlinks update themselves, `migrate.py`
appends new config sections (e.g. `[memory]`) and deploys the FTS5 memory
index. `config.toml` and the vault are never overwritten.

### Install modes

| Mode | Flag | What it does |
|------|------|-------------|
| **claude** (default) | `--mode claude` | Deps + config + global skill in `~/.claude/` |
| **codex** | `--mode codex` | Deps + config + verify `AGENTS.md` |
| **local** | `--mode local` | Deps + config only |

## How to use

After installation, just tell Claude Code what you need:

```
Process the document "Second Brain Architecture.docx"
```

```
Process note "My thoughts on productivity"
```

```
Download my files from Google Drive and split into atomic notes
```

```
Check setup — run doctor
```

More examples:

| What to say | What happens |
|-------------|-------------|
| `process MyDocument.docx` | Full cycle: download → parse → atomize → write to vault |
| `process MyDocument.docx --non-interactive --on-conflict skip` | Same, no prompts (for automation) |
| `process note "Title"` | Enrich or atomize an existing note |
| `process contacts "My Contacts"` | Split contacts note → individual cards + Networking MOC |
| `process_note "Note" --mode atomize` | Force atomization of a note |
| `dedup --dry-run` | Show duplicates without changes |
| `run research in notebook "<id>" "<query>"` | Deep research into NotebookLM via API |
| `dedupe notebook sources "<id>"` | Deduplicate sources in a notebook |
| `init wiki "<slug>"` | Scaffold a new LLM Wiki-space (project/corpus, RU/EN) |
| `ingest into wiki "<slug>" <path>` | Drop an article / folder into `raw/<kind>/` |
| `compile wiki "<slug>"` | Merge raw into pages (with `[[wikilink]]`-preservation guard) |
| `lint wiki "<slug>"` | Check frontmatter, link resolution, contour isolation |
| `search notes "<query>"` | FTS5 search across the whole vault (notes + wiki), bm25 + snippets |
| `rebuild memory index` | Full FTS5 index rebuild (`memory_index.py build`) |

## NotebookLM: full programmatic control

ObsidianLLMWIKI gives Claude Code / Codex full programmatic control over NotebookLM. Instead of manual work in the web UI, you tell the agent what you need and it runs research, manages sources, pulls notes, and atomizes them into Obsidian. The entire API layer uses `notebooklm-py` as a library (not the CLI), which guarantees one-shot behavior with no retry duplication.

### First-time login

The Google session is stored in `~/.notebooklm/storage_state.json` and reused — you only log in once.

1. Create a venv and install the dependencies:

```bash
python3 -m venv .venv
.venv/bin/python scripts/notebooklm_setup.py --skip-login
```

The script installs `notebooklm-py[browser]` and the Playwright Chromium browser into the active venv. The `--skip-login` flag separates installation from the interactive login — we handle the login manually in the next step because `notebooklm login` needs a real TTY.

> **Arch / Manjaro / Debian:** the system `pip` is locked down by PEP 668, so a venv is required. The script auto-detects venv and no longer passes `--user`. Running it against the system Python without a venv will fail fast with guidance to create one.

2. **Open a separate terminal window** and run:

```bash
cd /path/to/ObsidianLLMWIKI
.venv/bin/notebooklm login
```

> Do not run this via the `!` prefix inside Claude Code or Codex — such a session has no interactive stdin, and `notebooklm login` will abort with `Aborted!` the moment it asks you to press `ENTER`.

3. Sign in to Google in the Chromium window that opens and wait until the NotebookLM homepage loads.

4. Return to the terminal and press **ENTER** — `storage_state.json` is saved.

Verify: the file `~/.notebooklm/storage_state.json` should now exist.

### Usage

```bash
.venv/bin/python scripts/process_notebook.py <notebook_id>
```

`<notebook_id>` is the last URL segment of your notebook: `https://notebooklm.google.com/notebook/<notebook_id>`. Flags `--include-sources` and `--include-mindmap` add indexed source fulltext and mind maps. For multi-account setups use `--profile <name>`.

### Running deep research

```bash
.venv/bin/python scripts/research_notebook.py run "<notebook_id>" "<query>"
.venv/bin/python scripts/research_notebook.py run "<notebook_id>" "<query>" --dry-run
```

Options: `--mode fast|deep` (default `deep`), `--source web|drive`, `--max-sources N`, `--poll-interval` / `--poll-timeout`, `--profile <name>`, `--dry-run`.

> `research_notebook.py` drives `notebooklm-py` as a library — strictly one-shot. The raw CLI (`notebooklm source add-research --import-all`) duplicates sources on timeout ([upstream bug #241](https://github.com/teng-lin/notebooklm-py/issues/241)).

Clean a notebook from duplicates and error-state sources:

```bash
.venv/bin/python scripts/research_notebook.py dedupe "<notebook_id>" --dry-run
.venv/bin/python scripts/research_notebook.py dedupe "<notebook_id>" --include-error --non-interactive
```

`dedupe` groups sources by URL (title as fallback) and keeps the first occurrence; always start with `--dry-run`.

## How it works

**NotebookLM → Obsidian:**
```
NotebookLM API → fetch notes/sources/mindmaps → atomize (Claude) → generate → write → Obsidian vault
```

**Deep Research → NotebookLM:**
```
research_notebook.py → notebooklm-py API (one-shot) → poll → import sources → dedupe
```

**Google Drive → Obsidian:**
```
Google Drive → rclone fetch → parse .docx → atomize (Claude) → generate → write → Obsidian vault
```

**Personal notes:**
```
Vault note → detect mode → rewrite (Claude) → write back
```

**Memory (FTS5):**
```
any vault write → vault_writer → FTS5 index auto-refresh (outside the vault)
search: memory_index.py search "query" → bm25 + snippets → top notes
```

- **Enrich** — short note → adds tags, wikilinks, expands text (1 → 1)
- **Atomize** — long note → splits into atomic notes + MOC (1 → N)
- **Contacts** — networking note → individual contact cards + Networking MOC (1 → N)

## MOC + Zettelkasten

**MOC (Map of Content)** — a navigation hub collecting links to all atomic notes from a document. One MOC per document.

**Atomic notes** — one idea, 150–600 words, fully self-contained. Connected via `[[wikilinks]]` to each other and to the MOC.

**FTS5 Memory** (`scripts/memory_index.py`) — full-text search over the whole vault on SQLite FTS5: zero dependencies (stdlib), fully local, the index lives outside the vault and refreshes automatically after every write. The search layer for agents: `python3 scripts/memory_index.py search "query" --json`.

**Smart Connections** *(legacy)* — the previous search layer built on local embeddings. Superseded by the FTS5 memory: the pipeline no longer uses or requires it. You may keep the plugin for semantic suggestions in the Obsidian UI.

## LLM Wiki

A third knowledge layer on top of atomic notes — a **compiled wiki** in the Karpathy style. Not RAG (not recomputed on every query) and not a flat note pile (pages are interlinked and typed). It is a long-lived knowledge base that **accumulates** through explicit merge on each ingest: existing `[[wikilinks]]` must be preserved, otherwise compile fails with `WIKI_LINKS_LOST` (exit 5).

The wiki lives in an isolated folder inside the vault — `<vault>/<wiki_folder>/<slug>/` (default `LLM Wiki/`). Atomic notes never appear here, and `wiki_compile.py` never reads notes outside its own wiki-space.

**Two modes:**

- **project** — fixed core pages: overview, architecture, components, workflows, goals-and-roadmap, glossary, open-questions. Use for documenting a single coherent system.
- **corpus** — only entities/concepts grow as raw is ingested. Use for a reading-list / research knowledge base.

**Workflow:**

```bash
# 1. Create an empty wiki-space (+ optional --lang ru|en for template language)
python3 scripts/wiki_init.py demo --mode project --title "Demo Project"

# 2. Ingest raw inputs (articles, docs, transcripts)
python3 scripts/wiki_ingest.py demo path/to/article.md --kind articles
python3 scripts/wiki_ingest.py demo path/to/notes/ --kind docs

# 3. Compile (the LLM merges raw into pages)
python3 scripts/wiki_compile.py demo --since-last-compile

# 4. Lint the structure
python3 scripts/wiki_lint.py demo --strict

# Incremental single-page update from one new raw input
python3 scripts/wiki_update.py demo raw/docs/new-file.md
```

All scripts go through the single `vault_writer.py` writer — atomic pipelines and the wiki share the same write boundary.

**Template language.** `wiki_init.py` supports `--lang en` and `--lang ru`. The default comes from `[wiki].default_lang` in `config.toml` (falls back to `en`). Only the on-disk prose of SCHEMA.md, index.md, log.md, raw/_README.md, and core-page stubs is affected — structure and frontmatter contract are identical across languages. Wiki-spaces in different languages coexist in the same vault without conflicts.

## Templates

The `templates/` directory contains a starter vault structure:

- `Notes/Atomic Note Example.md` — example atomic note
- `MOCs/Topic Map - MOC.md` — example MOC

## Configuration

The project slug is `obsidian-llmwiki`, used for the skill and cache/staging
directories. Select the backend with `OBSIDIAN_LLMWIKI_BACKEND` (`claude` or
`codex`). When adopting the new name, update this variable and the skill
registration. Existing external directories are not automatically removed;
explicit `db_dir`, `state_dir` and `staging_dir` values remain authoritative.

`config.toml` (created during installation, never committed):

```toml
[vault]
vault_path = "/path/to/your/obsidian/vault"   # required, absolute path
notes_folder = "Research & Insights"           # atomic notes destination
moc_folder = "Guides & Overviews"              # MOC files destination
source_folder = "Sources"                       # source document references

[rclone]
remote = "gdrive:"                              # rclone remote name
staging_dir = "/tmp/obsidian-llmwiki/staging"                # temporary staging area
```

## Requirements

- Python 3.10+ (3.11+ recommended)
- [rclone](https://rclone.org/) configured with Google Drive access (for `.docx` import)
- [Claude Code](https://claude.ai/code) or [Codex](https://github.com/openai/codex)
- `vault_path` in `config.toml` — absolute path to your Obsidian vault
- No separate database needed: SQLite with FTS5 ships inside Python (the stdlib `sqlite3` module) — it powers the FTS5 memory; `doctor.py` verifies support

**Required Obsidian plugins:**

- [Local REST API](https://github.com/coddingtonbear/obsidian-local-rest-api) — HTTP interface for reading/writing vault contents. Required by MCP Obsidian
- [MCP Obsidian](https://github.com/MarkusPfundstein/mcp-obsidian) — MCP server connecting Claude Code to Obsidian via Local REST API. Depends on Local REST API

**Legacy (no longer required):**

- [Smart Connections](https://github.com/brianpetro/obsidian-smart-connections) — the previous semantic-search layer (local embeddings). Superseded by the FTS5 memory (`scripts/memory_index.py`); the old config in `templates/.smart-env/` is kept for existing installs

**Related projects:**

- [NotebookLM++](https://github.com/howdeploy/notebooklmplusplus) — Chrome extension for [NotebookLM](https://notebooklm.google.com) that adds bulk import: web pages, YouTube videos, Shorts, playlists, entire channels, comments, and PDF page snapshots. If you use NotebookLM as a second brain alongside Obsidian, this extension covers the same workflow on the Google side: quickly gather sources into a notebook for AI analysis

## Project structure

```
ObsidianLLMWIKI/
├── scripts/
│   ├── process.py            # Main pipeline (.docx → vault)
│   ├── process_note.py       # Personal note processing (enrich/atomize)
│   ├── process_contacts.py   # Contacts → individual cards + Networking MOC
│   ├── process_notebook.py   # NotebookLM notebook → atomic notes (full pipeline)
│   ├── fetch_notebook.py     # Pull notes from NotebookLM (without atomization)
│   ├── research_notebook.py  # Deep/fast research + source deduplication in NotebookLM
│   ├── notebooklm_setup.py   # Install notebooklm-py + Playwright (one-shot)
│   ├── fetch_docx.sh         # Download from Google Drive
│   ├── parse_docx.py         # .docx → JSON
│   ├── atomize.py            # JSON → atom plan (via Claude)
│   ├── generate_notes.py     # Plan → .md files
│   ├── vault_writer.py       # Staging → vault (with deduplication)
│   ├── dedup_vault.py        # Find and merge duplicate notes
│   ├── scan_vault.py         # Scan existing vault notes
│   ├── rewrite_backend.py    # Semantic rewrite backend (Claude CLI)
│   ├── config.py             # Configuration loader
│   ├── doctor.py             # Environment check
│   ├── memory_index.py       # FTS5 memory — index/search the whole vault (build/update/search)
│   ├── migrate.py            # Idempotent install upgrade (config + FTS5 index)
│   ├── wiki_init.py          # LLM Wiki — create an empty wiki-space
│   ├── wiki_ingest.py        # LLM Wiki — raw input intake (articles/docs/transcripts/assets)
│   ├── wiki_compile.py       # LLM Wiki — main compile pipeline
│   ├── wiki_update.py        # LLM Wiki — incremental single-page update
│   ├── wiki_lint.py          # LLM Wiki — read-only structural check
│   └── wiki_models.py        # LLM Wiki — ChangeSet / WikiPage / validation
├── rules/
│   ├── atomization.md        # Atomization rules
│   ├── taxonomy.md           # Tag taxonomy rules
│   ├── personal_notes.md     # Personal note processing rules
│   ├── contacts.md           # Contact note processing rules
│   ├── wiki_schema.md        # LLM Wiki — on-disk contract
│   ├── wiki_compile.md       # LLM Wiki — LLM contract for compile
│   └── wiki_update.md        # LLM Wiki — incremental merge semantics
├── templates/                # Starter vault structure (+ templates/wiki/ for LLM Wiki)
├── tests/                    # Regression tests
├── docs/                     # Agent-facing documentation
├── AGENTS.md                 # Agent contract (Claude Code + Codex)
├── SKILL.md                  # Claude adapter
├── SKILL_PERSONAL.md         # Prompt header for personal note processing
├── SKILL_CONTACTS.md         # Prompt header for contact processing
├── tags.yaml                 # Canonical tag list
├── config.example.toml       # Configuration template
├── install.sh                # Installer with global skill registration
└── requirements.txt          # Python dependencies
```

## License

MIT — see [LICENSE](LICENSE).
