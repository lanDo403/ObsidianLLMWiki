from scripts.memory_blocks import estimate_tokens, parse_blocks, split_frontmatter


def test_heading_hierarchy_and_targeted_sections():
    blocks = parse_blocks("# Page\n## WebSocket\n### Reconnect\nGet snapshot.\n## Auth\nSign request.", "wiki.md")
    assert [b.heading_path for b in blocks] == ["Page > WebSocket > Reconnect", "Page > Auth"]
    assert "Sign request" not in blocks[0].content
    assert "Get snapshot" not in blocks[1].content


def test_setext_and_repeated_headings_and_stable_keys():
    text = "Page\n====\n\n## Retry\nfirst\n## Retry\nsecond"
    a = parse_blocks(text, "page.md")
    b = parse_blocks(text.replace("first", "modified"), "page.md")
    assert a[0].heading_path == "Page > Retry"
    assert len({v.block_key for v in a}) == 2
    assert a[0].block_key == b[0].block_key
    assert a[0].content_hash != b[0].content_hash


def test_long_section_splits_by_paragraphs():
    text = "# Page\n\n" + "\n\n".join("word " * 200 for _ in range(7))
    blocks = parse_blocks(text, "p.md")
    assert len(blocks) == 4
    assert all(200 <= len(b.content.split()) <= 502 for b in blocks)
    assert all(b.heading_path == "Page" for b in blocks)


def test_long_single_paragraph_splits():
    assert len(parse_blocks("word " * 1200, "p.md")) == 3


def test_code_tables_loose_lists_preserved():
    code = "````python\n# not heading\n```\n" + "x = 1\n" * 300 + "````"
    table = "| key | value |\n|---|---|\n" + "| x | 1 |\n" * 200
    items = "- a\n\n  continuation\n\n- b\n" + "- item\n" * 600
    blocks = parse_blocks(f"# Page\n\n{code}\n\n{table}\n\n{items}\n\n## Next\nend", "p.md")
    assert sum(code in b.content for b in blocks) == 1
    assert sum(table.strip() in b.content for b in blocks) == 1
    assert sum(items.strip() in b.content for b in blocks) == 1
    assert not any("not heading" in b.heading_path for b in blocks)


def test_unclosed_fence_tilde_and_csharp():
    blocks = parse_blocks("# C#\n\n~~~\n## fake\n---\n", "p.md")
    assert len(blocks) == 1 and blocks[0].heading_path == "C#"
    assert "## fake" in blocks[0].content


def test_frontmatter_bad_or_missing_is_compatible():
    assert split_frontmatter("---\n[broken\n---\nbody") == ({}, "body")
    assert split_frontmatter("text") == ({}, "text")
    assert estimate_tokens("Привет") == 4
