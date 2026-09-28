# Import workflows and NotebookLM reference

AGENTS.md is the current contract. Memory and source protocols live in rules/memory_retrieval.md and rules/source_registry.md. The commands below retain the existing import workflows. Documentation mode skips automatic compilation.

## Workflow Mapping
Common user intent -> command:

- "process/import this .docx" -> `python3 scripts/process.py "<file>.docx"`
- "process/import this .docx without prompts" -> `python3 scripts/process.py "<file>.docx" --non-interactive --on-conflict skip`
- "process notebook" / "обработай ноутбук" / "pull from NotebookLM" -> `python3 scripts/process_notebook.py "<notebook_id>"`
- "process notebook with sources" -> `python3 scripts/process_notebook.py "<notebook_id>" --include-sources`
- "process notebook without prompts" -> `python3 scripts/process_notebook.py "<notebook_id>" --non-interactive --on-conflict skip`
- "fetch notebook notes only" (no atomization) -> `python3 scripts/fetch_notebook.py "<notebook_id>"`
- "process/enrich/atomize this note" -> `python3 scripts/process_note.py "<note title or path>"`
- "process contacts" / "обработай контакты" -> `python3 scripts/process_contacts.py "<note title or path>"`
- "atomize this note without prompts" -> `python3 scripts/process_note.py "<note title or path>" --mode atomize --non-interactive --on-conflict skip`
- "show duplicate candidates" -> `python3 scripts/dedup_vault.py --dry-run --skip-claude`
- "run full dedup review" -> `python3 scripts/dedup_vault.py`
- "write staged notes without prompts" -> `python3 scripts/vault_writer.py --staging "<dir>" --atom-plan "<plan.json>" --non-interactive --on-conflict skip`
- "run dedup without prompts" -> `python3 scripts/dedup_vault.py --non-interactive --decision skip`
- "render markdown from atom plan" -> `python3 scripts/generate_notes.py "<plan.json>"`
- "check setup/prereqs" -> `python3 scripts/doctor.py`
- "run deep research in notebook" / "запусти ресерч в ноутбук" / "deep research into notebook" -> `python3 scripts/research_notebook.py run "<notebook_id>" "<query>"`
- "dry-run research import" -> `python3 scripts/research_notebook.py run "<notebook_id>" "<query>" --dry-run`
- "dedupe notebook sources" / "почисти дубли источников в ноутбуке" -> `python3 scripts/research_notebook.py dedupe "<notebook_id>" --dry-run`
- "clean up broken research import" -> `python3 scripts/research_notebook.py dedupe "<notebook_id>" --include-error --non-interactive`
- "init wiki" / "создай вики <slug>" -> `python3 scripts/wiki_init.py <slug> --mode project --title "<title>"` (+ `--lang ru|en` if user specifies language; otherwise `[wiki].default_lang` from `config.toml`)
- "ingest <path> в вики <slug>" / "залей в вики" -> `python3 scripts/wiki_ingest.py <slug> <path> --kind <kind>`
- "compile wiki" / "собери вики <slug>" -> `python3 scripts/wiki_compile.py <slug> --since-last-compile`
- "update wiki page" / "обнови вики <slug> <raw-path>" -> `python3 scripts/wiki_update.py <slug> raw/docs/<file>.md`
- "lint wiki" / "проверь вики" -> `python3 scripts/wiki_lint.py [<slug>] --strict`
- "search notes/vault" / "найди в заметках" / "what do we know about X" / "что мы знаем про X" / "вспомни" / "recall" -> `python3 scripts/memory_index.py search "<query>" --json`
- "rebuild/refresh memory" / "перестрой индекс памяти" / "memory status" -> `python3 scripts/memory_index.py build | update | status`

## CLI Contracts
- `scripts/process.py`
  - Input: `.docx` filename or atom plan JSON with `--from-plan`
  - Safe automation flags for final vault writes: `--non-interactive --on-conflict skip|overwrite`
  - Output: summary to stdout, diagnostics to stderr
- `scripts/process_note.py`
  - Input: note title, filename, or absolute path
  - Safe automation flags for atomize writes: `--non-interactive --on-conflict skip|overwrite`
  - Output: writes updated/generated notes into the vault
- `scripts/process_contacts.py`
  - Input: note title, filename, or absolute path (containing contacts)
  - Safe automation flags: `--non-interactive --on-conflict skip|overwrite`
  - Output: writes individual contact notes + MOC to vault
- `scripts/generate_notes.py`
  - Input: atom plan JSON
  - Output: staging directory path to stdout
- `scripts/vault_writer.py`
  - Input: `--staging`, optional `--atom-plan`
  - Safe automation flags: `--non-interactive --on-conflict skip|overwrite`
  - Output: summary to stdout and stderr
- `scripts/dedup_vault.py`
  - Input: vault notes from configured vault path
  - Safe automation flags: `--non-interactive --decision merge|keep|skip`
  - Output: diagnostics to stderr, updates vault on confirmed merges
- `scripts/research_notebook.py`
  - Input: `run <notebook_id> "<query>"` or `dedupe <notebook_id>`
  - `run` flags: `--mode fast|deep` (default deep), `--source web|drive`, `--max-sources N`, `--poll-interval N`, `--poll-timeout N`, `--profile NAME`, `--dry-run`, `--non-interactive`
  - `dedupe` flags: `--key auto|url|title`, `--include-error`, `--dry-run`, `--non-interactive`, `--profile NAME`
  - Behavior: drives `notebooklm-py` Python API directly, so IMPORT_RESEARCH is a one-shot call with no retry-on-timeout duplication. Dedupe subcommand groups existing sources by URL (or title) and deletes everything except the first occurrence per group, optionally also removing sources in error state.
  - Output: progress + summary on stderr; dry-run plan or empty-plan JSON on stdout
- `scripts/wiki_init.py`
  - Input: `<slug>` plus `--mode {project|corpus} --title --description --force --non-interactive`
  - Exit codes: 0 (created), 1 (bad args), 2 (`WIKI_ALREADY_EXISTS`)
  - Output: confirmation line on stdout; no LLM is called
- `scripts/wiki_ingest.py`
  - Input: `<slug> <file-or-dir-or-url>` plus `--kind {articles|docs|transcripts|assets}`, `--label`, `--no-compile`, `--backend`
  - Exit codes: 0, 1, 2 (`WIKI_PROJECT_NOT_FOUND`), 3 (`WIKI_INGEST_EMPTY`), 4
  - Behavior: writes one .md per input under `raw/<kind>/<date>-<slug>.md`; chains `wiki_compile.py --since-last-compile` unless `--no-compile`
- `scripts/wiki_compile.py`
  - Input: `<slug>` plus `--since-last-compile`, `--raw-only <glob>`, `--update-only`, `--dry-run`, `--on-conflict {skip|overwrite|rename|ask}`, `--backend {auto|claude|codex}`, `--timeout-seconds N`
  - Exit codes: 0, 1, 2 (`WIKI_PROJECT_NOT_FOUND`), 3 (`WIKI_VALIDATION_FAILED`), 4 (`WIKI_RAW_LIMIT_EXCEEDED`), 5 (`WIKI_LINKS_LOST`)
  - Behavior: snapshots existing wiki, calls LLM via `rewrite_backend`, validates ChangeSet, materializes pages into staging, subprocesses `vault_writer.py`. The wikilink-preservation guard (exit 5) is the load-bearing safety property — never bypass it.
- `scripts/wiki_update.py`
  - Input: `<slug> <path-in-raw>` plus any flags forwarded to `wiki_compile.py`
  - Behavior: convenience wrapper for `wiki_compile.py <slug> --raw-only <path> --update-only`
- `scripts/wiki_lint.py`
  - Input: optional `<slug>` plus `--json --strict`
  - Exit codes: 0 (clean), 1 (`WIKI_LINT_FAILED`)
  - Read-only structural checks: meta/core pages present, frontmatter complete, wikilinks resolve, entities not duplicated, raw layout valid

## Recommended Agent Behavior
- Start with `python3 scripts/doctor.py` if setup is uncertain.
- Use `--dry-run` modes before destructive or high-impact operations.
- Prefer `--non-interactive` plus an explicit conflict/decision policy when running from an agent.
- Quote filenames with spaces or Cyrillic characters.
- When a task is unclear, inspect the relevant script help first.

## NotebookLM Auth Handling
- NotebookLM "login state" is a file on disk (`~/.notebooklm/storage_state.json` + `~/.notebooklm/browser_profile/`). Every run re-reads it; there is no in-memory session the agent needs to refresh. Once the user has logged in on this machine, subsequent runs go through without any browser prompt.
- `fetch_notebook.py` and `process_notebook.py` perform a pre-flight auth check (`check_auth_or_exit()`) before opening any network clients.
- Both scripts exit with code `2` and emit `NOTEBOOKLM_AUTH_REQUIRED` on stderr when the user is not authenticated OR when `notebooklm-py` is missing entirely.
- The canonical flow uses a **project venv** (`.venv/`) — system pip is blocked by PEP 668 on Arch/Manjaro/Debian. `scripts/notebooklm_setup.py` auto-detects venv and skips `--user` when inside one.
- Agent contract when `NOTEBOOKLM_AUTH_REQUIRED` is observed:
  1. Tell the user that dependencies will be installed automatically, but the actual browser login has to happen in a separate terminal window (because `notebooklm login` needs a real TTY to read the `ENTER` keypress; running it from Claude Code's shell aborts with `Aborted!`).
  2. Ensure a venv exists (create with `python3 -m venv .venv` if missing) and run:
     `.venv/bin/python scripts/notebooklm_setup.py --skip-login`
     This installs `notebooklm-py[browser]` and Playwright Chromium into the venv. Safe to run repeatedly and safe to run from a non-TTY shell (login is skipped).
  3. Ask the user to open a separate terminal window in the repo directory and run:
     `.venv/bin/notebooklm login`
     Sign in to Google in the Chromium window, wait for the NotebookLM homepage, return to that terminal and press ENTER. The session persists at `~/.notebooklm/storage_state.json`.
  4. When the user confirms, retry the original `process_notebook.py <notebook_id>` command. The preflight should now pass silently.
  5. Do not manually chain `pip install`, `playwright install`, and `notebooklm login` — always go through the setup script for steps 1–2.
  6. If the setup script exits non-zero, relay its stderr to the user and stop — do not attempt ad-hoc recovery. Exit code `3` specifically means "stdin is not a TTY and login was requested" — always invoke with `--skip-login` from the agent's shell.

- `scripts/notebooklm_setup.py`
  - Input: none (flags: `--skip-login`, `--reinstall`)
  - Behavior: auto-detects venv vs system Python; uses `pip --user` only outside a venv; refuses to launch `notebooklm login` on a non-TTY stdin
  - Output: diagnostics to stderr; exit 0 on success, 1 on install/login failure, 2 on missing auth file after login, 3 on missing TTY when login is requested
  - Use this as the canonical entrypoint whenever `NOTEBOOKLM_AUTH_REQUIRED` is observed

- `scripts/fetch_notebook.py`
  - Input: NotebookLM `notebook_id`
  - Optional flags: `--include-sources`, `--include-mindmap`, `--profile <name>`, `-o <path>`
  - Output: parsed-JSON path to stdout (compatible with `atomize.py`); diagnostics to stderr
  - Prereq: `notebooklm-py` installed + `notebooklm login` completed

- `scripts/process_notebook.py`
  - Input: NotebookLM `notebook_id`
  - Optional flags: `--include-sources`, `--include-mindmap`, `--profile <name>`
  - Safe automation flags: `--non-interactive --on-conflict skip|overwrite`
  - Output: summary to stdout; runs the full fetch -> atomize -> generate -> write pipeline
  - Prereq: same as `fetch_notebook.py`, plus `claude`/`codex` CLI for the rewrite step

## Why research_notebook.py exists

The upstream `notebooklm-py` CLI command
`notebooklm source add-research "<query>" --mode deep --import-all`
wraps `client.research.import_sources()` in an exponential-backoff retry loop that kicks in on `RPCTimeoutError`. Each retry re-imports the full source list without deduping against what's already in the notebook, so a single IMPORT_RESEARCH RPC that times out N times leaves N× duplicates. We hit this concretely: 78 research sources, 4 retries, 392 imported sources instead of ~78. Tracked upstream as `teng-lin/notebooklm-py` issue #241 (open).

Upstream explicitly documents the escape hatch in `src/notebooklm/cli/helpers.py::import_with_retry`:

> This is intentionally CLI-only policy. Library consumers calling `client.research.import_sources()` directly still get one-shot behavior.

`scripts/research_notebook.py` takes that escape hatch. It drives the research flow through the `notebooklm-py` Python API, so IMPORT_RESEARCH is a single call with no silent retry — duplication literally cannot happen through this code path. The `dedupe` subcommand is the cleanup for notebooks already poisoned by the CLI bug.

**Agents must prefer `research_notebook.py run` over `notebooklm source add-research --import-all`** until the upstream bug is fixed.
