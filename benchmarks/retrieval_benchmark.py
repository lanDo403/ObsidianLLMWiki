"""Synthetic retrieval regression benchmark, not a general RAG evaluation.

Run: python benchmarks/retrieval_benchmark.py
No LLM, network, user config or real vault is involved.
"""
from __future__ import annotations

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.memory_blocks import estimate_tokens
from scripts.memory_context import build_context
from scripts.memory_index import read_block, search, update_index


def fixture_vault(root: Path) -> tuple[dict, str]:
    """Fixed fictional text exercises routing, never claims actual API behavior."""
    vault = root / "vault"
    recovery = ("## Reconnect\nBybit orderbook reconnect requires a new snapshot in this synthetic fixture. "
                "Discard the old local book; apply deltas only after a replacement snapshot.\n")
    unrelated = "Routine background text about unrelated authentication configuration. " * 100
    canonical = "# Bybit Orderbook Synchronization\n\n" + recovery + "\n## Authentication\n" + unrelated + "\n## Heartbeat\n" + unrelated
    files = {
        "LLM Wiki/demo/concepts/orderbook.md": canonical,
        "LLM Wiki/demo/raw/docs/api.md": "# API reference\n" + recovery,
        "Notes/orderbook.md": "# Orderbook reminder\n" + recovery,
        "Notes/gardening.md": "# Gardening\nWater the tomatoes.\n",
        "System/metadata.md": "# Bybit orderbook reconnect\nInternal bookkeeping.\n",
    }
    for rel, content in files.items():
        path = vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return {"vault": {"vault_path": str(vault)}, "memory": {"db_dir": str(root / "cache")}}, canonical


def run() -> dict:
    with tempfile.TemporaryDirectory(prefix="memory-benchmark-") as temporary:
        config, page = fixture_vault(Path(temporary))
        stats = update_index(config, quiet=True)
        query = "bybit orderbook reconnect"
        hits = search(config, query)
        all_hits = search(config, query, scope="all")
        block = read_block(config, hits[0]["block_key"])
        pack = build_context(config, query, budget_tokens=1200)
        assert hits[0]["heading"].endswith("Reconnect")
        assert all_hits[0]["tier"] == "canonical_wiki"
        assert all(h["tier"] not in {"system", "raw_sources"} for h in hits)
        assert len(pack["sources"]) == 1 and pack["sources"][0]["tier"] == "canonical_wiki"
        assert estimate_tokens(pack["text"]) == pack["estimated_tokens"] <= 1200
        serialized = json.dumps(hits, ensure_ascii=False, separators=(",", ":"))
        assert len(serialized) < len(page) // 4
        assert len(block["content"]) < len(page) // 4
        durations = []
        for _ in range(30):
            started = time.perf_counter()
            search(config, query)
            durations.append((time.perf_counter() - started) * 1000)
        return {
            "fixture_notes": stats["total"], "fixture_blocks": stats["blocks"],
            "top_heading": hits[0]["heading"], "top_tier": hits[0]["tier"],
            "default_results": len(hits), "default_raw_results": 0,
            "full_page_chars": len(page), "search_json_chars": len(serialized),
            "targeted_block_chars": len(block["content"]),
            "context_estimated_tokens": pack["estimated_tokens"], "budget_tokens": 1200,
            "text_reduction_percent": round(100 * (1 - len(serialized) / len(page)), 1),
            "warm_search_median_ms": round(statistics.median(durations), 3),
            "repetitions": len(durations), "llm_calls": 0, "network_calls": 0,
        }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
