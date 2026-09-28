# Local memory retrieval contract

For code-local questions, read current repository code, AGENTS, architecture/ADR,
tests and dependency files first. Do not search global Wiki for every code edit.
For domain/external knowledge: FTS metadata → selected block/context → optional
local semantic fallback → official source if missing, stale or time-sensitive.
No LLM query expansion, reranker, token counting, freshness or chunking.

## CLI

Configure the existing config.toml vault path, then:

```bash
python scripts/migrate.py
python scripts/memory_index.py status
python scripts/memory_index.py search "bybit orderbook reconnect" --json
python scripts/memory_index.py read "<block_key>"
python scripts/memory_index.py context "bybit orderbook reconnect" --budget-tokens 1200
python scripts/memory_index.py search "ccxt create order" --version 4 --json
python scripts/memory_index.py search "reconnect" --scope raw --folder "LLM Wiki/demo/raw" --json
python scripts/memory_index.py search 'headings:Reconnect' --raw --scope wiki --debug --json
```

`--raw` still means raw FTS syntax. Raw evidence requires --scope raw/all.
--folder, --tag, --prefix, --limit, --json remain supported. JSON remains a list
with legacy path, folder, title, snippet, score keys. New keys include block_key,
page, heading, tier, estimated_tokens, source_ids, version, provider, applies_to,
last_verified and freshness. source_paths retains legacy local raw references.
Snippets are at most 240 characters. Full content appears only in read/context.

## Tiers and confidence

| Scope | Retrieval |
|---|---|
| auto (default) | canonical Wiki; atomic notes only on canonical lexical miss |
| wiki | canonical Wiki only |
| notes | atomic/ordinary notes only |
| raw | raw inputs and source-reference notes |
| all | Wiki + notes + raw, canonical first |

Wiki buckets and note_type/wiki_page_type identify canonical knowledge.
raw/, wiki_page_type raw, note_type source and configured source_folder identify
raw evidence. System/, hidden directories, wiki meta and raw README are system
content, excluded even from all. Existing ordinary notes need no migration.

Ordinary queries require all literal terms in FTS fields. A canonical match is a
lexical hit; BM25 is not a probability of truth. Freshness/version still need
inspection. A lexical miss is the semantic fallback trigger; no partial-query
relaxation occurs. Title/headings/tags/body/aliases weights are 10/4/6/1/8.
With prefer_wiki=true, tier priority precedes BM25; ties use block_key. Score is
positive BM25 with a 1.5 canonical multiplier. --debug exposes the components.
Do not compare scores across different queries. No automatic graph traversal.

## Blocks and derived state

ATX/Setext headings retain ancestry. Headings in backtick/tilde fences are ignored.
Sections split into roughly 500-word subblocks at paragraphs; long plain paragraphs
can split by words. Short sections remain short. Fences, tables and lists remain
indivisible even above 500 words. This is not a complete CommonMark implementation.

Block keys hash path + heading ancestry + occurrence + subblock ordinal. Body edits
usually preserve keys; renames/structural edits may invalidate them. Read returns
the last indexed snapshot. Run update after edits outside vault_writer: it checks
mtime_ns/size and reads only changed Markdown. Changes preserving both signatures
require build. Retain path/heading/provenance as citations, not only block keys.

SQLite stays outside the vault. Incompatible page-level tables rebuild in a
transaction, not row by row. Failed rebuilds retain the previous committed index.
Search/read/context never open Markdown or scan vault. They read SQLite plus one
small source registry/state overlay for current freshness. Registry errors are
reported, never silently interpreted as fresh.

## Hard estimated context budget

Estimator: ceil(UTF-8 byte length / 3), with no tokenizer dependency. The hard limit
covers this deterministic estimate of the entire output: query, provenance,
headings, content and footer. It is not a model-specific exact token count; allow
headroom when actual model limits matter.

Context considers at most 128 metadata candidates (default 32), fetches potentially
fitting blocks in one SQLite query, removes identical content ignoring section
labels, then greedily adds complete blocks in retrieval order. Code, numbers and
punctuation are preserved. Oversized blocks are omitted, never cut through code,
tables or lists. Tiny budgets can return no evidence or an empty string. CLI context
is text-only, avoiding unbudgeted JSON envelope overhead.

## Version and optional semantic contract

--version 4 accepts 4, 4.x, 4.2.1; excludes 40, 5 and unknown. Matching ignores case
and a leading v. No semver ranges, version inference from applies_to, or package
manager parsing. Read the current project's dependency lock/config first.

[memory.semantic] enabled=false, provider="none" is the default.
LocalSemanticProvider in scripts/memory_semantic.py receives a read-only SQLite
connection and returns block keys. No backend ships or downloads models. An
explicitly installed provider must use only local inference. Returned keys are
rechecked for scope/folder/tag/version; system/raw exclusions remain. An unknown
configured provider fails clearly only when a lexical miss invokes it.

## Promotion

Promote only durable, reusable, verified knowledge from official research. Record
source IDs, applicable version and actual verification date; ingest focused evidence
and explicitly update through the validated compile/writer workflow. Do not compile
after every web operation, copy every endpoint into canonical Wiki, or verify a
page merely because its source returned 304. Inspect 1–2 relevant wikilinks only
when needed, without recursive traversal.
