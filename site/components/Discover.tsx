import { useState } from "react";
import { pages as pageLabel, srdPageHref } from "./srd";

type Result = {
  id: string;
  chapter: string;
  path: string[];
  title: string;
  page_start: number;
  page_end: number;
  text: string;
};

const SUGGESTIONS = ["Grappled", "Fireball", "Owlbear", "Bag of Holding", "Opportunity Attack", "Rogue"];

const pages = (r: Result) => pageLabel(r.page_start, r.page_end);

function Entry({ r }: { r: Result }) {
  const [open, setOpen] = useState(false);
  const paragraphs = r.text.split("\n").filter((p) => p.trim());
  const shown = open ? paragraphs : paragraphs.slice(0, 2);
  return (
    <article className="pixel-box">
      <p className="text-mist text-lg truncate">{r.path.slice(0, -1).join(" › ")}</p>
      <div className="flex items-baseline justify-between gap-3 mb-1">
        <h2 className="font-pixel text-[11px] leading-relaxed text-ember">{r.title}</h2>
        <a
          href={srdPageHref(r.page_start)}
          target="_blank"
          rel="noopener"
          className="bg-crimson border-2 border-ink px-1.5 text-lg shrink-0 text-bone no-underline hover:bg-ember"
        >
          {pages(r)}
          <span className="sr-only"> (opens the SRD PDF in a new tab)</span>
        </a>
      </div>
      {shown.map((p, i) => (
        <p key={i} className="my-1">{p}</p>
      ))}
      {paragraphs.length > 2 && (
        <button onClick={() => setOpen(!open)} className="text-mist hover:text-ember cursor-pointer mt-1">
          {open ? "▲ Close the page" : `▼ Read on (${paragraphs.length - 2} more)`}
        </button>
      )}
    </article>
  );
}

export default function Discover() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Result[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function search(q: string) {
    q = q.trim();
    if (!q) {
      setError("Name something to look up first.");
      return;
    }
    setQuery(q);
    setBusy(true);
    setError("");
    try {
      const res = await fetch(`/api/search?q=${encodeURIComponent(q)}&k=8`);
      if (!res.ok) throw new Error(`The tome is sealed (HTTP ${res.status}).`);
      setResults((await res.json()).results);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The spell fizzled.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="font-pixel text-sm sm:text-base text-bone mb-2">THE SPELLBOOK</h1>
        <p className="text-mist">Search the SRD directly: spells, monsters, conditions, items and class features.</p>
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          search(query);
        }}
        className="pixel-box flex items-center gap-3 !py-2"
      >
        <span className="text-ember" aria-hidden="true">?</span>
        <label htmlFor="search" className="sr-only">Search the SRD</label>
        <input
          id="search"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setError("");
          }}
          placeholder="Owlbear"
          autoComplete="off"
          className="flex-1 min-w-0 bg-transparent outline-none placeholder:text-mist"
        />
        <button type="submit" className="pixel-btn" disabled={busy}>
          {busy ? "…" : "SEEK"}
        </button>
      </form>
      {error && <p className="text-ember -mt-3">✖ {error}</p>}

      <div className="flex flex-wrap gap-2 -mt-2">
        {SUGGESTIONS.map((s) => (
          <button key={s} onClick={() => search(s)} className="text-mist hover:text-ember border-2 border-slate px-2 cursor-pointer">
            {s}
          </button>
        ))}
      </div>

      {results?.length === 0 && <p className="text-mist">The tome is silent on that. Try other words.</p>}
      <div className="flex flex-col gap-6">
        {results?.map((r) => <Entry key={r.id} r={r} />)}
      </div>
    </div>
  );
}
