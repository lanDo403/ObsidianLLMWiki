# Sources, provenance and updates

User-maintained source definitions live in <vault>/System/sources.yaml.
[sources].registry_path overrides it; relative paths are vault-relative.
Registry metadata is excluded from recall. Example:

```yaml
sources:
  - source_id: example-api-v5
    name: Example API V5
    url: https://example.org/docs/v5.md
    type: official_docs
    provider: Example
    version: V5
    check_interval: 1d
```

Replace the illustrative URL with the actual official page. Effective records
support source_id, name, url, type, provider, version, last_checked, last_changed,
content_hash, etag, last_modified, check_interval and status. Missing registry means
no registered sources. Duplicate IDs or malformed fields fail clearly.

```bash
python scripts/source_watcher.py status --json
python scripts/source_watcher.py check
python scripts/source_watcher.py check example-api-v5 --force --json
```

check respects the interval; --force bypasses scheduling, not conditional HTTP.
ETag/Last-Modified produce conditional requests. HTTP 304 or unchanged normalized
hash produces no diff and zero LLM calls. First download establishes a baseline.
A changed hash saves immutable snapshots, unified diff and JSON with only changed,
added or removed heading sections. CLI returns artifact paths and compact metadata.
status has no network access. Errors retain known hash/snapshot. enabled=false
disables checks. No daemon is installed and no compile runs automatically.

Observations live in state.json beside snapshots/diffs in a per-vault directory
next to the external memory DB. [sources].state_dir overrides it; it must be
separate from the vault. FTS rebuild never deletes watcher state. Changing URL,
provider or version resets HTTP baseline. Writes use an exclusive lock and atomic
replacement. After a crash, remove .watcher.lock only when no watcher is running.

Wiki provenance is optional and backward compatible:

```yaml
sources: [example-api-v5, raw/docs/2026-09-23-evidence.md]
provider: Example
version: V5
applies_to: [Example API V5]
last_verified: 2026-09-23T12:00:00Z
```

Old raw paths and registry IDs can coexist. Missing version/freshness is unknown.
A verified page is fresh only when every registry ID has a known hash, current
successful check and no later change. Overdue/error checks or changes newer than
verification make it stale. Unknown IDs leave freshness unknown. Explicit stale
remains stale. Date-only verification means midnight UTC; a later same-day change
conservatively makes it stale. Checking sources never verifies Wiki prose.

Compile retains provenance, resolves cited raw metadata to registry IDs and persists
ChangeSet.sources. It does not invent verification dates. Changed prose omitting
last_verified becomes unknown. Versions/applicability are retained when omitted.
Consumed raw hashes live in generated log frontmatter; raw remains immutable and
ordinary repeated compile skips unchanged inputs. --raw-only can reprocess explicitly.

## Documentation ingestion

```bash
python scripts/wiki_ingest.py demo downloaded-api.md --mode documentation \
  --source-id example-api-v5 --provider Example --version V5
python scripts/memory_index.py search "authentication" --scope raw --json
# Explicit promotion of selected durable knowledge:
python scripts/wiki_update.py demo raw/docs/<ingested-file>.md
```

knowledge keeps the existing import/compile behavior. documentation preserves
Markdown in raw evidence, refreshes an existing index, and never chains compile.
A first index needs explicit build. Ordinary compile batches exclude documentation;
use --raw-only or wiki_update explicitly. URL ingestion retains the old stub behavior:
download textual docs first or inspect watcher snapshots. Existing docx/note
atomization pipelines are unchanged.

Only selected raw appears in a compile prompt, once. A watcher never automatically
ingests, rewrites or promotes. Inspect its changed-section artifact, prepare focused
evidence, then request a guarded update. Do not create one canonical page per endpoint.

Limitations: textual HTTP HTML/Markdown/text only; no JavaScript rendering or PDF.
HTML normalization removes scripts/styles/nav/footer and preserves basic headings/code.
Volatile prose can still cause false changes. Without headings, a page is one section;
inspect its line diff to prepare a small update instead of submitting the whole page.
