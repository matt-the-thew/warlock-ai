# AGENTS.md

Guidance for AI coding agents working on this repository. Read this file in
full before making changes, then read the doc for the area you are touching:

- [docs/architecture.md](docs/architecture.md) — how the pieces fit together:
  ingestion pipeline, hybrid index, rules agent, HTTP API, and the contracts
  between them.
- [docs/site.md](docs/site.md) — the Astro/React front end.
- [site/AGENTS.md](site/AGENTS.md) — Astro tooling notes for work inside
  `site/`.

## What this project is

A retrieval-augmented rules assistant for the D&D System Reference Document
5.2.1 (`SRD_CC_v5.2.1.pdf`, CC-BY-4.0). A Python package (`src/dnd_rag/`)
parses the PDF, builds a hybrid BM25 + vector index in SQLite, and runs a
Claude tool-use agent that answers rules questions with page citations. An
Astro site (`site/`) talks to it over a small HTTP API.

```
SRD PDF ──parse.py──▶ blocks ──chunk.py──▶ chunks ──index.py──▶ data/srd.db
                                                                    │
         site (Chat, Discover) ◀──SSE/JSON── api.py ◀── agent.py ◀──┘
```

## Common commands

Python uses [uv](https://docs.astral.sh/uv/); the site uses pnpm. Run Python
commands from the repo root and pnpm commands from `site/`.

| Task | Command |
| --- | --- |
| Install Python deps (query only) | `uv sync` |
| Install Python deps (+ PDF parsing) | `uv sync --extra ingest` |
| Rebuild the index (~2 min) | `uv run dnd-rag ingest` |
| Query the index, no Claude call | `uv run dnd-rag search "jump distance" -k 5` |
| Ask the agent once / interactively | `uv run dnd-rag ask "question"` / `uv run dnd-rag ask` |
| Run the HTTP API on :8000 | `uv run dnd-rag serve` |
| Install site deps | `pnpm install` |
| Run the site on :4321 (proxies `/api`) | `pnpm astro dev --background` (see `site/AGENTS.md`) |
| Production build of the site | `pnpm build` |
| Lint Python (ruff is installed globally) | `ruff check src` |

`ask` and `serve` need `ANTHROPIC_API_KEY` in `.env` (copy `.env.example`).
Every command except `ingest` needs `data/srd.db` to exist.

There is no automated test suite yet. Verify changes by exercising them:
`dnd-rag search` for parsing/chunking/index changes, `dnd-rag ask` for agent
changes, `curl` against `serve` for API changes, and `pnpm build` plus a look
in the browser for site changes. Say in your summary what you verified and
what you could not.

## Git: never push without asking

**Never run `git push` (or anything else that writes to a remote) without
the user's explicit go-ahead in the current conversation.** This includes
pushing a new branch, pushing tags, force-pushing, and opening a pull
request. Approval to push once is not approval to push again later. Local
commits are fine; the remote is the user's call.

Likewise, do not rewrite history that has already been pushed (rebase,
amend, reset of published commits) without asking first.

## Git: when to commit

Commit completed work without waiting to be asked — but only once the user
has seen and approved it. The flow is:

1. Implement a logical block of work and verify it.
2. Summarize what changed and ask the user to review it.
3. Once the user approves (an "ok", "looks good", "ship it", or moving on to
   the next task counts), stage and commit that block immediately. Don't
   wait for a separate "commit it" instruction.

A "logical block" is one coherent change that makes sense on its own: a
feature, a bug fix, a refactor. If the user asks for changes during review,
make them and commit once the result is approved. Don't leave approved work
sitting uncommitted in the working tree, and don't batch a whole session's
work into one commit at the end.

If the working tree already contains changes you did not make, leave them
out of your commits unless the user says otherwise.

## Git: how to structure commits

Prefer a fine-grained, reviewable history. Each commit should be as small
as possible while still being meaningful and self-contained.

- **Stage deliberately.** Use `git add <path>` or `git add -p` to stage
  exactly the files and hunks that belong to the commit. Never use
  `git add -A` / `git add .` / `git commit -a` blindly; check
  `git status` and `git diff --staged` before committing.
- **Split one task into several staged commits when it has several
  parts.** A task that needs a preparatory refactor plus a behavior change
  is two commits, refactor first. Backend and front-end halves of a feature
  are usually separate commits (`feat(api)` then `feat(site)`), API first so
  every commit works on its own.
- **Separate behavior-preserving changes from behavior changes.** A rename,
  extraction or move goes in its own commit before the change that needs
  it, even if you only noticed the need halfway through. Review your diff
  before committing and split out any such hunks.
- **Every commit must leave the project working.** No "WIP" commits and no
  commits that rely on a later one to fix them.
- **Never commit secrets or generated data.** `.env` and `data/` are
  git-ignored; keep it that way. Don't commit `site/dist/`,
  `site/.astro/` or `node_modules/` either.

### Commit message format

Use semantic (Conventional Commits) messages, matching the existing
history:

```
<type>(<scope>): <imperative summary, lower case, no trailing period>

<body: why the change was made, wrapped at 72 columns>
```

Types: `feat` (new behavior), `fix` (bug fix), `refactor` (no behavior
change), `perf`, `docs`, `test`, `style` (formatting only), `chore`
(tooling, dependencies, scaffolding), `build`.

Scopes in use — pick the area the change lives in:

| Scope | Files |
| --- | --- |
| `ingest` | `parse.py`, `chunk.py`, index building in `index.py` |
| `index` | search/ranking in `index.py` |
| `agent` | `agent.py` |
| `api` | `api.py` |
| `cli` | `cli.py`, `main.py` |
| `site` | anything under `site/` |
| `docs` | `README.md`, `AGENTS.md`, `docs/` (or use type `docs` with no scope) |

Omit the scope for changes that genuinely span the whole repo.

The subject says *what*; the body says *why* — the motivation, the
constraint, or the bug. Don't paraphrase the diff. A body is optional when
the subject is self-explanatory. Use `!` after the scope (`feat(api)!:`)
for a breaking change to the HTTP API or event contract.

Examples from this repo:

```
feat(agent): answer rules questions with cited SRD sources
feat(site): add Discover page for direct SRD search
chore(site): scaffold Astro site with React and Tailwind
```

### Refining unpushed commits

When follow-up changes belong to a commit that has not been pushed yet,
create a fixup (`git commit --fixup=<sha>`) instead of a new unrelated
commit, and leave it for the user to fold in with
`git rebase -i --autosquash`. Don't squash, reorder or amend commits
yourself unless the user asks. If changes cut across several existing
commits, stop and ask how to proceed.

## Code style: legibility over cleverness

This codebase is meant to be read and maintained by a person. When
choosing between a compact expression and a clear one, choose the clear
one.

- **Prefer explicit, multi-line code to dense one-liners.** Break nested
  comprehensions, chained ternaries, long boolean conditions and
  `x and y or z` tricks into named intermediate variables, small helper
  functions, or plain `if`/`for` blocks. A few extra lines are cheap; a
  reader puzzling over one line is not.
- **One idea per line.** If a line needs a comment to explain what it
  does, it is probably doing too much — split it first, then comment what
  remains non-obvious.
- **No shortcuts that hide intent.** Avoid relying on truthiness when you
  mean `is None` / `== 0` / `len(x) == 0`, avoid side effects inside
  expressions, and avoid "clever" use of short-circuiting for control
  flow.
- **Small, single-purpose functions.** When a function grows a second
  responsibility, extract it. Keep the happy path unindented: return
  early on invalid input.
- **Prefer the cleaner design over the smaller diff.** If a change would
  read better after extracting a function, splitting a component, or
  renaming something, do that refactor (as its own commit, first). Don't
  bolt new behavior onto code that no longer fits it. But don't invent
  abstractions for imagined future needs either.
- **Unify duplicated logic before changing it.** If the code you need to
  change exists in two places (say, citation handling in `cli.py` and
  `Chat.tsx`, or page formatting in `index.py` and `site/components/srd.ts`),
  make sure both stay consistent, and extract a shared helper where the
  languages allow.
- **Type everything you add.** Python: annotate function signatures and
  dataclass fields (`from __future__ import annotations` is already used in
  every module). TypeScript: the site uses Astro's `strict` tsconfig; give
  props and API payloads explicit types, avoid `any`.
- **Match the surrounding code.** No formatter is configured. Follow the
  style of the file you are in, and don't reformat code you aren't
  otherwise changing — reformatting buries the real change in the diff.

## Code style: naming

Names should let a reader understand the code without jumping to a
definition.

- **Use concise, descriptive, full-word names.** `query`, `chunk`,
  `page_start`, `search_results` — not `q`, `c`, `ps`, `res2`. Single
  letters are acceptable only for conventional cases: loop indices (`i`),
  coordinates, and very short lambdas where the meaning is obvious from
  the line itself.
- **Concise beats exhaustive.** `hits` is better than
  `list_of_search_hit_objects`. Drop words the type or context already
  says.
- **Functions are verbs, values are nouns.** `build_chunks()`,
  `extract_lines()`, `srdPageHref()`; `sources`, `turns`, `MAX_WORDS`.
  Booleans read as questions: `is_flowing`, `starts_paragraph`, `busy`.
- **Follow each language's casing.** Python: `snake_case` functions and
  variables, `PascalCase` classes, `UPPER_SNAKE` module constants, a
  leading `_` for module- or class-private helpers. TypeScript/React:
  `camelCase` functions and variables, `PascalCase` components and types,
  `UPPER_SNAKE` constants.
- **Use the domain's words.** The project already has a vocabulary —
  *block*, *chunk*, *hit*, *breadcrumb*/*path*, *citation*, *source*,
  *turn*, *session*. Reuse those terms rather than inventing synonyms.
- **Rename as you go.** Some existing code uses short names (`q`, `r`,
  `kv`). When you substantially change such code, rename them in a
  separate `refactor` commit first; don't mass-rename files you aren't
  working on.

## Comments and documentation

Comments exist so a future reader can maintain the code. Add them wherever
the code alone would leave a reader asking "why?".

- **Every module starts with a docstring** saying what it is responsible
  for (all current modules do — keep it accurate when you change one).
  Add docstrings to public classes and functions whose purpose isn't
  obvious from the name and signature.
- **Comment the why, not the what.** Explain constraints, invariants,
  non-obvious decisions, magic numbers and external quirks — e.g. why
  citations are held until `content_block_stop`, why pages 2–4 are
  skipped, why PyMuPDF is imported lazily. Don't narrate what a line
  obviously does.
- **Flag invariants and contracts loudly.** Where two pieces of code must
  agree (the `⟦n⟧` footnote marker, the SSE event shape, the
  `search_result_index` ↔ `Agent.sources` ordering), say so at both ends
  and name the other side.
- **Name constants instead of explaining numbers.** `MAX_TOOL_ROUNDS = 8`
  with a comment if the value needs justification, not a bare `8`.
- **Comments describe the code as it is, not its history.** Avoid "we used
  to…", "cleaner than the old approach", "after trying X". That story
  belongs in the commit message.
- **Use `TODO:` for known gaps** you are deliberately leaving, with enough
  context that someone else could pick it up.
- **Keep docs in sync.** If you change a command, an endpoint, the event
  contract, or the architecture, update `README.md` and the relevant file
  in `docs/` in the same commit.

## Working with the user

- **Surface real decisions instead of making them silently.** When a design
  fork appears mid-implementation — a trade-off, a scope cut, a change to a
  contract between components — stop, lay out the options with a
  recommendation, and let the user decide. Obvious mechanical choices don't
  need a checkpoint.
- **Ask before changing the model, effort or beta flags** in `agent.py`,
  the embedding model in `index.py`, or anything that forces a re-ingest.
- **Fix bugs, don't document them.** When presenting options for a defect,
  "leave it as is" is not one of them; if a real fix is out of reach, say
  so plainly.
- **Stay inside the project.** Search the working tree, `.venv/` and
  `site/node_modules/` for dependency sources; don't search the whole
  filesystem.
- **Don't edit generated or vendored files**: `data/`, `uv.lock` and
  `site/pnpm-lock.yaml` change only through `uv`/`pnpm`, never by hand.
  `SRD_CC_v5.2.1.pdf` (root and `site/public/`) is the source text; never
  modify it.
