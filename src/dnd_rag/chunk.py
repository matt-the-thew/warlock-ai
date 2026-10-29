"""Group parsed blocks into retrieval chunks, one per SRD entry.

Every heading (levels 1-5) starts a new chunk, so a spell, glossary term, magic
item, class feature or stat block is retrieved whole rather than cut mid-rule.

Oversized entries are split on paragraph boundaries, repeating the heading.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field

from .parse import Block

MAX_WORDS = 450


@dataclass
class Chunk:
    id: str
    chapter: str
    path: list[str]  # heading breadcrumb, chapter first
    title: str
    page_start: int
    page_end: int
    text: str
    part: int = 0

    @property
    def breadcrumb(self) -> str:
        return " > ".join(self.path)

    @property
    def embed_text(self) -> str:
        # The breadcrumb gives short entries ("Bloodied") enough context to
        # embed well, and lets "fireball" match the heading as well as the body.
        return f"{self.breadcrumb}\n\n{self.text}"

    def to_dict(self) -> dict:
        return asdict(self) | {"breadcrumb": self.breadcrumb}


@dataclass
class _Section:
    path: list[str]
    paragraphs: list[tuple[int, str]] = field(default_factory=list)


def build_chunks(blocks: list[Block]) -> list[Chunk]:
    stack: list[str] = []  # stack[i] is the current level-(i+1) heading
    sections: list[_Section] = []
    current = _Section(path=["Legal Information"])

    for block in blocks:
        if block.level:
            sections.append(current)
            stack = stack[: block.level - 1]
            stack += [""] * (block.level - 1 - len(stack))
            stack.append(block.text)
            current = _Section(path=[h for h in stack if h])
        else:
            current.paragraphs.append((block.page, block.text))
    sections.append(current)

    chunks: list[Chunk] = []
    for section in sections:
        if section.paragraphs:
            chunks.extend(_split(section))
    return chunks


def _split(section: _Section) -> list[Chunk]:
    groups: list[list[tuple[int, str]]] = [[]]
    words = 0
    for page, para in section.paragraphs:
        n = len(para.split())
        if groups[-1] and words + n > MAX_WORDS:
            groups.append([])
            words = 0
        groups[-1].append((page, para))
        words += n

    out = []
    for i, group in enumerate(groups):
        part = i + 1 if len(groups) > 1 else 0
        key = "/".join(section.path) + f"#{part}@{group[0][0]}"
        out.append(
            Chunk(
                id=hashlib.sha1(key.encode()).hexdigest()[:12],
                chapter=section.path[0],
                path=section.path,
                title=section.path[-1],
                page_start=group[0][0],
                page_end=group[-1][0],
                text="\n".join(p for _, p in group),
                part=part,
            )
        )
    return out
