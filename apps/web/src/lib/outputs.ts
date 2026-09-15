import type { FleetOutput, OutputKind } from "../api/client";
import type { IconName } from "../components/ui";

export const KIND_LABEL: Record<OutputKind, string> = {
  document: "Document",
  structured: "Structured result",
  graph: "Graph",
  markdown: "Markdown",
  summary: "Summary",
};

export const KIND_ICON: Record<OutputKind, IconName> = {
  document: "file",
  structured: "layers",
  graph: "graph",
  markdown: "file",
  summary: "chat",
};

export function matchesQuery(o: FleetOutput, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return [o.name, o.produced_by, o.case ?? "", o.zone, o.provenance].join(" ").toLowerCase().includes(q);
}

/** The cases present in a list, newest output first, for the Case filter. */
export function casesOf(outputs: FleetOutput[]): string[] {
  const seen: string[] = [];
  for (const o of outputs) if (o.case && !seen.includes(o.case)) seen.push(o.case);
  return seen;
}
