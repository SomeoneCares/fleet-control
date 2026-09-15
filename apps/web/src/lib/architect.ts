import type { ArchitectConstraints, ArchitectSession, ProposedAgent } from "../api/client";
import { BLUEPRINT_NAME } from "./names";

export const MISSION_MIN = 20;
export const MISSION_MAX = 2000;

// design/screens/FleetArchitectEmpty: "Try an example"
export const EXAMPLE_MISSIONS: { label: string; text: string }[] = [
  {
    label: "Vendor invoice triage",
    text: "Triage incoming vendor invoices: extract totals and line items, match them to purchase orders, flag duplicates and "
      + "price mismatches, and prepare a payment batch for a finance approver. Invoices carry supplier bank details, so they stay "
      + "on-premises; nothing is paid without human approval.",
  },
  {
    label: "KYC refresh for dormant accounts",
    text: "Refresh know-your-customer files for accounts dormant for more than 18 months: gather the identity documents on file, "
      + "screen names against sanctions and PEP lists, summarise the gaps, and draft outreach letters for a compliance officer to "
      + "review. Customer data stays on-premises; cloud models may only see redacted summaries.",
  },
  {
    label: "Nightly model-drift review",
    text: "Every night, compare the credit-scoring model's input distributions and approval rates with the last 30 days, explain "
      + "any significant drift in plain language, and open a ticket for the model risk team when a threshold is crossed. Read-only "
      + "access to the feature store; no changes to production models.",
  },
];

export const DEFAULT_CONSTRAINTS: ArchitectConstraints = {
  data_residency: "any",
  cloud_models: "allowed",
  external_actions_need_approval: true,
  budget_usd_per_day: null,
};

/** The agent as the person will save it: the architect's proposal with their edits over it. */
export function effectiveAgent(agent: ProposedAgent, edit?: Partial<ProposedAgent>): ProposedAgent {
  if (!edit) return agent;
  return { ...agent, ...edit, model: { ...agent.model, ...(edit.model ?? {}) }, soul: { ...agent.soul, ...(edit.soul ?? {}) } };
}

export function decisionCounts(s: Pick<ArchitectSession, "latest" | "decisions">): { accepted: number; removed: number; total: number } {
  const agents = s.latest?.proposal?.agents ?? [];
  return {
    total: agents.length,
    accepted: agents.filter((a) => s.decisions[a.id] === "accepted").length,
    removed: agents.filter((a) => s.decisions[a.id] === "removed").length,
  };
}

/** "1 orchestrator · 4 specialists": an orchestrator is an agent that delegates. */
export function fleetShape(agents: ProposedAgent[]): string {
  const orchestrators = agents.filter((a) => a.delegates_to.length > 0).length;
  const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
  if (!orchestrators) return plural(agents.length, "agent");
  return `${plural(orchestrators, "orchestrator")} · ${plural(agents.length - orchestrators, "specialist")}`;
}

export function constraintSummary(c: ArchitectConstraints): string[] {
  const out = [
    c.data_residency === "on-premises" ? "Customer data stays on-premises"
      : c.data_residency === "region" ? "Customer data stays within its region" : "No data residency restriction",
    c.cloud_models === "redacted-only" ? "Cloud models see redacted summaries only"
      : c.cloud_models === "none" ? "Local models only" : "Cloud models allowed",
    c.external_actions_need_approval ? "External actions need human approval" : "External actions need no approval",
  ];
  if (c.budget_usd_per_day !== null) out.push(`Budget $${c.budget_usd_per_day} a day`);
  return out;
}

export function agentName(a: Pick<ProposedAgent, "id" | "name">): string {
  return a.name.trim() || a.id.split("-").map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w)).join(" ");
}

const STOP_WORDS = new Set(["the", "and", "for", "every", "each", "with", "from", "that", "this", "our", "all", "into", "their"]);

/** A blueprint name to start from: three telling words of the mission, then "-fleet". */
export function blueprintNameFor(mission: string): string {
  const words = mission.toLowerCase().replace(/[^a-z0-9\s-]/g, " ").split(/\s+/)
    .filter((w) => w.length > 2 && !STOP_WORDS.has(w)).slice(0, 3);
  if (!words.length) return "proposed-fleet";
  const name = `${words.join("-")}-fleet`.replace(/-+/g, "-");
  return BLUEPRINT_NAME.test(name) ? name : "proposed-fleet";
}
