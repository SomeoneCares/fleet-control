import type { AskSource, AskTurn } from "../api/client";

export type AnswerPart = { text: string } | { cite: string };

/** An answer as text and [S1]-style citations, so each citation can be shown as a link to its source. */
export function answerParts(answer: string): AnswerPart[] {
  const parts: AnswerPart[] = [];
  let last = 0;
  for (const m of answer.matchAll(/\[(S\d+)\]/g)) {
    if (m.index! > last) parts.push({ text: answer.slice(last, m.index) });
    parts.push({ cite: m[1] });
    last = m.index! + m[0].length;
  }
  if (last < answer.length) parts.push({ text: answer.slice(last) });
  return parts;
}

/** The sources an answer rests on, and the ones it was given but did not use. */
export function splitSources(turn: Pick<AskTurn, "sources" | "cited">): { used: AskSource[]; unused: AskSource[] } {
  return {
    used: turn.sources.filter((s) => turn.cited.includes(s.id)),
    unused: turn.sources.filter((s) => !turn.cited.includes(s.id)),
  };
}

/** Where a source opens: files and outputs in their own screens, a room as itself. */
export function sourceLink(s: Pick<AskSource, "kind" | "ref" | "zone">): string {
  if (s.kind === "room") return `/rooms/${encodeURIComponent(s.ref)}`;
  if (s.kind === "output") return `/outputs?id=${encodeURIComponent(s.ref)}`;
  return `/content?zone=${encodeURIComponent(s.zone)}&file=${encodeURIComponent(s.ref)}`;
}

export const SOURCE_KIND_LABEL: Record<AskSource["kind"], string> = { file: "File", output: "Fleet output", room: "Decision Room" };

/** A default name for an answer kept as a fleet output: the question, shortened. */
export function outputName(question: string): string {
  const q = question.trim().replace(/\s+/g, " ").replace(/[?.!]+$/, "");
  return q.length <= 80 ? q : `${q.slice(0, 77).trimEnd()}…`;
}
