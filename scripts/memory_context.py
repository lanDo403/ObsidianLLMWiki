"""Deterministic evidence packs. The budget covers the complete rendered output."""
from __future__ import annotations

import re

try:
    from scripts.memory_blocks import estimate_tokens, _fence_after
    from scripts.memory_index import memory_config, read_blocks, search
except ModuleNotFoundError:
    from memory_blocks import estimate_tokens, _fence_after
    from memory_index import memory_config, read_blocks, search


def _identity(content: str) -> str:
    # Ignore section labels for cross-page duplicates. Keep code case, numbers,
    # punctuation and order: similar-looking but conflicting facts must survive.
    lines = []
    fence = ""
    for line in content.splitlines():
        if fence or not re.match(r"^#{1,6}\s", line):
            lines.append(line)
        fence = _fence_after(line, fence)
    return "\n".join(lines).strip()


def _render(query: str, sources: list[dict], budget: int) -> tuple[str, int]:
    parts = [f"QUERY: {query}\n"]
    for i, source in enumerate(sources, 1):
        parts.append(
            f"SOURCE {i}\nPage: {source['page']}\nSection: {source['heading']}\n"
            f"Path: {source['path']}\nBlock: {source['block_key']}\nTier: {source['tier']}\n"
            f"Version: {source['version']} | Freshness: {source['freshness']}\n"
            + (f"Sources: {', '.join(source['source_ids'])}\n" if source['source_ids'] else "")
            + (f"Raw evidence: {', '.join(source['source_paths'])}\n" if source.get('source_paths') else "")
            + f"\n{source['content']}\n"
        )
    if not sources:
        parts.append("No evidence fits this query and budget.\n")
    prefix = "\n".join(parts)
    total = 0
    while True:
        text = prefix + f"\nTOTAL ESTIMATED TOKENS: {total} / {budget}\n"
        measured = estimate_tokens(text)
        if measured == total:
            return text, total
        total = measured


def build_context(config: dict, query: str, *, budget_tokens: int | None = None, **search_options) -> dict:
    budget = memory_config(config)["context_budget_tokens"] if budget_tokens is None else budget_tokens
    if budget < 0:
        raise ValueError("budget-tokens must be non-negative")
    if budget == 0:
        return {"text": "", "sources": [], "estimated_tokens": 0, "budget_tokens": 0}
    # Bound retrieval independently of vault size. Only fetch content that might fit.
    search_options["limit"] = min(128, search_options.get("limit") or memory_config(config)["search_limit"] * 4)
    hits = search(config, query, **search_options)
    keys = [hit["block_key"] for hit in hits if hit["estimated_tokens"] <= budget]
    candidates = read_blocks(config, keys)
    selected, seen = [], set()
    for source in candidates:
        identity = _identity(source["content"])
        if identity in seen:
            continue
        _, total = _render(query, [*selected, source], budget)
        if total <= budget:
            selected.append(source)
            seen.add(identity)
    text, total = _render(query, selected, budget)
    if total > budget:  # Even the empty header cannot fit; emit zero bytes.
        text, total = "", 0
    return {"text": text, "sources": selected, "estimated_tokens": total, "budget_tokens": budget}
