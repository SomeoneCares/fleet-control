import type { GoamlReportRow, GoamlSettings, GoamlStatus } from "../api/client";
import type { Tone } from "./view";

export const GOAML_STATUS: Record<GoamlStatus, { label: string; tone: Tone; hint: string }> = {
  unchecked: { label: "Not checked", tone: "warning", hint: "No FIU schema is loaded yet: load the goAML XSD from the FIU's portal (Settings → goAML), then check it again." },
  invalid: { label: "Fails the FIU schema", tone: "error", hint: "Fix what is listed in the draft, then prepare the report again." },
  ready: { label: "Ready to file", tone: "success", hint: "Passed the FIU's schema. Download it, upload it in goAML, then record the FIU's reference here." },
  filed: { label: "Filed", tone: "neutral", hint: "Filed in goAML by a person; the FIU's reference is recorded." },
};

/** The report was checked against a schema other than the one loaded now: checking it again may change the verdict. */
export function checkedAgainstOlderSchema(report: Pick<GoamlReportRow, "schema" | "status">, settings: GoamlSettings | null): boolean {
  if (report.status === "filed" || !settings?.schema) return false;
  return !report.schema || report.schema.sha256 !== settings.schema.sha256;
}

/** What is still missing before a report can be prepared at all. */
export function goamlGaps(settings: GoamlSettings | null): string[] {
  const gaps: string[] = [];
  if (!settings?.profile?.rentity_id) gaps.push("the bank's reporting-entity id (Settings → goAML)");
  if (!settings?.schema) gaps.push("the FIU's goAML schema (Settings → goAML)");
  return gaps;
}
