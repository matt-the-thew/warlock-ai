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

## Agent

```sh
cp .env.example .env               # then fill in ANTHROPIC_API_KEY
uv run dnd-rag ask "Can a grappled creature still cast spells?"
uv run dnd-rag ask                 # interactive session
```

`agent.py` runs a streaming tool-use loop on Claude Opus 5.5. Claude calls
`search_srd(query)`, as often as the question needs. Each hit goes back as a
`search_result` block (one text block per paragraph) with citations enabled.
Claude's answer then carries `search_result_location` citations, which the
agent maps back to SRD pages. `Agent.ask()` yields `search` / `text` /
`citation` / `done` / `error` events for a UI to render.

## Site

```sh
uv run dnd-rag serve               # API on http://127.0.0.1:8000
cd site && pnpm install && pnpm dev   # http://localhost:4321, proxies /api
```

`api.py` exposes `POST /api/ask` (the agent's events as server-sent events, with a
`session` id for follow-up questions), `GET /api/search?q=` (the index, no Claude
call) and `GET /api/usage` (the caller's daily token budget). The site has three
pages: Home, Chat and Discover (direct SRD search).

Sign-in uses [Clerk](https://clerk.com): set `PUBLIC_CLERK_PUBLISHABLE_KEY` in `.env` (no
secret key needed). Chat is metered by tokens per day: 20k for anonymous
visitors (by IP), 200k for signed-in users (`limits.py`), plus 5 questions a minute.
On top of that, chat stops for everyone once the month's estimated spend reaches
`MONTHLY_SPEND_LIMIT_USD` (default $9.50, `spend.py`). Set a matching spend limit on the Claude Console workspace that
issued the API key as the hard backstop. Usage and spend are stored in `data/usage.db`.

`data/` is generated and git-ignored. PyMuPDF (AGPL) is an optional `ingest` extra; you
don't need it to query the index.
