// Pure view logic shared by the screens. No React here, so it is unit-tested directly.
import type { DriftMap, Environment, Instance, Me, Plan, PlanSummary } from "../api/client";

export type Tone = "success" | "warning" | "error" | "neutral" | "info";

// ---------------------------------------------------------------- formatting

export const ENV_LABEL: Record<Environment, string> = { lab: "Lab", staging: "Staging", production: "Production" };

export function timeAgo(ts: number | null | undefined, now = Date.now() / 1000): string {
  if (!ts) return "never";
  const s = Math.max(0, Math.round(now - ts));
  if (s < 45) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  return `${Math.round(h / 24)} d ago`;
}

export function formatDate(ts: number): string {
  return new Date(ts * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export function formatDateTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

const ACRONYMS = new Set(["sar", "mlro", "aml", "kyc", "mcp", "api", "ubo", "soc", "llm"]);
const SMALL_WORDS = new Set(["a", "and", "for", "in", "of", "on", "or", "the", "to"]);

/** "sar-drafter" -> "SAR Drafter", "case-to-sar-draft" -> "Case to SAR Draft"; ids stay visible in mono where they matter. */
export function prettyId(id: string): string {
  return id.split(/[-_]/).filter(Boolean).map((w, i) =>
    ACRONYMS.has(w) ? w.toUpperCase() : i > 0 && SMALL_WORDS.has(w) ? w : w[0].toUpperCase() + w.slice(1)).join(" ");
}

// ---------------------------------------------------------------- audit

const AUDIT_LABEL: Record<string, string> = {
  "instance.created": "Instance connected",
  "instance.import_requested": "Import requested",
  "agent.paired": "Agent paired",
  "blueprint.saved": "Blueprint saved",
  "blueprint.edited": "Blueprint edited",
  "blueprint.draft_created": "Draft created",
  "plan.created": "Plan created",
  "plan.approved": "Plan approved",
  "plan.apply_requested": "Apply requested",
  "job.done": "Agent job done",
  "job.failed": "Agent job failed",
  "drift.scan_requested": "Drift scan requested",
  "drift.ignored_once": "Drift ignored once",
  "drift.exception_created": "Drift exception created",
  "drift.revert_planned": "Drift revert planned",
  "drift.accepted": "Drift accepted",
  "audit.exported": "Audit log exported",
};

export function auditLabel(action: string): string {
  return AUDIT_LABEL[action] ?? action;
}

export function actorKind(actor: string): "person" | "agent" | "system" {
  if (actor.startsWith("agent:")) return "agent";
  if (actor === "fleetcontrol") return "system";
  return "person";
}

// ---------------------------------------------------------------- instances

/** The daemon heartbeats every 30 s; three missed beats is offline. */
export const HEARTBEAT_STALE_SECONDS = 90;

export function instanceStatus(inst: Instance, now = Date.now() / 1000): { label: string; tone: Tone } {
  if (inst.mode === "agent") {
    if (!inst.last_heartbeat) return { label: "Waiting for agent", tone: "neutral" };
    if (now - inst.last_heartbeat > HEARTBEAT_STALE_SECONDS) return { label: "Offline", tone: "error" };
  } else if (inst.status === "pending") {
    return { label: "Not checked", tone: "neutral" };
  }
  if (inst.open_drift > 0) return { label: "Drift", tone: "warning" };
  if (inst.status === "healthy") return { label: "Healthy", tone: "success" };
  if (inst.status === "degraded") return { label: "Degraded", tone: "warning" };
  return { label: "Unknown", tone: "neutral" };
}

export interface CapabilityRow {
  label: string;
  value: string;
  tone: Tone;
}

/** "What Fleet Control can do here": derived from the agent's capability report, never assumed. */
export function capabilityRows(inst: Instance): CapabilityRow[] {
  const r = inst.report ?? {};
  const caps = new Set(inst.capabilities ?? r.capabilities ?? []);
  const apiOk = r.surfaces?.api === "ok";
  if (inst.mode === "agent" && !inst.agent_version) {
    return [{ label: "Fleet Control Agent", value: "Not paired yet", tone: "neutral" }];
  }
  const agent = inst.mode === "agent";
  return [
    {
      label: "Read profiles, skills, MCPs",
      value: agent && r.surfaces?.dashboard === "loopback" ? "Agent" : apiOk ? "Stable API (partial)" : "Not available",
      tone: agent && r.surfaces?.dashboard === "loopback" ? "success" : apiOk ? "warning" : "neutral",
    },
    { label: "Submit runs, stream events", value: apiOk ? "Stable API" : "Not available", tone: apiOk ? "success" : "neutral" },
    {
      label: "Write profiles & config",
      value: agent && caps.has("profiles.write") ? "Agent" : "Not available (API only)",
      tone: agent && caps.has("profiles.write") ? "success" : "neutral",
    },
    {
      label: "Tool-call evidence",
      value: caps.has("hooks") ? "Agent hook (real time)" : r.plugins?.langfuse === "enabled" ? "Langfuse trace (after the fact)" : "Not verifiable",
      tone: caps.has("hooks") ? "success" : r.plugins?.langfuse === "enabled" ? "warning" : "neutral",
    },
    {
      label: "Policy enforcement",
      value: caps.has("policy.enforce") ? "Agent pre_tool_call" : "Not enforced",
      tone: caps.has("policy.enforce") ? "success" : "neutral",
    },
  ];
}

// ---------------------------------------------------------------- plans

export function changeCounts(plan: Plan): Record<"create" | "update" | "remove" | "approval", number> {
  const c = { create: 0, update: 0, remove: 0, approval: 0 };
  for (const r of plan.changes) c[r.kind] += 1;
  return c;
}

export type PlanPhase = "blocked" | "no-changes" | "needs-approval" | "ready" | "applying" | "applied" | "failed";

export function planPhase(plan: Plan): PlanPhase {
  if (plan.status === "applying" || plan.status === "applied" || plan.status === "failed") return plan.status;
  if (!plan.can_apply) return "blocked";
  if (!plan.changes.some((r) => r.kind === "create" || r.kind === "update" || r.kind === "remove")) return "no-changes";
  if (plan.approvals.length < plan.approvals_required) return "needs-approval";
  return "ready";
}

export function applyLabel(env: Environment): string {
  return `Apply to ${ENV_LABEL[env].toLowerCase()}`;
}

type ApprovalView = Pick<PlanSummary, "environment" | "approvals" | "approvals_required" | "status"> & { created_by?: string };

/** Mirrors the API's approval rules so the screen explains a missing Approve button instead of hiding it. */
export function approvalFor(plan: ApprovalView, me: Me): { can: boolean; reason: string | null } {
  if (plan.status !== "planned") return { can: false, reason: null };
  if (plan.approvals.length >= plan.approvals_required) return { can: false, reason: null };
  const production = plan.environment === "production";
  if (!me.permissions.includes(production ? "plans.approve.production" : "plans.approve.nonprod")) {
    return { can: false, reason: production ? "Production plans are approved by an Admin or an Approver." : `The ${me.role_label} role cannot approve this plan.` };
  }
  if (plan.created_by === me.email) return { can: false, reason: "You created this plan; another person has to approve it." };
  if (plan.approvals.includes(me.email)) return { can: false, reason: "You have approved this plan; it needs someone else too." };
  return { can: true, reason: null };
}

export function canApply(plan: Pick<Plan, "environment">, me: Me): boolean {
  return me.permissions.includes(plan.environment === "production" ? "plans.apply.production" : "plans.apply.nonprod");
}

export function initials(nameOrEmail: string): string {
  const words = nameOrEmail.split("@")[0].split(/[\s._-]+/).filter(Boolean);
  return (words.length > 1 ? words[0][0] + words[1][0] : (words[0] ?? "?").slice(0, 2)).toUpperCase();
}

// ---------------------------------------------------------------- drift

export const FIELD_LABEL: Record<string, string> = {
  description: "description",
  model: "model",
  soul_sha256: "SOUL",
  skills: "skills",
  toolsets: "toolsets",
  mcps: "MCP servers",
  profile: "profile",
};

export function formatValue(field: string, v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (field === "model" && typeof v === "object") {
    const m = v as { name?: string; provider?: string };
    return m.name ? `${m.name}${m.provider ? ` (${m.provider})` : ""}` : "—";
  }
  if (field === "soul_sha256" && typeof v === "string") return v.replace(/^sha256:/, "sha256 ").slice(0, 19) + "…";
  if (Array.isArray(v)) return v.length ? v.join(", ") : "none";
  return String(v);
}

export interface DriftRow {
  profile: string;
  field: string;
  label: string;
  blueprint: string;
  live: string;
  added: string[];
  removed: string[];
  change: "added" | "removed" | "changed" | "missing";
}

export function driftRows(drift: DriftMap | null | undefined): DriftRow[] {
  const rows: DriftRow[] = [];
  for (const [profile, diffs] of Object.entries(drift ?? {})) {
    for (const d of diffs) {
      const bp = Array.isArray(d.blueprint) ? (d.blueprint as string[]) : null;
      const lv = Array.isArray(d.live) ? (d.live as string[]) : null;
      const added = bp && lv ? lv.filter((x) => !bp.includes(x)) : [];
      const removed = bp && lv ? bp.filter((x) => !lv.includes(x)) : [];
      rows.push({
        profile,
        field: d.field,
        label: FIELD_LABEL[d.field] ?? d.field,
        blueprint: formatValue(d.field, d.blueprint),
        live: formatValue(d.field, d.live),
        added,
        removed,
        change: d.field === "profile" ? "missing" : added.length && !removed.length ? "added" : removed.length && !added.length ? "removed" : "changed",
      });
    }
  }
  return rows;
}

export function driftCount(drift: DriftMap | null | undefined): number {
  return Object.values(drift ?? {}).reduce((n, diffs) => n + diffs.length, 0);
}
