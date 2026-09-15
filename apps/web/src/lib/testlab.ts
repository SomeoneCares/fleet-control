import type { BlueprintTest, Suite, SuiteTest, TestRunSummary, TestStatus, Verdict } from "../api/client";
import type { Tone } from "./view";

// The four assurance verdicts (build document §6), never anything else.
export const VERDICT_TONE: Record<Verdict, Tone> = {
  "Evidence found": "success",
  "No evidence": "error",
  "Not verifiable": "neutral",
  "Policy blocked": "warning",
};

export const VERDICT_MEANING: Record<Verdict, string> = {
  "Evidence found": "The run's transcript shows it happened.",
  "No evidence": "The run's transcript does not show it: the agent claimed or was expected to do something that did not run.",
  "Not verifiable": "There is no transcript for this run, so Fleet Control cannot tell either way.",
  "Policy blocked": "A Fleet Control policy blocked the call.",
};

export function statusChip(status: TestStatus | "not_run"): { label: string; tone: Tone } {
  switch (status) {
    case "running": return { label: "Running", tone: "info" };
    case "passed": return { label: "Passed", tone: "success" };
    case "failed": return { label: "Failed", tone: "error" };
    case "not_verifiable": return { label: "Not verifiable", tone: "neutral" };
    case "error": return { label: "Error", tone: "error" };
    default: return { label: "Not run", tone: "neutral" };
  }
}

export interface SuiteGroup {
  key: string;
  label: string;
  kind: "agent" | "workflow";
  tests: SuiteTest[];
}

/** A suite's tests grouped by what they target: agents first (in blueprint order), then workflows. */
export function suiteGroups(suite: Suite): SuiteGroup[] {
  const groups: SuiteGroup[] = [];
  for (const a of suite.agents) {
    const tests = suite.tests.filter((t) => t.test.target === a.id);
    if (tests.length) groups.push({ key: a.id, label: a.id, kind: "agent", tests });
  }
  for (const w of suite.workflows) {
    const tests = suite.tests.filter((t) => t.test.target === w);
    if (tests.length) groups.push({ key: w, label: `Workflow: ${w}`, kind: "workflow", tests });
  }
  return groups;
}

/** Across every suite: how many finished last runs passed, and where and when the newest one ran. */
export function lastRunSummary(suites: Suite[]): { passed: number; ran: number; instance: string | null; at: number | null } {
  const runs = suites.flatMap((s) => s.tests.map((t) => t.last_run)).filter((r): r is TestRunSummary => r !== null && r.status !== "running");
  const newest = runs.reduce<TestRunSummary | null>((a, r) => (!a || r.created_at > a.created_at ? r : a), null);
  return { passed: runs.filter((r) => r.status === "passed").length, ran: runs.length, instance: newest?.instance_id ?? null, at: newest?.finished_at ?? newest?.created_at ?? null };
}

export const EVALUATOR_LABEL: Record<BlueprintTest["evaluator"], string> = {
  schema: "Output matches the agent's output contract",
  contains: "Output contains the expected text",
  exact: "Output is exactly the expected text",
  "artifact-exists": "The expected artifact was produced",
  none: "No output check (tools and limits only)",
};

export function newTest(target: string, id = ""): BlueprintTest {
  return {
    id, target, scenario: "", required_tools: [], forbidden_tools: [], expected_artifact: null, evaluator: "none", expected: null,
    limits: { max_seconds: 60, max_tokens: 20000, max_cost_usd: 0.1 },
  };
}

/** A test id from a short description ("Screener cites list versions" -> "screener-cites-list-versions"). */
export function testIdFrom(text: string): string {
  const id = text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 63).replace(/-+$/, "");
  return /^[a-z][a-z0-9-]{1,62}$/.test(id) ? id : "";
}
