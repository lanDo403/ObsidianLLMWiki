# ObsidianLLMWIKI Agent Contract

## Routing

- For code-local tasks, inspect this repository's source, architecture/ADR, tests
  and dependency/config files first. Do not search Wiki for every code operation.
- For domain/external knowledge, use memory search/context, then official docs if
  missing or stale. For time-sensitive questions, verify freshness and the current
  official source. Read project dependency versions before selecting knowledge.
- Prefer cheap deterministic work → targeted retrieval → optional local semantic
  fallback → official external source → LLM only when needed.

## Memory protocol

```bash
python scripts/memory_index.py status
python scripts/memory_index.py search "query" --json
python scripts/memory_index.py read "<block_key>"
python scripts/memory_index.py context "query" --budget-tokens 1200
python scripts/memory_index.py search "query" --version 4 --scope wiki --json
```

On missing/incompatible index, run build/update or scripts/migrate.py once.
Ordinary recall prefers canonical Wiki, falls back to atomic notes, excludes raw
and system. Use --scope raw/all only for explicit evidence/provenance work;
--raw means FTS query syntax. Read only needed blocks. Check freshness and version.
No LLM query expansion/reranking, automatic web search or recursive graph traversal.
Refresh with update after external Markdown edits. vault_writer refreshes an
existing index automatically; the first build stays explicit.

After official research, promote only durable, reusable, verified knowledge.
Search for existing concepts, ingest focused evidence, then explicitly compile/update.
Do not save every web result or compile after every small lookup.

## Write rules and invariants

- Markdown is authoritative. SQLite is rebuildable derived state outside the vault.
- raw/ is immutable. Generated knowledge is staged and written through vault_writer.
- Never bypass WIKI_LINKS_LOST or reroute an update to evade its snapshot guard.
- Keep Wiki and atomic-note workflows isolated; retain [[wikilinks]].
- Never replace user config.toml, delete vault content or erase runtime registries.
  Migration adds absent defaults only; no content migration is needed.
- Documentation ingestion: wiki_ingest.py <slug> <file> --mode documentation;
  preserves sections, uses no LLM, and does not automatically compile endpoints.
- Source checks: source_watcher.py check/status. A check never verifies Wiki prose
  or automatically merges changed content.

## Work and validation

Preserve existing commands and others' changes. Default to one agent.
Before edits, define invariants and run relevant baseline tests. Use targeted tests
per phase and the full existing/new suite for shared schema/interface changes.
Record decisions in DECISIONS.md and completed phases/evidence in TASKS.md.
Validate with python -m pytest -q; retrieval regression:
python benchmarks/retrieval_benchmark.py. Never use a real vault as a test fixture.

## Read on demand

- [Retrieval, budgets, versions, semantic extension](rules/memory_retrieval.md)
- [Sources, freshness and documentation ingestion](rules/source_registry.md)
- [Wiki schema](rules/wiki_schema.md), [compile](rules/wiki_compile.md),
  [incremental update](rules/wiki_update.md)
- [Other import workflows and NotebookLM authentication](docs/agent-workflows.md)
- [Original requirements](docs/MEMORY_PLAN.md), [progress](TASKS.md),
  [decisions](DECISIONS.md)
