"""Command line entry point: `dnd-rag ingest` and `dnd-rag search`."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PDF = ROOT / "SRD_CC_v5.2.1.pdf"
DEFAULT_DB = ROOT / "data" / "srd.db"


def ingest(pdf: Path, db: Path) -> None:
    # Imported here so `search` (and later the API server) never loads PyMuPDF.
    from .chunk import build_chunks
    from .index import build_index
    from .parse import extract_lines, to_blocks

    chunks = build_chunks(to_blocks(extract_lines(pdf)))
    jsonl = db.with_name("chunks.jsonl")
    jsonl.parent.mkdir(parents=True, exist_ok=True)
    with jsonl.open("w") as f:
        for c in chunks:
            f.write(json.dumps(c.to_dict(), ensure_ascii=False) + "\n")

    words = [len(c.text.split()) for c in chunks]
    print(f"{len(chunks)} chunks, {sum(words)} words (median {sorted(words)[len(words) // 2]})")
    for chapter, n in Counter(c.chapter for c in chunks).items():
        print(f"  {n:5}  {chapter}")
    print(f"Wrote {jsonl}")

    build_index(chunks, db)
    print(f"Wrote {db}")


def search(query: str, db: Path, k: int) -> None:
    from .index import SrdIndex

    for hit in SrdIndex(db).search(query, k):
        print(f"[{hit.score:.4f}] {hit.citation}  ({' > '.join(hit.path)})")
        print("    " + hit.text[:240].replace("\n", " ") + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(prog="dnd-rag")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="Parse the SRD PDF and build the index")
    p_ingest.add_argument("--pdf", type=Path, default=DEFAULT_PDF)

    p_search = sub.add_parser("search", help="Query the index")
    p_search.add_argument("query")
    p_search.add_argument("-k", type=int, default=5)

    args = parser.parse_args()
    if args.command == "ingest":
        ingest(args.pdf, args.db)
    else:
        search(args.query, args.db, args.k)
