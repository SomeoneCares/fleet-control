import { describe, expect, it } from "vitest";
import type { AgentDoc, BlueprintDoc } from "../api/client";
import { actorKind, auditLabel, prettyId } from "./view";
import { cleanLines, renderSoul } from "./soul";
import { NODE_H, NODE_W, layoutTopology } from "./topology";

function agent(id: string, over: Partial<AgentDoc> = {}): AgentDoc {
  return {
    id, role: `${id} role`, model: { provider: "local", name: "llama-4-70b-q4", data_class: "raw" },
    soul: { objective: "o", principles: [], boundaries: [] }, skills: [], toolsets: [], mcps: [], delegates_to: [],
    content_zones: [], tests: [], ...over,
  };
}

const doc: BlueprintDoc = {
  metadata: { name: "aml-investigation", version: 3, owner: "dana" },
  mission: "m", policies: [], tests: [], delivery: [], targets: [],
  agents: [
    agent("case-orchestrator", { delegates_to: ["sanctions-screener", "ownership-tracer", "challenger", "sar-drafter"] }),
    agent("sanctions-screener"), agent("ownership-tracer"),
    agent("challenger", { model: { provider: "openai", name: "gpt-5", data_class: "redacted-only" } }), agent("sar-drafter"),
  ],
  workflows: [{
    id: "case-to-sar-draft", tests: [],
    steps: [
      { agent: "case-orchestrator", artifact: "case-brief.md" },
      { parallel: [{ agent: "sanctions-screener" }, { agent: "ownership-tracer" }] },
      { agent: "challenger" },
      { human_gate: "approver", timeout: "24h", escalate_to: "mlro" },
      { agent: "sar-drafter", artifact: "sar-draft.docx" },
      { open_decision_room: true, question_template: "Should we file a SAR?" },
    ],
  }],
};

describe("topology", () => {
  it("lays a workflow out as rows, parallel steps side by side", () => {
    const t = layoutTopology(doc, "case-to-sar-draft");
    expect(t.mode).toBe("workflow");
    expect(t.nodes).toHaveLength(7);
    const y = (id: string) => t.nodes.find((n) => n.id.endsWith(id))!.y;
    expect(y(":sanctions-screener")).toBe(y(":ownership-tracer"));
    expect(y(":challenger")).toBeGreaterThan(y(":sanctions-screener"));
    expect(t.edges).toHaveLength(1 * 2 + 2 * 1 + 1 + 1 + 1);
    expect(t.nodes.find((n) => n.kind === "gate")).toMatchObject({ label: "Approver sign-off", sub: "Timeout 24h, then MLRO" });
    expect(t.nodes.find((n) => n.agent === "challenger")!.chip).toBe("gpt-5 · redacted");
    expect(t.nodes.find((n) => n.agent === "case-orchestrator")!.sub).toBe("→ case-brief.md");
    expect(t.unplaced).toEqual([]);
    for (const n of t.nodes) expect(n.x + NODE_W).toBeLessThanOrEqual(t.width);
    expect(Math.max(...t.nodes.map((n) => n.y + NODE_H))).toBeLessThanOrEqual(t.height);
  });

  it("falls back to delegation layers without a workflow", () => {
    const t = layoutTopology({ ...doc, workflows: [] }, null);
    expect(t.mode).toBe("delegation");
    expect(t.nodes).toHaveLength(5);
    expect(t.edges).toHaveLength(4);
    expect(t.nodes.find((n) => n.agent === "case-orchestrator")!.y).toBeLessThan(t.nodes.find((n) => n.agent === "challenger")!.y);
  });

  it("lists agents the chosen workflow does not use", () => {
    const t = layoutTopology({ ...doc, workflows: [{ id: "w", tests: [], steps: [{ agent: "challenger" }] }] }, "w");
    expect(t.unplaced).toEqual(["case-orchestrator", "sanctions-screener", "ownership-tracer", "sar-drafter"]);
  });
});

describe("soul", () => {
  it("renders like the Python schema", () => {
    const text = renderSoul({ objective: " Screen names. ", principles: ["Query first."], boundaries: [],
      output_contract: { format: "json", required: ["match_count", "matches"], artifact: null } });
    expect(text).toBe("# Objective\n\nScreen names.\n\n# Operating principles\n\n- Query first.\n\n# Output contract\n\nformat: json\nrequired: match_count, matches\n");
    expect(renderSoul({ objective: "ignored", principles: [], boundaries: [], raw: "custom soul  \n\n" })).toBe("custom soul\n");
  });

  it("drops blank lines on save", () => {
    expect(cleanLines([" a ", "", "b", "   "])).toEqual(["a", "b"]);
  });
});

describe("labels", () => {
  it("names ids and audit events for people", () => {
    expect(prettyId("sar-drafter")).toBe("SAR Drafter");
    expect(prettyId("case-orchestrator")).toBe("Case Orchestrator");
    expect(prettyId("case-to-sar-draft")).toBe("Case to SAR Draft");
    expect(prettyId("to-do")).toBe("To Do");
    expect(auditLabel("drift.revert_planned")).toBe("Drift revert planned");
    expect(auditLabel("something.new")).toBe("something.new");
    expect(actorKind("agent:hermes-lab-01")).toBe("agent");
    expect(actorKind("fleetcontrol")).toBe("system");
    expect(actorKind("dev@local")).toBe("person");
  });
});
