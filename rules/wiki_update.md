# Wiki Update Rules

You are operating in **incremental update mode** — the user is feeding
you one or a small handful of newly-added raw inputs (typically via
`wiki_update.py <slug> <path-in-raw>`) and asking you to merge them into
the existing wiki-space.

This file is loaded **in addition to** `wiki_compile.md`. The full
ChangeSet contract there still applies. The rules below sharpen behavior
for the incremental case.

Source watcher diffs are evidence artifacts, not automatic update commands. Ingest
only the relevant changed evidence before a targeted update. Preserve source IDs,
applicable version and legacy raw citations; never infer last_verified from a
download or compilation timestamp. Follow rules/source_registry.md for freshness.

## Mindset

You are **extending**, not rebuilding. Treat the snapshot as the current
truth. Your job is to thread the new raw inputs into the existing graph,
not to rewrite pages from scratch.

A good update pass typically produces:

- 0-2 `creates[]` (only when the new raw introduces a genuinely new
  entity or concept that does not yet exist)
- 1-5 `updates[]` (the existing pages that the new raw materially
  affects)
- 0-3 `open_questions[]` (gaps the new raw exposed but did not close)

If you find yourself producing 10+ updates from a single new raw,
re-think — most of those updates probably do not have new information.

## Preservation is the default

The wikilink-preservation guard from `wiki_compile.md` is **especially**
load-bearing here. In an update pass, the existing wiki is the bulk of
the body; the new raw is the delta. Dropping a wikilink because it
"didn't show up in the new raw" is the most common failure mode.

For each `updates[]` entry:

1. Read the snapshot's `existing_links[<rel_path>]` set
2. Copy it verbatim into `expected_existing_links`
3. Confirm every link in that list still appears in your new `body`
4. If a link genuinely needs to disappear, use `renames[]`

## When NOT to update a page

Do not include a page in `updates[]` if:

- the new raw says nothing new about it
- your only change is wording polish unrelated to the new raw
- you are tempted to "tidy up" the existing body

These produce churn without information gain and burn the LLM's careful-
preservation budget. Leave the page alone.

## When to create vs. update

**Create** a new page when:

- the new raw introduces an entity/concept that has no existing page and
  is substantive enough to stand alone (would carry 3+ paragraphs)
- the new raw is a dedicated comparison or saved query

**Update** an existing page when:

- the new raw adds facts, clarifies existing ones, or contradicts them
- the new raw is a minor mention of an existing entity (add a sentence,
  not a new page)

When in doubt, prefer updating an existing page over creating a new one.
Pages should be reused, not duplicated.

## Open questions over guesses

If the new raw partially answers an existing entry in
`pages/open-questions.md`, update that page to mark the question
resolved (with citation). If the new raw raises a question it does not
answer, append a new entry to `open_questions[]` rather than guessing.

Updates that fabricate facts to "tie things together" are worse than
updates that surface honest gaps.

## Single-page targeted updates

When the user invokes `wiki_update.py <slug> <single-raw-file>`, the raw
batch contains exactly one input. The update should be tightly scoped
to that input. Do not sweep the whole wiki for unrelated improvements.

## Status transitions

- A page that gains substantive new information moves
  `stub → draft` or `draft → stable`. A `stable` page that gets a small
  addition stays `stable`.
- A page that the new raw contradicts moves to `contradicted` (and the
  contradiction is recorded — see `wiki_compile.md` rule 5).
- A page whose primary source has been superseded by the new raw moves
  to `stale` if you cannot rewrite it confidently in this pass; otherwise
  rewrite and keep it `draft`/`stable`.
