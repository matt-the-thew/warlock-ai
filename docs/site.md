# Site

The front end in `site/`: Astro 7 with React 19 islands and Tailwind 4,
themed as a retro JRPG ("Warlock"). It has no backend of its own; in
development Vite proxies `/api` to the Python API (`astro.config.mjs`). For
the API and event contract see [architecture.md](architecture.md).

## Running it

```sh
uv run dnd-rag serve          # from the repo root: API on :8000
cd site && pnpm install
pnpm astro dev --background   # site on http://localhost:4321
```

Manage the background dev server with `pnpm astro dev status`,
`pnpm astro dev logs` and `pnpm astro dev stop`. `pnpm build` produces a static build in `site/dist/`;
note the `/api` proxy only exists in the dev server, so a deployed build
needs the API served from the same origin.

## Deploying

`pnpm run deploy` builds the site and deploys it to Cloudflare with
Wrangler (`wrangler.jsonc`). Cloudflare serves `dist/` directly; only
`/api/*` runs the Worker in `worker/index.ts`, which forwards the request
to the API on Cloud Run (`API_ORIGIN`) and streams the response back, so
the site's relative `/api` URLs work unchanged. The Worker also replaces
`X-Forwarded-For` with the visitor's address (`CF-Connecting-IP`), which
is what the API's `TRUSTED_PROXY_HOPS=2` relies on.

To try the Worker locally, run the API and then
`pnpm wrangler dev --var API_ORIGIN:http://127.0.0.1:8000` after a build.

## Layout

Pages live under `site/src/pages/`, but components and layouts live at
`site/components/` and `site/layouts/` (outside `src/`). Pages import them
with `../../`. Keep new files in those same folders.

| Path | What it is |
| --- | --- |
| `layouts/DefaultLayout.astro` | HTML shell: fonts, navbar links, `<main>`, CC-BY footer. Every page uses it. |
| `src/pages/index.astro` | Home / title screen. Random splash title is set by an inline script before first paint. |
| `src/pages/chat.astro` | Mounts `<Chat client:load />`. |
| `src/pages/discover.astro` | Mounts `<Discover client:load />`. |
| `components/Navbar.tsx` | Top bar; highlights the active link from `Astro.url.pathname`. Renders its `children` (the `Account` island) at the right. |
| `components/Account.tsx` | Clerk sign-in button, or the user menu when signed in. |
| `components/Chat.tsx` | Streaming chat with the agent. |
| `components/Discover.tsx` | Direct index search ("The Spellbook"). |
| `components/Markdown.tsx` | Tiny renderer for answer text. |
| `components/srd.ts` | PDF path, page-link and page-label helpers. |
| `src/styles/global.css` | Tailwind import, theme tokens, shared component classes. |
| `worker/index.ts` | Cloudflare Worker that forwards `/api/*` to Cloud Run. Not part of the Astro build. |
| `wrangler.jsonc` | Cloudflare deployment: assets, Worker, `API_ORIGIN`, custom domain. |
| `public/` | `SRD_CC_v5.2.1.pdf` (for citation links), favicon, warlock icon. |

## Sign-in (Clerk)

`@clerk/astro` runs client-side only; the site stays static output with no
SSR adapter. `astro.config.mjs` sets `vite.envDir` to the repo root so the
site and the API share one `.env` (`PUBLIC_CLERK_PUBLISHABLE_KEY`). Islands
use hooks and components from `@clerk/astro/react`, which share one Clerk
instance across islands. Without keys Clerk logs an error in the console
and sign-in does nothing, but the rest of the site works.

## Chat (`Chat.tsx`)

State is a list of `Turn`s: `user` turns hold text; `warlock` turns hold
streamed `text`, the `searches` run, deduplicated `sources`, an optional
`error`, and `done`.

`ask()` POSTs `{question, session}` to `/api/ask`, with
`Authorization: Bearer <token>` from Clerk's `getToken()` when signed in,
and reads the SSE body
with the local `events()` async generator (the browser's `EventSource` is
GET-only). `update()` applies each event to the last turn:

- `session` → stored in a ref and sent with the next question.
- `search` → appended to `searches` ("Consulting the tome: …").
- `text` → appended to `text`.
- `citation` with a `label` → the source is added once per label and a
  `⟦n⟧` marker is appended to the text; repeated citations of the same
  source in a row collapse into one marker.
- `error` → shown under the answer.

A non-OK response (429 over the daily budget or the monthly spend cap, 401
expired token) shows its JSON
`error` message under the turn. Above the input, a meter shows today's
token usage from `GET /api/usage`, refreshed on load, on sign-in/out and
after each answer.

### Footnote marker contract

`Chat.tsx` writes `⟦n⟧` into the answer text; `Markdown.tsx`'s `INLINE`
regex turns it into a superscript link to `#turn-<i>-source-<n>`, which is
the `id` of that source's badge. Each badge opens the PDF at the cited page
via `srdPageHref`. Changing the marker or the id scheme means changing both
files. `global.css` gives `.source-badge` a scroll margin so the target
clears the sticky input bar, and outlines the `:target` badge.

## Discover (`Discover.tsx`)

Calls `GET /api/search?q=…&k=8` and renders each result as an `Entry`:
breadcrumb, title, page badge linking to the PDF, and the first two
paragraphs with a "Read on" toggle. Suggestions are canned queries.

## Markdown (`Markdown.tsx`)

Deliberately minimal: paragraphs, `#` headings, `-`/`*`/`1.` bullet lists,
`**bold**`, `*italic*` and footnote markers. Answers are short and the
system prompt keeps formatting light, so there is no markdown dependency.
If answers start needing tables or links, discuss with the user before
adding a library.

## Styling

- Theme tokens are defined in `@theme` in `global.css` and used as Tailwind
  classes: colors `night`, `deep`, `slate`, `mist`, `crimson`, `ember`,
  `bone`, `ink`; fonts `font-pixel` (Press Start 2P, for labels/headings)
  and `font-term` (VT323, the body font). Use these instead of raw hex
  values.
- Shared look-and-feel classes: `.pixel-box` (dialog box; `.slate` variant
  for user messages), `.pixel-btn`, `.portrait`, `.blink` (disabled under
  `prefers-reduced-motion`), `.source-badge`.
- One focus style for the whole site via the `--outline` /
  `--outline-offset` variables.

## Conventions

- Keep components typed: explicit prop types and API payload types (see
  `Result` in `Discover.tsx`, `Turn` / `Source` in `Chat.tsx`).
- Keep accessibility on par with existing code: `sr-only` labels for
  icon-only or new-tab links, `aria-hidden` on decorative glyphs,
  `aria-live` for streaming status, `aria-current` on the active nav link.
- User-facing copy is in character ("The tome is sealed", "The spell
  fizzled"); keep new messages in the same voice.
- Shared helpers go in `srd.ts` (or a new small module) rather than being
  copied between components.
- `site/AGENTS.md` holds Astro-specific tool notes (`site/CLAUDE.md` is a
  symlink to it, so edit `AGENTS.md`); the repo-wide rules in the root
  `AGENTS.md` apply here too.
