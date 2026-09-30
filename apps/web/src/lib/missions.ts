// Mission library view logic: labels, filters, grouping and the summaries the detail view shows.
import type { MissionBlueprint, MissionMarketLevel, MissionReach, MissionStep, MissionSummary } from "../api/client";
import type { Tone } from "./view";

export const REACH: Record<MissionReach, { label: string; tone: Tone; hint: string }> = {
  "mcp-today": { label: "Works today", tone: "success", hint: "Everything the agents need is in the SAS Viya MCP Server." },
  "public-api": { label: "Needs connector", tone: "warning", hint: "SAS publishes a REST API for it; a connector wraps it as MCP tools." },
  connector: { label: "Needs SAS access", tone: "error", hint: "No public SAS API: the connector is built with SAS or customer access." },
};

export const MARKET: Record<MissionMarketLevel, { label: string; tone: Tone; hint: string }> = {
  open: { label: "Open market", tone: "success", hint: "No agent product found for this task." },
  thin: { label: "Thin market", tone: "success", hint: "Assistants exist, but no governed agent workforce." },
  contested: { label: "Contested", tone: "warning", hint: "Some vendors offer agents; none on SAS." },
  crowded: { label: "Crowded", tone: "error", hint: "Several vendors already sell agents for this." },
  unknown: { label: "Not researched", tone: "neutral", hint: "The market for this mission has not been researched yet." },
};

export interface MissionFilters {
  sector: string; // "" = all
  reach: MissionReach | "";
  market: MissionMarketLevel | "";
  text: string;
}

export const NO_FILTERS: MissionFilters = { sector: "", reach: "", market: "", text: "" };

export function filterMissions(missions: MissionSummary[], f: MissionFilters): MissionSummary[] {
  const words = f.text.toLowerCase().split(/\s+/).filter(Boolean);
  return missions.filter((m) => {
    if (f.sector && m.sector !== f.sector) return false;
    if (f.reach && m.reach !== f.reach) return false;
    if (f.market && m.saturation !== f.market) return false;
    const hay = [m.title, m.summary, m.sector_label, ...m.modules].join(" ").toLowerCase();
    return words.every((w) => hay.includes(w));
  });
}

/** Missions grouped by sector, in the library's sector order. */
export function bySector(missions: MissionSummary[], sectors: Record<string, string>): { sector: string; label: string; missions: MissionSummary[] }[] {
  return Object.entries(sectors)
    .map(([sector, label]) => ({ sector, label, missions: missions.filter((m) => m.sector === sector) }))
    .filter((g) => g.missions.length > 0);
}

export type StepView =
  | { kind: "agents"; agents: string[]; artifacts: string[] }
  | { kind: "gate"; role: string; timeout: string; onReject: string | null; escalateTo: string | null }
  | { kind: "room"; question: string | null };

export function workflowSteps(steps: MissionStep[]): StepView[] {
  return steps.map((s): StepView => {
    if ("parallel" in s) return { kind: "agents", agents: s.parallel.map((a) => a.agent), artifacts: s.parallel.map((a) => a.artifact ?? "").filter(Boolean) };
    if ("human_gate" in s) return { kind: "gate", role: s.human_gate, timeout: s.timeout ?? "24h", onReject: s.on_reject ?? null, escalateTo: s.escalate_to ?? null };
    if ("open_decision_room" in s) return { kind: "room", question: s.question_template ?? null };
    return { kind: "agents", agents: [s.agent], artifacts: s.artifact ? [s.artifact] : [] };
  });
}

/** What the policies do, in one line each: how many SAS tools are denied, which need a person, what else is blocked. */
export function guardrails(bp: MissionBlueprint): { deniedSas: number; approval: string[]; otherDenied: string[] } {
  const denied = bp.policies.filter((p) => p.enforcement === "block").flatMap((p) => p.params.tools ?? []);
  const approval = bp.policies.filter((p) => p.enforcement === "approve").flatMap((p) => p.params.tools ?? []);
  const strip = (t: string) => t.replace(/^sas-viya\./, "");
  return {
    deniedSas: new Set(denied.filter((t) => t.startsWith("sas-viya."))).size,
    approval: [...new Set(approval)].map(strip).sort(),
    otherDenied: [...new Set(denied.filter((t) => !t.startsWith("sas-viya.")))].sort(),
  };
}
