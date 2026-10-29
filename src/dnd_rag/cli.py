"""Command line entry point: `dnd-rag ingest`, `search`, `ask` and `serve`."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

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


def chat(db: Path, question: str | None) -> None:
    """Ask one question, or with no question start an interactive session."""
    from .agent import Agent
    from .index import SrdIndex

    agent = Agent(SrdIndex(db))
    while True:
        q = question or input("\n> ").strip()
        if not q:
            continue
        if q.lower() in {"exit", "quit"}:
            return
        footnotes: dict[str, int] = {}
        for event in agent.ask(q):
            match event.type:
                case "search":
                    print(f"  [searching: {event.data['query']}]", flush=True)
                case "text":
                    print(event.data["text"], end="", flush=True)
                case "citation" if "label" in event.data:
                    n = footnotes.setdefault(event.data["label"], len(footnotes) + 1)
                    print(f"[{n}]", end="", flush=True)
                case "error":
                    print(f"\n[error: {event.data['message']}]")
        print()
        for label, n in footnotes.items():
            print(f"  [{n}] {label}")
        if question:
            return


def main() -> None:
    # Put ANTHROPIC_API_KEY from the project's .env into the environment, where
    # anthropic.Anthropic() looks for it. A variable already exported wins.
    load_dotenv(ROOT / ".env")

    parser = argparse.ArgumentParser(prog="dnd-rag")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="Parse the SRD PDF and build the index")
    p_ingest.add_argument("--pdf", type=Path, default=DEFAULT_PDF)

    p_search = sub.add_parser("search", help="Query the index")
    p_search.add_argument("query")
    p_search.add_argument("-k", type=int, default=5)

    p_ask = sub.add_parser("ask", help="Ask the rules agent (interactive without a question)")
    p_ask.add_argument("question", nargs="?")

    p_serve = sub.add_parser("serve", help="Run the HTTP API the site talks to")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)

    args = parser.parse_args()
    if args.command == "ingest":
        ingest(args.pdf, args.db)
    elif args.command == "search":
        search(args.query, args.db, args.k)
    elif args.command == "serve":
        from .api import serve

        serve(args.db, args.host, args.port)
    else:
        chat(args.db, args.question)
