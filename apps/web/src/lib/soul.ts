import type { SoulDoc } from "../api/client";

/** Mirrors Soul.render() in packages/blueprint_schema: what SOUL.md becomes on the Hermes profile. */
export function renderSoul(s: SoulDoc): string {
  if (s.raw !== null && s.raw !== undefined) return s.raw.replace(/\s+$/, "") + "\n";
  const parts = ["# Objective", "", s.objective.trim(), ""];
  if (s.principles.length) parts.push("# Operating principles", "", ...s.principles.map((p) => `- ${p}`), "");
  if (s.boundaries.length) parts.push("# Boundaries", "", ...s.boundaries.map((b) => `- ${b}`), "");
  const oc = s.output_contract;
  if (oc) {
    parts.push("# Output contract", "", `format: ${oc.format}`);
    if (oc.required.length) parts.push(`required: ${oc.required.join(", ")}`);
    if (oc.artifact) parts.push(`artifact: ${oc.artifact}`);
    parts.push("");
  }
  return parts.join("\n");
}

/** Editing keeps blank lines so a textarea can grow; saving drops them. */
export function cleanLines(lines: string[]): string[] {
  return lines.map((l) => l.trim()).filter(Boolean);
}
