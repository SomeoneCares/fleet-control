import { describe, expect, it } from "vitest";
import type { Integration, IntegrationsDoc } from "../api/client";
import { HEALTH_LABEL, allowedAgents, discoveryState, distinctAgents, matchesQuery, toolRules } from "./integrations";

const row: Integration = {
  kind: "mcp", name: "opensanctions", instances: ["prod-01"], profiles: ["prod-01/screener"], environments: ["production"],
  tools: [{ name: "search", description: "Search the lists" }, { name: "submit", description: "" }], models: [],
  used_by: [{ agent: "screener", blueprint: "aml", version: 2 }, { agent: "drafter", blueprint: "aml", version: 2 }],
  planned_by: [], profile_health: [{ instance: "prod-01", profile: "screener", health: "healthy", error: null }],
  allow: { "opensanctions.submit": { blocked_for: ["drafter"], approval_for: ["screener"] } },
  health: "healthy", error: null, enabled_everywhere: true, endpoint: "https://os.example/mcp", transport: "http", auth: "oauth",
};

describe("integrations view logic", () => {
  it("reads the per-tool rules and who is left allowed", () => {
    expect(toolRules(row, "opensanctions.submit")).toEqual({ blockedFor: ["drafter"], approvalFor: ["screener"] });
    expect(toolRules(row, "opensanctions.search")).toEqual({ blockedFor: [], approvalFor: [] });
    expect(allowedAgents(row, "opensanctions.submit")).toEqual(["screener"]);
    expect(allowedAgents(row, "opensanctions.search")).toEqual(["screener", "drafter"]);
  });

  it("counts an agent once even when several blueprints define it", () => {
    const twice: Integration = { ...row, used_by: [{ agent: "screener", blueprint: "aml", version: 2 }, { agent: "screener", blueprint: "kyc", version: 1 }] };
    expect(distinctAgents(twice)).toEqual([{ agent: "screener", blueprints: ["aml", "kyc"] }]);
    expect(allowedAgents(twice, "opensanctions.search")).toEqual(["screener"]);
  });

  it("labels a server some profiles cannot reach as degraded", () => {
    expect(HEALTH_LABEL.degraded).toBe("Degraded");
  });

  it("summarises discovery", () => {
    const doc: IntegrationsDoc = {
      integrations: [], discovery: [
        { instance_id: "a", at: 100, can_discover: true },
        { instance_id: "b", at: null, can_discover: true },
        { instance_id: "c", at: null, can_discover: false },
      ],
    };
    expect(discoveryState(doc)).toEqual({ at: 100, never: ["b"], canDiscover: ["a", "b"] });
    expect(discoveryState(null)).toEqual({ at: null, never: [], canDiscover: [] });
  });

  it("filters on everything a person might type", () => {
    for (const q of ["", "OPENSANC", "search", "prod-01", "screener", "os.example"]) expect(matchesQuery(row, q), q).toBe(true);
    expect(matchesQuery(row, "langfuse")).toBe(false);
    expect(HEALTH_LABEL.unknown).toBe("Not probed");
  });
});
