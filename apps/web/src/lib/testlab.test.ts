import { describe, expect, it } from "vitest";
import type { Suite, SuiteTest } from "../api/client";
import { VERDICT_TONE, lastRunSummary, newTest, statusChip, suiteGroups, testIdFrom } from "./testlab";

const t = (id: string, target: string, run: SuiteTest["last_run"] = null): SuiteTest => ({
  test: newTest(target, id), target_kind: target === "wf" ? "workflow" : "agent", profile: target, last_run: run,
});
const run = (status: "passed" | "failed" | "running", at: number, instance = "lab-01") => ({ id: `r${at}`, status, instance_id: instance, created_at: at, finished_at: at + 5, failure: null });

const suite: Suite = {
  blueprint: "aml", version: 3, status: "applied", workflows: ["wf"], gates_production: true, applied_on: [],
  agents: [{ id: "screener", profile: "screener", role: "" }, { id: "idle", profile: "idle", role: "" }, { id: "challenger", profile: "challenger", role: "" }],
  tests: [t("a", "screener", run("passed", 10)), t("b", "challenger", run("failed", 30, "staging-01")), t("c", "screener"), t("d", "wf", run("running", 40))],
};

describe("test lab view logic", () => {
  it("groups tests by target, agents first and only when they have tests", () => {
    expect(suiteGroups(suite).map((g) => [g.label, g.tests.map((x) => x.test.id)])).toEqual([
      ["screener", ["a", "c"]], ["challenger", ["b"]], ["Workflow: wf", ["d"]],
    ]);
  });

  it("summarises the last finished runs", () => {
    expect(lastRunSummary([suite])).toEqual({ passed: 1, ran: 2, instance: "staging-01", at: 35 });
    expect(lastRunSummary([])).toEqual({ passed: 0, ran: 0, instance: null, at: null });
  });

  it("labels statuses and verdicts with the four verdicts only", () => {
    expect(statusChip("not_verifiable")).toEqual({ label: "Not verifiable", tone: "neutral" });
    expect(statusChip("not_run").label).toBe("Not run");
    expect(Object.keys(VERDICT_TONE)).toEqual(["Evidence found", "No evidence", "Not verifiable", "Policy blocked"]);
  });

  it("makes test ids", () => {
    expect(testIdFrom("Screener cites list versions!")).toBe("screener-cites-list-versions");
    expect(testIdFrom("9")).toBe("");
  });
});
