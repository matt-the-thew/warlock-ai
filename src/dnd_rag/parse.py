"""Extract structured lines from the SRD 5.2.1 PDF.

The SRD has no usable bookmarks, but its typography is very regular, so heading
levels are recovered from font size and weight:

    26pt GillSans-SemiBold   chapter        ("Spells", "Rules Glossary")
    18pt GillSans-SemiBold   section        ("Spell Descriptions", "Aboleth")
    14pt GillSans-SemiBold   subsection     ("Cover", "Mastery Properties")
    12pt GillSans-SemiBold   entry          (a spell, glossary term, class feature)
  14.8pt GillSans-SemiBold   stat block     ("Adult Red Dragon", or one inside a spell)

Stat blocks get the deepest level so that one embedded in a spell (Animate
Objects -> "Animated Object") nests under it instead of outliving it.

Everything else is body text, which is reflowed into paragraphs here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf

# Pages 2-4 are the table of contents; indexing them would pollute retrieval.
SKIP_PAGES = {2, 3, 4}
# Running header/footer ("System Reference Document 5.2.1" + page number).
FOOTER_Y = 740

HEADING_LEVELS = {26.0: 1, 18.0: 2, 14.0: 3, 12.0: 4, 14.8: 5}

# Font families whose consecutive lines are one flowing paragraph (prose and
# stat block actions), in any style. Other fonts (tables, spell metadata, stat
# lines) keep their line breaks.
FLOWING_FONTS = {(10.0, "Cambria"), (9.5, "Optima")}


@dataclass
class Line:
    page: int  # 1-based; matches the page number printed in the SRD footer
    text: str
    level: int  # 1-5 for headings, 0 for body
    y: float
    flows_in: bool  # first span is prose, so it may continue the previous line
    flows_out: bool  # last span is prose, so the next line may continue it
    starts_paragraph: bool


@dataclass
class Block:
    """A heading or a reflowed paragraph."""

    page: int
    text: str
    level: int  # 0 = paragraph


def _span_key(span: dict) -> tuple[float, str]:
    return round(span["size"], 1), span["font"]


def _is_flowing(span: dict) -> bool:
    size, font = _span_key(span)
    return (size, font.split("-")[0]) in FLOWING_FONTS


def _heading_level(span: dict) -> int:
    size, font = _span_key(span)
    if font != "GillSans-SemiBold":
        return 0
    return HEADING_LEVELS.get(size, 0)


def _is_stat_table(span: dict) -> bool:
    # Ability score grid in monster stat blocks: 9.8pt cells, 6pt "MOD SAVE".
    size, font = _span_key(span)
    return font.startswith("GillSans") and size in (9.8, 6.0, 6.8)


def extract_lines(pdf_path: Path) -> list[Line]:
    doc = pymupdf.open(pdf_path)
    lines: list[Line] = []
    for page in doc:
        pno = page.number + 1
        if pno in SKIP_PAGES:
            continue
        stat_cells: list[str] = []
        for block in page.get_text("dict")["blocks"]:
            for raw in block.get("lines", []):
                spans = [s for s in raw["spans"] if s["text"]]
                if not spans or raw["bbox"][1] > FOOTER_Y:
                    continue
                first = next((s for s in spans if s["text"].strip()), spans[0])
                text = "".join(s["text"] for s in spans)

                if _is_stat_table(first):
                    if _span_key(first)[0] != 6.0:  # drop "MOD SAVE" labels
                        stat_cells.append(text)
                    continue
                if stat_cells:
                    lines.append(_stat_line(pno, stat_cells))
                    stat_cells = []

                if not text.strip():
                    continue
                last = next(
                    (s for s in reversed(spans) if s["text"].strip()), spans[-1]
                )
                lines.append(
                    Line(
                        page=pno,
                        text=text,
                        level=_heading_level(first),
                        y=raw["bbox"][1],
                        flows_in=_is_flowing(first),
                        flows_out=_is_flowing(last),
                        # Prose paragraphs are tab-indented; stat block actions
                        # ("Multiattack.") start with an unindented bold run-in.
                        starts_paragraph=text.startswith(("\t", "•"))
                        or _span_key(first)[1] == "Optima-BoldItalic",
                    )
                )
        if stat_cells:
            lines.append(_stat_line(pno, stat_cells))
    return lines


def _stat_line(page: int, cells: list[str]) -> Line:
    text = " ".join(" ".join(c.split()) for c in cells)
    return Line(
        page, text, 0, -1, flows_in=False, flows_out=False, starts_paragraph=True
    )


def _clean(text: str) -> str:
    text = text.replace("\t", " ").replace("­", "")
    return re.sub(r"[  ]+", " ", text).strip()


def _join(prev: str, nxt: str) -> str:
    # Rejoin words hyphenated across a line break ("typ-" + "ically").
    if re.search(r"[a-z]-$", prev) and re.match(r"[a-z]", nxt):
        return prev[:-1] + nxt
    return f"{prev} {nxt}"


def to_blocks(lines: list[Line]) -> list[Block]:
    """Merge multi-line headings and reflow wrapped body lines into paragraphs."""
    blocks: list[Block] = []
    prev: Line | None = None
    for line in lines:
        text = _clean(line.text)
        if not text:
            continue
        last = blocks[-1] if blocks else None
        if last and prev and line.level and line.level == last.level == prev.level:
            # Heading wrapped onto a second line ("Character " / "Creation").
            last.text = f"{last.text} {text}"
        elif (
            last
            and prev
            and not line.level
            and not last.level
            and not line.starts_paragraph
            and (
                (prev.flows_out and line.flows_in)
                # A hyphenated break joins lines in any font (tables, sidebars).
                or re.search(r"[a-z]-$", last.text)
                and re.match(r"[a-z]", text)
            )
        ):
            last.text = _join(last.text, text)
        elif (
            last
            and prev
            and not line.level
            and not last.level
            and abs(line.y - prev.y) < 2
        ):
            # Same baseline as the previous line: another cell in a table row.
            last.text = f"{last.text} | {text}"
        else:
            blocks.append(Block(line.page, text, line.level))
        prev = line
    return blocks
