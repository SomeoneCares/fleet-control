import type { WorkflowRunStatus, WorkflowStep, WorkflowStepStatus } from "../api/client";
import type { Tone } from "./view";

export const RUN_STATUS: Record<WorkflowRunStatus, { label: string; tone: Tone }> = {
  running: { label: "Running", tone: "info" },
  waiting: { label: "Waiting for a person", tone: "warning" },
  done: { label: "Done", tone: "success" },
  failed: { label: "Failed", tone: "error" },
  cancelled: { label: "Cancelled", tone: "neutral" },
};

export const STEP_STATUS: Record<WorkflowStepStatus, { label: string; tone: Tone }> = {
  pending: { label: "Not started", tone: "neutral" },
  running: { label: "Running", tone: "info" },
  waiting: { label: "Waiting", tone: "warning" },
  done: { label: "Done", tone: "success" },
  failed: { label: "Failed", tone: "error" },
  skipped: { label: "Skipped", tone: "neutral" },
};

/** "sanctions-screener" -> "Sanctions Screener": how the design names agents and roles. */
export function titleCase(id: string): string {
  return id.split(/[-_]/).filter(Boolean).map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");
}

/** The step's one-line description, as the design writes it. */
export function stepSummary(step: Pick<WorkflowStep, "kind" | "members" | "role" | "timeout" | "escalate_to" | "question_template">): string {
  if (step.kind === "human_gate") {
    return `Any ${titleCase(step.role ?? "")} · ${gateTimeout(step)}`;
  }
  if (step.kind === "decision_room") return step.question_template ? `Asks: ${step.question_template}` : "Opens a Decision Room with every artifact as evidence";
  const m = step.members?.[0];
  if (!m) return "";
  return [m.input ? `Input: ${m.input}` : null, m.artifact ? `Artifact: ${m.artifact}` : null].filter(Boolean).join(" · ");
}

/** "24 h then escalate to MLRO" */
export function gateTimeout(step: Pick<WorkflowStep, "timeout" | "escalate_to">): string {
  const t = (step.timeout ?? "24h").replace(/^(\d+)([mhd])$/, (_, n: string, u: string) => `${n} ${u === "m" ? "min" : u}`);
  return step.escalate_to ? `${t} then escalate to ${step.escalate_to.includes("@") ? step.escalate_to : titleCase(step.escalate_to)}` : `${t}, no escalation`;
}

/** How long a waiting gate has left, or how late it is. */
export function gateClock(step: Pick<WorkflowStep, "timeout" | "started_at">, now: number): { text: string; late: boolean } | null {
  if (step.started_at == null || !step.timeout) return null;
  const m = /^(\d+)([mhd])$/.exec(step.timeout);
  if (!m) return null;
  const due = step.started_at + Number(m[1]) * { m: 60, h: 3600, d: 86400 }[m[2] as "m" | "h" | "d"];
  const left = due - now;
  const fmt = (s: number) => (s >= 86400 ? `${Math.round(s / 86400)} d` : s >= 3600 ? `${Math.round(s / 3600)} h` : `${Math.max(1, Math.round(s / 60))} min`);
  return left >= 0 ? { text: `${fmt(left)} left`, late: false } : { text: `${fmt(-left)} overdue`, late: true };
}
