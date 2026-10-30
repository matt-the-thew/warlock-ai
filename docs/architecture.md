# Architecture

How the Python side of dnd-rag works, end to end, and the contracts that
hold it together. For the front end see [site.md](site.md); for workflow
rules see [../AGENTS.md](../AGENTS.md).

```
                         ┌──────────── ingest (offline, ~2 min) ───────────┐
SRD_CC_v5.2.1.pdf ──▶ parse.py ──▶ list[Block] ──▶ chunk.py ──▶ list[Chunk] ──▶ index.py
                                                                    │           │
                                                       data/chunks.jsonl   data/srd.db
                                                                                │
                         ┌──────────────── query (online) ─────────────────┐    │
 site / curl ──HTTP──▶ api.py ──▶ agent.py ──search_srd──▶ SrdIndex.search ◀────┘
                         ▲           │
                         └── SSE ────┘ (Event stream)
```

All code lives in `src/dnd_rag/`. `cli.py` is the single entry point
(`dnd-rag` script, also `main.py`) and wires everything together.

## Module map

| Module | Responsibility | Depends on |
| --- | --- | --- |
| `parse.py` | PDF → typed lines → reflowed `Block`s | PyMuPDF (`ingest` extra) |
| `chunk.py` | `Block`s → retrieval `Chunk`s, one per SRD entry | `parse.Block` |
| `index.py` | Build the SQLite index; hybrid search returning `Hit`s | fastembed, numpy, `chunk.Chunk` |
| `agent.py` | Claude tool-use loop; yields `Event`s | anthropic, `index` |
| `auth.py` | Verify Clerk session tokens; `Identity` of a caller | pyjwt |
| `limits.py` | Daily token budgets per plan (`data/usage.db`), burst cap | `auth` |
| `api.py` | FastAPI app: `/api/ask` (SSE), `/api/search`, `/api/usage` | fastapi, uvicorn, `agent`, `index`, `auth`, `limits` |
| `cli.py` | `ingest`, `search`, `ask`, `serve` subcommands; loads `.env` | everything, lazily |

### Licensing boundary: PyMuPDF

PyMuPDF is AGPL, so it is an optional extra (`uv sync --extra ingest`) and
only `parse.py` imports it. `cli.py` imports `parse`, `chunk` and `index`
*inside* the `ingest()` function so that `search`, `ask` and `serve` never
load it. **Keep it that way:** never import `parse` at module level from
any module used at query time.

## 1. Parsing (`parse.py`)

The SRD PDF has no usable bookmarks, but its typography is regular, so
structure is recovered from font size and face:

| Size | Font | Level | Example |
| --- | --- | --- | --- |
| 26pt | GillSans-SemiBold | 1 chapter | "Spells" |
| 18pt | GillSans-SemiBold | 2 section | "Spell Descriptions", "Aboleth" |
| 14pt | GillSans-SemiBold | 3 subsection | "Cover" |
| 12pt | GillSans-SemiBold | 4 entry | a spell, glossary term, feature |
| 14.8pt | GillSans-SemiBold | 5 stat block | "Adult Red Dragon" |

Stat blocks are deliberately the *deepest* level so a stat block embedded in
a spell (Animate Objects → "Animated Object") nests under the spell.

Two stages:

1. **`extract_lines(pdf) -> list[Line]`** walks every span. It skips the
   table of contents (`SKIP_PAGES = {2, 3, 4}`) and the running footer
   (`FOOTER_Y`), classifies each line's heading level, records whether the
   line's first/last span is flowing prose (`FLOWING_FONTS`: 10pt Cambria
   body text and 9.5pt Optima stat-block actions), and collapses the
   monster ability-score grid (9.8 / 6 / 6.8pt GillSans cells) into one
   line.
2. **`to_blocks(lines) -> list[Block]`** merges headings that wrap onto two
   lines, reflows wrapped prose into paragraphs (rejoining words hyphenated
   across a break), and joins same-baseline lines into `a | b | c` table
   rows.

`Line.page` / `Block.page` are 1-based PDF page numbers, which match the
page numbers printed in the SRD footer. The site relies on this to deep-link
into the PDF (`#page=N`).

If you change font tables or thresholds here, re-run `dnd-rag ingest` and
spot-check with `dnd-rag search` on spells, conditions, monsters and tables.

## 2. Chunking (`chunk.py`)

`build_chunks(blocks)` keeps a heading stack and starts a new section at
every heading (levels 1–5), so each spell, glossary term, magic item, class
feature and stat block is retrieved whole. Text before the first heading
goes under "Legal Information".

Sections over `MAX_WORDS = 450` are split on paragraph boundaries into
parts (`part = 1, 2, …`; `0` means unsplit), each repeating the heading
path.

A `Chunk` carries:

- `id` — first 12 hex chars of `sha1("<path>#<part>@<first page>")`.
  Deterministic across re-ingests **as long as parsing doesn't change**;
  any change to headings, splitting or pages changes ids.
- `path` — heading breadcrumb, chapter first; `title` is `path[-1]`.
- `page_start` / `page_end` — for citations.
- `embed_text` — breadcrumb + text. The breadcrumb gives short entries
  ("Bloodied") enough context to embed well.

`dnd-rag ingest` also writes every chunk to `data/chunks.jsonl` for
inspection; nothing reads that file at query time.

## 3. Index (`index.py`)

One SQLite file, `data/srd.db` (schema in `SCHEMA`):

- `chunks` — one row per chunk, including its embedding as a float32,
  L2-normalised BLOB.
- `chunks_fts` — contentless FTS5 table over `title`, `breadcrumb`, `text`
  with the `porter unicode61` tokenizer.
- `meta` — records `embed_model`.

**Building** (`build_index`) embeds with `BAAI/bge-small-en-v1.5` via
fastembed (runs locally, no API key). Texts are embedded shortest-first so
each batch pads to a similar length, then written back in original order.

**Searching** (`SrdIndex.search(query, k)`) runs two rankings of up to
`CANDIDATES = 50` rows each and merges them with reciprocal rank fusion
(`score += 1 / (RRF_K + rank + 1)`, `RRF_K = 60`):

- **Keyword** (`_keyword`): query words OR-ed into an FTS5 MATCH, ranked by
  `bm25` with column weights title 10, breadcrumb 3, body 1. Best for exact
  game terms ("Prone", "Bonus Action").
- **Vector** (`_vector`): cosine similarity against the in-memory embedding
  matrix loaded at construction. Best for paraphrased questions ("can I
  attack after dashing?").

The embedding model is loaded lazily on the first vector query
(`cached_property`). `Hit.citation` formats the human-readable label,
`SRD 5.2.1, p. 12, “Title”`, which the CLI and site use as the identity of a
source.

**Changing `EMBED_MODEL` requires a full re-ingest.** The model name is
stored in `meta`, but `SrdIndex` does not currently check it on load.

## 4. Agent (`agent.py`)

`Agent` holds one conversation (`messages`) and runs a streaming tool-use
loop on `MODEL = "claude-opus-5-5"` with `effort: medium`. Claude has one
tool, `search_srd(query)`, and a system prompt that tells it to answer only
from search results and to search each rule separately.

Per `ask(question)`:

1. Append the user message and stream a turn (`_stream_turn`). Text deltas
   are yielded as they arrive.
2. If the response has no `tool_use` blocks, yield `done` and stop.
3. Otherwise run each tool call (`_run_tool`): validate the input (eager
   input streaming means the API does not), search the index with
   `SEARCH_K = 5`, and return the hits as `search_result` blocks with
   citations enabled.
4. Repeat, up to `MAX_TOOL_ROUNDS = 8`.

`Agent(index, on_usage=...)` takes an optional callback that receives each
Claude response's `usage` once per tool round; the API uses it to meter
token budgets (see §5).

Request options worth knowing: `cache_control` caches the prompt prefix
across rounds; `fallbacks="default"` with the `server-side-fallback` beta
retries on a fallback model after a safety-classifier decline. A
`refusal` stop reason becomes an `error` event. Ask the user before
changing the model, effort, betas or fallback settings.

### Citation invariant

Each paragraph of a hit is its own text block inside the `search_result`
(`hit_to_search_result`), so Claude can cite one line of a long entry.

Claude's citations come back as `search_result_location` with a
`search_result_index` that counts **every `search_result` block in the
request, across all turns, in order**. `Agent.sources` mirrors that list:
`_run_tool` appends hits in exactly the order it sends them. `_citation()`
looks the index up there to attach page and title. If you change how
results are sent — filtering, reordering, sending a no-results block as a
`search_result`, or trimming history — `sources` must change in lockstep,
or citations will point at the wrong pages.

Citation events for a block are buffered until `content_block_stop` because
the API streams a block's citations before its text; releasing them at the
end makes each footnote marker follow the sentence it supports.

### Event contract

`Agent.ask()` yields `Event(type, data)`:

| `type` | `data` | Meaning |
| --- | --- | --- |
| `search` | `{query}` | A `search_srd` call is about to run |
| `text` | `{text}` | A chunk of answer text |
| `citation` | `{cited_text, id?, title?, path?, page_start?, page_end?, label?}` | A citation for the text just emitted; the optional fields are missing if the index could not be resolved |
| `done` | `{stop_reason}` | Answer finished |
| `error` | `{message}` | Answer stopped early (refusal, round limit, max_tokens mid tool call) |

Consumers: `cli.chat()` (prints footnotes) and `api.py` (forwards as SSE).
The site's `Chat.tsx` depends on these exact field names — treat changes as
breaking (`feat(agent)!:`) and update all three consumers together.

## 5. HTTP API (`api.py`)

A FastAPI app (`create_app`) run by uvicorn, started by `dnd-rag serve` on
`127.0.0.1:8000`. Endpoints are plain `def` functions, so FastAPI runs them
in its thread pool and the blocking search and Claude stream never stall the
event loop. Errors are JSON `{"error": str}`, including request validation
failures (400).

- **`GET /api/search?q=<query>&k=<n>`** → `{"results": [Hit fields +
  label]}`. `k` defaults to 8, clamped to `1..MAX_K` (20). No Claude call.
- **`GET /api/usage`** → `{"plan", "used", "limit", "resets_at",
  "signed_in"}` for the caller (see below).
- **`POST /api/ask`** with JSON `{"question": str, "session"?: str}` →
  `text/event-stream`. Each event is one line `data: {"type", "data"}`
  followed by a blank line. The first event is always
  `{"type": "session", "data": {"session": "<id>"}}`; send that id back
  with the next question to continue the conversation. The rest are the
  agent events above. Unexpected exceptions are logged and sent as an
  `error` event. Over a limit it returns **429** `{"error", "retry_after"}`
  with a `Retry-After` header instead of a stream.

### Auth and rate limits (`auth.py`, `limits.py`)

Sign-in happens entirely in the browser with Clerk. The site sends the
session token as `Authorization: Bearer <jwt>`; the `identify` dependency
verifies it against Clerk's JWKS (derived from
`PUBLIC_CLERK_PUBLISHABLE_KEY`, so the API never needs the secret key) and
checks `azp` against `CLERK_AUTHORIZED_PARTIES` when set. A request with
no header is anonymous, identified as `ip:<address>`; a header that fails
verification is a 401, never a silent downgrade. With no publishable key
configured, everyone is anonymous.

An `Identity.key` (`user:<clerk id>` or `ip:<address>`) is what usage is
billed to. `/api/ask` checks, in order:

1. **Daily token budget** — `PLAN_DAILY_TOKENS` per plan (`anonymous`
   20k, `free` 200k), reset at midnight UTC. Usage lives in
   `data/usage.db`, apart from the index so re-ingesting keeps it. The
   check runs before an answer starts, so one answer can overshoot.
2. **Burst cap** — `MAX_ASKS_PER_MINUTE = 5` per identity, in memory.

Tokens are recorded through `Agent`'s `on_usage` callback after every
Claude response (`billable_tokens`: input, cache reads and writes, and
output all count). Each session remembers the identity that started it and
bills that identity; a different caller presenting the same session id
gets a new session, so a leaked id cannot spend someone else's budget.

Signed-in users are all on the `free` plan for now. Paid tiers would add
entries to `PLAN_DAILY_TOKENS` and read the user's plan from their Clerk
session claims in `plan_for`.

Behind a reverse proxy, anonymous callers are told apart by
`request.client.host`, which uvicorn takes from `X-Forwarded-For` only for
trusted proxies (`--forwarded-allow-ips`, default `127.0.0.1`). Through the
Vite dev proxy every anonymous caller shares one `127.0.0.1` budget.

Concurrency and state:

- `LockedIndex` wraps one `SrdIndex` (one SQLite connection, one embedding
  model) behind a lock shared by all request threads.
- `Sessions` holds `Agent`s in memory, evicted oldest first past
  `MAX_SESSIONS = 200`, and lost on restart.

## 6. CLI (`cli.py`)

`main()` loads `.env` from the repo root (an already-exported variable
wins), then dispatches:

- `ingest [--pdf]` — parse → chunk → write `data/chunks.jsonl` → build
  `data/srd.db`, printing chunk counts per chapter.
- `search QUERY [-k]` — print ranked hits.
- `ask [QUESTION]` — one question, or an interactive loop (`exit`/`quit`
  to leave) with numbered footnotes.
- `serve [--host] [--port] [--usage-db]` — run the API.

`--db` (before the subcommand) overrides the index path for all of them.

## Generated files

| Path | Produced by | Committed? |
| --- | --- | --- |
| `data/srd.db`, `data/chunks.jsonl` | `dnd-rag ingest` | No (git-ignored) |
| `data/usage.db` | `dnd-rag serve` (token usage) | No (git-ignored) |
| `.venv/` | `uv sync` | No |
| `site/node_modules/`, `site/dist/`, `site/.astro/` | pnpm / Astro | No |
| `uv.lock`, `site/pnpm-lock.yaml` | uv / pnpm | Yes — never edit by hand |
