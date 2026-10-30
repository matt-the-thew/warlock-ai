import { useAuth } from "@clerk/astro/react";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import Markdown from "./Markdown";
import { pages, srdPageHref } from "./srd";

type Source = {
  n: number;
  label: string;
  title: string;
  page: string;
  pageStart: number;
  quote: string;
};

type Turn =
  | { role: "user"; text: string }
  | {
      role: "warlock";
      text: string;
      searches: string[];
      sources: Source[];
      error?: string;
      done: boolean;
    };

// GET /api/usage: the caller's daily token budget (see limits.py).
type Allowance = {
  plan: string;
  used: number;
  limit: number;
  resets_at: string;
  signed_in: boolean;
};

const EXAMPLES = [
  "Can a grappled creature still cast spells?",
  "How far can I long jump?",
  "What does the Fireball spell do?",
];

// "7.4k" for compact token counts.
const thousands = (tokens: number) => `${(tokens / 1000).toFixed(1)}k`;

// The `error` message from an API error response, or a fallback.
async function errorMessage(res: Response): Promise<string> {
  try {
    const body: { error?: string } = await res.json();
    if (body.error) return body.error;
  } catch {
    // Not JSON (e.g. the dev proxy could not reach the API).
  }
  return `The tome is sealed (HTTP ${res.status}).`;
}

// Reads a server-sent event stream from a POST response (EventSource is GET-only).
async function* events(res: Response) {
  const reader = res.body!.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += value;
    let end;
    while ((end = buffer.indexOf("\n\n")) !== -1) {
      const line = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      if (line.startsWith("data: ")) yield JSON.parse(line.slice(6));
    }
  }
}

export default function Chat() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [allowance, setAllowance] = useState<Allowance | null>(null);
  const { isSignedIn, getToken } = useAuth();
  const session = useRef<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const field = useRef<HTMLTextAreaElement>(null);

  // Grow the textarea with its content; CSS caps it at 25% of the viewport,
  // past which it scrolls.
  useLayoutEffect(() => {
    const el = field.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [input]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [turns]);

  // Signed-in requests carry the Clerk session token, which the API verifies
  // to bill the user's budget; without one the API meters by IP. getToken()
  // refreshes the short-lived token when needed.
  const authHeaders = useCallback(async (): Promise<Record<string, string>> => {
    if (!isSignedIn) return {};
    const token = await getToken();
    if (!token) return {};
    return { Authorization: `Bearer ${token}` };
  }, [isSignedIn, getToken]);

  const refreshAllowance = useCallback(async () => {
    try {
      const res = await fetch("/api/usage", { headers: await authHeaders() });
      if (res.ok) setAllowance(await res.json());
    } catch {
      // The meter is informational; a failed refresh just leaves it stale.
    }
  }, [authHeaders]);

  // Signing in or out changes whose budget is shown.
  useEffect(() => {
    refreshAllowance();
  }, [refreshAllowance]);

  // Apply a change to the warlock turn that is currently streaming.
  const update = (
    fn: (t: Extract<Turn, { role: "warlock" }>) => Partial<Turn>,
  ) =>
    setTurns((ts) => {
      const last = ts[ts.length - 1];
      if (last?.role !== "warlock") return ts;
      return [...ts.slice(0, -1), { ...last, ...fn(last) } as Turn];
    });

  async function ask(question: string) {
    question = question.trim();
    if (!question || busy) return;
    setInput("");
    setBusy(true);
    setTurns((ts) => [
      ...ts,
      { role: "user", text: question },
      { role: "warlock", text: "", searches: [], sources: [], done: false },
    ]);
    try {
      const res = await fetch("/api/ask", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(await authHeaders()),
        },
        body: JSON.stringify({ question, session: session.current }),
      });
      if (!res.ok) throw new Error(await errorMessage(res));
      if (!res.body) throw new Error("The tome is sealed (empty response).");
      for await (const { type, data } of events(res)) {
        if (type === "session") session.current = data.session;
        else if (type === "search")
          update((t) => ({ searches: [...t.searches, data.query] }));
        else if (type === "text") update((t) => ({ text: t.text + data.text }));
        else if (type === "citation" && data.label)
          update((t) => {
            const found = t.sources.find((s) => s.label === data.label);
            const n = found?.n ?? t.sources.length + 1;
            const sources = found
              ? t.sources
              : [
                  ...t.sources,
                  {
                    n,
                    label: data.label,
                    title: data.title,
                    page: pages(data.page_start, data.page_end),
                    pageStart: data.page_start,
                    quote: data.cited_text,
                  },
                ];
            // Several citations of one source in a row collapse into one marker.
            const marker = `⟦${n}⟧`;
            return {
              sources,
              text: t.text.endsWith(marker) ? t.text : t.text + marker,
            };
          });
        else if (type === "error") update(() => ({ error: data.message }));
      }
    } catch (e) {
      update(() => ({
        error: e instanceof Error ? e.message : "The spell fizzled.",
      }));
    } finally {
      update(() => ({ done: true }));
      setBusy(false);
      refreshAllowance();
    }
  }

  return (
    <div className="flex-1 flex flex-col gap-6">
      {turns.length === 0 && (
        <div className="flex gap-4 items-start">
          <img
            src="/warlock_icon.jpg"
            alt="Warlock"
            width={64}
            height={64}
            className="portrait shrink-0 m-1"
          />
          <div className="pixel-box flex-1">
            <p className="font-pixel text-[10px] text-ember mb-2">WARLOCK</p>
            <p>
              Greetings, traveller. Ask me any rule from the SRD 5.2.1 and I
              shall find it in the tome, with the page.
            </p>
            <div className="flex flex-wrap gap-2 mt-3">
              {EXAMPLES.map((q) => (
                <button
                  key={q}
                  onClick={() => ask(q)}
                  className="text-left text-mist hover:text-ember border-2 border-slate px-2 cursor-pointer"
                >
                  ▶ {q}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {turns.map((turn, i) =>
        turn.role === "user" ? (
          <div key={i} className="flex justify-end">
            <div className="pixel-box slate max-w-[80%] whitespace-pre-wrap">
              {turn.text}
            </div>
          </div>
        ) : (
          <div key={i} className="flex flex-col gap-2">
            {turn.searches.length > 0 && (
              <p className="text-mist text-lg pl-1" aria-live="polite">
                ◆ Consulting the tome:{" "}
                {turn.searches.map((q) => `“${q}”`).join(" ◆ ")}
                {!turn.done && !turn.text && <span className="blink">…</span>}
              </p>
            )}
            <div className="flex gap-3 sm:gap-4 items-start">
              <img
                src="/warlock_icon.jpg"
                alt="Warlock"
                width={64}
                height={64}
                className="portrait shrink-0 m-1 w-12 h-12 sm:w-16 sm:h-16"
              />
              <div className="pixel-box flex-1 min-w-0">
                <p className="font-pixel text-[10px] text-ember mb-2">
                  WARLOCK
                </p>
                {turn.text ? (
                  <Markdown
                    text={turn.text}
                    noteHref={(n) => `#turn-${i}-source-${n}`}
                  />
                ) : (
                  !turn.error && (
                    <p className="text-mist">
                      The warlock leafs through the pages…
                    </p>
                  )
                )}
                {!turn.done && (
                  <span className="blink text-ember" aria-hidden="true">
                    █
                  </span>
                )}
                {turn.error && (
                  <p className="text-ember mt-2">✖ {turn.error}</p>
                )}
                {turn.sources.length > 0 && (
                  <div className="mt-3 pt-2 border-t-2 border-dashed border-slate flex flex-wrap gap-1">
                    {turn.sources.map((s) => (
                      <a
                        key={s.n}
                        id={`turn-${i}-source-${s.n}`}
                        href={srdPageHref(s.pageStart)}
                        target="_blank"
                        rel="noopener"
                        title={s.quote}
                        className="source-badge bg-crimson border-2 border-ink px-1.5 text-lg text-bone no-underline hover:bg-ember"
                      >
                        {s.n} {s.title} · {s.page}
                        <span className="sr-only">
                          {" "}
                          (opens the SRD PDF in a new tab)
                        </span>
                      </a>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        ),
      )}
      <div ref={bottom} />

      {allowance && (
        <p className="text-mist text-lg text-right -mb-4" aria-live="polite">
          ◆ Power used today: {thousands(allowance.used)} /{" "}
          {thousands(allowance.limit)}
          {!allowance.signed_in && " · sign in for more"}
        </p>
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          ask(input);
        }}
        className="pixel-box sticky bottom-4 mt-auto flex items-end gap-3 py-2!"
      >
        <span className="text-ember pb-1.5" aria-hidden="true">
          &gt;
        </span>
        <label htmlFor="question" className="sr-only">
          Your question
        </label>
        <textarea
          id="question"
          ref={field}
          rows={1}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            // Enter sends; Shift+Enter adds a line. Leave IME composition alone.
            if (
              e.key === "Enter" &&
              !e.shiftKey &&
              !e.nativeEvent.isComposing
            ) {
              e.preventDefault();
              ask(input);
            }
          }}
          placeholder="Ask about a spell, monster or rule…"
          autoComplete="off"
          className="flex-1 min-w-0 self-center resize-none max-h-[25dvh] overflow-y-auto py-1 bg-transparent outline-none placeholder:text-mist"
        />
        <button type="submit" className="pixel-btn" disabled={busy}>
          {busy ? "…" : "QUERY"}
        </button>
      </form>
    </div>
  );
}
