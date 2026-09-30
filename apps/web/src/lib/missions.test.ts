import { describe, expect, it } from "vitest";
import type { MissionBlueprint, MissionSummary } from "../api/client";
import { MARKET, NO_FILTERS, REACH, bySector, filterMissions, guardrails, workflowSteps } from "./missions";

const m = (id: string, sector: string, reach: MissionSummary["reach"], saturation: MissionSummary["saturation"], title = id): MissionSummary => ({
  id, title, sector, sector_label: sector, summary: `${title} summary`, reach, saturation, modules: ["SAS Intelligent Decisioning"],
  connectors: [], agents: ["a"], gates: 1,
});

const LIST = [
  m("credit-early-warning", "banking", "mcp-today", "contested", "Credit early warning"),
  m("ecl-movement-explanation", "banking", "connector", "open", "ECL explanation"),
  m("clinical-data-review", "health-life-sciences", "public-api", "crowded", "Clinical data review"),
];

describe("mission filters", () => {
  it("keeps everything with no filter", () => {
    expect(filterMissions(LIST, NO_FILTERS)).toHaveLength(3);
  });
  it("combines sector, reach, market and text", () => {
    expect(filterMissions(LIST, { ...NO_FILTERS, sector: "banking" }).map((x) => x.id)).toEqual(["credit-early-warning", "ecl-movement-explanation"]);
    expect(filterMissions(LIST, { ...NO_FILTERS, reach: "mcp-today" }).map((x) => x.id)).toEqual(["credit-early-warning"]);
    expect(filterMissions(LIST, { ...NO_FILTERS, market: "open" }).map((x) => x.id)).toEqual(["ecl-movement-explanation"]);
    expect(filterMissions(LIST, { ...NO_FILTERS, text: "clinical REVIEW" }).map((x) => x.id)).toEqual(["clinical-data-review"]);
    expect(filterMissions(LIST, { ...NO_FILTERS, text: "decisioning" })).toHaveLength(3);  // modules are searched too
  });
});

describe("grouping and labels", () => {
  it("groups in sector order and drops empty sectors", () => {
    const groups = bySector(LIST, { banking: "Banking", insurance: "Insurance", "health-life-sciences": "Health" });
    expect(groups.map((g) => [g.label, g.missions.length])).toEqual([["Banking", 2], ["Health", 1]]);
  });
  it("labels every reach and market level", () => {
    expect(Object.keys(REACH).sort()).toEqual(["connector", "mcp-today", "public-api"]);
    expect(Object.keys(MARKET).sort()).toEqual(["contested", "crowded", "open", "thin", "unknown"]);
  });
});

describe("workflow and guardrails", () => {
  const bp: MissionBlueprint = {
    mission: "x",
    policies: [
      { id: "sas-viya-read-only", kind: "tool-denylist", description: "", applies_to: ["*"], params: { tools: ["sas-viya.execute_sas_code", "sas-viya.delete_report"] }, enforcement: "block" },
      { id: "no-web", kind: "tool-denylist", description: "", applies_to: ["*"], params: { tools: ["web.fetch"] }, enforcement: "block" },
      { id: "sas-viya-approve-scoring", kind: "external-action-approval", description: "", applies_to: ["*"], params: { tools: ["sas-viya.score_data"] }, enforcement: "approve" },
    ],
    agents: [],
    workflows: [],
    tests: [],
  };
  it("summarises what is denied and what needs a person", () => {
    expect(guardrails(bp)).toEqual({ deniedSas: 2, approval: ["score_data"], otherDenied: ["web.fetch"] });
  });
  it("turns steps into agents, gates and the decision room", () => {
    const steps = workflowSteps([
      { parallel: [{ agent: "a", artifact: "a.md" }, { agent: "b" }] },
      { agent: "c", artifact: "c.md" },
      { human_gate: "approver", timeout: "2d", on_reject: "a", escalate_to: "admin" },
      { open_decision_room: true, question_template: "Decide {case}" },
    ]);
    expect(steps.map((s) => s.kind)).toEqual(["agents", "agents", "gate", "room"]);
    expect(steps[0]).toEqual({ kind: "agents", agents: ["a", "b"], artifacts: ["a.md"] });
    expect(steps[2]).toEqual({ kind: "gate", role: "approver", timeout: "2d", onReject: "a", escalateTo: "admin" });
  });
});
