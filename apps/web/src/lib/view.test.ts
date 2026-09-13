import { describe, expect, it } from "vitest";
import type { Instance, Plan } from "../api/client";
import { capabilityRows, changeCounts, driftCount, driftRows, formatValue, instanceStatus, planPhase, timeAgo } from "./view";

const NOW = 1_789_400_000;

function inst(over: Partial<Instance> = {}): Instance {
  return {
    id: "hermes-staging-eu-01", environment: "staging", owner: "me", mode: "agent", status: "healthy",
    hermes_version: "0.21.2", agent_version: "0.1.0", capabilities: ["runs", "sessions", "profiles.read", "profiles.write", "hooks", "policy.enforce"],
    report: { surfaces: { api: "ok", dashboard: "loopback", cli: "ok" }, plugins: { fleetcontrol: "installed", langfuse: "not enabled" } },
    last_heartbeat: NOW - 10, created_at: NOW - 3600, open_drift: 0, live_profile_count: 5, applied: null, ...over,
  };
}

function plan(over: Partial<Plan> = {}): Plan {
  return {
    id: "plan_1", target_instance: "x", environment: "staging", blueprint: { name: "aml", version: 3 },
    changes: [
      { kind: "create", symbol: "+", object: "profile a", description: "", method: "Agent", risk: "medium", ops: [] },
      { kind: "update", symbol: "~", object: "profile b", description: "", method: "Agent", risk: "low", ops: [] },
      { kind: "approval", symbol: "!", object: "policy p", description: "", method: "Agent", risk: "high", ops: [] },
    ],
    no_change: { profiles: [] }, unmanaged_profiles: [], approvals_required: 0, approvals: [], policy_push: {}, can_apply: true,
    blocked_reason: null, status: "planned", ...over,
  };
}

describe("instances", () => {
  it("derives status from heartbeat, drift and health", () => {
    expect(instanceStatus(inst(), NOW).label).toBe("Healthy");
    expect(instanceStatus(inst({ open_drift: 2 }), NOW).label).toBe("Drift");
    expect(instanceStatus(inst({ last_heartbeat: NOW - 300 }), NOW)).toEqual({ label: "Offline", tone: "error" });
    expect(instanceStatus(inst({ last_heartbeat: null, agent_version: null }), NOW).label).toBe("Waiting for agent");
    expect(instanceStatus(inst({ mode: "api-only", status: "pending", last_heartbeat: null }), NOW).label).toBe("Not checked");
  });

  it("capability rows are honest about API-only instances", () => {
    const agent = capabilityRows(inst());
    expect(agent.find((r) => r.label === "Write profiles & config")?.value).toBe("Agent");
    expect(agent.find((r) => r.label === "Tool-call evidence")?.value).toBe("Agent hook (real time)");
    const apiOnly = capabilityRows(inst({ mode: "api-only", capabilities: ["runs", "sessions"], report: { surfaces: { api: "ok" } } }));
    expect(apiOnly.find((r) => r.label === "Write profiles & config")?.value).toBe("Not available (API only)");
    expect(apiOnly.find((r) => r.label === "Tool-call evidence")?.value).toBe("Not verifiable");
    expect(capabilityRows(inst({ agent_version: null }))).toHaveLength(1);
  });

  it("formats relative time", () => {
    expect(timeAgo(NOW - 40, NOW)).toBe("40s ago");
    expect(timeAgo(NOW - 600, NOW)).toBe("10 min ago");
    expect(timeAgo(null, NOW)).toBe("never");
  });
});

describe("plans", () => {
  it("counts rows by kind", () => {
    expect(changeCounts(plan())).toEqual({ create: 1, update: 1, remove: 0, approval: 1 });
  });

  it("knows what the apply button may do", () => {
    expect(planPhase(plan())).toBe("ready");
    expect(planPhase(plan({ can_apply: false, blocked_reason: "API only" }))).toBe("blocked");
    expect(planPhase(plan({ approvals_required: 2, approvals: ["a"] }))).toBe("needs-approval");
    expect(planPhase(plan({ changes: [plan().changes[2]] }))).toBe("no-changes");
    expect(planPhase(plan({ status: "applied" }))).toBe("applied");
  });
});

describe("drift", () => {
  const drift = {
    "sanctions-screener": [{ field: "skills", blueprint: ["a", "b"], live: ["a", "b", "quick-lookup"] }],
    "ownership-tracer": [{ field: "model", blueprint: { provider: "local", name: "llama-4-70b-q4" }, live: { provider: "local", name: "llama-4-8b" } }],
    challenger: [{ field: "profile", blueprint: "present", live: "missing (404)" }],
  };

  it("turns a report into rows with added/removed items", () => {
    const rows = driftRows(drift);
    expect(rows).toHaveLength(3);
    expect(rows[0]).toMatchObject({ profile: "sanctions-screener", label: "skills", added: ["quick-lookup"], change: "added" });
    expect(rows[1]).toMatchObject({ blueprint: "llama-4-70b-q4 (local)", live: "llama-4-8b (local)", change: "changed" });
    expect(rows[2].change).toBe("missing");
    expect(driftCount(drift)).toBe(3);
  });

  it("shortens SOUL hashes", () => {
    expect(formatValue("soul_sha256", "sha256:0123456789abcdef0123")).toBe("sha256 0123456789ab…");
  });
});
