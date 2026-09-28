---
name: obsidian-llmwiki
description: >-
  Search and maintain Obsidian Wiki memory: compact block search, targeted read,
  budgeted context, source freshness checks, Wiki compilation and updates.
  Also import/atomize notes, contacts, docx and NotebookLM material. Use for vault
  recall and these workflows; code-local questions start with repository files.
when_to_use: >-
  search the vault; what do we know about; recall; найди в вики; вспомни;
  проверь источники; compile wiki; update wiki; process note; import document;
  process contacts; process notebook; run research in notebook.
---

# ObsidianLLMWIKI adapter

Use [AGENTS.md](AGENTS.md) as the primary contract. Load detailed rules only for
the requested workflow. Code-local questions start with repository files.

## Execution location

When installed globally, `${CLAUDE_SKILL_DIR}/repository` points to this repository.
Resolve it before running commands below. For a repo-local SKILL.md, use the
directory containing this file. Use that repository's virtualenv Python
(`.venv/bin/python` on Linux, `.venv/Scripts/python.exe` on Windows), or an activated
environment with its dependencies. The examples abbreviate this interpreter as
`python`. Run commands with this repository as the working directory; never look
for scripts/config in the unrelated project that requested a memory lookup.
Resolve rules/docs links against this skill directory, not the calling project's cwd.

## Recall and external knowledge

```bash
python scripts/memory_index.py search "query" --json
python scripts/memory_index.py read "<block_key>"
python scripts/memory_index.py context "query" --budget-tokens 1200
python scripts/memory_index.py search "query" --version 4 --scope wiki --json
python scripts/memory_index.py search "query" --scope raw --json
```

Build once with memory_index.py build or migrate.py. Default scope auto prefers
canonical Wiki, then notes. Raw/system do not enter normal recall. --raw retains
FTS syntax semantics. Inspect version/freshness; use official docs on a miss or
stale evidence. Optional semantic providers are local and disabled by default.
See [retrieval rules](rules/memory_retrieval.md).

## Wiki and sources

```bash
python scripts/wiki_init.py demo --mode corpus --title "Domain Wiki"
python scripts/wiki_ingest.py demo article.md --kind articles --no-compile
python scripts/wiki_ingest.py demo api.md --mode documentation --source-id example-api-v5 --version V5
python scripts/wiki_compile.py demo --since-last-compile
python scripts/wiki_update.py demo raw/docs/<file>.md
python scripts/wiki_lint.py demo --strict
python scripts/source_watcher.py check
python scripts/source_watcher.py status --json
```

Documentation stays structured raw evidence until explicitly selected for
promotion. Watcher performs conditional HTTP/hash/diff without LLM or automatic
updates. Promote only verified reusable durable knowledge. Preserve raw,
provenance and every existing wikilink through the compile/writer guards.
Read [sources](rules/source_registry.md) and rules/wiki_*.md for these operations.

## Existing import workflows

- Documents: process.py "Document.docx".
- Personal notes: process_note.py "Note Title".
- Contacts: process_contacts.py "Contacts Note".
- NotebookLM: process_notebook.py "<id>" or fetch_notebook.py "<id>".
- Research: research_notebook.py run "<id>" "<query>".
- Dedup: dedup_vault.py --dry-run; setup: doctor.py.
- Automated writes use --non-interactive with an explicit conflict policy.

Detailed commands, prerequisites and NotebookLM login recovery are in
[agent-workflows.md](docs/agent-workflows.md). No browser login is needed while
the saved NotebookLM session remains valid.
