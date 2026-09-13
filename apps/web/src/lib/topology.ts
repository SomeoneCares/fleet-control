// Fleet Designer layout: a blueprint's workflow (or, without workflows, its delegation graph) as rows of
// nodes with orthogonal edges. Pure and deterministic, so the same blueprint always draws the same way.
import type { AgentDoc, BlueprintDoc, WorkflowStepDoc } from "../api/client";
import { prettyId } from "./view";

export const NODE_W = 176;
export const NODE_H = 112;
export const GAP_X = 24;
export const GAP_Y = 56;
export const PAD = 24;

export type NodeKind = "agent" | "gate" | "output";

export interface TNode {
  id: string;
  kind: NodeKind;
  label: string;
  sub: string;
  chip: string | null;
  agent?: string;
  step?: number;
  x: number;
  y: number;
}

export interface TEdge {
  from: string;
  to: string;
  d: string;
}

export interface Topology {
  mode: "workflow" | "delegation";
  nodes: TNode[];
  edges: TEdge[];
  width: number;
  height: number;
  unplaced: string[];
}

type Unplaced = Omit<TNode, "x" | "y">;

export function modelChip(a: AgentDoc): string {
  return `${a.model.name}${a.model.data_class === "redacted-only" ? " · redacted" : ""}`;
}

function agentNode(byId: Map<string, AgentDoc>, id: string, key: string, artifact?: string | null, step?: number): Unplaced {
  const a = byId.get(id);
  return { id: key, kind: "agent", label: prettyId(id), sub: artifact ? `→ ${artifact}` : a?.role ?? "not in this blueprint", chip: a ? modelChip(a) : null, agent: id, step };
}

function stepNodes(s: WorkflowStepDoc, i: number, byId: Map<string, AgentDoc>): Unplaced[] {
  if ("parallel" in s) return s.parallel.map((p, j) => agentNode(byId, p.agent, `s${i}.${j}:${p.agent}`, p.artifact, i));
  if ("agent" in s) return [agentNode(byId, s.agent, `s${i}:${s.agent}`, s.artifact, i)];
  if ("human_gate" in s) {
    return [{ id: `s${i}:gate`, kind: "gate", label: `${prettyId(s.human_gate)} sign-off`,
      sub: `Timeout ${s.timeout}${s.escalate_to ? `, then ${prettyId(s.escalate_to)}` : ""}`, chip: "Approval required", step: i }];
  }
  return [{ id: `s${i}:room`, kind: "output", label: "Decision Room", sub: s.question_template ?? "Opened for approvers", chip: "Workspace", step: i }];
}

function edgePath(a: TNode, b: TNode): string {
  const x1 = a.x + NODE_W / 2;
  const y1 = a.y + NODE_H;
  const x2 = b.x + NODE_W / 2;
  const y2 = b.y - 2;
  if (y2 <= y1) return `M ${x1} ${a.y} L ${x2} ${b.y + NODE_H}`; // a delegation cycle points back up
  const ym = y1 + (y2 - y1) / 2;
  return `M ${x1} ${y1} V ${ym} H ${x2} V ${y2}`;
}

export function layoutTopology(doc: BlueprintDoc, workflowId: string | null): Topology {
  const byId = new Map(doc.agents.map((a) => [a.id, a]));
  const wf = doc.workflows.find((w) => w.id === workflowId) ?? null;
  let rows: Unplaced[][];
  let explicit: [string, string][] | null = null;

  if (wf) {
    rows = wf.steps.map((s, i) => stepNodes(s, i, byId));
  } else {
    const delegated = new Set(doc.agents.flatMap((a) => a.delegates_to));
    const level = new Map<string, number>();
    const queue = doc.agents.filter((a) => !delegated.has(a.id)).map((a) => a.id);
    for (const id of queue) level.set(id, 0);
    for (let i = 0; i < queue.length; i++) {
      for (const d of byId.get(queue[i])?.delegates_to ?? []) {
        if (!level.has(d)) {
          level.set(d, (level.get(queue[i]) ?? 0) + 1);
          queue.push(d);
        }
      }
    }
    for (const a of doc.agents) if (!level.has(a.id)) level.set(a.id, 0); // agents only reachable through a cycle
    const depth = Math.max(0, ...level.values());
    rows = Array.from({ length: depth + 1 }, (_, l) => doc.agents.filter((a) => level.get(a.id) === l).map((a) => agentNode(byId, a.id, `a:${a.id}`)));
    explicit = doc.agents.flatMap((a) => a.delegates_to.map((d): [string, string] => [`a:${a.id}`, `a:${d}`]));
  }

  rows = rows.filter((r) => r.length > 0);
  const widest = Math.max(1, ...rows.map((r) => r.length));
  const width = PAD * 2 + widest * NODE_W + (widest - 1) * GAP_X;
  const height = PAD * 2 + rows.length * NODE_H + Math.max(0, rows.length - 1) * GAP_Y;
  const nodes: TNode[] = rows.flatMap((r, ri) => {
    const x0 = (width - (r.length * NODE_W + (r.length - 1) * GAP_X)) / 2;
    return r.map((n, ci) => ({ ...n, x: x0 + ci * (NODE_W + GAP_X), y: PAD + ri * (NODE_H + GAP_Y) }));
  });
  const pos = new Map(nodes.map((n) => [n.id, n]));
  const pairs = explicit ?? rows.slice(0, -1).flatMap((r, ri) => r.flatMap((a) => rows[ri + 1].map((b): [string, string] => [a.id, b.id])));
  const edges = pairs
    .filter(([f, t]) => pos.has(f) && pos.has(t))
    .map(([f, t]) => ({ from: f, to: t, d: edgePath(pos.get(f)!, pos.get(t)!) }));
  const placed = new Set(nodes.map((n) => n.agent).filter(Boolean));
  return { mode: wf ? "workflow" : "delegation", nodes, edges, width, height, unplaced: doc.agents.filter((a) => !placed.has(a.id)).map((a) => a.id) };
}
