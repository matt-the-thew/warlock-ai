# dnd-rag

A retrieval-augmented rules assistant for the D&D **System Reference Document 5.2.1**
(CC-BY-4.0, `SRD_CC_v5.2.1.pdf`), plus an Astro site (`site/`) to chat with it.

## Ingestion

```sh
uv sync --extra ingest
uv run dnd-rag ingest              # ~2 min: PDF -> data/chunks.jsonl + data/srd.db
uv run dnd-rag search "how far can I jump" -k 5
```

Pipeline (`src/dnd_rag/`):

1. **`parse.py`**: reads the PDF with PyMuPDF and works out heading levels from font size
   (26pt chapter, 18pt section, 14pt subsection, 12pt entry, 14.8pt stat block). It reflows
   two-column prose into paragraphs, rejoins hyphenated line breaks, and flattens the
   monster ability-score grid into a single line.
2. **`chunk.py`**: makes one chunk per heading, so each spell, glossary term, magic item,
   class feature or stat block is a separate chunk. Entries over 450 words are split on
   paragraph boundaries. Each chunk keeps its heading breadcrumb and page range for citations.
3. **`index.py`**: stores everything in one SQLite file: an FTS5 table for BM25 keyword
   search and `bge-small-en-v1.5` embeddings (local, via fastembed) for vector search.
   `SrdIndex.search()` merges both rankings with reciprocal rank fusion.

`data/` is generated and git-ignored. PyMuPDF (AGPL) is an optional `ingest` extra; you
don't need it to query the index.
