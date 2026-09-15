import type { Integration, IntegrationHealth, IntegrationsDoc } from "../api/client";
import type { Tone } from "./view";

export const HEALTH_TONE: Record<IntegrationHealth, Tone> = {
  healthy: "success",
  unreachable: "error",
  disabled: "neutral",
  unknown: "neutral",
};

export const HEALTH_LABEL: Record<IntegrationHealth, string> = {
  healthy: "Healthy",
  unreachable: "Unreachable",
  disabled: "Disabled",
  unknown: "Not probed",
};

export const KIND_LABEL: Record<Integration["kind"], string> = { mcp: "MCP", model: "Model provider" };

/** Which agents a tool is blocked for, and which need approval, from the blueprint's policies. */
export function toolRules(row: Integration, tool: string): { blockedFor: string[]; approvalFor: string[] } {
  const rule = row.allow[tool];
  return { blockedFor: rule?.blocked_for ?? [], approvalFor: rule?.approval_for ?? [] };
}

/** The agents that use it, each once: the same agent id can come from more than one blueprint. */
export function distinctAgents(row: Integration): { agent: string; blueprints: string[] }[] {
  const out: { agent: string; blueprints: string[] }[] = [];
  for (const u of row.used_by) {
    const seen = out.find((x) => x.agent === u.agent);
    if (seen) {
      if (!seen.blueprints.includes(u.blueprint)) seen.blueprints.push(u.blueprint);
    } else {
      out.push({ agent: u.agent, blueprints: [u.blueprint] });
    }
  }
  return out;
}

/** Agents allowed to use a server's tool: those whose blueprint lists the server, minus the blocked ones. */
export function allowedAgents(row: Integration, tool: string): string[] {
  const { blockedFor } = toolRules(row, tool);
  return distinctAgents(row).map((u) => u.agent).filter((a) => !blockedFor.includes(a));
}

/** The newest discovery time, and the instances that could be discovered but never have been. */
export function discoveryState(doc: IntegrationsDoc | null): { at: number | null; never: string[]; canDiscover: string[] } {
  const rows = doc?.discovery ?? [];
  const times = rows.map((d) => d.at).filter((t): t is number => t !== null);
  return {
    at: times.length ? Math.max(...times) : null,
    never: rows.filter((d) => d.can_discover && d.at === null).map((d) => d.instance_id),
    canDiscover: rows.filter((d) => d.can_discover).map((d) => d.instance_id),
  };
}

export function matchesQuery(row: Integration, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return [row.name, row.endpoint ?? "", ...row.models, ...row.instances, ...row.tools.map((t) => t.name), ...row.used_by.map((u) => u.agent)]
    .join(" ").toLowerCase().includes(q);
}
