/**
 * Cloudflare Worker in front of the static site: forwards `/api/*` to the
 * Python API on Cloud Run, so the site and the API share one origin as
 * `Chat.tsx` and `Discover.tsx` expect (they fetch relative `/api/...` URLs).
 *
 * `wrangler.jsonc` runs this Worker only for `/api/*`; every other path is
 * served from `dist/` without invoking it. Responses are returned as they
 * arrive, so `/api/ask`'s server-sent events stream straight through.
 */

interface Env {
  /** The Cloud Run service URL, e.g. `https://dnd-rag-api-abc123-uc.a.run.app`. */
  API_ORIGIN: string;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const target = new URL(url.pathname + url.search, env.API_ORIGIN);
    const forwarded = new Request(target, request);

    // The API tells anonymous callers apart by IP (`auth.client_ip`). Replace
    // whatever the visitor sent with the address Cloudflare saw, so the header
    // holds one trustworthy entry before Google appends the Worker's own:
    // that is why the API runs with TRUSTED_PROXY_HOPS=2.
    const visitorIp = request.headers.get("CF-Connecting-IP");
    if (visitorIp === null) {
      forwarded.headers.delete("X-Forwarded-For");
    } else {
      forwarded.headers.set("X-Forwarded-For", visitorIp);
    }
    return fetch(forwarded);
  },
};
