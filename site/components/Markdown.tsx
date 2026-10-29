import type { ReactNode } from "react";

// Answers are short and use only a little markdown (bold, italics, bullets), so
// a tiny renderer is enough. Footnote markers arrive in the text as `⟦n⟧`.
const INLINE = /(\*\*[^*]+\*\*|\*[^*]+\*|⟦\d+⟧)/g;

type NoteHref = (n: number) => string;

function inline(text: string, key: string, noteHref?: NoteHref): ReactNode[] {
  return text.split(INLINE).map((part, i) => {
    const k = `${key}-${i}`;
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={k} className="text-ember font-normal">{part.slice(2, -2)}</strong>;
    if (part.startsWith("*") && part.endsWith("*") && part.length > 2) return <em key={k}>{part.slice(1, -1)}</em>;
    const note = part.match(/^⟦(\d+)⟧$/);
    if (note)
      return (
        <sup key={k} className="text-ember">
          {noteHref ? (
            <a href={noteHref(Number(note[1]))} aria-label={`Source ${note[1]}`} className="hover:text-bone no-underline">
              [{note[1]}]
            </a>
          ) : (
            `[${note[1]}]`
          )}
        </sup>
      );
    return part;
  });
}

const BULLET = /^\s*([-*]|\d+\.)\s+/;

// Groups lines into paragraphs, headings and lists: a run of bullet lines is a
// list even when it directly follows a paragraph line.
function groups(text: string) {
  const out: Array<{ kind: "p" | "ul" | "h"; lines: string[] }> = [];
  for (const line of text.trim().split("\n")) {
    const kind = !line.trim() ? null : BULLET.test(line) ? "ul" : /^#{1,4}\s/.test(line) ? "h" : "p";
    const last = out[out.length - 1];
    if (!kind) out.push({ kind: "p", lines: [] });
    else if (last && last.kind === kind && kind !== "h") last.lines.push(line);
    else out.push({ kind, lines: [line] });
  }
  return out.filter((g) => g.lines.length);
}

export default function Markdown({ text, noteHref }: { text: string; noteHref?: NoteHref }) {
  return (
    <>
      {groups(text).map(({ kind, lines }, b) => {
        if (kind === "ul")
          return (
            <ul key={b} className="my-2 space-y-1">
              {lines.map((l, i) => (
                <li key={i} className="flex gap-2">
                  <span className="text-crimson" aria-hidden="true">◆</span>
                  <span>{inline(l.replace(BULLET, ""), `${b}-${i}`, noteHref)}</span>
                </li>
              ))}
            </ul>
          );
        if (kind === "h")
          return <p key={b} className="font-pixel text-[11px] text-ember mt-3 mb-1">{lines[0].replace(/^#+\s+/, "")}</p>;
        return (
          <p key={b} className="my-2 first:mt-0 last:mb-0">
            {lines.map((l, i) => (
              <span key={i}>{i > 0 && <br />}{inline(l, `${b}-${i}`, noteHref)}</span>
            ))}
          </p>
        );
      })}
    </>
  );
}
