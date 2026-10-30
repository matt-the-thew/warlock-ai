# Warlock_AI

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

`agent.py` runs a streaming tool-use loop on Claude Haiku 5.5. Claude calls
`search_srd(query)`, as often as the question needs. Each hit goes back as a
`search_result` block (one text block per paragraph) with citations enabled.
Claude's answer then carries `search_result_location` citations, which the
agent maps back to SRD pages. `Agent.ask()` yields `search` / `text` /
`citation` / `done` / `error` events for a UI to render.

## Formatting

Ruff formats Python and Prettier formats the site. A pre-commit hook runs both
on staged files; install it once per clone (after `pnpm install` in `site/`):

```sh
uv sync
uv run pre-commit install
```

If the hook reformats a file, the commit stops; review the change, `git add` it
and commit again. `.vscode/` turns on format-on-save with the same settings.

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
`MONTHLY_SPEND_LIMIT_USD` (default $9.50, `spend.py`); `uv run dnd-rag spend` shows
the running total. Set a matching spend limit on the Claude Console workspace that
issued the API key as the hard backstop. Usage and spend are stored in `data/usage.db`.

`data/` is generated and git-ignored. PyMuPDF (AGPL) is an optional `ingest` extra; you
don't need it to query the index.

## Deploying

The API runs on Google Cloud Run; the site runs on Cloudflare, where a small
Worker (`site/worker/index.ts`) forwards `/api/*` to Cloud Run so both share
your domain.

**API.** The `Dockerfile` packages `dnd-rag serve`. Run `uv run dnd-rag ingest`
first: the image copies in `data/srd.db` from your working tree (`.gcloudignore`
lets it through) and downloads the embedding model at build time, so it starts
without the network or PyMuPDF.

```sh
openssl rand -hex 32 | tr -d '\n' | gcloud secrets create proxy-secret --data-file=-
gcloud run deploy dnd-rag-api --source . --region us-central1 \
  --allow-unauthenticated --max-instances=1 --session-affinity --memory=1Gi \
  --set-secrets=ANTHROPIC_API_KEY=anthropic-api-key:latest,PROXY_SECRET=proxy-secret:latest \
  --set-env-vars=TRUSTED_PROXY_HOPS=2,PUBLIC_CLERK_PUBLISHABLE_KEY=pk_live_...,CLERK_AUTHORIZED_PARTIES=https://your.domain
```

`TRUSTED_PROXY_HOPS=2` counts the Worker and Google's front end. With `PROXY_SECRET`
set, the API answers only requests carrying it, which the Worker adds, so nobody can
call the public Cloud Run URL directly and forge their IP. `data/usage.db` lives
inside the container, so the daily budgets and the monthly spend cap reset whenever
Cloud Run replaces it, and one instance only keeps chat sessions together; the
Claude Console spend limit is the real backstop until usage moves to a shared
database.

**Site.** Put the Cloud Run URL and your domain in `site/wrangler.jsonc`, make
sure `.env` has the `pk_live_` Clerk key (Astro bakes it in at build time), then:

```sh
cd site && pnpm wrangler login
gcloud secrets versions access latest --secret=proxy-secret | pnpm wrangler secret put PROXY_SECRET
pnpm run deploy
```
