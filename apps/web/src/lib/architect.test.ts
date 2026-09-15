import { describe, expect, it } from "vitest";
import type { ProposedAgent } from "../api/client";
import { agentName, blueprintNameFor, constraintSummary, decisionCounts, effectiveAgent, fleetShape } from "./architect";

const agent = (id: string, delegates: string[] = []): ProposedAgent => ({
  id, name: "", role: "r", model: { provider: "nous", name: "m", data_class: "raw" }, soul: { objective: "o", principles: [], boundaries: [] },
  skills: [], toolsets: [], mcps: [], delegates_to: delegates,
});

describe("architect view logic", () => {
  it("applies edits over the proposal, model and soul field by field", () => {
    const edited = effectiveAgent(agent("extractor"), { role: "Extracts totals", model: { provider: "nous", name: "llama-4", data_class: "raw" } });
    expect(edited.role).toBe("Extracts totals");
    expect(edited.model.name).toBe("llama-4");
    expect(edited.soul.objective).toBe("o");
    expect(effectiveAgent(agent("x"))).toEqual(agent("x"));
  });

  it("counts decisions on the latest proposal only", () => {
    const latest = { proposal: { agents: [agent("a"), agent("b"), agent("c")] } } as never;
    expect(decisionCounts({ latest, decisions: { a: "accepted", b: "removed", gone: "accepted" } })).toEqual({ accepted: 1, removed: 1, total: 3 });
    expect(decisionCounts({ latest: null, decisions: {} })).toEqual({ accepted: 0, removed: 0, total: 0 });
  });

  it("describes the fleet shape", () => {
    expect(fleetShape([agent("boss", ["a", "b"]), agent("a"), agent("b")])).toBe("1 orchestrator · 2 specialists");
    expect(fleetShape([agent("solo")])).toBe("1 agent");
  });

  it("summarises constraints and names things", () => {
    expect(constraintSummary({ data_residency: "on-premises", cloud_models: "redacted-only", external_actions_need_approval: true, budget_usd_per_day: 40 }))
      .toEqual(["Customer data stays on-premises", "Cloud models see redacted summaries only", "External actions need human approval", "Budget $40 a day"]);
    expect(agentName({ id: "case-orchestrator", name: "" })).toBe("Case Orchestrator");
    expect(blueprintNameFor("Triage incoming vendor invoices: extract totals")).toBe("triage-incoming-vendor-fleet");
    expect(blueprintNameFor("!!")).toBe("proposed-fleet");
  });
});
