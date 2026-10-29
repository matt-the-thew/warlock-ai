"""Hybrid (BM25 + vector) index over SRD chunks, stored in one SQLite file.

Exact game terms ("Bonus Action", "Prone", "Fireball") are best matched by
keyword search; paraphrased questions ("can I attack after dashing?") by
embeddings. Both rankings are merged with reciprocal rank fusion.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import numpy as np
from fastembed import TextEmbedding

from .chunk import Chunk

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
RRF_K = 60
CANDIDATES = 50

SCHEMA = """
CREATE TABLE chunks (
    rowid INTEGER PRIMARY KEY,
    id TEXT UNIQUE NOT NULL,
    chapter TEXT NOT NULL,
    path TEXT NOT NULL,          -- JSON list of headings
    title TEXT NOT NULL,
    page_start INTEGER NOT NULL,
    page_end INTEGER NOT NULL,
    part INTEGER NOT NULL,
    text TEXT NOT NULL,
    embedding BLOB NOT NULL      -- float32, L2-normalised
);
CREATE VIRTUAL TABLE chunks_fts USING fts5(
    title, breadcrumb, text,
    content='', tokenize='porter unicode61'
);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


@dataclass
class Hit:
    id: str
    chapter: str
    path: list[str]
    title: str
    page_start: int
    page_end: int
    text: str
    score: float

    @property
    def citation(self) -> str:
        pages = (
            f"p. {self.page_start}"
            if self.page_start == self.page_end
            else f"pp. {self.page_start}–{self.page_end}"
        )
        return f"SRD 5.2.1, {pages}, “{self.title}”"


def _normalise(vectors: np.ndarray) -> np.ndarray:
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def build_index(chunks: list[Chunk], db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)

    # Embed shortest-first so each batch pads to a similar length; batches
    # mixing 30- and 500-word chunks spend most of their time on padding.
    model = TextEmbedding(EMBED_MODEL)
    texts = [c.embed_text for c in chunks]
    order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
    embedded = list(model.embed([texts[i] for i in order], batch_size=32))
    vectors = np.empty((len(texts), len(embedded[0])), dtype=np.float32)
    vectors[order] = embedded
    vectors = _normalise(vectors)

    with sqlite3.connect(db_path) as db:
        db.executescript(SCHEMA)
        for rowid, (chunk, vec) in enumerate(zip(chunks, vectors, strict=True), start=1):
            db.execute(
                "INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    rowid,
                    chunk.id,
                    chunk.chapter,
                    json.dumps(chunk.path),
                    chunk.title,
                    chunk.page_start,
                    chunk.page_end,
                    chunk.part,
                    chunk.text,
                    vec.astype(np.float32).tobytes(),
                ),
            )
            db.execute(
                "INSERT INTO chunks_fts(rowid, title, breadcrumb, text) VALUES (?,?,?,?)",
                (rowid, chunk.title, chunk.breadcrumb, chunk.text),
            )
        db.execute("INSERT INTO meta VALUES ('embed_model', ?)", (EMBED_MODEL,))


class SrdIndex:
    def __init__(self, db_path: Path):
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        rows = self.db.execute("SELECT rowid, embedding FROM chunks ORDER BY rowid").fetchall()
        self._rowids = np.array([r["rowid"] for r in rows])
        self._matrix = np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])

    @cached_property
    def _model(self) -> TextEmbedding:
        return TextEmbedding(EMBED_MODEL)

    def search(self, query: str, k: int = 5) -> list[Hit]:
        fused: dict[int, float] = {}
        for ranking in (self._keyword(query), self._vector(query)):
            for rank, rowid in enumerate(ranking):
                fused[rowid] = fused.get(rowid, 0.0) + 1.0 / (RRF_K + rank + 1)
        top = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:k]
        return [self._hit(rowid, score) for rowid, score in top]

    def _keyword(self, query: str) -> list[int]:
        terms = re.findall(r"\w+", query.lower())
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in terms)
        rows = self.db.execute(
            # Column weights: title, breadcrumb, body.
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? "
            "ORDER BY bm25(chunks_fts, 10.0, 3.0, 1.0) LIMIT ?",
            (match, CANDIDATES),
        ).fetchall()
        return [r["rowid"] for r in rows]

    def _vector(self, query: str) -> list[int]:
        q = next(iter(self._model.query_embed(query)))
        scores = self._matrix @ (q / np.linalg.norm(q))
        best = np.argsort(-scores)[:CANDIDATES]
        return [int(self._rowids[i]) for i in best]

    def _hit(self, rowid: int, score: float) -> Hit:
        r = self.db.execute("SELECT * FROM chunks WHERE rowid = ?", (rowid,)).fetchone()
        return Hit(
            id=r["id"],
            chapter=r["chapter"],
            path=json.loads(r["path"]),
            title=r["title"],
            page_start=r["page_start"],
            page_end=r["page_end"],
            text=r["text"],
            score=score,
        )
