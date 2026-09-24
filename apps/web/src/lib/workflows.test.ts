import { describe, expect, it } from "vitest";
import { gateClock, gateTimeout, stepSummary, titleCase } from "./workflows";

describe("workflow view logic", () => {
  it("writes steps the way the design does", () => {
    expect(titleCase("sanctions-screener")).toBe("Sanctions Screener");
    expect(stepSummary({ kind: "agent", members: [{ agent: "case-orchestrator", artifact: "case brief", input: "flagged transaction" }] }))
      .toBe("Input: flagged transaction · Artifact: case brief");
    expect(gateTimeout({ timeout: "24h", escalate_to: "mlro" })).toBe("24 h then escalate to Mlro");
    expect(gateTimeout({ timeout: "30m", escalate_to: "boss@bank.example" })).toBe("30 min then escalate to boss@bank.example");
    expect(gateTimeout({ timeout: "2d", escalate_to: null })).toBe("2 d, no escalation");
  });

  it("says how long a gate has left, and how late it is", () => {
    expect(gateClock({ timeout: "24h", started_at: 0 }, 3600)).toEqual({ text: "23 h left", late: false });
    expect(gateClock({ timeout: "24h", started_at: 0 }, 26 * 3600)).toEqual({ text: "2 h overdue", late: true });
    expect(gateClock({ timeout: "24h", started_at: null }, 0)).toBeNull();
  });
});
