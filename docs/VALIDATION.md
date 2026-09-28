# Validation — 2026-09-23

## Claude follow-up — finalized 2026-09-24

CLAUDE.md now imports AGENTS.md; SKILL.md uses documented discovery metadata and
specifies the repository/virtualenv execution location. install.sh exposes the
shared contract, rules, docs and repository to the installed skill.

Before the user stopped checks, the targeted run had 55 passed; the isolated
registration test failed on MSYS filesystem permissions. An escalated retry was
rejected by automatic approval review. The registration test is now POSIX-only.
No checks were rerun after that restriction, and no real Claude session or global
skill installation is claimed as verified. User profile settings were not changed.
The full-suite result below belongs to the preceding memory implementation.

Upstream base: `ddc40c68941d66943824c668ed192aced866fce8`.
Platform: Windows, CPython 3.14, local `.venv`; pytest 9.1.1, PyYAML 6.0.3.
No new production dependency was added. Tests use temporary synthetic vaults;
neither user config nor a real vault was created/modified for validation.

## Tests and CLI

| Check | Result |
|---|---|
| Original baseline before code edits | 192 passed, 23 failed |
| Final full suite | **359 passed**, 0 failed (3.72 s) |
| Existing tests retained | All 215; no regressions removed |
| CLI search → read → context via subprocess | Passed; complete stdout 158/1200 estimated tokens |
| Help: memory_index, source_watcher, wiki_ingest, wiki_compile, wiki_lint, vault_writer, process, process_note | Passed |
| Git whitespace check | Passed (normal repository CRLF conversion settings) |

Baseline failures were Windows separators in memory results, Wiki snapshots,
raw selection and lint. POSIX vault-relative identifiers fix those failures
without changing Markdown content. Final suite also covers aliases/tags, tier
priority, raw exclusions, block hierarchy/splitting, fenced code, targeted read,
hard context budget, dedup, rename/delete, transactional rebuild, version filters,
local semantic gating, migration preservation/idempotency/failed publication,
provenance, documentation ingestion and raw/link guards.

Source tests use deterministic fake HTTP responses: 304, ETag/Last-Modified, same
hash, changes, errors, size/time bounds, state locking and freshness. They do not
contact live documentation sites or invoke an LLM. Source subsystem implementation
and tests were delegated to one independent agent; integration remained with the
main agent.

Local logs (ignored by Git): `.validation/baseline.txt`, `retrieval.txt`,
`integration-final.txt`, `sources.txt`, `sources-integration.txt`, `final-tests.txt`,
`cli-smoke.txt`, `diff-check.txt`. The final result applies to all code changes,
including atomic config replacement. Subsequent edits are documentation only.

## Reproducible fixture benchmark

Command: `python benchmarks/retrieval_benchmark.py`.
Five synthetic notes, nine blocks: canonical Wiki, duplicate atomic note and raw
source, unrelated note, system metadata. The API prose is fictional test content.

| Metric | Observed |
|---|---:|
| Top result | Bybit Orderbook Synchronization → Reconnect |
| Top tier | canonical_wiki |
| Default results / raw results | 1 / 0 |
| Full canonical page | 14,234 characters |
| Compact search JSON | 602 characters |
| Targeted block | 166 characters |
| Search text reduction | 95.8% |
| Complete context | 158 / 1200 estimated tokens |
| Warm search median, 30 runs | 6.705 ms |
| LLM calls / network calls | 0 / 0 |

Timing is a local observation on this tiny fixture, not a latency guarantee or
general RAG performance claim. The deterministic relevance/budget/size assertions
are the regression contract. Full output: `.validation/benchmark.json`.
Example CLI outputs: `.validation/example-search.json`, `.validation/example-context.txt`.

## Limits and deferred work

- Token counts use ceil(UTF-8 bytes / 3), not an exact model tokenizer. All emitted
  context text fits that estimate; large indivisible blocks may be omitted.
- Markdown parsing covers headings, fences and paragraph boundaries, not all CommonMark.
  Default lexical confidence means an all-term FTS match, not semantic correctness.
- Search reads the last indexed snapshot. External edits need update; edits retaining
  both mtime/size need build. Structural changes can invalidate block keys.
- Semantic backend/embeddings are intentionally absent; only a disabled local interface
  is provided. No remote embedding API or required vector database.
- Watcher handles textual HTTP, not JS-rendered pages/PDF; a page without headings
  is one section. Volatile prose can produce false changes. No automatic compile.
- Version filtering supports dotted-prefix matching, not semver ranges or automatic
  package-manager inference.
- No live LLM compile, actual source-site integration or real user-vault migration
  was run. Those require a configured vault/source registry and existing backend setup.

## Example agent workflow

1. Inspect repository code/tests and lockfile for the current dependency version.
2. Search memory metadata with that version; read the relevant block or request
   context with a budget.
3. If evidence is missing/stale, inspect current official docs and solve the task.
4. Run the project's relevant verification.
5. If the new knowledge is durable, reusable and verified, ingest focused evidence
   with source/version metadata, then explicitly invoke wiki_update.py and wiki_lint.py.
   Do not promote transient incidents or recompile the whole Wiki after a web lookup.

## Changed files

- Retrieval: scripts/memory_index.py, memory_blocks.py, memory_context.py,
  memory_semantic.py; benchmarks/retrieval_benchmark.py.
- Sources: scripts/source_registry.py, source_watcher.py.
- Integration: scripts/migrate.py, vault_writer.py, wiki_ingest.py,
  wiki_compile.py, wiki_lint.py.
- Tests: tests/test_memory_blocks.py, test_memory_retrieval.py,
  test_memory_integration.py, test_source_registry.py, test_source_watcher.py.
- Contract/config/docs: AGENTS.md, SKILL.md, config.example.toml, README.md,
  README.en.md, TASKS.md, DECISIONS.md, docs/MEMORY_PLAN.md,
  docs/agent-workflows.md, docs/VALIDATION.md, rules/memory_retrieval.md,
  rules/source_registry.md, rules/wiki_schema.md, rules/wiki_compile.md,
  rules/wiki_update.md, .gitignore.
