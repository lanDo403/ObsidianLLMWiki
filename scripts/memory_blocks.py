"""Deterministic Markdown sections and conservative, dependency-free token estimates."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import yaml

FRONTMATTER = re.compile(r"\A\ufeff?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", re.S)
HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*$")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
LIST = re.compile(r"^\s*(?:[-+*]|\d+[.)])[ \t]+")


def estimate_tokens(text: str) -> int:
    """ceil(UTF-8 bytes / 3); an estimate, not a model-specific tokenizer."""
    return (len(text.encode("utf-8")) + 2) // 3


def string_list(value) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    return [str(v) for v in values if isinstance(v, (str, int, float)) and str(v)]


def split_frontmatter(text: str) -> tuple[dict, str]:
    match = FRONTMATTER.match(text)
    if not match:
        return {}, text
    try:
        fm = yaml.safe_load(match[1])
    except yaml.YAMLError:
        fm = {}
    return (fm if isinstance(fm, dict) else {}), text[match.end():]


def _fence_after(line: str, fence: str) -> str:
    match = FENCE.match(line.rstrip("\r\n"))
    if not match:
        return fence
    marker, suffix = match.groups()
    if not fence:
        return marker
    if marker[0] == fence[0] and len(marker) >= len(fence) and not suffix.strip():
        return ""
    return fence


def markdown_units(text: str) -> list[str]:
    """Paragraph units; preserve fences, tables and loose lists as single units."""
    lines = text.splitlines(keepends=True)
    units, current = [], []
    fence, in_list = "", False
    for i, line in enumerate(lines):
        current.append(line)
        fence = _fence_after(line, fence)
        in_list = in_list or bool(LIST.match(line))
        if line.strip() or fence:
            continue
        following = next((v for v in lines[i + 1:] if v.strip()), "")
        if in_list and (LIST.match(following) or following.startswith(("  ", "\t"))):
            continue
        value = "".join(current).strip()
        if value:
            units.append(value)
        current, in_list = [], False
    if "".join(current).strip():
        units.append("".join(current).strip())
    return units


def _split_long_prose(unit: str, max_words: int) -> list[str]:
    # Structured or multiline units stay intact even above the preferred size.
    if "\n" in unit or FENCE.match(unit) or LIST.match(unit) or unit.startswith(("|", ">", "    ")):
        return [unit]
    words = unit.split()
    if len(words) <= max_words:
        return [unit]
    return [" ".join(words[i:i + max_words]) for i in range(0, len(words), max_words)]


@dataclass(frozen=True)
class MarkdownBlock:
    block_key: str
    heading_path: str
    content: str
    content_hash: str
    estimated_tokens: int


def parse_blocks(body: str, path: str, *, max_words: int = 500) -> list[MarkdownBlock]:
    """ATX/Setext headings with ancestry; headings in fences are never boundaries."""
    if max_words < 1:
        raise ValueError("max_words must be positive")
    lines = body.splitlines(keepends=True)
    sections: list[tuple[str, str, str]] = []
    stack: list[tuple[int, str]] = []
    current: list[str] = []
    heading, heading_line, fence = "", "", ""
    i = 0
    while i < len(lines):
        line = lines[i]
        match = HEADING.match(line.rstrip("\r\n")) if not fence else None
        setext = (not fence and not FENCE.match(line) and line.strip() and i + 1 < len(lines)
                  and re.fullmatch(r" {0,3}(?:=+|-+)[ \t]*\r?\n?", lines[i + 1])
                  and not LIST.match(line) and not line.startswith(("    ", "\t")))
        if match or setext:
            sections.append((heading, heading_line, "".join(current).strip()))
            level = len(match[1]) if match else (1 if lines[i + 1].lstrip().startswith("=") else 2)
            title = re.sub(r"[ \t]+#+$", "", match[2]) if match else line.strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            heading = " > ".join(t for _, t in stack)
            heading_line = line.strip() if match else f"{'#' * level} {title}"
            current = []
            i += 1 if match else 2
            continue
        current.append(line)
        fence = _fence_after(line, fence)
        i += 1
    sections.append((heading, heading_line, "".join(current).strip()))

    blocks, occurrences = [], {}
    for heading, heading_line, text in sections:
        if not text:
            continue
        occurrence = occurrences.get(heading, 0)
        occurrences[heading] = occurrence + 1
        units = [piece for unit in markdown_units(text) for piece in _split_long_prose(unit, max_words)]
        chunks, chunk, words = [], [], 0
        for unit in units:
            count = len(unit.split())
            if chunk and words + count > max_words:
                chunks.append("\n\n".join(chunk))
                chunk, words = [], 0
            chunk.append(unit)
            words += count
        if chunk:
            chunks.append("\n\n".join(chunk))
        for part, chunk_text in enumerate(chunks):
            content = (heading_line + "\n\n" + chunk_text).strip()
            identity = f"{path}\0{heading}\0{occurrence}\0{part}"
            key = "b-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            blocks.append(MarkdownBlock(key, heading, content, digest, estimate_tokens(content)))
    if not blocks and body.strip():
        content = body.strip()
        key = "b-" + hashlib.sha256(f"{path}\0empty".encode()).hexdigest()[:24]
        blocks.append(MarkdownBlock(key, heading, content, hashlib.sha256(content.encode()).hexdigest(), estimate_tokens(content)))
    return blocks
